from datetime import timedelta

from app import datas, db, supervisor


def _iso_dias_atras(d):
    return (datas.agora() - timedelta(days=d)).isoformat()


def test_idade_dias():
    assert round(supervisor._idade_dias(_iso_dias_atras(3))) == 3
    assert supervisor._idade_dias(None) is None
    assert supervisor._idade_dias("lixo") is None


def test_r1_flag_so_carro_velho(monkeypatch):
    rows = [
        {"id": "v1", "marca": "Chevrolet", "modelo": "Onix", "versao": "LT", "created_at": _iso_dias_atras(10)},
        {"id": "v2", "marca": "Fiat", "modelo": "Argo", "versao": "", "created_at": _iso_dias_atras(1)},
    ]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: rows)
    out = supervisor._r1_a_anunciar()
    assert len(out) == 1 and out[0]["entidade_id"] == "v1" and out[0]["tipo"] == "R1"


def test_r4_entrega_vencida(monkeypatch):
    rows = [{"id": "s1", "cliente_nome": "Joao", "modelo": "HB20", "versao": "",
             "data_entrega_prevista": "2026-06-01"}]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: rows)
    out = supervisor._r4_entrega_vencida()
    assert len(out) == 1 and out[0]["severidade"] == "critico"


def test_persistir_conta_so_novos(monkeypatch):
    # insert_lock devolve None p/ duplicado (já aberto) e dict p/ novo
    seq = iter([{"id": "a"}, None, {"id": "b"}])
    monkeypatch.setattr(db, "insert_lock", lambda t, d: next(seq))
    monkeypatch.setattr(supervisor, "_silenciados", lambda: set())
    alertas = [{"tipo": "R1", "chave": str(i)} for i in range(3)]
    assert supervisor.persistir(alertas) == 2


def test_persistir_respeita_snooze(monkeypatch):
    monkeypatch.setattr(supervisor, "_silenciados", lambda: {("R2", "veiculo:x")})
    chamou = []
    monkeypatch.setattr(db, "insert_lock", lambda t, d: chamou.append(d) or {"id": "z"})
    n = supervisor.persistir([{"tipo": "R2", "chave": "veiculo:x"}, {"tipo": "R3", "chave": "venda:y"}])
    assert n == 1 and len(chamou) == 1 and chamou[0]["tipo"] == "R3"


def test_radar_texto_vazio(monkeypatch):
    monkeypatch.setattr(supervisor, "abertos", lambda: [])
    assert "limpo" in supervisor.radar_texto().lower()


def test_radar_texto_com_alertas(monkeypatch):
    monkeypatch.setattr(supervisor, "abertos", lambda: [
        {"severidade": "critico", "titulo": "Entrega atrasada", "detalhe": "x"},
        {"severidade": "aviso", "titulo": "Carro encalhado", "detalhe": None},
    ])
    txt = supervisor.radar_texto()
    assert "Entrega atrasada" in txt and "Carro encalhado" in txt


def test_disparar_radar_forcar_envia_e_marca(monkeypatch):
    monkeypatch.setattr(supervisor, "persistir", lambda a: 0)
    monkeypatch.setattr(supervisor, "avaliar", lambda com_watchdog=True: [])
    monkeypatch.setattr(db, "select_all", lambda t, p=None: [
        {"id": "1", "severidade": "aviso", "titulo": "A", "detalhe": None}])
    enviados, marcados = {}, {}
    monkeypatch.setattr(supervisor.evolution, "enviar_relatorio", lambda txt: enviados.update(txt=txt))
    monkeypatch.setattr(db, "update", lambda t, d, p: marcados.update(d))
    n = supervisor.disparar_radar(forcar=True)
    assert n == 1 and "A" in enviados["txt"] and marcados.get("notificado") is True


def test_disparar_radar_sem_forcar_so_critico(monkeypatch):
    monkeypatch.setattr(supervisor, "persistir", lambda a: 0)
    monkeypatch.setattr(supervisor, "avaliar", lambda com_watchdog=True: [])
    # só avisos pendentes → sem forçar, não envia nada
    monkeypatch.setattr(db, "select_all", lambda t, p=None: [
        {"id": "1", "severidade": "aviso", "titulo": "A", "detalhe": None}])
    monkeypatch.setattr(supervisor.evolution, "enviar_relatorio", lambda txt: (_ for _ in ()).throw(AssertionError("não devia enviar")))
    assert supervisor.disparar_radar(forcar=False) == 0
