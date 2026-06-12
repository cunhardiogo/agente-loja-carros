from app import confirmacao, consulta, db, ingest
from app.schemas import Extracao, TipoEvento


# ---- I1: unicidade nas ações ----
def test_marcar_pago_ambiguo_nao_mexe(monkeypatch):
    vendas = [{"id": "1", "cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "", "status_pagamento": "pendente"},
              {"id": "2", "cliente_nome": "Joao Souza", "modelo": "HB20", "versao": "", "status_pagamento": "pendente"}]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: vendas)
    chamou = []
    monkeypatch.setattr(db, "update", lambda t, d, p: chamou.append(1))
    r = consulta.marcar_pago(cliente="Joao")
    assert "achei 2" in r["erro"] and not chamou  # ambíguo → não atualiza nada


def test_marcar_pago_unico_aplica(monkeypatch):
    vendas = [{"id": "1", "cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "", "status_pagamento": "pendente"},
              {"id": "2", "cliente_nome": "Maria", "modelo": "HB20", "versao": "", "status_pagamento": "pendente"}]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: vendas)
    captura = {}
    monkeypatch.setattr(db, "update", lambda t, d, p: captura.update(p=p))
    r = consulta.marcar_pago(cliente="Maria")
    assert r["ok"] and captura["p"] == {"id": "eq.2"}


def test_matches_venda_cliente_e_veiculo(monkeypatch):
    vendas = [{"id": "1", "cliente_nome": "Joao", "modelo": "Onix", "versao": "LT"},
              {"id": "2", "cliente_nome": "Joao", "modelo": "Corolla", "versao": ""}]
    m = consulta._matches_venda(vendas, "joao", "onix")
    assert len(m) == 1 and m[0]["id"] == "1"


# ---- I3: entrega não quita com saldo ----
def test_entrega_com_saldo_nao_marca_pago(monkeypatch):
    monkeypatch.setattr(ingest, "_venda_pendente", lambda ext, campo: {"id": "v1"})
    monkeypatch.setattr(db, "select", lambda t, p=None: [{"valor_total": 50000, "valor_venda": 50000, "valor_entrada": 10000}])
    captura = {}
    monkeypatch.setattr(db, "update", lambda t, d, p: captura.update(d))
    ingest.aplicar(Extracao(tipo_evento=TipoEvento.entrega, confianca=1, cliente_nome="x"))
    assert captura["status_entrega"] == "entregue" and "status_pagamento" not in captura


def test_entrega_quitada_marca_pago(monkeypatch):
    monkeypatch.setattr(ingest, "_venda_pendente", lambda ext, campo: {"id": "v1"})
    monkeypatch.setattr(db, "select", lambda t, p=None: [{"valor_total": 50000, "valor_entrada": 50000}])
    captura = {}
    monkeypatch.setattr(db, "update", lambda t, d, p: captura.update(d))
    ingest.aplicar(Extracao(tipo_evento=TipoEvento.entrega, confianca=1, cliente_nome="x"))
    assert captura["status_pagamento"] == "pago"


# ---- I2: confirmação por palavra fraca exige código ----
def test_ok_solto_nao_confirma(monkeypatch):
    monkeypatch.setattr(confirmacao, "_pendentes", lambda: [
        {"id": "a3f29999", "dados_extraidos": {}, "mensagem_original": "m", "tipo_evento": "venda"}])
    assert confirmacao.tentar_resolver("ok") is None      # fraca sem código → agente responde
    assert confirmacao.tentar_resolver("pode") is None


def test_sim_forte_confirma(monkeypatch):
    monkeypatch.setattr(confirmacao, "_pendentes", lambda: [
        {"id": "a3f29999", "dados_extraidos": {}, "mensagem_original": "m", "tipo_evento": "venda"}])
    monkeypatch.setattr(confirmacao, "_aplicar_sim", lambda ev: "ok")
    assert confirmacao.tentar_resolver("sim") == "ok"


def test_ok_com_codigo_confirma(monkeypatch):
    monkeypatch.setattr(confirmacao, "_pendentes", lambda: [
        {"id": "a3f29999", "dados_extraidos": {}, "mensagem_original": "m", "tipo_evento": "venda"}])
    monkeypatch.setattr(confirmacao, "_aplicar_sim", lambda ev: "ok")
    assert confirmacao.tentar_resolver("a3f2 ok") == "ok"


# ---- C4: corrige não finge sucesso em duplicada ----
def test_corrige_forca_venda(monkeypatch):
    novo = Extracao(tipo_evento=TipoEvento.venda, confianca=1, cliente_nome="X")
    monkeypatch.setattr(ingest, "reextrair", lambda *a: novo)
    capt = {}
    monkeypatch.setattr(ingest, "aplicar", lambda ext, forcar_venda=False: capt.update(forcar=forcar_venda) or ("vendas", "id1"))
    monkeypatch.setattr(db, "update", lambda t, d, p: [])
    confirmacao._aplicar_corrige({"id": "e", "mensagem_original": "m"}, "corrige o valor")
    assert capt["forcar"] is True
