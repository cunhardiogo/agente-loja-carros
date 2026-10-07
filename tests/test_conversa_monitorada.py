from app import main


def _evento(jid, from_me=False, instancia="diogo4895"):
    return {"instance": instancia, "data": {
        "key": {"remoteJid": jid, "id": "MSG1", "fromMe": from_me},
        "pushName": "Igor", "message": {"conversation": "vendi o onix"}}}


def _setup(monkeypatch, monitorados):
    monkeypatch.setattr(main.settings, "evolution_instance", "diogo4895")
    monkeypatch.setattr(main.settings, "evolution_assist_instance", "lojasb")
    monkeypatch.setattr(main.settings, "meu_numero", "5521980994895")
    monkeypatch.setattr(main.media, "conteudo_texto", lambda *a: "vendi o onix")
    monkeypatch.setattr(main.ingest, "grupo_por_jid", lambda jid: monitorados.get(jid))
    monkeypatch.setattr(main.ingest, "ja_processada", lambda mid: False)
    chamadas = []
    monkeypatch.setattr(main.ingest, "processar", lambda **kw: chamadas.append(kw) or {"ok": True})
    return chamadas


def test_dm_monitorada_e_ingerida_com_remetente(monkeypatch):
    jid = "5521983325969@s.whatsapp.net"
    chamadas = _setup(monkeypatch, {jid: {"id": "g1", "nome": "Igor", "tipo": "conversa"}})
    main._rotear_evento(_evento(jid))
    main._rotear_evento(_evento(jid, from_me=True))
    assert [c["remetente"] for c in chamadas] == ["5521983325969", "5521980994895"]


def test_dm_nao_monitorada_segue_ignorada(monkeypatch):
    chamadas = _setup(monkeypatch, {})
    r = main._rotear_evento(_evento("5521900000000@s.whatsapp.net"))
    assert r == {"ignored": "dm_nao_autorizado"} and not chamadas


def test_dm_no_assistente_nao_e_ingerida(monkeypatch):
    jid = "5521983325969@s.whatsapp.net"
    chamadas = _setup(monkeypatch, {jid: {"id": "g1", "nome": "Igor", "tipo": "conversa"}})
    main._rotear_evento(_evento(jid, instancia="lojasb"))
    assert not chamadas
