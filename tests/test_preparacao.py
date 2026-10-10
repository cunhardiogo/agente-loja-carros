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
