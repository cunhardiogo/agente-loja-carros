from app import db, ingest
from app.schemas import Extracao, TipoEvento


def _ext(**kw):
    return Extracao(tipo_evento=TipoEvento.pagamento, confianca=1.0, **kw)


def _rows(rows, monkeypatch):
    monkeypatch.setattr(db, "select_all", lambda t, p=None: rows)


def test_placa_unica_casa(monkeypatch):
    _rows([{"id": "v1", "cliente_nome": "A", "modelo": "Onix", "versao": "", "placa": "ABC1D23"},
           {"id": "v2", "cliente_nome": "B", "modelo": "HB20", "versao": "", "placa": "XYZ9Z99"}], monkeypatch)
    assert ingest._venda_pendente(_ext(placa="ABC-1D23"), "status_pagamento")["id"] == "v1"


def test_cliente_ambiguo_sem_veiculo_retorna_none(monkeypatch):
    # dois "Joao" pendentes e a extração não diz o veículo → não chuta
    _rows([{"id": "v1", "cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "", "placa": ""},
           {"id": "v2", "cliente_nome": "Joao Souza", "modelo": "HB20", "versao": "", "placa": ""}], monkeypatch)
    assert ingest._venda_pendente(_ext(cliente_nome="Joao"), "status_pagamento") is None


def test_cliente_ambiguo_desempata_por_veiculo(monkeypatch):
    _rows([{"id": "v1", "cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "", "placa": ""},
           {"id": "v2", "cliente_nome": "Joao Souza", "modelo": "HB20", "versao": "", "placa": ""}], monkeypatch)
    assert ingest._venda_pendente(_ext(cliente_nome="Joao", modelo="HB20"), "status_pagamento")["id"] == "v2"


def test_token_proibido_nao_casa(monkeypatch):
    # "2020" (ano) e "preto" (cor) não podem casar venda
    _rows([{"id": "v1", "cliente_nome": "A", "modelo": "Onix 2020", "versao": "preto", "placa": ""},
           {"id": "v2", "cliente_nome": "B", "modelo": "Corolla 2020", "versao": "preto", "placa": ""}], monkeypatch)
    assert ingest._venda_pendente(_ext(veiculo_descricao="2020 preto"), "status_pagamento") is None


def test_token_modelo_unico_casa(monkeypatch):
    _rows([{"id": "v1", "cliente_nome": "A", "modelo": "Onix", "versao": "", "placa": ""},
           {"id": "v2", "cliente_nome": "B", "modelo": "Corolla", "versao": "", "placa": ""}], monkeypatch)
    assert ingest._venda_pendente(_ext(modelo="Corolla"), "status_pagamento")["id"] == "v2"


def test_tokens_veiculo_filtra_ano_motor_cor():
    toks = ingest._tokens_veiculo(Extracao(tipo_evento=TipoEvento.venda, confianca=1.0,
                                           veiculo_descricao="Onix 1.0 2020 preto LTZ"))
    assert "onix" in toks and "ltz" in toks
    assert "2020" not in toks and "1.0" not in toks and "preto" not in toks
