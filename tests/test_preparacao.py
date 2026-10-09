from app import preparacao_grupo as pg


def test_missao_repostada_casa_com_a_tarefa():
    assert pg._parecida("Levar para pintura", "Levar pra pintura")
    assert not pg._parecida("Trocar pneu", "Levar para pintura")


def test_nome_do_carro_sem_vendedor():
    assert pg._carro({"card_nome": "Fiat Palio 2016 (KWU-8934) - Vinicius"}) == "Fiat Palio 2016 (KWU-8934)"
    assert pg._carro({"card_nome": None, "carro_texto": "Versa branco"}) == "Versa branco"
