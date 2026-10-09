from app import trello


def test_placa_no_nome_do_cartao():
    assert trello._placa("FASTBACK 2026 (TTD-9E46) - Carlos") == "TTD9E46"
    assert trello._placa("MINI  2019 - Vinicius") is None


def test_cartao_sem_placa_por_modelo_e_ano():
    listas = {"l1": trello.VENDIDOS, "l2": "estoque sb!"}
    cards = [{"id": "a", "name": "Honda Fit Ex 2018 - Vinicius", "idList": "l1"},
             {"id": "b", "name": "HONDA FIT 2015 (KYX-6J98) 1.5 LX", "idList": "l2"},
             {"id": "c", "name": "FIAT PULSE 2023 (SHQ-8A86)", "idList": "l2"},
             {"id": "d", "name": "PULSE 2026 HIBRIDO - Yan", "idList": "l1"}]
    assert trello._por_modelo({"modelo": "FIT", "versao": "LX", "ano": 2018}, cards, listas, "Vinicius")["id"] == "a"
    assert trello._por_modelo({"modelo": "Pulse Hybrid", "versao": None, "ano": None}, cards, listas, "Yan")["id"] == "d"
    assert trello._por_modelo({"modelo": "Corolla", "versao": None, "ano": 2020}, cards, listas, None) is None


def test_nome_do_cartao_novo():
    v = {"modelo": "FIAT", "versao": "Fastback", "ano": 2026, "placa": "TTD9E46"}
    assert trello.nome_cartao(v, "Carlos") == "FASTBACK 2026 (TTD-9E46) - Carlos"
    assert trello.nome_cartao({"modelo": "Xmax 2026", "placa": None}, "Carlos") == "XMAX 2026 - Carlos"


def test_itens_da_preparacao():
    assert trello.itens_preparacao("Revisar, martelinho na lateral, polir e higienizar") == \
        ["Revisar", "Martelinho na lateral", "Polir", "Higienizar"]
    assert trello.itens_preparacao("*Trocar o Oleo e o Filtro*, Polimento, Tanque Cheio") == \
        ["Trocar o Oleo e o Filtro", "Polimento", "Tanque Cheio"]
