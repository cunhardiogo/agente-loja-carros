from app import consulta, db


def test_trunc_corta_em_2000():
    s = "x" * 5000
    out = consulta._trunc(s)
    assert len(out) <= 2010 and out.endswith("…")
    assert consulta._trunc("curto") == "curto"


def test_carregar_historico_trunca_e_ordena(monkeypatch):
    # vem desc do banco → função inverte p/ ordem cronológica
    monkeypatch.setattr(db, "select", lambda t, p=None: [
        {"papel": "assistant", "conteudo": "B"}, {"papel": "user", "conteudo": "A" * 3000}])
    h = consulta.carregar_historico("5511")
    assert [m["role"] for m in h] == ["user", "assistant"]
    assert len(h[0]["content"]) <= 2010 and h[0]["content"].endswith("…")


def test_compactar_nao_faz_nada_abaixo_do_limite(monkeypatch):
    monkeypatch.setattr(consulta, "_client", object())  # só p/ passar do guard
    monkeypatch.setattr(db, "select_all", lambda t, p=None: [{"id": str(i), "papel": "user", "conteudo": "oi"} for i in range(10)])
    chamou = {"insert": False}
    monkeypatch.setattr(db, "insert", lambda *a, **k: chamou.__setitem__("insert", True))
    consulta._compactar_memoria("5511")
    assert chamou["insert"] is False  # 10 <= 30 → não compacta


def test_compactar_resume_e_apaga_antigas(monkeypatch):
    msgs = [{"id": str(i), "papel": "user", "conteudo": f"m{i}"} for i in range(40)]
    monkeypatch.setattr(consulta, "_client", _FakeClient("RESUMO"))
    monkeypatch.setattr(db, "select_all", lambda t, p=None: msgs)
    monkeypatch.setattr(consulta, "carregar_resumo", lambda n: "")
    inserts, deletes = [], []
    monkeypatch.setattr(db, "insert", lambda t, d: inserts.append(d))
    monkeypatch.setattr(db, "delete", lambda t, p: deletes.append(p))
    consulta._compactar_memoria("5511")
    # resumo gravado
    assert any(d.get("papel") == "resumo" and d["conteudo"] == "RESUMO" for d in inserts)
    # 40-20 = 20 antigas apagadas (mais a limpeza do resumo anterior)
    assert any("id" in p for p in deletes)


class _FakeClient:
    def __init__(self, texto):
        self.chat = self
        self.completions = self
        self._t = texto

    def create(self, **kw):
        class R:
            choices = [type("C", (), {"message": type("M", (), {"content": "RESUMO"})()})()]
        return R()
