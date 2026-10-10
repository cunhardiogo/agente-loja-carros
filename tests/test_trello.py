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
    assert trello.nome_cartao(v, "Carlos") == "Fiat Fastback 2026 (TTD-9E46) - Carlos"
    assert trello.nome_cartao({"modelo": "Xmax 2026", "placa": None}, "Carlos") == "Yamaha Xmax - Carlos"
    assert trello.nome_cartao({"modelo": "FIT", "versao": "LX", "ano": 2018, "placa": "LUH6H38"}, "Vinicius") == "Honda Fit 2018 (LUH-6H38) - Vinicius"


def test_itens_da_preparacao():
    assert trello.itens_preparacao("Revisar, martelinho na lateral, polir e higienizar") == \
        ["Revisar", "Martelinho na lateral", "Polir", "Higienizar"]
    assert trello.itens_preparacao("*Trocar o Oleo e o Filtro*, Polimento, Tanque Cheio") == \
        ["Trocar o Oleo e o Filtro", "Polimento", "Tanque Cheio"]


def test_achar_cartao_modelo_curto_com_numero():
    listas = {"l1": "estoque sb!"}
    cards = [{"id": "a", "name": "Citroen C3 2023 (RJN-9H13)", "idList": "l1"},
             {"id": "b", "name": "Citroen C3 2017 (KRM-8551)", "idList": "l1"}]
    assert trello.achar_cartao("C3 2023", cards, listas)["id"] == "a"
    assert trello.achar_cartao("C3", cards, listas) is None
