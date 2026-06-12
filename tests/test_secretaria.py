from datetime import timedelta

from app import consulta, datas, db


def _dia(n):
    return (datas.hoje() - timedelta(days=n)).isoformat()


def test_ligar_hoje_junta_e_prioriza(monkeypatch):
    ags = [
        {"cliente_nome": "Faltou", "telefone": "21999", "data_agendada": _dia(1) + "T10:00", "compareceu": False, "resultado": "Faltou"},
        {"cliente_nome": "Reserva", "telefone": "21888", "data_agendada": _dia(6) + "T10:00", "compareceu": None, "resultado": "Reservado"},
        {"cliente_nome": "Novo", "telefone": "21777", "data_agendada": _dia(0) + "T10:00", "compareceu": None, "resultado": "Agendado"},
    ]
    vendas = [
        {"cliente_nome": "Atrasado", "cliente_telefone": "2155", "modelo": "Onix", "versao": "", "created_at": _dia(20),
         "data_entrega_prevista": _dia(2), "valor_total": 50000, "valor_venda": 50000, "valor_entrada": 50000,
         "status_pagamento": "pago", "status_entrega": "pendente"},
        {"cliente_nome": "Devendo", "cliente_telefone": "2144", "modelo": "HB20", "versao": "", "created_at": _dia(10),
         "data_entrega_prevista": None, "valor_total": 60000, "valor_venda": 60000, "valor_entrada": 20000,
         "status_pagamento": "pendente", "status_entrega": "pendente"},
    ]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: ags if t == "agendamentos" else vendas)
    out = consulta.lista_ligar_hoje()
    motivos = {i["cliente"]: i["motivo"] for i in out["itens"]}
    assert "Faltou" in motivos and "faltou ontem" in motivos["Faltou"]
    assert "Reserva" in motivos and "reservou" in motivos["Reserva"]
    assert "Atrasado" in motivos and "atrasada" in motivos["Atrasado"]
    assert "Devendo" in motivos and "falta" in motivos["Devendo"]
    assert "Novo" not in motivos  # agendado de hoje não entra
    assert out["itens"][0]["prioridade"] == 1  # ordenado por prioridade


def test_cobranca_formata_so_numero(monkeypatch):
    vendas = [{"cliente_nome": "Joao Silva", "modelo": "Onix", "versao": "LT", "valor_total": 50000,
               "valor_venda": 50000, "valor_entrada": 100, "status_pagamento": "pendente", "status_entrega": "pendente"}]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: vendas)
    r = consulta.mensagem_cobranca(cliente="Joao")
    assert r["saldo"] == 49900
    assert "49.900" in r["mensagem"]
    assert "Oi Joao, tudo bem?" in r["mensagem"]  # vírgula do texto preservada
    assert "dúvida, é só" in r["mensagem"]


def test_cobranca_ambigua_pede_especificar(monkeypatch):
    vendas = [{"cliente_nome": "Joao A", "modelo": "Onix", "versao": "", "valor_total": 10, "valor_venda": 10,
               "valor_entrada": 0, "status_pagamento": "pendente", "status_entrega": "pendente"},
              {"cliente_nome": "Joao B", "modelo": "HB20", "versao": "", "valor_total": 10, "valor_venda": 10,
               "valor_entrada": 0, "status_pagamento": "pendente", "status_entrega": "pendente"}]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: vendas)
    assert "achei 2" in consulta.mensagem_cobranca(cliente="Joao")["erro"]
