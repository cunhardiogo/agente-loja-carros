from app import preparacao_grupo as pg


def test_missao_repostada_casa_com_a_tarefa():
    assert pg._parecida("Levar para pintura", "Levar pra pintura")
    assert not pg._parecida("Trocar pneu", "Levar para pintura")


def test_nome_do_carro_sem_vendedor():
    assert pg._carro({"card_nome": "Fiat Palio 2016 (KWU-8934) - Vinicius"}) == "Fiat Palio 2016 (KWU-8934)"
    assert pg._carro({"card_nome": None, "carro_texto": "Versa branco"}) == "Versa branco"


def test_avaliacao_nao_casa_pela_marca():
    from app import avaliacoes_grupo as ag
    assert not ag._toks("Volkswagen Virtus 1.6 MSI manual") & ag._toks("Volkswagen Nivus 1.0 Comfortline")
    assert ag._toks("nmax") & ag._toks("Nmax Conected")


def test_edicao_acrescenta_e_tira_itens(monkeypatch):
    tarefas = [{"id": "t1", "message_id": "m1", "card_id": "c1", "descricao": "Limpar motor", "status": "pendente",
                "checklist_item_id": "i1", "criado_em": "2026-10-10T15:00:00+00:00", "responsavel": None},
               {"id": "t2", "message_id": "m1", "card_id": "c1", "descricao": "Retirar insulfilm", "status": "pendente",
                "checklist_item_id": "i2", "criado_em": "2026-10-10T15:00:00+00:00", "responsavel": None}]
    criados, removidos = [], []

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(pg.trello, "_cliente", lambda: C())
    monkeypatch.setattr(pg.trello, "cartoes", lambda c: ([{"id": "c1", "name": "Renault Kwid 2024 (SEZ-3A93)", "idList": "l"}], {"l": "estoque sb!"}))
    monkeypatch.setattr(pg.trello, "item_checklist", lambda c, card, txt: criados.append(txt) or "novo")
    monkeypatch.setattr(pg.trello, "remover_item", lambda c, card, item: removidos.append(item))
    monkeypatch.setattr(pg, "extrair", lambda texto, *a: [
        {"acao": "tarefa", "carro": "Kwid SEZ-3A93", "descricao": "Limpar motor", "tipo": "estetica"},
        {"acao": "tarefa", "carro": "Kwid SEZ-3A93", "descricao": "Polir farol", "tipo": "estetica"}])
    monkeypatch.setattr(pg.db, "select_all", lambda t, p=None: [x for x in tarefas if x["status"] == "pendente"]
                        if t == "prep_tarefas" else [])
    monkeypatch.setattr(pg.db, "insert", lambda t, d: {"id": "t3", **d})
    monkeypatch.setattr(pg.db, "update", lambda *a: [])
    monkeypatch.setattr(pg.db, "delete", lambda *a: None)
    r = pg._edicao({"key": {"id": "m1"}, "editedMessage": {"conversation": "Kwid: limpar motor e polir farol"}}, {"key": {}})
    assert criados == ["Polir farol"] and removidos == ["i2"] and r["removidas"] == 1
