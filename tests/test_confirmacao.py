from app import confirmacao, ingest


def _pend(id_, resumo="venda Onix"):
    return {"id": id_, "dados_extraidos": {"tipo_evento": "venda", "resumo": resumo},
            "mensagem_original": "msg", "tipo_evento": "venda", "created_at": "2026-06-10T10:00:00"}


def test_pergunta_normal_nao_e_sequestrada(monkeypatch):
    # havendo pendência, uma pergunta comum NÃO pode ser tratada como confirmação
    monkeypatch.setattr(confirmacao, "_pendentes", lambda: [_pend("a3f29999")])
    assert confirmacao.tentar_resolver("quantas vendas hoje?") is None
    assert confirmacao.tentar_resolver("era pra ser ontem") is None  # gatilho antigo removido


def test_uma_pendencia_sim_aplica(monkeypatch):
    ev = _pend("a3f29999")
    monkeypatch.setattr(confirmacao, "_pendentes", lambda: [ev])
    monkeypatch.setattr(confirmacao, "_aplicar_sim", lambda e: "OK " + e["id"])
    assert confirmacao.tentar_resolver("sim") == "OK a3f29999"


def test_duas_pendencias_sim_pede_codigo(monkeypatch):
    monkeypatch.setattr(confirmacao, "_pendentes",
                        lambda: [_pend("a3f29999", "venda A"), _pend("b7c10000", "venda B")])
    r = confirmacao.tentar_resolver("sim")
    assert "código" in r.lower()
    assert "A3F2" in r and "B7C1" in r


def test_codigo_explicito_seleciona(monkeypatch):
    ev_b = _pend("b7c10000", "venda B")
    monkeypatch.setattr(confirmacao, "_pendentes",
                        lambda: [_pend("a3f29999", "venda A"), ev_b])
    capt = {}
    monkeypatch.setattr(confirmacao, "_aplicar_sim", lambda e: capt.update(id=e["id"]) or "ok")
    confirmacao.tentar_resolver("b7c1 sim")
    assert capt["id"] == "b7c10000"


def test_codigo_pendencia_formato():
    assert ingest.codigo_pendencia("a3f29999-1111-2222-3333-444455556666") == "A3F2"
    assert ingest.codigo_pendencia("") == ""
