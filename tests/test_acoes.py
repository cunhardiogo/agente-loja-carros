from app import acoes


def test_lista_por_apelido():
    assert acoes._lista("pintura") == "pintura"
    assert acoes._lista("em trânsito") == "carro em transito"
    assert acoes._lista("pronto pra foto") == "preparacao / foto"
    assert acoes._lista(None) == "estoque sb!"


def test_bate_ignora_acento_e_hifen():
    assert acoes._bate("xmax tul5j38", "Yamaha Xmax", "TUL-5J38")
    assert acoes._bate("joão", "Joao Silva")
    assert not acoes._bate("civic", "Honda Fit")


def test_unico_pede_escolha_quando_ha_varios():
    r, err = acoes._unico([{"n": "a"}, {"n": "b"}], lambda x: x["n"], "lead")
    assert r is None and "achei 2 leads" in err["erro"]
    assert acoes._unico([], str, "lead")[1] == {"erro": "não achei lead"}


def test_confirmacao_executa_ou_cancela(monkeypatch):
    pend = [{"id": "a1", "tipo": "criar_cartao", "params": {"nome": "Honda Fit 2015", "destino": "estoque sb!"},
             "descricao": "criar o cartão 'Honda Fit 2015' em estoque sb!"}]
    status, feitos = {}, []
    monkeypatch.setattr(acoes.db, "select", lambda t, p=None: pend)
    monkeypatch.setattr(acoes.db, "update", lambda t, d, f: status.update({f["id"]: d["status"]}))
    monkeypatch.setattr(acoes, "_executar", lambda a: feitos.append(a["id"]))
    assert acoes.tentar_confirmar("quantos carros vendemos?") is None and not feitos
    assert acoes.tentar_confirmar("Não").startswith("Ok, não fiz") and status["eq.a1"] == "cancelada"
    assert acoes.tentar_confirmar("ok!").startswith("✅") and feitos == ["a1"] and status["eq.a1"] == "feita"


def test_sem_pendente_nao_intercepta(monkeypatch):
    monkeypatch.setattr(acoes.db, "select", lambda t, p=None: [])
    assert acoes.tentar_confirmar("ok") is None
