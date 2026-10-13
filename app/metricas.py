"""Métricas de todos os setores (vendas, leads, entregas, preparação, estoque, avaliações, recall, marketing,
comunicação e equipe) num período — base do dashboard e das perguntas de KPI ao agente.
Cada setor é uma função (ini, fim) → dict; `calcular` junta tudo. Datas no fuso de São Paulo."""
import calendar
import logging
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import datas, db
from .leads import _ascii, _dt

log = logging.getLogger("agente")

DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
_ESTAGIOS = {"estoque sb!": "Estoque", "carro em transito": "Em trânsito", "pintura": "Pintura", "mecanico": "Mecânico",
             "preparacao / foto": "Preparação/Foto", "vendidos / para entregar": "Vendido → entregar",
             "recall": "Recall", "finalizado": "Finalizado"}


# ===== período =====
def periodo(nome: str | None = "mes", inicio: str | None = None, fim: str | None = None) -> tuple[str, str]:
    hoje = datas.hoje()
    if inicio or fim:
        return inicio or "2024-01-01", fim or hoje.isoformat()
    p = (nome or "mes").lower()
    if re.fullmatch(r"\d{4}-\d{2}", p):
        a, m = map(int, p.split("-"))
        return f"{p}-01", f"{p}-{calendar.monthrange(a, m)[1]:02d}"
    if p == "hoje":
        return hoje.isoformat(), hoje.isoformat()
    if p == "ontem":
        d = hoje - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if p == "semana":
        return (hoje - timedelta(days=hoje.weekday())).isoformat(), hoje.isoformat()
    if p == "mes_passado":
        ult = hoje.replace(day=1) - timedelta(days=1)
        return ult.replace(day=1).isoformat(), ult.isoformat()
    if p.endswith("d") and p[:-1].isdigit():
        return (hoje - timedelta(days=int(p[:-1]) - 1)).isoformat(), hoje.isoformat()
    if p == "ano":
        return hoje.replace(month=1, day=1).isoformat(), hoje.isoformat()
    return hoje.replace(day=1).isoformat(), hoje.isoformat()  # mês atual


def _dia(iso: str | None) -> str | None:
    """Data local (AAAA-MM-DD) de um timestamp ou data."""
    if not iso:
        return None
    if len(iso) == 10:
        return iso
    return _dt(iso).astimezone(datas.TZ).date().isoformat()


def _no(iso, ini, fim) -> bool:
    d = _dia(iso)
    return bool(d) and ini <= d <= fim


def _pct(a, b) -> float | None:
    return round(100 * a / b, 1) if b else None


def _med(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _media(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _horas(a, b) -> float | None:
    if not a or not b:
        return None
    return (_dt(b) - _dt(a)).total_seconds() / 3600


def _valor(v) -> float:
    from .vendas_grupo import _valor as vv
    if isinstance(v, (int, float)):
        return float(v)
    return vv(str(v)) or 0.0 if v else 0.0


def _nomes() -> dict:
    return {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}


def canal(txt: str | None) -> str:
    a = _ascii(txt or "")
    for chave, nome in (("trafeg", "Tráfego"), ("olx", "OLX"), ("webmotor", "Webmotors"), ("whats", "WhatsApp"),
                        ("insta", "Instagram"), ("indica", "Indicação"), ("cliente meu", "Carteira do vendedor"),
                        ("carteira", "Carteira do vendedor"), ("site", "Site"), ("facebook", "Facebook"),
                        ("mercado", "Mercado Livre"), ("icarros", "iCarros"), ("google", "Google"),
                        ("passando", "Passante/loja"), ("loja", "Passante/loja")):
        if chave in a:
            return nome
    return (txt or "").strip().title() or "Não informado"


def forma_pagamento(v: dict) -> str:
    texto = _ascii(f"{v.get('forma_pagamento') or ''} {v.get('banco') or ''} {v.get('texto_resumo') or ''}")
    if "consorc" in texto:
        return "Consórcio"
    banco = _ascii(v.get("banco") or "")
    if (v.get("valor_financiado") or 0) > 0 or (banco and "vista" not in banco and "pix" not in banco):
        return "Financiado"
    return "À vista"


def _faixa_preco(p: float) -> str:
    for lim, nome in ((50000, "até 50 mil"), (80000, "50–80 mil"), (120000, "80–120 mil"), (180000, "120–180 mil")):
        if p <= lim:
            return nome
    return "acima de 180 mil"


def _faixa_dias(d: int | None) -> str:
    if d is None:
        return "?"
    for lim, nome in ((30, "0–30 dias"), (60, "31–60 dias"), (90, "61–90 dias")):
        if d <= lim:
            return nome
    return "mais de 90 dias"


# ===== vendas =====
def _vendas() -> list[dict]:
    return db.select_all("vendas", {"select": "*", "removido": "eq.false", "origem": "in.(grupo,planilha)"})


def vendas(ini: str, fim: str) -> dict:
    nomes = _nomes()
    todas = _vendas()
    validas = [v for v in todas if v["status_venda"] in ("completa", "aguardando_resumo") and _no(v.get("data_venda"), ini, fim)]
    loja = [v for v in validas if not v.get("revenda")]
    revenda = [v for v in validas if v.get("revenda")]
    total = lambda v: v.get("valor_total") or v.get("valor_venda") or 0
    fat = sum(total(v) for v in loja)
    com_valor = [v for v in loja if total(v)]
    reservas = [v for v in todas if v["status_venda"] == "reservado"]
    reservas_periodo = [v for v in todas if v.get("reservado_em") and _no(v["reservado_em"], ini, fim)]
    caidas = [v for v in todas if v["status_venda"] == "desistiu" and _no(v.get("data_venda") or v.get("reservado_em")
                                                                        or v.get("updated_at"), ini, fim)]
    por_vend: dict = defaultdict(lambda: {"vendas": 0, "faturamento": 0.0})
    for v in loja:
        p = por_vend[nomes.get(v.get("vendedor_id"), "Sem vendedor")]
        p["vendas"] += 1
        p["faturamento"] += total(v)
    for p in por_vend.values():
        p["ticket_medio"] = round(p["faturamento"] / p["vendas"]) if p["vendas"] else None
    por_canal = Counter(canal(v.get("portal_venda")) for v in loja)
    fat_canal: dict = defaultdict(float)
    for v in loja:
        fat_canal[canal(v.get("portal_venda"))] += total(v)
    formas = Counter(forma_pagamento(v) for v in loja)
    com_troca = [v for v in loja if v.get("troca_modelo") or v.get("troca_valor")]
    resumo_h = [_horas(v.get("aviso_em"), v.get("resumo_em")) for v in loja if v.get("aviso_em") and v.get("resumo_em")]
    resumo_h = [h for h in resumo_h if h is not None and h >= 0]
    pend = [v for v in loja if v.get("pendencias")]
    faltando = Counter(c for v in pend for c in (v.get("pendencias") or []))
    serie = Counter(v["data_venda"] for v in loja)
    pags = [p for p in db.select_all("pagamentos", {"select": "valor,pago_em,venda_id"}) if _no(p.get("pago_em"), ini, fim)]
    try:
        from .consulta import a_receber_vendas
        receber = a_receber_vendas("tudo")
        a_receber = {k: receber[k] for k in ("total_a_receber", "financiamento_banco", "a_vista")}
    except Exception:
        a_receber = None
    return {
        "vendidos": len(loja), "faturamento": round(fat), "ticket_medio": round(fat / len(com_valor)) if com_valor else None,
        "vendas_sem_valor": len(loja) - len(com_valor), "revenda": len(revenda),
        "faturamento_revenda": round(sum(total(v) for v in revenda)),
        "reservas_abertas": len(reservas), "reservas_no_periodo": len(reservas_periodo),
        "caidas": len(caidas), "taxa_queda_pct": _pct(len(caidas), len(loja) + len(caidas)),
        "por_vendedor": dict(sorted(por_vend.items(), key=lambda kv: -kv[1]["vendas"])),
        "por_canal": dict(por_canal.most_common()), "faturamento_por_canal": {k: round(v) for k, v in fat_canal.items()},
        "por_forma_pagamento": dict(formas),
        "financiado_pct": _pct(formas.get("Financiado", 0), len(loja)),
        "com_troca": len(com_troca), "com_troca_pct": _pct(len(com_troca), len(loja)),
        "valor_trocas": round(sum(v.get("troca_valor") or 0 for v in com_troca)),
        "over_total": round(sum(_valor(v.get("over_valor")) for v in loja)),
        "desconto_total": round(sum(_valor(v.get("desconto")) for v in loja)),
        "retorno_total": round(sum(_valor(v.get("retorno")) for v in loja)),
        "por_faixa_preco": dict(Counter(_faixa_preco(total(v)) for v in com_valor)),
        "por_marca": dict(Counter((v.get("marca") or (v.get("modelo") or "?").split()[0]).title() for v in loja).most_common(10)),
        "por_dia_semana": {DIAS_SEMANA[i]: n for i, n in sorted(Counter(date.fromisoformat(v["data_venda"]).weekday()
                                                                          for v in loja).items())},
        "serie_diaria": dict(sorted(serie.items())),
        "resumo_completo_pct": _pct(sum(1 for v in loja if v["status_venda"] == "completa" and not v.get("pendencias")), len(loja)),
        "sem_resumo": sum(1 for v in loja if v["status_venda"] == "aguardando_resumo"),
        "resumo_com_pendencia": len(pend), "campos_mais_faltando": dict(faltando.most_common(6)),
        "tempo_aviso_ate_resumo_h": _med(resumo_h),
        "vindas_de_lead": sum(1 for v in loja if v.get("lead_id")),
        "recebido_no_periodo": round(sum(p.get("valor") or 0 for p in pags)), "comprovantes_no_periodo": len(pags),
        "a_receber": a_receber,
    }


# ===== leads (agendamento) =====
def leads(ini: str, fim: str) -> dict:
    from .leads import STATUS_LABEL, TIPO_LABEL
    rows = [l for l in db.select_all("leads", {"select": "*", "removido": "eq.false"}) if _no(l.get("recebido_em"), ini, fim)]
    ids = {l["id"] for l in rows}
    primeiro: dict = {}
    for e in db.select_all("lead_eventos", {"select": "lead_id,em", "order": "em.asc"}):
        if e["lead_id"] in ids:
            primeiro.setdefault(e["lead_id"], e["em"])
    retorno_min = {l["id"]: _horas(l["recebido_em"], primeiro[l["id"]]) * 60 for l in rows if l["id"] in primeiro}
    hoje = datas.hoje_iso()
    visitas = [l for l in rows if l.get("tipo") == "visita" and l.get("data_agendada") and l["data_agendada"] < hoje]
    vieram = [l for l in visitas if l["status"] in ("compareceu", "vendido", "negociando")]
    faltaram = [l for l in visitas if l["status"] == "nao_veio"]
    vendidos = [l for l in rows if l["status"] == "vendido"]
    ligados = {v["lead_id"] for v in _vendas() if v.get("lead_id")}
    convertidos = {l["id"] for l in vendidos} | (ids & ligados)
    por_vend: dict = defaultdict(lambda: {"leads": 0, "vendidos": 0, "sem_retorno": 0, "retorno_min": []})
    for l in rows:
        p = por_vend[l.get("vendedor_nome") or "Sem vendedor"]
        p["leads"] += 1
        p["vendidos"] += l["id"] in convertidos
        p["sem_retorno"] += l["id"] not in primeiro
        if l["id"] in retorno_min:
            p["retorno_min"].append(retorno_min[l["id"]])
    for p in por_vend.values():
        p["conversao_pct"] = _pct(p["vendidos"], p["leads"])
        p["retorno_mediano_min"] = _med(p.pop("retorno_min"))
    por_canal_conv: dict = defaultdict(lambda: [0, 0])
    for l in rows:
        c = canal(l.get("canal"))
        por_canal_conv[c][0] += 1
        por_canal_conv[c][1] += l["id"] in convertidos
    return {
        "recebidos": len(rows), "por_tipo": dict(Counter(TIPO_LABEL.get(l["tipo"], l["tipo"]) for l in rows)),
        "por_canal": {c: {"leads": a, "vendidos": b, "conversao_pct": _pct(b, a)} for c, (a, b) in
                      sorted(por_canal_conv.items(), key=lambda kv: -kv[1][0])},
        "por_sdr": dict(Counter(l.get("sdr") or "?" for l in rows)),
        "por_status": dict(Counter(STATUS_LABEL.get(l["status"], l["status"]) for l in rows)),
        "por_vendedor": dict(sorted(por_vend.items(), key=lambda kv: -kv[1]["leads"])),
        "convertidos": len(convertidos), "conversao_pct": _pct(len(convertidos), len(rows)),
        "visitas_agendadas_passadas": len(visitas), "compareceram": len(vieram), "nao_vieram": len(faltaram),
        "comparecimento_pct": _pct(len(vieram), len(visitas)),
        "sem_retorno": sum(1 for l in rows if l["id"] not in primeiro),
        "retorno_mediano_min": _med(retorno_min.values()),
        "retorno_ate_30min_pct": _pct(sum(1 for m in retorno_min.values() if m <= 30), len(retorno_min)),
        "remarcados": sum(1 for l in rows if l.get("nova_data") or l["status"] == "remarcado"),
        "cancelados": sum(1 for l in rows if l["status"] == "cancelado"),
        "cobrancas_propostas": sum(1 for l in rows if l.get("cobranca_proposta_em")),
        "cobrancas_enviadas": sum(1 for l in rows if l.get("cobranca_status") == "enviada"),
        "por_dia_semana": {DIAS_SEMANA[i]: n for i, n in sorted(Counter(
            _dt(l["recebido_em"]).astimezone(datas.TZ).weekday() for l in rows).items())},
        "por_hora_chegada": dict(sorted(Counter(_dt(l["recebido_em"]).astimezone(datas.TZ).hour for l in rows).items())),
        "serie_diaria": dict(sorted(Counter(_dia(l["recebido_em"]) for l in rows).items())),
    }


# ===== entregas =====
def entregas(ini: str, fim: str) -> dict:
    nomes = _nomes()
    rows = db.select_all("entregas", {"select": "*", "removido": "eq.false"})
    vendas_por_id = {v["id"]: v for v in _vendas()}
    feitas = [e for e in rows if e["status"] == "entregue" and _no(e.get("entregue_em"), ini, fim)]
    hoje = datas.hoje_iso()
    abertas = [e for e in rows if e["status"] == "agendada"]
    no_prazo = [e for e in feitas if e.get("data_entrega") and _dia(e["entregue_em"]) <= e["data_entrega"]]
    com_data = [e for e in feitas if e.get("data_entrega")]
    dias_venda = []
    for e in feitas:
        v = vendas_por_id.get(e.get("venda_id"))
        if v and v.get("data_venda"):
            dias_venda.append((date.fromisoformat(_dia(e["entregue_em"])) - date.fromisoformat(v["data_venda"])).days)
    pos = [v for v in vendas_por_id.values() if v.get("posvenda_proposta_em") and _no(v["posvenda_proposta_em"], ini, fim)]
    return {
        "entregues": len(feitas), "no_prazo_pct": _pct(len(no_prazo), len(com_data)),
        "dias_venda_ate_entrega": _med(dias_venda),
        "abertas": len(abertas), "atrasadas": sum(1 for e in abertas if e.get("data_entrega") and e["data_entrega"] < hoje),
        "sem_data": sum(1 for e in abertas if not e.get("data_entrega")),
        "proximos_7_dias": sum(1 for e in abertas if e.get("data_entrega")
                               and hoje <= e["data_entrega"] <= (datas.hoje() + timedelta(days=7)).isoformat()),
        "vendidos_sem_entrega_no_quadro": sum(1 for v in vendas_por_id.values()
                                              if v["status_venda"] in ("completa", "aguardando_resumo")
                                              and v.get("status_entrega") != "entregue" and not v.get("revenda")
                                              and (v.get("data_venda") or "") >= "2026-10-01"
                                              and not any(e.get("venda_id") == v["id"] for e in rows)),
        "por_vendedor": dict(Counter(nomes.get(e.get("vendedor_id")) or e.get("vendedor_nome") or "?" for e in feitas)),
        "posvenda_propostos": len(pos), "posvenda_enviados": sum(1 for v in pos if v.get("posvenda_status") == "enviada"),
        "serie_diaria": dict(sorted(Counter(_dia(e["entregue_em"]) for e in feitas).items())),
    }


# ===== preparação =====
def preparacao(ini: str, fim: str) -> dict:
    rows = db.select_all("prep_tarefas", {"select": "*", "status": "neq.removida"})
    criadas = [t for t in rows if _no(t.get("criado_em"), ini, fim)]
    feitas = [t for t in rows if t["status"] == "feito" and _no(t.get("feito_em"), ini, fim)]
    abertas = [t for t in rows if t["status"] == "pendente" and not t.get("historico")]
    agora = datetime.now(datas.TZ)
    idade = [(agora - _dt(t["criado_em"])).days for t in abertas]
    duracao = [_horas(t["criado_em"], t["feito_em"]) / 24 for t in feitas if t.get("feito_em")]
    probs = [p for p in db.select_all("prep_problemas", {"select": "*"}) if _no(p.get("em"), ini, fim)]
    por_carro = Counter(re.sub(r"\s*-\s*[A-Za-zÀ-ú]+$", "", t.get("card_nome") or t.get("carro_texto") or "?") for t in abertas)
    return {
        "tarefas_criadas": len(criadas), "tarefas_concluidas": len(feitas), "abertas": len(abertas),
        "conclusao_pct": _pct(len([t for t in criadas if t["status"] == "feito"]), len(criadas)),
        "dias_para_concluir": _med(duracao), "idade_media_abertas_dias": _media(idade),
        "paradas_2_dias": sum(1 for d in idade if d >= 2), "paradas_7_dias": sum(1 for d in idade if d >= 7),
        "abertas_por_tipo": dict(Counter(t.get("tipo") or "outro" for t in abertas)),
        "abertas_por_responsavel": dict(Counter(t.get("responsavel") or "sem responsável" for t in abertas)),
        "carros_com_mais_pendencias": dict(por_carro.most_common(10)),
        "problemas_relatados": len(probs), "problemas_por_autor": dict(Counter(p.get("autor") or "?" for p in probs)),
        "sem_cartao_no_trello": sum(1 for t in abertas if not t.get("card_id")),
    }


# ===== estoque (Autocerto + Trello) =====
_cache_trello: dict = {"t": 0.0, "acoes": []}


def _acoes_trello() -> list[dict]:
    """Histórico de movimentação do quadro (criação e troca de lista), cache 10 min."""
    if time.time() - _cache_trello["t"] < 600:
        return _cache_trello["acoes"]
    from . import trello
    from .config import settings
    tudo, antes = [], None
    with trello._cliente() as c:
        while True:
            p = {"filter": "updateCard:idList,createCard", "limit": 1000, "fields": "type,date,data"}
            if antes:
                p["before"] = antes
            r = c.get(f"/boards/{settings.trello_board}/actions", params=p).json()
            tudo += r
            if len(r) < 1000:
                break
            antes = r[-1]["id"]
    _cache_trello.update({"t": time.time(), "acoes": tudo})
    return tudo


def estoque(ini: str, fim: str) -> dict:
    from . import autocerto, trello
    out: dict = {}
    try:
        itens = autocerto.estoque()
        precos = [i["preco"] for i in itens if i["preco"]]
        dias = [i["dias_no_estoque"] for i in itens if i["dias_no_estoque"] is not None]
        out["autocerto"] = {
            "carros": len(itens), "valor_total": round(sum(precos)), "preco_medio": round(sum(precos) / len(precos)) if precos else None,
            "idade_media_dias": _media(dias), "idade_mediana_dias": _med(dias),
            "por_idade": dict(Counter(_faixa_dias(i["dias_no_estoque"]) for i in itens)),
            "valor_parado_mais_90_dias": round(sum(i["preco"] or 0 for i in itens if (i["dias_no_estoque"] or 0) > 90)),
            "por_faixa_preco": dict(Counter(_faixa_preco(i["preco"]) for i in itens if i["preco"])),
            "por_marca": dict(Counter(i["carro"].split()[0].title() for i in itens).most_common(10)),
            "entraram_no_periodo": sum(1 for i in itens if i["cadastrado_em"] and ini <= i["cadastrado_em"] <= fim),
            "mais_parados": [{k: i[k] for k in ("carro", "ano", "placa", "preco", "dias_no_estoque")}
                             for i in sorted(itens, key=lambda i: -(i["dias_no_estoque"] or 0))[:10]],
        }
    except Exception:
        log.exception("métricas: autocerto")
    try:
        with trello._cliente() as c:
            cards, listas = trello.cartoes(c)
        por_lista = Counter(_ESTAGIOS.get(listas.get(cd["idList"]), listas.get(cd["idList"])) for cd in cards)
        out["trello_por_lista"] = dict(por_lista)
        placas_trello = {trello._placa(cd["name"]) for cd in cards if listas.get(cd["idList"]) != trello.FINALIZADO}
        if "autocerto" in out:
            placas_ac = {(i["placa"] or "").replace("-", "").upper() for i in autocerto.estoque()}
            out["no_autocerto_sem_cartao"] = len(placas_ac - placas_trello - {""})
        out["cartoes_sem_placa"] = sum(1 for cd in cards if listas.get(cd["idList"]) != trello.FINALIZADO
                                       and not trello._placa(cd["name"]))
        out.update(_etapas(ini, fim))
    except Exception:
        log.exception("métricas: trello")
    return out


def _etapas(ini: str, fim: str) -> dict:
    """Tempo em cada etapa do quadro (saídas no período) e fluxo de entradas/vendas."""
    acoes = sorted(_acoes_trello(), key=lambda a: a["date"])
    entrou: dict = {}
    dur: dict = defaultdict(list)
    fluxo = Counter()
    for a in acoes:
        card = a["data"].get("card", {}).get("id")
        if a["type"] == "createCard":
            entrou[card] = (_ascii(a["data"].get("list", {}).get("name", "")).strip(), a["date"])
            if _no(a["date"], ini, fim):
                fluxo["cartoes_criados"] += 1
            continue
        de = _ascii(a["data"].get("listBefore", {}).get("name", "")).strip()
        para = _ascii(a["data"].get("listAfter", {}).get("name", "")).strip()
        if card in entrou and entrou[card][0] == de and _no(a["date"], ini, fim):
            dur[_ESTAGIOS.get(de, de)].append(_horas(entrou[card][1], a["date"]) / 24)
        entrou[card] = (para, a["date"])
        if _no(a["date"], ini, fim):
            fluxo[f"para {_ESTAGIOS.get(para, para)}"] += 1
    return {"dias_por_etapa": {k: {"mediana": _med(v), "media": _media(v), "carros": len(v)} for k, v in dur.items()},
            "movimentos_no_periodo": dict(fluxo)}


# ===== avaliações =====
def avaliacoes(ini: str, fim: str) -> dict:
    nomes = _nomes()
    rows = [a for a in db.select_all("avaliacoes", {"select": "*", "origem": "eq.grupo"})
            if a.get("status") != "removida" and _no(a.get("avaliado_em"), ini, fim)]
    com_valor = [a for a in rows if a.get("valor_texto")]
    minutos = [_horas(a["avaliado_em"], a["valor_em"]) * 60 for a in com_valor if a.get("valor_em")]
    pct_fipe = [a["valor_avaliacao"] / a["fipe"] * 100 for a in com_valor if a.get("valor_avaliacao") and a.get("fipe")]
    fechou = [a for a in rows if a.get("status") == "fechou"]
    por_vend: dict = defaultdict(lambda: {"avaliacoes": 0, "fechou": 0})
    for a in rows:
        p = por_vend[nomes.get(a.get("vendedor_id"), "Sem vendedor")]
        p["avaliacoes"] += 1
        p["fechou"] += a.get("status") == "fechou"
    return {
        "avaliacoes": len(rows), "com_valor": len(com_valor), "com_valor_pct": _pct(len(com_valor), len(rows)),
        "minutos_ate_valor": _med(minutos), "valor_em_ate_5min_pct": _pct(sum(1 for m in minutos if m <= 5), len(minutos)),
        "valor_medio_pct_fipe": _media(pct_fipe),
        "fechou_negocio": len(fechou), "conversao_pct": _pct(len(fechou), len(rows)),
        "por_vendedor": dict(por_vend), "valor_dado_por": dict(Counter(a.get("valor_por") or "?" for a in com_valor)),
        "por_marca": dict(Counter(((a.get("modelo") or "?").split() or ["?"])[0].title() for a in rows).most_common(10)),
        "cliente_informou_interesse_pct": _pct(sum(1 for a in rows if a.get("carro_interesse")), len(rows)),
    }


# ===== recall =====
def recall(ini: str, fim: str) -> dict:
    from .recall_grupo import garantia_ate
    nomes = _nomes()
    rows = db.select_all("recalls", {"select": "*", "origem": "eq.grupo"})
    novos = [r for r in rows if _no(r.get("aberto_em"), ini, fim)]
    abertos = [r for r in rows if r["status"] in ("aberto", "agendado", "na_loja")]
    prontos = [r for r in rows if r["status"] == "pronto" and _no(r.get("pronto_em"), ini, fim)]
    motivos = Counter(m.strip().lower() for r in novos for m in (r.get("motivo") or "").split(" / ") if m.strip())
    dentro = [r for r in novos if garantia_ate(r) and _dia(r["aberto_em"]) <= garantia_ate(r).isoformat()]
    return {
        "novos": len(novos), "abertos": len(abertos), "resolvidos": len(prontos),
        "abertos_por_status": dict(Counter({"aberto": "sem data", "agendado": "agendado", "na_loja": "na loja"}[r["status"]]
                                           for r in abertos)),
        "dias_para_resolver": _med(_horas(r["aberto_em"], r["pronto_em"]) / 24 for r in prontos if r.get("pronto_em")),
        "dentro_da_garantia_pct": _pct(len(dentro), len([r for r in novos if r.get("data_saida")])),
        "por_vendedor": dict(Counter(nomes.get(r.get("vendedor_id"), "Sem vendedor") for r in novos)),
        "motivos_mais_comuns": dict(motivos.most_common(10)),
    }


# ===== marketing =====
def marketing(ini: str, fim: str, vend: dict | None = None, lds: dict | None = None) -> dict:
    from . import meta_ads
    if not meta_ads.configurado():
        return {}
    dias = (date.fromisoformat(fim) - date.fromisoformat(ini)).days + 1
    nome = "hoje" if dias == 1 and fim == datas.hoje_iso() else "semana" if dias <= 7 else "mes" if ini.endswith("-01") \
        and fim == datas.hoje_iso() else "30d"
    try:
        r = meta_ads.resumo(nome)
    except Exception:
        log.exception("métricas: meta ads")
        return {}
    if r.get("erro"):
        return {"erro": r["erro"]}
    trafego_vendas = (vend or {}).get("por_canal", {}).get("Tráfego", 0)
    trafego_leads = ((lds or {}).get("por_canal", {}).get("Tráfego") or {}).get("leads", 0)
    return {"periodo_meta": nome, "gasto": r.get("gasto"), "leads_meta": r.get("leads"), "conversas": r.get("conversas"),
            "custo_por_lead_meta": r.get("custo_por_lead"),
            "leads_trafego_no_agendamento": trafego_leads,
            "custo_por_agendamento": round(r["gasto"] / trafego_leads) if r.get("gasto") and trafego_leads else None,
            "vendas_trafego": trafego_vendas,
            "custo_por_venda": round(r["gasto"] / trafego_vendas) if r.get("gasto") and trafego_vendas else None}


# ===== comunicação (todas as conversas) =====
def comunicacao(ini: str, fim: str) -> dict:
    rows = [m for m in db.select_all("mensagens", {"select": "jid,conversa,autor_nome,from_me,tipo,em",
                                                    "em": f"gte.{ini}T00:00:00-03:00", "order": "em.asc"})
            if _dia(m["em"]) <= fim]
    por_conversa = Counter(m.get("conversa") or m["jid"] for m in rows)
    gerencia = [m for m in rows if "gerencia" in _ascii(m.get("conversa") or "")]
    resp: dict = defaultdict(list)  # conversa privada: quanto o dono demora a responder e vice-versa
    privadas = defaultdict(list)
    for m in rows:
        if m["jid"].endswith("@s.whatsapp.net"):
            privadas[m.get("conversa")].append(m)
    for conv, ms in privadas.items():
        for a, b in zip(ms, ms[1:]):
            if a["from_me"] != b["from_me"]:
                quem = "Diogo responde" if b["from_me"] else "contato responde"
                h = _horas(a["em"], b["em"])
                if h is not None and h < 24:
                    resp[f"{conv} — {quem}"].append(h * 60)
    return {
        "mensagens": len(rows), "por_conversa": dict(por_conversa.most_common()),
        "quem_mais_fala": dict(Counter(m.get("autor_nome") or "?" for m in rows).most_common(15)),
        "por_tipo": dict(Counter(m.get("tipo") for m in rows)),
        "por_hora": dict(sorted(Counter(_dt(m["em"]).astimezone(datas.TZ).hour for m in rows).items())),
        "por_dia_semana": {DIAS_SEMANA[i]: n for i, n in sorted(Counter(_dt(m["em"]).astimezone(datas.TZ).weekday()
                                                                          for m in rows).items())},
        "gerencia": {"mensagens": len(gerencia), "quem_fala": dict(Counter(m.get("autor_nome") or "?" for m in gerencia))},
        "conversas_privadas": {c.replace("Conversa privada - ", ""): len(ms) for c, ms in privadas.items()},
        "tempo_resposta_mediano_min": {k: _med(v) for k, v in resp.items()},
    }


# ===== equipe (placar por vendedor) =====
def equipe(vend: dict, lds: dict, ents: dict, avs: dict, rec: dict) -> dict:
    nomes = set(vend["por_vendedor"]) | set(lds["por_vendedor"]) | set(avs["por_vendedor"])
    out = {}
    for n in sorted(nomes):
        v = vend["por_vendedor"].get(n, {})
        l = lds["por_vendedor"].get(n, {})
        a = avs["por_vendedor"].get(n, {})
        out[n] = {"vendas": v.get("vendas", 0), "faturamento": round(v.get("faturamento", 0)),
                  "ticket_medio": v.get("ticket_medio"), "leads": l.get("leads", 0),
                  "conversao_leads_pct": l.get("conversao_pct"), "retorno_mediano_min": l.get("retorno_mediano_min"),
                  "leads_sem_retorno": l.get("sem_retorno", 0), "avaliacoes": a.get("avaliacoes", 0),
                  "avaliacoes_fechadas": a.get("fechou", 0), "entregas": ents["por_vendedor"].get(n, 0),
                  "recalls": rec["por_vendedor"].get(n, 0)}
    return out


_cache_calc: dict = {}


def em_cache(nome: str = "mes", inicio: str | None = None, fim: str | None = None,
             setores: list[str] | None = None) -> dict:
    """Mesmo cálculo, guardado 5 min (o dashboard recarrega sem refazer tudo)."""
    chave = (nome, inicio, fim, tuple(sorted(setores or SETORES)))
    t, r = _cache_calc.get(chave, (0.0, None))
    if r is None or time.time() - t > 300:
        r = calcular(nome, inicio, fim, setores)
        _cache_calc[chave] = (time.time(), r)
    return r


# ===== tudo =====
SETORES = ("vendas", "leads", "entregas", "preparacao", "estoque", "avaliacoes", "recall", "marketing", "comunicacao", "equipe")


def calcular(nome: str = "mes", inicio: str | None = None, fim: str | None = None,
             setores: list[str] | None = None) -> dict:
    ini, fim = periodo(nome, inicio, fim)
    pedir = set(setores or SETORES)
    if "equipe" in pedir:
        pedir |= {"vendas", "leads", "entregas", "avaliacoes", "recall"}
    if "marketing" in pedir:
        pedir |= {"vendas", "leads"}
    out: dict = {"periodo": {"inicio": ini, "fim": fim}}
    for s, fn in (("vendas", vendas), ("leads", leads), ("entregas", entregas), ("preparacao", preparacao),
                  ("estoque", estoque), ("avaliacoes", avaliacoes), ("recall", recall), ("comunicacao", comunicacao)):
        if s in pedir:
            try:
                out[s] = fn(ini, fim)
            except Exception as e:
                log.exception("métricas: %s", s)
                out[s] = {"erro": str(e)}
    if "marketing" in pedir:
        out["marketing"] = marketing(ini, fim, out.get("vendas"), out.get("leads"))
    if "equipe" in pedir and all("erro" not in out.get(s, {}) for s in ("vendas", "leads", "entregas", "avaliacoes", "recall")):
        out["equipe"] = equipe(out["vendas"], out["leads"], out["entregas"], out["avaliacoes"], out["recall"])
    if "leads" in out and "vendas" in out and "entregas" in out and "erro" not in out["leads"]:
        out["funil"] = {"leads": out["leads"]["recebidos"], "visitas_que_vieram": out["leads"]["compareceram"],
                        "vendas": out["vendas"]["vendidos"],
                        "entregas": out["entregas"]["entregues"]}
    return {k: v for k, v in out.items() if k == "periodo" or k in pedir or k == "funil"}
