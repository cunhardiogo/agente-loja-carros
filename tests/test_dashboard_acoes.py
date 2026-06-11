from app import confirmacao, db


def test_confirmar_id_pendencia_inexistente(monkeypatch):
    monkeypatch.setattr(db, "select", lambda t, p=None: [])
    assert confirmacao.confirmar_id("xx")["erro"]


def test_confirmar_id_aplica(monkeypatch):
    ev = {"id": "e1", "dados_extraidos": {"tipo_evento": "venda", "resumo": "X"}, "mensagem_original": "m"}
    monkeypatch.setattr(db, "select", lambda t, p=None: [ev])
    monkeypatch.setattr(confirmacao, "_aplicar_sim", lambda e: "ok " + e["id"])
    r = confirmacao.confirmar_id("e1")
    assert r["ok"] and r["mensagem"] == "ok e1"


def test_descartar_id(monkeypatch):
    captura = {}
    monkeypatch.setattr(db, "update", lambda t, d, p: captura.update(d=d, p=p) or [{"id": "e1"}])
    r = confirmacao.descartar_id("e1")
    assert r["ok"] is True
    assert captura["d"]["status"] == "descartado"
    assert captura["p"]["status"] == "eq.pendente_confirmacao"  # só desce pendência


def test_pendentes_itens_formata_codigo(monkeypatch):
    monkeypatch.setattr(confirmacao, "_pendentes", lambda: [
        {"id": "a3f29999-0000", "dados_extraidos": {"resumo": "venda Onix"},
         "mensagem_original": "msg longa", "tipo_evento": "venda"}])
    itens = confirmacao.pendentes_itens()
    assert itens[0]["codigo"] == "A3F2" and itens[0]["resumo"] == "venda Onix"
