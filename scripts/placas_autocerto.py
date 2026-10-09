"""Completa a placa dos cartões do Trello que estão sem, usando o estoque ATIVO do Autocerto (só leitura).
Pontua cada carro do Autocerto contra o cartão (modelo, versão, cor, km — inclusive do nome antigo do cartão)
e só preenche quando o melhor par é único e a placa não está em outro cartão.
Uso: python -m scripts.placas_autocerto estoque.json [--aplicar]"""
import json
import re
import sys

from app import trello
from app.config import settings
from app.leads import _ascii

ANO = re.compile(r"\b(?:19|20)\d{2}\b")


def _junto(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _ascii(t))


def _km(texto: str) -> int | None:
    m = re.search(r"(\d+)\s*(mil)?\s*km", _ascii(texto))
    if not m:
        return None
    n = int(m.group(1))
    return n * 1000 if m.group(2) or n < 1000 else n


def _pontos(v: dict, texto: str) -> int:
    """Quanto o carro do Autocerto combina com o cartão (nome atual + nome antigo)."""
    a, junto = _ascii(texto), _junto(texto)
    toks = [t for t in re.split(r"[^a-z0-9]+", _ascii(v["Modelo"])) if t]
    if _junto(v["Modelo"]) in junto:
        pts = 10
    elif _ascii(v["Marca"]) in a and any(len(t) >= 4 and t in a for t in toks):
        pts = 5
    else:
        return 0
    versao = [t for t in re.split(r"[^a-z0-9]+", _ascii(v.get("Versao"))) if len(t) >= 3 and not t.isdigit()]
    pts += sum(2 for t in versao if re.search(r"\b" + re.escape(t) + r"\b", a))
    if v.get("Cor") and re.search(r"\b" + re.escape(_ascii(v["Cor"])) + r"\b", a):
        pts += 3
    km = _km(texto)
    if km and v.get("Km") and abs(km - v["Km"]) <= 5000:
        pts += 2
    return pts


def _placa(v: dict) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v.get("Placa") or "").upper())


def plano(estoque):
    try:
        antes = json.load(open("scripts/trello_nomes_antes_2026-10-09.json", encoding="utf-8"))
    except FileNotFoundError:
        antes = {}
    with trello._cliente() as c:
        cards = c.get(f"/boards/{settings.trello_board}/cards", params={"fields": "name,idList"}).json()
        listas = {l["id"]: l["name"] for l in c.get(f"/boards/{settings.trello_board}/lists").json()}
    em_uso = {trello._placa(cd["name"]) for cd in cards if trello._placa(cd["name"])}
    abertos = [cd for cd in cards if not trello._placa(cd["name"]) and listas.get(cd["idList"]) != "FINALIZADO"]
    pares = []
    for cd in abertos:
        ano = ANO.search(cd["name"])
        if not ano:
            continue
        texto = f"{cd['name']} {antes.get(cd['id'], '')}"
        for v in estoque:
            if _placa(v) and str(v.get("AnoModelo")) == ano.group():
                pts = _pontos(v, texto)
                if pts:
                    pares.append((pts, cd, v))
    achados, ligados = [], set()
    # melhor par primeiro; cartão ou placa já usados saem; empate no topo do mesmo cartão = ambíguo (fica sem)
    for pts, cd, v in sorted(pares, key=lambda x: -x[0]):
        placa = _placa(v)
        if cd["id"] in ligados or placa in em_uso:
            continue
        if any(p == pts and c2["id"] == cd["id"] and v2 is not v and _placa(v2) not in em_uso for p, c2, v2 in pares):
            continue
        ligados.add(cd["id"])
        em_uso.add(placa)
        m = ANO.search(cd["name"])
        novo = f"{cd['name'][:m.end()]} ({placa[:3]}-{placa[3:]}){cd['name'][m.end():]}"
        achados.append((listas.get(cd["idList"]), cd["name"], novo, cd["id"],
                        f"{v['Marca']} {v['Modelo']} {v['AnoModelo']} {v.get('Cor') or ''} {round(v.get('Km') or 0)}km"))
    sem = [(listas.get(cd["idList"]), cd["name"]) for cd in abertos if cd["id"] not in ligados]
    return achados, sem


if __name__ == "__main__":
    achados, sem = plano(json.load(open(sys.argv[1], encoding="utf-8")))
    for lista, atual, novo, _, ref in achados:
        print(f"[{lista}] {atual}  →  {novo}   (Autocerto: {ref})")
    print("\nSEM PLACA (não achei com segurança):")
    for lista, atual in sem:
        print(f"[{lista}] {atual}")
    if "--aplicar" in sys.argv:
        with trello._cliente() as c:
            for _, _, novo, cid, _ in achados:
                c.put(f"/cards/{cid}", params={"name": novo}).raise_for_status()
        print("aplicado")
