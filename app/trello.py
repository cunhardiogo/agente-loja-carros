"""Quadro ESTOQUE no Trello acompanha as vendas e entregas dos grupos:
venda avisada → "Vendidos / Para entregar" (com o vendedor no nome e a data de entrega no cartão),
entregue → "FINALIZADO", venda que caiu → volta para "Estoque SB!".
O cartão é achado pela placa no nome ("FASTBACK 2026 (TTD-9E46) - Carlos"). Só move para frente a partir
das listas esperadas — cartão em RECALL ou em lista fora do fluxo não é mexido."""
import logging
import re
from datetime import datetime

import httpx

from . import datas, db
from .config import settings
from .leads import _ascii

log = logging.getLogger("agente")
_API = "https://api.trello.com/1"

VENDIDOS, FINALIZADO, ESTOQUE = "vendidos / para entregar", "finalizado", "estoque sb!"
ANTES_DA_VENDA = {"estoque sb!", "carro em transito", "pintura", "mecanico", "preparacao / foto"}

sujo = {"v": True}


def configurado() -> bool:
    return bool(settings.trello_key and settings.trello_token and settings.trello_board)


def _cliente() -> httpx.Client:
    return httpx.Client(base_url=_API, params={"key": settings.trello_key, "token": settings.trello_token},
                        verify=settings.verify_ssl, timeout=30)


def _placa(texto: str | None) -> str | None:
    m = re.search(r"\b([A-Za-z]{3})[\s-]?(\d[A-Za-z0-9]\d{2})\b", texto or "")
    return (m.group(1) + m.group(2)).upper() if m else None


def _due(venda: dict) -> str | None:
    """Data de entrega (da lista de entregas) no cartão, com horário quando houver."""
    d = venda.get("data_entrega_prevista")
    if not d:
        return None
    h = re.search(r"(\d{1,2})[:h](\d{2})?", venda.get("_horario") or "")
    hora, minuto = (int(h.group(1)), int(h.group(2) or 0)) if h else (9, 0)
    return datetime.fromisoformat(d).replace(hour=hora, minute=minuto, tzinfo=datas.TZ).isoformat()


_GENERICOS = {"fiat", "renault", "peugeot", "chevrolet", "volkswagen", "ford", "honda", "toyota", "hyundai", "nissan",
              "jeep", "citroen", "mitsubishi", "byd", "yamaha", "hatch", "sedan", "flex", "manual", "automatico",
              "griffe", "allure", "style", "exclusive", "ultra", "zen", "hybrid", "hibrido"}


def _por_modelo(v: dict, cards: list[dict], listas: dict, vendedor: str | None) -> dict | None:
    """Cartão sem placa no nome: casa pelo modelo (+ ano), preferindo o que tem o vendedor ou já está em
    Vendidos. Só devolve se sobrar exatamente um."""
    toks = {t for t in re.split(r"[^a-z0-9]+", _ascii(f"{v.get('modelo')} {v.get('versao')}"))
            if len(t) >= 3 and t not in _GENERICOS and not t.isdigit()}
    if not toks:
        return None
    cands = [cd for cd in cards if not _placa(cd["name"]) and toks & set(re.split(r"[^a-z0-9]+", _ascii(cd["name"])))]
    if v.get("ano"):
        com_ano = [cd for cd in cands if str(v["ano"]) in cd["name"]]
        cands = com_ano or cands
    for filtro in (lambda cd: vendedor and _ascii(vendedor) in _ascii(cd["name"]),
                   lambda cd: listas.get(cd["idList"]) == VENDIDOS):
        if len(cands) > 1:
            cands = [cd for cd in cands if filtro(cd)] or cands
    return cands[0] if len(cands) == 1 else None


def plano(c: httpx.Client) -> list[dict]:
    """O que precisa mudar no quadro (sem aplicar)."""
    from .sheets import DESDE
    from .vendas_grupo import nome_carro
    listas = {l["id"]: _ascii(l["name"]).strip() for l in c.get(f"/boards/{settings.trello_board}/lists").json()}
    por_nome = {v: k for k, v in listas.items()}
    cards = c.get(f"/boards/{settings.trello_board}/cards", params={"fields": "name,idList,due"}).json()
    por_placa = {}
    for card in cards:
        p = _placa(card["name"])
        if p:
            por_placa.setdefault(p, card)
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    horarios = {e["venda_id"]: e.get("horario") for e in db.select_all(
        "entregas", {"select": "venda_id,horario", "venda_id": "not.is.null", "removido": "eq.false"})}
    vendas = db.select_all("vendas", {"select": "*", "removido": "eq.false", "origem": "in.(grupo,planilha)",
                                      "status_venda": "in.(completa,aguardando_resumo,reservado,desistiu)"})
    acoes = []
    for v in vendas:
        if v.get("revenda") or (v.get("data_venda") or (v.get("reservado_em") or "")[:10] or v["created_at"][:10]) < DESDE:
            continue
        card = next((cd for cd in cards if cd["id"] == v.get("trello_card_id")), None) or por_placa.get(
            (v.get("placa") or "").replace("-", "").upper()) or _por_modelo(v, cards, listas,
                                                                           nomes.get(v.get("vendedor_id")))
        if not card:
            acoes.append({"venda": v, "acao": "sem_cartao", "carro": f"{nome_carro(v)} {v.get('placa') or ''}"})
            continue
        lista = listas.get(card["idList"], "")
        if lista == FINALIZADO and v["status_venda"] != "desistiu" and v.get("status_entrega") != "entregue":
            acoes.append({"venda": v, "card": card, "de": lista, "mudar": {}, "para": lista, "entregue_no_trello": True})
            continue  # dono finalizou o cartão no Trello → a venda é dada como entregue
        destino = None
        if v["status_venda"] == "desistiu":
            destino = ESTOQUE if lista == VENDIDOS else None
        elif v.get("status_entrega") == "entregue":
            destino = FINALIZADO if lista in ANTES_DA_VENDA | {VENDIDOS} else None
        else:
            destino = VENDIDOS if lista in ANTES_DA_VENDA else None
        mudar = {}
        if destino and por_nome.get(destino):
            mudar["idList"] = por_nome[destino]
        vend = nomes.get(v.get("vendedor_id"))
        if vend and v["status_venda"] != "desistiu" and _ascii(vend) not in _ascii(card["name"]):
            mudar["name"] = f"{card['name'].rstrip()} - {vend}"
        ativa = v["status_venda"] != "desistiu" and v.get("status_entrega") != "entregue"
        if ativa and listas.get(mudar.get("idList", card["idList"])) != FINALIZADO:
            due = _due({**v, "_horario": horarios.get(v["id"])})
            atual = card.get("due")
            if due and (not atual or datetime.fromisoformat(atual.replace("Z", "+00:00")) != datetime.fromisoformat(due)):
                mudar["due"] = due
        if mudar or v.get("trello_card_id") != card["id"]:
            acoes.append({"venda": v, "card": card, "de": lista, "mudar": mudar,
                          "para": listas.get(mudar.get("idList"), lista)})
    return acoes


def sincronizar(aplicar: bool = True) -> list[dict]:
    if not configurado():
        return []
    with _cliente() as c:
        acoes = plano(c)
        if aplicar:
            for a in acoes:
                if a.get("acao") == "sem_cartao":
                    continue
                if a.get("entregue_no_trello"):
                    db.update("vendas", {"status_entrega": "entregue",
                                         "data_entrega_real": a["venda"].get("data_entrega_real") or datas.hoje_iso()},
                              {"id": f"eq.{a['venda']['id']}"})
                    db.update("entregas", {"status": "entregue", "entregue_em": datas.agora().isoformat()},
                              {"venda_id": f"eq.{a['venda']['id']}", "status": "eq.agendada"})
                if a.get("mudar"):
                    c.put(f"/cards/{a['card']['id']}", params=a["mudar"]).raise_for_status()
                if a["venda"].get("trello_card_id") != a["card"]["id"]:
                    db.update("vendas", {"trello_card_id": a["card"]["id"]}, {"id": f"eq.{a['venda']['id']}"})
    return acoes
