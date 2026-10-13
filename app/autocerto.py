"""Estoque do Autocerto (só leitura): preço, km, cor, placa e há quantos dias o carro está cadastrado.
Cruza com o Trello pra dizer onde o carro está (estoque, pintura, mecânico...)."""
import time
from datetime import datetime
from urllib.parse import urlencode

import httpx

from . import datas
from .config import settings
from .leads import _ascii

_UA = "Mozilla/5.0"
_cache: dict = {"t": 0.0, "itens": [], "token": ("", 0.0)}


def configurado() -> bool:
    return bool(settings.autocerto_user and settings.autocerto_pass)


def _token(c: httpx.Client) -> str:
    tok, exp = _cache["token"]
    if tok and time.time() < exp:
        return tok
    r = c.post(f"{settings.autocerto_base}/oauth/token",
               content=urlencode({"grant_type": "password", "username": settings.autocerto_user,
                                  "password": settings.autocerto_pass}),
               headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _UA})
    r.raise_for_status()
    d = r.json()
    _cache["token"] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)) - 60)
    return d["access_token"]


def estoque(forcar: bool = False) -> list[dict]:
    """Carros com preço de todas as unidades, cache de 5 min."""
    if not forcar and time.time() - _cache["t"] < 300:
        return _cache["itens"]
    vistos: dict = {}
    with httpx.Client(timeout=30, verify=settings.verify_ssl) as c:
        tok = _token(c)
        for u in [x for x in settings.autocerto_unidades.replace(";", ",").split(",") if x.strip()] \
                or [str(settings.autocerto_unidade)]:
            r = c.get(f"{settings.autocerto_base}/api/Veiculo/ObterEstoque?"
                      f"{urlencode({'codigoUnidade': u.strip(), 'precoDe': 0, 'precoAte': 999999999})}",
                      headers={"Authorization": f"Bearer {tok}", "User-Agent": _UA})
            if r.is_success and isinstance(r.json(), list):
                for v in r.json():
                    vistos.setdefault(v.get("Codigo"), v)
    hoje = datas.hoje()
    itens = []
    for v in vistos.values():
        cad = (v.get("DataCadastro") or "")[:10]
        itens.append({"carro": " ".join(str(x) for x in (v.get("Marca"), v.get("Modelo"), v.get("Versao")) if x),
                      "ano": v.get("AnoModelo") or v.get("AnoFabricacao"), "km": round(v.get("Km") or 0),
                      "cor": v.get("Cor"), "cambio": v.get("Cambio"), "placa": v.get("Placa"),
                      "preco": v.get("Preco") or None,
                      "dias_no_estoque": (hoje - datetime.fromisoformat(cad).date()).days if cad else None,
                      "cadastrado_em": cad})
    _cache.update({"t": time.time(), "itens": itens})
    return itens


def consultar(termo: str | None = None, preco_min: float | None = None, preco_max: float | None = None,
              ordem: str = "preco") -> dict:
    from . import trello
    itens = estoque()
    if termo:
        toks = [t for t in _ascii(termo).replace("-", "").split() if t]
        itens = [i for i in itens
                 if all(t in _ascii(f"{i['carro']} {i['ano']} {i['cor']} {i['placa']}").replace("-", "") for t in toks)]
    if preco_min:
        itens = [i for i in itens if (i["preco"] or 0) >= preco_min]
    if preco_max:
        itens = [i for i in itens if i["preco"] and i["preco"] <= preco_max]
    chave = {"dias": lambda i: -(i["dias_no_estoque"] or 0), "km": lambda i: i["km"],
             "ano": lambda i: -(i["ano"] or 0)}.get(ordem, lambda i: i["preco"] or 0)
    itens = sorted(itens, key=chave)
    if itens and trello.configurado():  # onde o carro está agora no quadro
        try:
            with trello._cliente() as c:
                cards, listas = trello.cartoes(c)
            por_placa = {trello._placa(cd["name"]): listas.get(cd["idList"]) for cd in cards if trello._placa(cd["name"])}
            for i in itens:
                i["no_trello"] = por_placa.get((i["placa"] or "").replace("-", "").upper())
        except Exception:
            pass
    com_preco = [i["preco"] for i in itens if i["preco"]]
    return {"quantidade": len(itens), "valor_total": sum(com_preco),
            "preco_medio": round(sum(com_preco) / len(com_preco)) if com_preco else None,
            "carros": itens[:40]}
