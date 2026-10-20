from app import fotos_grupo


class _C:
    def __init__(self):
        self.criados = []

    def post(self, url, params):
        self.criados.append(params["name"])
        return self

    def raise_for_status(self):
        pass


def test_foto_sem_cartao_cria_pelo_autocerto(monkeypatch):
    monkeypatch.setattr(fotos_grupo, "_placa_autocerto", lambda modelo, ano: "RKA-2F15" if ano == "2021" else None)
    monkeypatch.setattr(fotos_grupo.evolution, "enviar_texto", lambda *a: None)
    c, cards = _C(), [{"name": "Honda HR-V 2019 (LMU-9J17)", "idList": "x"}]
    nome = fotos_grupo._cartao_do_autocerto(c, "HR-V Touring 2021", cards, "est")
    assert "RKA-2F15" in nome and "2021" in nome and c.criados == [nome]
    assert fotos_grupo._cartao_do_autocerto(c, "HR-V Touring 2021", cards, "est") is None  # placa já no quadro
