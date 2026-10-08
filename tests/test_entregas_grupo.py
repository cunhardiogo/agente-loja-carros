from datetime import date, datetime, timedelta, timezone

from app import entregas_grupo as eg

LISTA = """🎁 ENTREGAS:

Loja:Grupo SB
Dia da entrega: 09/10
Horário: 15:00
Vendedor: Carlos
Veículo: Fastback
Observação: higienização e polimento


🎁 Entregas;

Loja; GRUPO SB
Dia da entrega: 07/10/2026
Horário:15:00
Vendedor: Vinicius
Veículo: MT 03
Obs; caprichar na lavagem.

_________________
🎁 Entregas ;

Loja: Grupo SB
Dia da entrega: sem data /10
Horário ; 16:30
Vendedor ; Vinicius
Veículo ; Sentra 2024
Obs; tirar mancha do banco do motorista
______________________________________

🎁 Entregas:

Loja: Grupo SB
Dia da entrega: XX/10
Horário: 16:00
Vendedor: Yan
Veículo: Kwid 1.0 Zen 2025
Obs: *Trocar o Oleo e o Filtro*, Polimento"""


def test_parse_lista_real():
    itens = eg.parse_lista(LISTA, date(2026, 10, 7))
    assert [i["veiculo"] for i in itens] == ["Fastback", "MT 03", "Sentra 2024", "Kwid 1.0 Zen 2025"]
    assert [i["data_entrega"] for i in itens] == ["2026-10-09", "2026-10-07", None, None]
    assert [i["horario"] for i in itens] == ["15:00", "15:00", "16:30", "16:00"]
    assert itens[2]["data_texto"] == "sem data /10" and itens[3]["vendedor_nome"] == "Yan"
    assert itens[1]["observacao"] == "caprichar na lavagem." and itens[0]["loja"] == "Grupo SB"
    assert eg.eh_lista(LISTA) and not eg.eh_lista("O 208 azul será entregue qual dia?")


def test_chave_carro():
    assert eg.chave_carro("MT 03") == "mt" and eg.chave_carro("Fit 18") == "fit"
    assert eg.chave_carro("Kwid 1.0 Zen 2025") == "kwid"


class FakeDB:
    def __init__(self):
        self.entregas, self.vendas_upd, self.n = [], [], 0

    def select(self, t, p=None):
        p = p or {}
        if t == "entregas" and "ultima_lista_em" in p:
            datas_ = sorted((e["ultima_lista_em"] for e in self.entregas if e.get("ultima_lista_em")), reverse=True)
            return [{"ultima_lista_em": datas_[0]}] if datas_ else []
        return []

    def select_all(self, t, p=None):
        if t == "entregas":
            status = (p or {}).get("status", "eq.agendada")[3:]
            return [dict(e) for e in self.entregas if e["status"] == status]
        return []

    def insert(self, t, d):
        self.n += 1
        row = {"id": f"e{self.n:03d}0000", **d}
        self.entregas.append(row)
        return row

    def update(self, t, d, p):
        if t == "entregas":
            for e in self.entregas:
                if f"eq.{e['id']}" == p.get("id"):
                    e.update(d)
        else:
            self.vendas_upd.append(d)
        return []


def _msg(texto, quando, dono=False):
    return {"message": {"conversation": texto}, "messageTimestamp": int(quando.timestamp()),
            "key": {"id": "x", "fromMe": dono, "participant": None if dono else "outro@lid"}}


def test_quadro_sumiu_entregue_e_lista_parcial(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(eg, "db", fake)
    monkeypatch.setattr("app.vendas_grupo.vendedor_por_nome", lambda n, v=None: None)
    monkeypatch.setattr("app.vendas_grupo.vendedor_por_lid", lambda l, v=None: None)
    t0 = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    r = eg.processar(_msg(LISTA, t0))
    assert r["novas"] == 4 and r["entregues"] == 0
    sem_mt = LISTA.replace("Veículo: MT 03", "Veículo: Fit 18").replace("07/10/2026", "08/10/2026")
    # MT 03 sumiu da lista (de qualquer pessoa) → entregue
    r = eg.processar(_msg(sem_mt, t0 + timedelta(hours=3)))
    status = {e["veiculo"]: e["status"] for e in fake.entregas}
    assert status["MT 03"] == "entregue" and status["Fit 18"] == "agendada" and r["entregues"] == 1
    # voltou pra lista → reaberto, sem duplicar
    eg.processar(_msg(LISTA, t0 + timedelta(hours=3, minutes=30), dono=True))
    mts = [e for e in fake.entregas if e["veiculo"] == "MT 03"]
    assert len(mts) == 1 and mts[0]["status"] == "agendada"
    # lista com uma entrega só (parcial) não dá baixa nas outras
    so_um = "🎁 Entregas:\nDia da entrega: 10/10\nHorário: 10:00\nVendedor: Yan\nVeículo: Kwid\nObs: x"
    r = eg.processar(_msg(so_um, t0 + timedelta(hours=4), dono=True))
    assert r["parcial"] and r["entregues"] == 0
    # lista antiga reentregue é ignorada
    assert eg.processar(_msg(LISTA, t0)) == {"ignored": "lista_antiga"}
