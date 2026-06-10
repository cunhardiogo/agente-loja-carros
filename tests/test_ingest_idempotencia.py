from app import db, ingest
from app.schemas import Extracao, TipoEvento

GRUPO = {"id": "g1", "nome": "VENDAS", "tipo": "vendas"}


def test_lock_duplicado_nao_extrai(monkeypatch):
    # insert_lock devolve None (message_id já existe) → não pode chamar a IA nem aplicar nada
    chamou = {"extrair": False}
    monkeypatch.setattr(db, "insert_lock", lambda t, d: None)
    monkeypatch.setattr(ingest.llm, "extrair",
                        lambda *a, **k: chamou.__setitem__("extrair", True) or Extracao(tipo_evento=TipoEvento.nenhum, confianca=1.0))
    r = ingest._processar_um(GRUPO, "MID1", None, None, "oi", None)
    assert r == {"ignored": "duplicada", "message_id": "MID1"}
    assert chamou["extrair"] is False


def test_executar_finaliza_status(monkeypatch):
    updates = {}
    monkeypatch.setattr(db, "select", lambda t, p=None: [])  # vendedores
    monkeypatch.setattr(db, "update", lambda t, d, p: updates.update(d) or [])
    monkeypatch.setattr(ingest.llm, "extrair",
                        lambda *a, **k: Extracao(tipo_evento=TipoEvento.nenhum, confianca=0.95))
    r = ingest._executar("EV1", GRUPO, "bom dia")
    assert r["status"] == "auto"
    assert updates["status"] == "auto"  # a linha travada foi finalizada via UPDATE


def test_split_entregas_usa_message_id_composto(monkeypatch):
    vistos = []
    monkeypatch.setattr(ingest, "_split_entregas", lambda t: ["a", "b", "c"])
    monkeypatch.setattr(ingest, "_processar_um",
                        lambda g, mid, *a, **k: vistos.append(mid) or {"mid": mid})
    ingest.processar(GRUPO, "BASE", None, None, "lista", None)
    assert vistos == ["BASE#0", "BASE#1", "BASE#2"]


def test_split_sem_message_id_fica_none(monkeypatch):
    vistos = []
    monkeypatch.setattr(ingest, "_split_entregas", lambda t: ["a", "b"])
    monkeypatch.setattr(ingest, "_processar_um",
                        lambda g, mid, *a, **k: vistos.append(mid))
    ingest.processar(GRUPO, None, None, None, "lista", None)
    assert vistos == [None, None]
