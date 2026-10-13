from app import metricas as m


def test_canal_padronizado():
    assert m.canal("trafego") == m.canal("Tráfego ") == "Tráfego"
    assert m.canal("OLX soberano") == m.canal("OLX Brutus") == "OLX"
    assert m.canal("WebMotors") == "Webmotors"
    assert m.canal("Whatssap") == m.canal("WhatsApp ( Verificar onde cliente viu anúncio)") == "WhatsApp"
    assert m.canal("CLIENTE MEU") == "Carteira do vendedor"
    assert m.canal(None) == "Não informado"


def test_forma_de_pagamento():
    assert m.forma_pagamento({"banco": "Safra", "valor_financiado": 21900}) == "Financiado"
    assert m.forma_pagamento({"banco": "a vista", "forma_pagamento": "a vista"}) == "À vista"
    assert m.forma_pagamento({"texto_resumo": "Pagamento: carta de consórcio"}) == "Consórcio"
    assert m.forma_pagamento({}) == "À vista"


def test_periodos(monkeypatch):
    from datetime import date
    monkeypatch.setattr(m.datas, "hoje", lambda: date(2026, 10, 13))
    assert m.periodo("mes") == ("2026-10-01", "2026-10-13")
    assert m.periodo("mes_passado") == ("2026-09-01", "2026-09-30")
    assert m.periodo("2026-02") == ("2026-02-01", "2026-02-28")
    assert m.periodo("semana") == ("2026-10-12", "2026-10-13")
    assert m.periodo("30d") == ("2026-09-14", "2026-10-13")


def test_dias_por_etapa(monkeypatch):
    acoes = [
        {"type": "createCard", "date": "2026-10-01T12:00:00Z", "data": {"card": {"id": "c1"}, "list": {"name": "Estoque SB!"}}},
        {"type": "updateCard", "date": "2026-10-02T12:00:00Z",
         "data": {"card": {"id": "c1"}, "listBefore": {"name": "Estoque SB!"}, "listAfter": {"name": "PINTURA"}}},
        {"type": "updateCard", "date": "2026-10-06T12:00:00Z",
         "data": {"card": {"id": "c1"}, "listBefore": {"name": "PINTURA"}, "listAfter": {"name": "Estoque SB!"}}},
    ]
    monkeypatch.setattr(m, "_acoes_trello", lambda: acoes)
    r = m._etapas("2026-10-01", "2026-10-31")
    assert r["dias_por_etapa"]["Pintura"] == {"mediana": 4.0, "media": 4.0, "carros": 1}
    assert r["dias_por_etapa"]["Estoque"]["mediana"] == 1.0
    assert r["movimentos_no_periodo"]["para Pintura"] == 1
