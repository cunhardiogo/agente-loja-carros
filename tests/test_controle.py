from app import acoes, arquivo, controle, flags


def test_linha_do_arquivo():
    m = {"key": {"id": "X1", "remoteJid": "g@g.us", "participant": "p@lid"}, "pushName": "Carlos",
         "messageTimestamp": 1791800000,
         "message": {"imageMessage": {"caption": "comprovante fastback",
                                      "contextInfo": {"stanzaId": "Y9"}}}}
    arquivo._nomes["p@lid"] = "Carlos"
    r = arquivo.linha(m, "VENDAS")
    assert r["tipo"] == "foto" and r["texto"] == "comprovante fastback" and r["citada"] == "Y9"
    assert r["autor_nome"] == "Carlos" and r["conversa"] == "VENDAS"
    assert arquivo.linha({"key": {"id": "Z"}, "message": {"protocolMessage": {}}}, "x") is None


def test_flag_padrao_e_definida(monkeypatch):
    monkeypatch.setattr(flags, "_cache", {"t": 9e18, "rows": {"cobranca_leads": {"nome": "cobranca_leads", "ativo": False}}})
    assert flags.ativo("cobranca_leads") is False
    assert flags.ativo("posvenda") is True            # sem linha → padrão
    assert flags.ativo("recados_preparacao") is False  # padrão desligado


def test_automacao_pelo_jeito_que_o_dono_fala(monkeypatch):
    feitas = []
    monkeypatch.setattr(flags, "definir", lambda n, ativo=None, valor=None: feitas.append((n, ativo)))
    monkeypatch.setattr(flags, "_cache", {"t": 9e18, "rows": {}})
    assert controle.automacao("recados de preparação", True)["automacao"] == "recados_preparacao"
    assert controle.automacao("cobrança dos leads", False)["automacao"] == "cobranca_leads"
    assert controle.automacao("agenda da manhã", False)["automacao"] == "relatorio_agenda"
    assert "opcoes" in controle.automacao("cobrança", False)  # ambíguo: pergunta
    assert feitas == [("recados_preparacao", True), ("cobranca_leads", False), ("relatorio_agenda", False)]


def test_desfazer_volta_valor_e_criacao(monkeypatch):
    log = [{"id": "a2", "tabela": "leads", "registro_id": "L1", "antes": {"vendedor_nome": "Carlos"},
            "descricao": "corrigir lead", "desfeito": False},
           {"id": "a1", "tabela": "vendas", "registro_id": "V1", "antes": None, "descricao": "criar venda",
            "desfeito": False}]
    updates = []

    def select(t, p=None):
        return [r for r in log if not r["desfeito"]][:1]

    def update(t, d, f):
        updates.append((t, d, f["id"]))
        if t == "acoes_log":
            next(r for r in log if f["id"] == f"eq.{r['id']}")["desfeito"] = True

    monkeypatch.setattr(acoes.db, "select", select)
    monkeypatch.setattr(acoes.db, "update", update)
    assert acoes.desfazer()["desfeito"] == "corrigir lead"
    assert acoes.desfazer()["desfeito"] == "criar venda"
    assert ("leads", {"vendedor_nome": "Carlos"}, "eq.L1") in updates
    assert ("vendas", {"removido": True}, "eq.V1") in updates
    assert "erro" in acoes.desfazer()


def test_dias_no_quadro_pelo_id_do_cartao():
    assert controle._dias_no_quadro("00000000aaaaaaaaaaaaaaaa") > 10000


def test_gerente_nao_ve_ferramentas_do_dono():
    from app import consulta
    nomes = {t["function"]["name"] for t in consulta._ferramentas("operacional")}
    assert "apagar" not in nomes and "enviar_mensagem" not in nomes and "automacao" not in nomes
    assert {"corrigir_lead", "criar_venda", "buscar_mensagens", "estoque_precos"} <= nomes
    assert len(consulta._ferramentas("dono")) == len(consulta.TOOLS)


def test_cada_um_confirma_so_o_que_pediu(monkeypatch):
    pend = [{"id": "f1", "numero": "5521994110597", "tipo": "x", "params": {}, "descricao": "mover Civic"}]
    feitos = []
    monkeypatch.setattr(acoes.db, "select", lambda t, p=None: pend)
    monkeypatch.setattr(acoes.db, "update", lambda *a: None)
    monkeypatch.setattr(acoes, "_executar", lambda a: feitos.append(a["id"]))
    monkeypatch.setattr(acoes.settings, "meu_numero", "5521980994895")
    assert acoes.tentar_confirmar("ok", "5521980994895") is None   # o dono não confirma o pedido do Felipe
    import app.evolution as ev
    monkeypatch.setattr(ev, "enviar_texto", lambda n, t: None)
    assert acoes.tentar_confirmar("ok", "5521994110597").startswith("✅") and feitos == ["f1"]
