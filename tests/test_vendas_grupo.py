from datetime import date, datetime, timezone

from app import datas, vendas_grupo as vg

RESUMO = """Resumo de Venda

📆 Data da venda:  06/10/2026
📅 Data da entrega:  09/10/2026

👨‍💻 Vendedor: Carlos

🚘 Modelo: FIAT
 Versão: Fastback
📅 Ano: 2026
🌈 Cor: cinza
🔢 Km: 19.000
🧮 Estoque: (X) Sim ()Não
🔤 Placa: TTD-9E46

💰 Tabela: R$ 119.900                                Vendido: R$ 119.900
🚫 Desconto:
💸 Over: 500 no cartão de crédito
♻ Retorno:
🏦 Banco:  A vista
🏦 Financiado:
🏦 Pix: 100.000 Pix 19.900 cartão                     🚘. Carro:
🚘 Placa:
🚘 Valor troca:
✅ Total: R$ 119.900
🧾 IPVA: ()Cliente (X) Loja

OBS: carro será revisado pela Fiat
por nossa conta

Nome: Victoria Girao
CPF/ CNPJ : 150.184.597-78                         EMAIL: victoria@gmail.com
Tel: (21) 979510902
Endereço: Rua A, 10
CEP: 22000-000
 📲 Portal da Venda: site

ANEXAR CNH E COMPROVANTE DE RESIDÊNCIA"""


def test_parse_resumo_completo():
    c = vg.parse_resumo(RESUMO)
    f = vg.campos_venda(c, date(2026, 10, 7))
    assert (f["modelo"], f["versao"], f["placa"], f["ano"], f["km"]) == ("FIAT", "Fastback", "TTD9E46", 2026, 19000)
    assert (f["tabela_preco"], f["valor_venda"], f["valor_total"]) == (119900, 119900, 119900)
    assert f["banco"] == "A vista" and f["valor_pix"].startswith("100.000")
    assert f["troca_modelo"] is None and f["troca_placa"] is None
    assert (f["ipva"], f["em_estoque"], f["portal_venda"]) == ("loja", True, "site")
    assert f["cliente_cpf"] == "150.184.597-78" and f["cliente_email"] == "victoria@gmail.com"
    assert f["cliente_telefone"] == "5521979510902"
    assert f["data_venda"] == "2026-10-06" and f["data_entrega_prevista"] == "2026-10-09"
    assert "revisado pela Fiat" in f["observacoes"] and "por nossa conta" in f["observacoes"]
    assert vg.pendencias_venda({**f, "vendedor_id": "v1"}) == []


def test_pendencias_so_opcionais_podem_faltar():
    txt = RESUMO.replace("CEP: 22000-000", "CEP:").replace("Total: R$ 119.900", "Total: R$") \
        .replace("09/10/2026", "xx/10/2026").replace("Over: 500 no cartão de crédito", "Over:")
    f = vg.campos_venda(vg.parse_resumo(txt), date(2026, 10, 7))
    assert vg.pendencias_venda({**f, "vendedor_id": "v1"}) == ["data da entrega", "total", "CEP"]


def test_aviso_curto_e_reserva():
    assert vg.classificar_aviso("Fastback TTD-9E46 vendido") == "venda"
    assert vg.classificar_aviso("Hrv 2019 vendida ✅") == "venda"
    assert vg.classificar_aviso("Venda Vinicius") == "venda"
    assert vg.classificar_aviso("Pulse Hybrid reservado") == "reserva"
    assert vg.classificar_aviso("Recorde de vendas no dia?") is None
    assert vg.classificar_aviso(RESUMO) is None
    assert vg.placa_norm("Fit 2018 LUH6H38 vendido ✅") == "LUH6H38"
    assert vg.placa_norm("Hrv 2019 vendida") is None
    assert vg._modelo_do_aviso("Kwid 2025 SVN-1D97 vendido") == "Kwid 2025"


def _sp(d, h, m=0):
    return datetime(2026, 10, d, h, m, tzinfo=datas.TZ)


def test_prazo_resumo_3h_ou_9h_do_dia_seguinte():
    assert vg.prazo_resumo(_sp(7, 10)) == _sp(7, 13)
    assert vg.prazo_resumo(_sp(7, 15, 30)) == _sp(7, 18, 30)
    assert vg.prazo_resumo(_sp(7, 17)) == _sp(8, 9)      # passaria das 19h
    assert vg.prazo_resumo(_sp(7, 21)) == _sp(8, 9)


def _venda(**kw):
    base = {"id": "beef0000-1111", "status_venda": "aguardando_resumo", "modelo": "Fastback", "versao": None,
            "placa": "TTD9E46", "vendedor_id": "v1", "aviso_em": "2026-10-07T13:00:00+00:00", "cobrado": {},
            "cobranca_status": None, "pendencias": [], "historico": False, "removido": False}
    base.update(kw)
    return base


def test_checar_prazos_propoe_cobranca_do_resumo(monkeypatch):
    v = _venda()
    enviados = []
    monkeypatch.setattr(vg.db, "select_all", lambda *a, **k: [v])
    monkeypatch.setattr(vg.db, "select", lambda t, p=None: [{"nome": "Carlos"}])
    monkeypatch.setattr(vg.db, "update", lambda t, d, p: v.update(d))
    monkeypatch.setattr(vg.evolution, "enviar_texto", lambda n, t: enviados.append(t))
    vg.checar_prazos(datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc))  # 12h SP: antes do prazo (13h)
    assert not enviados
    vg.checar_prazos(datetime(2026, 10, 7, 16, 1, tzinfo=timezone.utc))
    assert "Resumo de Venda atrasado" in enviados[0] and "BEEF" in enviados[0]
    assert v["cobranca_status"] == "proposta" and v["cobrado"]["resumo"]
    v["cobranca_status"] = "enviada"
    vg.checar_prazos(datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc))
    assert len(enviados) == 1  # cada motivo é cobrado uma vez


def test_aprovar_cobranca_de_venda(monkeypatch):
    v = _venda(cobranca_status="proposta", cobranca_motivo="resumo", cobranca_texto="manda o resumo")
    enviados, updates = [], []

    def select(t, p=None):
        if t == "vendas":
            return [v]
        if t == "vendedores":
            return [{"telefone": "5521995828941", "nome": "Carlos"}]
        return []
    monkeypatch.setattr(vg.db, "select", select)
    monkeypatch.setattr(vg.db, "update", lambda t, d, p: updates.append(d))
    monkeypatch.setattr(vg.evolution, "enviar_por_coletor", lambda n, t: enviados.append((n, t)))
    assert "enviada" in vg.tentar_resolver("ok beef")
    assert enviados == [("5521995828941", "manda o resumo")]
    v["status_venda"] = "completa"  # resumo chegou depois da proposta
    assert "já foi resolvido" in vg.tentar_resolver("ok beef")


def test_pagamento_repetido_sem_id_nao_duplica(monkeypatch):
    monkeypatch.setattr(vg.db, "select", lambda t, p=None: [{"id": "x"}] if t == "pagamentos" else [])
    r = vg._pagamento({"valor": 500, "pago_em": "2026-10-07T08:07:07"}, None, "m1", "a", datetime.now(timezone.utc),
                      "comprovante.pdf", False)
    assert r == {"ignored": "pagamento_repetido"}


def test_pagamento_para_terceiro_nao_conta(monkeypatch):
    monkeypatch.setattr(vg.db, "select", lambda t, p=None: [])
    r = vg._pagamento({"valor": 349.96, "pago_em": "2026-10-06T10:00:00", "recebedor": "Claro S/A"}, None, "m2", "a",
                      datetime(2026, 10, 7, 12, tzinfo=timezone.utc), None, False)
    assert r == {"ignored": "pagamento_terceiro"}


def test_queda_e_ranking(monkeypatch):
    assert vg.classificar_aviso("A venda do Fastback caiu") == "queda"
    assert vg.classificar_aviso("Cliente do Kwid desistiu") == "queda"
    rows = [{"modelo": "FIAT", "versao": "Fastback", "ano": 2026, "placa": "TTD9E46", "vendedor_id": "c", "data_venda": "2026-10-06"},
            {"modelo": "FIT", "versao": "LX", "ano": 2018, "placa": "LUH6H38", "vendedor_id": "v", "data_venda": "2026-10-06"},
            {"modelo": "Mt03", "versao": "MT03", "ano": 2020, "placa": "RJR0E26", "vendedor_id": "v", "data_venda": "2026-10-06"},
            {"modelo": "Fiat toro", "versao": "ultra", "ano": 2021, "placa": "RJI2F10", "vendedor_id": "e", "data_venda": "2026-10-06"}]
    monkeypatch.setattr(vg.db, "select", lambda t, p=None: [{"id": "c", "nome": "Carlos"}, {"id": "v", "nome": "Vinicius"},
                                                            {"id": "e", "nome": "Edson"}])
    monkeypatch.setattr(vg.db, "select_all", lambda t, p=None: rows)
    txt = vg.texto_ranking("2026-10")
    assert txt.startswith("💰*TOTAL DE VENDAS GRUPO SB: 4*")
    assert "*🥇Vinicius: 2*\nFIT 2018 - LUH6H38\nMt03 2020 - RJR0E26" in txt
    assert "*🥈Carlos: 1*\nFastback 2026 - TTD9E46" in txt and "*🥈Edson: 1*" in txt


def test_valor_formatos_br_e_americano():
    casos = {"R$69.900,00": 69900, "R$136.900": 136900, "50.000,00.": 50000, "R$65,159.30": 65159.30,
             "R$2.625,16": 2625.16, "13000": 13000, "100.000 Pix 19.90": 100000, "R$": None}
    for txt, esperado in casos.items():
        assert vg._valor(txt) == esperado, txt


def test_troca_maior_que_total_vira_pendencia():
    f = vg.campos_venda(vg.parse_resumo(RESUMO.replace("🚘 Valor troca:", "🚘 Valor troca: R$520.000")), date(2026, 10, 7))
    assert "valor da troca (maior que o total)" in vg.pendencias_venda({**f, "vendedor_id": "v1"})
