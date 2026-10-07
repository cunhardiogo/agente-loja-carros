from datetime import date, datetime, timedelta, timezone

from app import leads

FORM_FILA = """*AGENDAMENTO*
*(Fila Negociação)*

🏛️ LOJA: Soberano

📍LOCALIZAÇÃO: Barra

👨🏻‍💻 ATENDENTE: Renata

🙎🏻‍♂️ CLIENTE: Adilson
(21) 98050-3235


📆 DATA: 29/09/2026

🕐 *HORÁRIO: Ligação*

🚘 VEÍCULO: *Outalander azul*

🙋🏻‍♂️ VENDEDOR: Edson

📩 CANAL DE VENDA: Tráfego"""

FORM_TURNO = FORM_FILA.replace("*(Fila Negociação)*", "*(Fila Negociação)*\n*TURNO MANHÃ*").replace("Ligação", "9h")
FORM_VISITA = FORM_FILA.replace("*AGENDAMENTO*\n*(Fila Negociação)*", "*AGENDAMENTO NORMAL*") \
    .replace("Ligação", "10:00").replace("VENDEDOR: Edson", "VENDEDOR: Vinícius")


def test_parse_fila_vira_negociacao_telefone():
    p = leads.parse_formulario(FORM_FILA, date(2026, 9, 29))
    assert p["tipo"] == "negociacao_telefone"
    assert p["fila"] == "Fila Negociação"
    assert (p["sdr"], p["cliente_nome"], p["telefone"]) == ("Renata", "Adilson", "5521980503235")
    assert (p["data_agendada"], p["veiculo"], p["vendedor_nome"], p["canal"]) == \
        ("2026-09-29", "Outalander azul", "Edson", "Tráfego")


def test_parse_turno_e_visita():
    assert leads.parse_formulario(FORM_TURNO)["tipo"] == "turno"
    v = leads.parse_formulario(FORM_VISITA)
    assert (v["tipo"], v["horario"], v["fila"]) == ("visita", "10:00", None)


def test_vendedor_sem_nome_fica_vazio():
    assert leads.parse_formulario(FORM_FILA.replace("Edson", "???"))["vendedor_nome"] is None


def test_eh_formulario():
    assert leads.eh_formulario(FORM_FILA) and leads.eh_formulario(FORM_VISITA)
    assert not leads.eh_formulario("☝🏼*Cliente a caminho da loja.*")


def test_classificar_resposta():
    ref = date(2026, 10, 3)
    casos = {"Vendido Fastback 2026": "vendido", "Não veio e não tem previsão": "nao_veio",
             "Cancelado": "cancelado", "Negociando": "negociando", "Confirmado, mas vem a tarde!": "confirmado",
             "Já em contato !": "retorno"}
    for texto, esperado in casos.items():
        assert leads.classificar_resposta(texto, ref)[0] == esperado
    assert leads.classificar_resposta("05/10", ref) == ("remarcado", "2026-10-05")


def _lead(**kw):
    base = {"id": "abcd1234-0000", "tipo": "negociacao_telefone", "cliente_nome": "Jairo", "veiculo": "Toro",
            "vendedor_nome": "Yan", "vendedor_id": "v1", "sdr": "Renata", "status": "aguardando_retorno",
            "recebido_em": "2026-10-07T12:00:00+00:00", "cobranca_status": None, "aviso_dono_em": None,
            "ultimo_retorno_em": None, "cobranca_texto": "texto padrão da cobrança"}
    base.update(kw)
    return base


def test_prazos_avisa_30_e_propoe_60(monkeypatch):
    enviados, updates = [], []
    lead = _lead()
    monkeypatch.setattr(leads.db, "select_all", lambda *a, **k: [lead])
    monkeypatch.setattr(leads.db, "update", lambda t, d, p: updates.append(d) or lead.update(d))
    monkeypatch.setattr(leads.evolution, "enviar_texto", lambda n, t: enviados.append(t))
    t0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    leads.checar_prazos(t0 + timedelta(minutes=10))
    assert not enviados
    leads.checar_prazos(t0 + timedelta(minutes=31))
    assert enviados[-1].startswith("⏰") and lead["aviso_dono_em"]
    leads.checar_prazos(t0 + timedelta(minutes=45))
    assert len(enviados) == 1
    leads.checar_prazos(t0 + timedelta(minutes=61))
    assert enviados[-1].startswith("📨") and "ABCD" in enviados[-1] and lead["cobranca_status"] == "proposta"


def _setup_aprovacao(monkeypatch, lead, telefone="5521981262421"):
    enviados, updates = [], []

    def select(tabela, params=None):
        if tabela == "leads":
            return [lead] if params.get("cobranca_status") == "eq.proposta" or params.get("id") else []
        if tabela == "vendedores":
            return [{"telefone": telefone}]
        return []
    monkeypatch.setattr(leads.db, "select", select)
    monkeypatch.setattr(leads.db, "update", lambda t, d, p: updates.append(d))
    monkeypatch.setattr(leads.evolution, "enviar_por_coletor", lambda n, t: enviados.append((n, t)))
    return enviados, updates


def test_aprovar_com_codigo_envia_texto_padrao(monkeypatch):
    lead = _lead(cobranca_status="proposta")
    enviados, updates = _setup_aprovacao(monkeypatch, lead)
    assert "enviada" in leads.tentar_resolver("ok ABCD")
    assert enviados == [("5521981262421", "texto padrão da cobrança")]
    assert updates[-1]["cobranca_status"] == "enviada"


def test_texto_corrigido_e_descartar(monkeypatch):
    lead = _lead(cobranca_status="proposta")
    enviados, updates = _setup_aprovacao(monkeypatch, lead)
    leads.tentar_resolver("abcd: Yan, me dá um retorno do Jairo agora por favor")
    assert enviados[-1][1] == "Yan, me dá um retorno do Jairo agora por favor"
    leads.tentar_resolver("não abcd")
    assert updates[-1]["cobranca_status"] == "descartada"


def test_codigo_de_outra_pendencia_nao_e_capturado(monkeypatch):
    lead = _lead(cobranca_status="proposta")
    _setup_aprovacao(monkeypatch, lead)
    assert leads.tentar_resolver("sim F00D") is None
    assert leads.tentar_resolver("quantos carros vendemos hoje?") is None


def test_nao_envia_se_vendedor_ja_respondeu(monkeypatch):
    lead = _lead(cobranca_status="proposta", ultimo_retorno_em="2026-10-07T13:10:00+00:00")
    enviados, _ = _setup_aprovacao(monkeypatch, lead)
    assert "já respondeu" in leads.tentar_resolver("ok abcd") and not enviados
