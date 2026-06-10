from app import db, ingest
from app.schemas import Extracao, TipoEvento


def _ext(**kw):
    return Extracao(tipo_evento=TipoEvento.venda, confianca=1.0, **kw)


def test_mesmo_cliente_outro_veiculo_nao_e_dup(monkeypatch):
    existentes = [{"id": "v1", "cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "LT", "placa": "ABC1D23"}]
    monkeypatch.setattr(db, "select", lambda t, p=None: existentes)
    # mesmo cliente, mas comprou um Corolla → não é duplicata
    assert ingest._venda_existente(_ext(cliente_nome="Joao Silva", modelo="Corolla")) is None


def test_mesmo_cliente_mesmo_veiculo_e_dup(monkeypatch):
    existentes = [{"id": "v1", "cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "LT", "placa": "ABC1D23"}]
    monkeypatch.setattr(db, "select", lambda t, p=None: existentes)
    r = ingest._venda_existente(_ext(cliente_nome="Joao Silva", modelo="Onix"))
    assert r and r["id"] == "v1"


def test_dup_por_placa(monkeypatch):
    existentes = [{"id": "v1", "cliente_nome": "Maria", "modelo": "HB20", "versao": "", "placa": "ABC-1D23"}]
    monkeypatch.setattr(db, "select", lambda t, p=None: existentes)
    r = ingest._venda_existente(_ext(cliente_nome="Maria", placa="ABC1D23"))
    assert r and r["id"] == "v1"


def test_forcar_venda_ignora_dedup(monkeypatch):
    monkeypatch.setattr(ingest, "_venda_existente", lambda ext: {"id": "v1"})
    inseriu = {}
    monkeypatch.setattr(ingest, "resolver_pessoa", lambda *a, **k: None)
    monkeypatch.setattr(db, "insert", lambda t, d: inseriu.update(d) or {"id": "novo"})
    tabela, rid = ingest.aplicar(_ext(cliente_nome="Joao", modelo="Onix"), forcar_venda=True)
    assert tabela == "vendas" and rid == "novo"


def test_dup_retorna_id_existente_nao_silencioso(monkeypatch):
    monkeypatch.setattr(ingest, "_venda_existente", lambda ext: {"id": "v1"})
    tabela, rid = ingest.aplicar(_ext(cliente_nome="Joao", modelo="Onix"))
    assert tabela == "duplicada" and rid == "v1"  # _executar transforma em pergunta, não descarte
