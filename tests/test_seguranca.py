from app import main
from app.config import settings


def test_token_ok_constante(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_token", "segredo")
    assert main._token_ok("segredo") is True
    assert main._token_ok("errado") is False
    assert main._token_ok("") is False


def test_token_ok_sem_token_configurado_nega(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_token", "")
    assert main._token_ok("") is False  # sem token configurado, ninguém entra


def test_rate_limit(monkeypatch):
    main._rate["min"], main._rate["n"] = 0, 0
    monkeypatch.setattr(main._time, "time", lambda: 0.0)
    assert all(main._rate_limit_ok(limite=5) for _ in range(5))
    assert main._rate_limit_ok(limite=5) is False  # 6ª no mesmo minuto → barra


def test_rate_limit_reseta_no_minuto_seguinte(monkeypatch):
    main._rate["min"], main._rate["n"] = 0, 0
    t = {"v": 0.0}
    monkeypatch.setattr(main._time, "time", lambda: t["v"])
    assert main._rate_limit_ok(limite=1) is True
    assert main._rate_limit_ok(limite=1) is False
    t["v"] = 61.0  # minuto seguinte
    assert main._rate_limit_ok(limite=1) is True
