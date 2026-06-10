from app import main


def _tipos(dow, hhmm):
    return {t for t, _ in main._jobs_relatorio(dow, hhmm)}


def test_agenda_dentro_da_janela():
    assert "agenda" in _tipos(0, "09:30")  # segunda 09:30


def test_agenda_fora_da_janela_nao_dispara():
    assert "agenda" not in _tipos(0, "23:00")  # app subiu tarde → não manda agenda velha


def test_planejamento_so_segunda_de_manha():
    assert "planejamento" in _tipos(0, "08:10")
    assert "planejamento" not in _tipos(1, "08:10")  # terça não


def test_fechamento_sabado_15h():
    assert "fechamento" in _tipos(5, "15:30")
    assert "fechamento" not in _tipos(5, "14:00")


def test_semanal_so_domingo_noite():
    assert "semanal" in _tipos(6, "18:30")
    assert "semanal" not in _tipos(6, "09:00")


def test_domingo_nao_tem_agenda_nem_fechamento():
    assert _tipos(6, "09:30") == set()
    assert _tipos(6, "19:00") == {"semanal"}
