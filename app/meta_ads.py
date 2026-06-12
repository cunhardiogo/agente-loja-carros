"""Conector Meta Ads (Graph API) — cruza gasto/leads/conversas com as vendas do
canal Tráfego. Token e conta vêm do ambiente (META_TOKEN, META_AD_ACCOUNT)."""
import logging
import time

import httpx

from .config import settings

log = logging.getLogger("agente")
_BASE = "https://graph.facebook.com/v21.0"
_http = httpx.Client(verify=settings.verify_ssl, timeout=40)

# cache simples (Graph API é lenta e tem limite); o dashboard atualiza a cada 60s
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 600  # 10 min


def _cacheado(chave: str, fn):
    agora = time.time()
    hit = _CACHE.get(chave)
    if hit and agora - hit[0] < _TTL:
        return hit[1]
    val = fn()
    if not val.get("erro"):  # não cacheia erro transitório
        _CACHE[chave] = (agora, val)
    return val

# O Meta reporta a MESMA conversão sob vários action_type (lead == lead_grouped ==
# onsite_web_lead == ...). Somar tudo triplica o número. Então usamos UMA métrica
# canônica por prioridade (a 1ª presente vence) — nunca a soma.
_LEADS = ["lead", "onsite_conversion.lead_grouped", "onsite_web_lead"]
_CONVERSAS = ["onsite_conversion.messaging_conversation_started_7d",
              "onsite_conversion.total_messaging_connection"]


def configurado() -> bool:
    return bool(settings.meta_token and settings.meta_ad_account)


def _get(path: str, params: dict) -> dict:
    params = {**params, "access_token": settings.meta_token}
    r = _http.get(f"{_BASE}/{path}", params=params)
    r.raise_for_status()
    return r.json()


def _valor_acao(actions: list, prioridade: list) -> int:
    """Valor de UMA ação canônica: a primeira da lista de prioridade que existir.
    Evita somar rótulos duplicados que o Meta usa pra mesma conversão."""
    idx = {a.get("action_type"): a for a in (actions or [])}
    for t in prioridade:
        if t in idx:
            return int(float(idx[t]["value"]))
    return 0


def _preset(periodo: str) -> str:
    return {"hoje": "today", "ontem": "yesterday", "semana": "last_7d",
            "mes": "this_month", "7d": "last_7d", "30d": "last_30d"}.get((periodo or "semana").lower(), "last_7d")


def _resumo(periodo: str = "semana") -> dict:
    """Totais da conta no período: gasto, impressões, cliques, leads, conversas, CPL."""
    if not configurado():
        return {"erro": "Meta Ads não configurado (defina META_TOKEN e META_AD_ACCOUNT)."}
    try:
        data = _get(f"{settings.meta_ad_account}/insights", {
            "fields": "spend,impressions,clicks,ctr,cpc,actions", "date_preset": _preset(periodo)})
    except httpx.HTTPStatusError as e:
        corpo = e.response.text[:200]
        if "expired" in corpo.lower() or e.response.status_code == 190:
            return {"erro": "Token do Meta expirado. Renove o META_TOKEN."}
        log.warning("meta insights erro: %s", corpo)
        return {"erro": "Não consegui falar com o Meta agora."}
    except Exception:
        log.exception("meta resumo")
        return {"erro": "Não consegui falar com o Meta agora."}

    linha = (data.get("data") or [{}])[0]
    gasto = float(linha.get("spend") or 0)
    leads = _valor_acao(linha.get("actions"), _LEADS)
    conversas = _valor_acao(linha.get("actions"), _CONVERSAS)
    return {
        "periodo": periodo,
        "gasto": round(gasto, 2),
        "impressoes": int(linha.get("impressions") or 0),
        "cliques": int(linha.get("clicks") or 0),
        "ctr": round(float(linha.get("ctr") or 0), 2),
        "cpc": round(float(linha.get("cpc") or 0), 2),
        "leads": leads,
        "conversas": conversas,
        "custo_por_lead": round(gasto / leads, 2) if leads else None,
        "custo_por_conversa": round(gasto / conversas, 2) if conversas else None,
    }


def _campanhas(periodo: str = "semana") -> dict:
    """Gasto e leads por campanha ativa no período."""
    if not configurado():
        return {"erro": "Meta Ads não configurado."}
    try:
        data = _get(f"{settings.meta_ad_account}/insights", {
            "fields": "campaign_name,spend,impressions,clicks,actions",
            "level": "campaign", "date_preset": _preset(periodo), "limit": "50"})
    except Exception:
        log.exception("meta campanhas")
        return {"erro": "Não consegui falar com o Meta agora."}
    itens = []
    for c in data.get("data", []):
        gasto = float(c.get("spend") or 0)
        leads = _valor_acao(c.get("actions"), _LEADS)
        itens.append({"campanha": c.get("campaign_name"), "gasto": round(gasto, 2),
                      "cliques": int(c.get("clicks") or 0), "leads": leads,
                      "custo_por_lead": round(gasto / leads, 2) if leads else None})
    itens.sort(key=lambda x: x["gasto"], reverse=True)
    return {"periodo": periodo, "campanhas": itens}


def _roas(periodo: str = "mes") -> dict:
    """Cruza o gasto do Meta com o faturamento das vendas do canal Tráfego."""
    r = _resumo(periodo)
    if r.get("erro"):
        return r
    from . import consulta
    canais = consulta.vendas_por_canal("mes" if periodo in ("mes", "30d") else "semana").get("canais", [])
    trafego = next((c for c in canais if "tráfeg" in (c.get("canal") or "").lower()
                    or "trafeg" in (c.get("canal") or "").lower() or "meta" in (c.get("canal") or "").lower()), None)
    fat = (trafego or {}).get("valor_total", 0) or 0
    vendas = (trafego or {}).get("quantidade", 0) or 0
    gasto = r["gasto"]
    return {
        "periodo": periodo, "gasto": gasto, "leads": r["leads"],
        "faturamento_trafego": fat, "vendas_trafego": vendas,
        "roas": round(fat / gasto, 2) if gasto else None,
        "custo_por_venda": round(gasto / vendas, 2) if vendas else None,
    }


def resumo(periodo: str = "semana") -> dict:
    return _cacheado(f"resumo:{periodo}", lambda: _resumo(periodo))


def campanhas(periodo: str = "semana") -> dict:
    return _cacheado(f"campanhas:{periodo}", lambda: _campanhas(periodo))


def roas(periodo: str = "mes") -> dict:
    return _cacheado(f"roas:{periodo}", lambda: _roas(periodo))


def token_status() -> dict:
    """Validade do token (pro watchdog W6)."""
    if not configurado():
        return {"ok": False, "motivo": "nao_configurado"}
    try:
        d = _get("debug_token", {"input_token": settings.meta_token})["data"]
        return {"ok": bool(d.get("is_valid")), "expira_em": d.get("expires_at")}
    except Exception:
        log.exception("meta token_status")
        return {"ok": False, "motivo": "erro_consulta"}
