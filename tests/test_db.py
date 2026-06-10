import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from app import db


def test_ilike_escapa_virgula_e_aspas():
    # vírgula separaria filtros no PostgREST → tem de virar literal entre aspas
    v = db.ilike('Fulano, "Beltrano"')
    assert v.startswith('ilike."*') and v.endswith('*"')
    assert '\\"Beltrano\\"' in v
    # a vírgula não pode aparecer "solta" fora das aspas de proteção
    assert v == 'ilike."*Fulano, \\"Beltrano\\"*"'


def test_ilike_escapa_barra():
    assert db.ilike("a\\b") == 'ilike."*a\\\\b*"'


def test_eq_text_envolve_em_aspas():
    assert db.eq_text("Onix.LT") == 'eq."Onix.LT"'


def test_select_all_pagina_ate_esgotar(monkeypatch):
    paginas = [list(range(1000)), list(range(500))]  # 2ª página < page → para
    chamadas = []

    class FakeResp:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    def fake_get(path, params=None, headers=None):
        chamadas.append(headers["Range"])
        return FakeResp(paginas[len(chamadas) - 1])

    monkeypatch.setattr(db._client, "get", fake_get)
    out = db.select_all("vendas")
    assert len(out) == 1500
    assert chamadas == ["0-999", "1000-1999"]
