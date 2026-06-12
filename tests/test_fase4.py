from app import db, ingest, meta_ads
from app.schemas import Extracao, TipoEvento


# ---- meta_ads (puro) ----
def test_soma_acoes_filtra_tipos():
    actions = [{"action_type": "lead", "value": "13"},
               {"action_type": "link_click", "value": "2579"},
               {"action_type": "onsite_conversion.lead_grouped", "value": "5"}]
    assert meta_ads._soma_acoes(actions, meta_ads._LEADS) == 18


def test_preset_mapeia_periodos():
    assert meta_ads._preset("mes") == "this_month"
    assert meta_ads._preset("hoje") == "today"
    assert meta_ads._preset("qualquer") == "last_7d"


def test_resumo_sem_config(monkeypatch):
    monkeypatch.setattr(meta_ads.settings, "meta_token", "")
    monkeypatch.setattr(meta_ads.settings, "meta_ad_account", "")
    assert "erro" in meta_ads._resumo("semana")


# ---- giro do estoque ----
def test_giro_bucketiza_por_idade(monkeypatch):
    from app import consulta, datas
    hoje = datas.agora()
    def dias_atras(n):
        return (hoje - __import__("datetime").timedelta(days=n)).isoformat()
    rows = [{"modelo": "Onix", "status": "anunciado", "created_at": dias_atras(3), "data_anuncio": None},
            {"modelo": "HB20", "status": "anunciado", "created_at": dias_atras(40), "data_anuncio": dias_atras(40)},
            {"modelo": "Kicks", "status": "a_anunciar", "created_at": dias_atras(70), "data_anuncio": None}]
    monkeypatch.setattr(consulta.db, "select_all", lambda t, p=None: rows)
    g = consulta.giro_estoque()
    assert g["em_estoque"] == 3
    assert g["faixas"]["0-15"] == 1 and g["faixas"]["31-60"] == 1 and g["faixas"]["60+"] == 1
    assert g["mais_parados"][0]["dias"] >= 70  # ordena do mais parado


# ---- link venda -> veiculo ----
def test_linkar_veiculo_por_placa(monkeypatch):
    veiculos = [{"id": "vc1", "modelo": "Onix", "versao": "LT", "placa": "ABC1D23", "status": "anunciado"}]
    monkeypatch.setattr(db, "select_all", lambda t, p=None: veiculos)
    chamadas = []
    monkeypatch.setattr(db, "update", lambda t, d, p: chamadas.append((t, d)) or [])
    ingest._linkar_veiculo(Extracao(tipo_evento=TipoEvento.venda, confianca=1, placa="ABC1D23"), "venda1")
    tabelas = {t for t, _ in chamadas}
    assert tabelas == {"vendas", "veiculos"}
    assert any(d.get("status") == "vendido" for _, d in chamadas)


def test_linkar_veiculo_sem_match_nao_atualiza(monkeypatch):
    monkeypatch.setattr(db, "select_all", lambda t, p=None: [])
    chamadas = []
    monkeypatch.setattr(db, "update", lambda t, d, p: chamadas.append(t))
    ingest._linkar_veiculo(Extracao(tipo_evento=TipoEvento.venda, confianca=1, modelo="Onix"), "v1")
    assert chamadas == []


# ---- recall ----
def test_aplicar_recall_insere(monkeypatch):
    inseriu = {}
    monkeypatch.setattr(db, "insert", lambda t, d: inseriu.update(tabela=t, **d) or {"id": "rc1"})
    ext = Extracao(tipo_evento=TipoEvento.recall, confianca=1, cliente_nome="Joao",
                   modelo="Onix", motivo="revisão 10mil km")
    tabela, rid = ingest.aplicar(ext)
    assert tabela == "recalls" and rid == "rc1"
    assert inseriu["motivo"] == "revisão 10mil km" and inseriu["cliente_nome"] == "Joao"
