from datetime import date

from app import sdrs_grupo


def test_le_relatorio_de_produtividade():
    t = "Produtividade 15/10/2026\n\nLeads - 57\nAgendamentos -03\nNegociação -05\nVendas  - 00"
    assert sdrs_grupo.ler(t) == {"data": "2026-10-15", "leads": 57, "agendamentos": 3, "negociacao": 5, "vendas": 0}
    assert sdrs_grupo.ler("Produtividade 08/09\nLeads - 12\nAgendamentos -01\nVendas - 0", date(2026, 9, 9))["negociacao"] is None
    assert sdrs_grupo.ler("Quem está disponível agora?") is None


def test_data_repetida_vale_o_dia_do_envio(monkeypatch):
    salvos = []
    monkeypatch.setattr(sdrs_grupo.db, "select", lambda t, p: [{"message_id": "ANTIGA"}])
    monkeypatch.setattr(sdrs_grupo.db, "upsert", lambda t, rows, on_conflict: salvos.extend(rows))
    sdrs_grupo.processar({"message_id": "NOVA", "autor_nome": "Mario", "em": "2026-10-17T20:18:00+00:00",
                          "texto": "Produtividade 16/10/2026\nLeads - 34\nAgendamentos -02\nNegociação -02\nVendas - 00"})
    assert salvos[0]["data"] == "2026-10-17" and salvos[0]["sdr"] == "Mario"
