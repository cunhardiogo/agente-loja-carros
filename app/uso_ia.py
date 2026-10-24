"""Quanto cada parte do agente gasta de IA: conta chamadas e tokens por dia, origem (módulo.função) e modelo.
Fica em memória e vai pra tabela uso_ia a cada poucos minutos (somando ao que já estava lá)."""
import inspect
import logging
import threading
from collections import defaultdict

from . import datas, db

log = logging.getLogger("agente")

PRECO = {"gpt-4.1": (2.0, 8.0), "gpt-4.1-mini": (0.4, 1.6), "gpt-4.1-nano": (0.1, 0.4)}  # US$ por 1M tokens
_lock = threading.Lock()
_delta: dict = defaultdict(lambda: [0, 0, 0])


def _origem() -> str:
    f = inspect.currentframe()
    while f:
        mod = f.f_globals.get("__name__", "")
        if mod.startswith("app.") and mod != __name__:
            return f"{mod[4:]}.{f.f_code.co_name}"
        f = f.f_back
    return "?"


def medir(client) -> None:
    """Envolve client.chat.completions.create pra registrar o uso."""
    if client is None:
        return
    original = client.chat.completions.create

    def wrapper(*a, **k):
        resp = original(*a, **k)
        try:
            u = getattr(resp, "usage", None)
            with _lock:
                d = _delta[(datas.hoje_iso(), _origem(), k.get("model") or "?")]
                d[0] += 1
                d[1] += getattr(u, "prompt_tokens", 0) or 0
                d[2] += getattr(u, "completion_tokens", 0) or 0
        except Exception:
            pass
        return resp

    client.chat.completions.create = wrapper


def gravar() -> None:
    with _lock:
        pend = dict(_delta)
        _delta.clear()
    if not pend:
        return
    try:
        atuais = {(r["dia"], r["origem"], r["modelo"]): r for r in db.select_all(
            "uso_ia", {"select": "*", "dia": f"in.({','.join(sorted({k[0] for k in pend}))})"})}
        rows = []
        for (dia, origem, modelo), (n, tin, tout) in pend.items():
            a = atuais.get((dia, origem, modelo)) or {}
            rows.append({"dia": dia, "origem": origem, "modelo": modelo, "chamadas": (a.get("chamadas") or 0) + n,
                         "tokens_in": (a.get("tokens_in") or 0) + tin, "tokens_out": (a.get("tokens_out") or 0) + tout})
        db.upsert("uso_ia", rows, on_conflict="dia,origem,modelo")
    except Exception:
        log.exception("falha gravando uso de IA")
        with _lock:
            for k, (n, tin, tout) in pend.items():
                d = _delta[k]
                d[0] += n
                d[1] += tin
                d[2] += tout


def custo(modelo: str, tin: int, tout: int) -> float:
    pi, po = PRECO.get(modelo, (2.0, 8.0))
    return tin / 1e6 * pi + tout / 1e6 * po


def resumo(dias: int = 7) -> dict:
    """Gasto por dia e por origem (US$ estimado pela tabela de preço)."""
    from datetime import timedelta
    desde = (datas.hoje() - timedelta(days=dias - 1)).isoformat()
    rows = db.select_all("uso_ia", {"select": "*", "dia": f"gte.{desde}"})
    por_dia: dict = defaultdict(float)
    por_origem: dict = defaultdict(lambda: [0, 0.0])
    for r in rows:
        c = custo(r["modelo"], r["tokens_in"], r["tokens_out"])
        por_dia[r["dia"]] += c
        por_origem[f"{r['origem']} ({r['modelo']})"][0] += r["chamadas"]
        por_origem[f"{r['origem']} ({r['modelo']})"][1] += c
    return {"por_dia_usd": {d: round(v, 3) for d, v in sorted(por_dia.items())},
            "por_origem": sorted(([o, n, round(c, 3)] for o, (n, c) in por_origem.items()), key=lambda x: -x[2])}
