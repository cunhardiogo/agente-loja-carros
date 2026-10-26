from datetime import datetime, timezone

import pytest

from app import recall_grupo as rg


class FakeDB:
    def __init__(self):
        self.t = {"recalls": [], "recall_msgs": [], "vendas": [], "mensagens": []}

    def _match(self, row, params):
        for k, v in (params or {}).items():
            if k in ("select", "order", "limit"):
                continue
            op, _, val = v.partition(".")
            if op == "eq" and str(row.get(k)).lower() != val.lower():
                return False
            if op == "in" and str(row.get(k)) not in val.strip("()").split(","):
                return False
            if op == "is" and row.get(k) is not None:
                return False
            if op == "lte" and not (row.get(k) or "") <= val:
                return False
        return True

    def select(self, t, params=None):
        return [r for r in self.t[t] if self._match(r, params)]

    select_all = select

    def insert_lock(self, t, row):
        if any(r.get("message_id") == row.get("message_id") for r in self.t[t]):
            return None
        row = {"id": f"{t}{len(self.t[t]) + 1}", "checklist": {}, **row}
        self.t[t].append(row)
        return row

    def update(self, t, upd, params):
        rows = self.select(t, params)
        for r in rows:
            r.update(upd)
        return rows


@pytest.fixture
def fake(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(rg, "db", db)
    abertos, fechados = [], []
    monkeypatch.setattr(rg, "_trello_abrir", lambda r, motivos=None: abertos.append((r["id"], motivos))
                        or r.update({"trello_card_id": "card1"}))
    monkeypatch.setattr(rg, "_trello_fechar", lambda c, r: fechados.append(r["id"]))
    monkeypatch.setattr(rg.trello, "configurado", lambda: True)

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def put(self, *a, **k):
            return type("R", (), {"raise_for_status": lambda s: None})()
    monkeypatch.setattr(rg.trello, "_cliente", lambda: C())
    import app.vendas_grupo as vg
    monkeypatch.setattr(vg, "vendedor_por_lid", lambda lid, v=None: {"id": "v1"} if lid else None)
    return db, abertos, fechados


def _msg(mid, texto, stanza=None, ts=1791800000):
    m = {"key": {"id": mid, "participant": "x@lid"}, "messageTimestamp": ts,
         "message": {"extendedTextMessage": {"text": texto}}}
    if stanza:
        m["message"]["extendedTextMessage"]["contextInfo"] = {"stanzaId": stanza}
    return m


def test_formulario_abre_recall_e_resposta_pronto_fecha(fake, monkeypatch):
    db, abertos, fechados = fake
    monkeypatch.setattr(rg, "extrair", lambda t, c, a, ant=None: {
        "tipo": "novo", "veiculo": "Duster", "placa": "QPH-4J66", "km_atual": 69562,
        "motivos": ["câmbio", "trava da porta"], "na_loja": False})
    r = rg.processar(_msg("m1", "Veículo: Duster / Placa: QPH-4J66 / Motivo do recall: câmbio / trava"))
    rec = db.t["recalls"][0]
    assert r == {"recall": rec["id"], "carro": "Duster QPH-4J66"}
    assert rec["placa"] == "QPH4J66" and rec["motivo"] == "câmbio / trava da porta" and rec["status"] == "aberto"
    assert abertos == [(rec["id"], None)]

    monkeypatch.setattr(rg, "extrair", lambda t, c, a, ant=None: {"tipo": "agendado", "quando": "2026-10-15T10:30",
                                                        "quando_texto": "quinta 10h30"})
    rg.processar(_msg("m2", "cliente traz quinta 10h30", stanza="m1"))
    assert rec["status"] == "agendado" and rec["agendado_texto"] == "quinta 10h30"

    monkeypatch.setattr(rg, "extrair", lambda t, c, a, ant=None: {"tipo": "pronto"})
    rg.processar(_msg("m3", "Duster pronto, pode avisar o cliente", stanza="m2"))  # resposta em cadeia
    assert rec["status"] == "pronto" and fechados == [rec["id"]]


def test_mesma_placa_soma_motivo(fake, monkeypatch):
    db, abertos, _ = fake
    monkeypatch.setattr(rg, "extrair", lambda t, c, a, ant=None: {"tipo": "novo", "veiculo": "T-Cross", "placa": "SRO2C68",
                                                        "motivos": ["vazamento de óleo"]})
    rg.processar(_msg("a1", "Veículo: VW T-Cross / Placa: SRO2C68 / Motivo: vazamento"))
    monkeypatch.setattr(rg, "extrair", lambda t, c, a, ant=None: {"tipo": "novo", "veiculo": "T-Cross", "placa": "SRO2C68",
                                                        "motivos": ["barulho na caixa de direção"]})
    rg.processar(_msg("a2", "Veículo: VW T-Cross / Placa: SRO2C68 / Motivo: barulho na caixa de direção"))
    assert len(db.t["recalls"]) == 1
    assert db.t["recalls"][0]["motivo"] == "vazamento de óleo / barulho na caixa de direção"
    assert abertos[-1][1] == ["barulho na caixa de direção"]


def test_ok_solto_e_conversa_sao_ignorados(fake, monkeypatch):
    monkeypatch.setattr(rg, "extrair", lambda t, c, a, ant=None: {"tipo": "info"})
    assert rg.processar(_msg("o1", "Ok")) == {"ignored": "sem_conteudo"}
    assert rg.processar(_msg("o2", "Esse cara tá maluco kkk")) == {"ignored": "conversa"}


def test_aviso_pro_dono_depois_de_2_dias(fake, monkeypatch):
    db, _, _ = fake
    db.t["recalls"] += [
        {"id": "r1", "origem": "grupo", "status": "aberto", "historico": False, "aviso_em": None,
         "aberto_em": "2026-10-08T14:00:00+00:00", "veiculo": "Duster", "placa": "QPH4J66", "motivo": "câmbio"},
        {"id": "r2", "origem": "grupo", "status": "agendado", "historico": False, "aviso_em": None,
         "aberto_em": "2026-10-08T14:00:00+00:00", "veiculo": "Ka", "placa": None, "motivo": "pneu"}]
    enviados = []
    monkeypatch.setattr(rg.evolution, "enviar_texto", lambda n, t: enviados.append((n, t)))
    agora = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    assert rg.checar(agora) == 1
    assert enviados[0][0] == rg.settings.meu_numero and "Duster QPH-4J66" in enviados[0][1]
    assert rg.checar(agora) == 0  # não repete


def test_garantia_e_agenda(fake):
    db, _, _ = fake
    assert str(rg.garantia_ate({"data_saida": "2026-08-15"})) == "2026-11-13"
    db.t["recalls"].append({"id": "r1", "origem": "grupo", "status": "agendado", "veiculo": "Duster",
                            "placa": "QPH4J66", "motivo": "câmbio", "agendado_para": "2026-10-12T13:30:00+00:00",
                            "aberto_em": "2026-10-08T14:00:00+00:00"})
    assert "Duster QPH-4J66 (hoje 10:30): câmbio" in rg.agenda_texto("2026-10-12")


def test_mesmo_carro_sem_placa():
    from app import recall_grupo
    abertos = [{"veiculo": "Duster", "placa": "QPH4J66"}, {"veiculo": "2008 2017", "placa": None}]
    assert recall_grupo._mesmo_carro("2008 2017", abertos)["veiculo"] == "2008 2017"
    assert recall_grupo._mesmo_carro(None, abertos) is None
    assert recall_grupo._mesmo_carro("Civic", abertos) is None


def test_cartao_no_finalizado_fecha_recall(fake, monkeypatch):
    db, _, fechados = fake
    db.t["recalls"].append({"id": "r1", "origem": "grupo", "status": "aberto", "trello_card_id": "c1",
                              "veiculo": "Sandero", "placa": "LSB9B88", "checklist": {}, "aberto_em": "2026-10-19"})
    db.t["recalls"].append({"id": "r2", "origem": "grupo", "status": "aberto", "trello_card_id": "c2",
                              "veiculo": "Duster", "placa": "QPH4J66", "checklist": {}, "aberto_em": "2026-10-08"})
    monkeypatch.setattr(rg.trello, "cartoes", lambda c: ([{"id": "c1", "idList": "F"}, {"id": "c2", "idList": "R"}],
                                                          {"F": rg.trello.FINALIZADO, "R": rg.RECALL}))
    assert rg.sincronizar_trello() == ["Sandero LSB-9B88"]
    assert [r["status"] for r in db.t["recalls"]] == ["pronto", "aberto"] and fechados == ["r1"]
