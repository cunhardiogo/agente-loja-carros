"""Grupo ENTREGAS: a lista "🎁 Entregas" repostada é o quadro atual. Entrega nova entra, alterada é
atualizada e carro que SUMIU da lista foi entregue. Cada entrega é ligada à venda (vendedor + carro) e
a data da lista passa a ser a data de entrega da venda. Entrega sem data e venda sem entrega geram
cobrança aprovada pelo dono; entrega com data vencida ainda no quadro gera aviso."""
import logging
import re
from datetime import datetime, timedelta, timezone

from . import datas, db, evolution
from .config import settings
from .leads import _ascii, _data, _dt, _iso, _quando, _texto

log = logging.getLogger("agente")

SEM_DATA_DIAS = 1          # entrega sem data no quadro há 1 dia → cobra o vendedor
SEM_ENTREGA_DIAS = 1       # venda com resumo há 1 dia e fora do quadro → cobra o vendedor
PARCIAL = 0.5              # lista com menos da metade das entregas abertas = parcial (não dá baixa)
REABRIR_DIAS = 3           # carro "entregue" que volta à lista em até 3 dias é reaberto

STATUS_LABEL = {"agendada": "Agendada", "entregue": "Entregue"}
_ROTULO = re.compile(r"^(dia da entrega|horario|vendedor|veiculo|observacao|obs|loja)\s*[:;]")
_CAMPO = {"dia da entrega": "data", "horario": "horario", "vendedor": "vendedor", "veiculo": "veiculo",
          "observacao": "obs", "obs": "obs", "loja": "loja"}

sujo = {"v": True}


# ===== parsing =====
def eh_lista(texto: str) -> bool:
    a = _ascii(texto)
    return "entregas" in a and "dia da entrega" in a


def _data_entrega(valor: str | None, ref) -> str | None:
    a = _ascii(valor)
    if not a.strip() or "sem data" in a or "xx" in a:
        return None
    d = _data(valor, ref)
    return d.isoformat() if d else None


def parse_lista(texto: str, ref=None) -> list[dict]:
    """Uma entrega por bloco. Rótulos aceitam ':' ou ';' ("Horário ; 16:30")."""
    ref = ref or datas.hoje()
    blocos: list[dict] = []
    atual: dict | None = None
    ultimo = None
    for linha in texto.splitlines():
        limpa = linha.strip().strip("*")
        a = re.sub(r"^[^a-z0-9]+", "", _ascii(limpa)).strip()
        if not a or set(a) <= {"_", "-"}:
            continue
        if re.fullmatch(r"entregas\W*", a) or (a.startswith("entregas") and len(a) < 15):
            atual, ultimo = {}, None
            blocos.append(atual)
            continue
        m = _ROTULO.match(a)
        if m:
            if atual is None:
                atual = {}
                blocos.append(atual)
            campo = _CAMPO[m.group(1)]
            valor = re.split(r"[:;]", limpa, maxsplit=1)[1].strip(" *_") if re.search(r"[:;]", limpa) else ""
            atual[campo] = valor
            ultimo = campo
        elif atual is not None and ultimo == "obs":
            atual["obs"] = (atual.get("obs", "") + " " + limpa).strip()
    itens = []
    for b in blocos:
        if not (b.get("veiculo") or "").strip():
            continue
        itens.append({"veiculo": b["veiculo"].strip(), "vendedor_nome": (b.get("vendedor") or "").strip() or None,
                      "data_texto": (b.get("data") or "").strip() or None,
                      "data_entrega": _data_entrega(b.get("data"), ref),
                      "horario": (b.get("horario") or "").strip() or None,
                      "observacao": (b.get("obs") or "").strip().strip("*") or None,
                      "loja": (b.get("loja") or "").strip() or None})
    return itens


def chave_carro(veiculo: str | None) -> str:
    """Primeira palavra do carro: 'MT 03' → 'mt', 'Fit 18' → 'fit', 'Kwid 1.0 Zen 2025' → 'kwid'."""
    toks = [t for t in re.split(r"[^a-z0-9]+", _ascii(veiculo)) if len(t) >= 2]
    nome = [t for t in toks if not t.isdigit()]
    return (nome or toks or [""])[0]  # "2008 2017": o modelo é o número


# ===== processamento =====
def _ultima_lista() -> datetime | None:
    r = db.select("entregas", {"select": "ultima_lista_em", "origem": "eq.grupo", "ultima_lista_em": "not.is.null",
                               "order": "ultima_lista_em.desc", "limit": "1"})
    return _dt(r[0]["ultima_lista_em"]) if r else None


def _abertas() -> list[dict]:
    return db.select_all("entregas", {"select": "*", "origem": "eq.grupo", "status": "eq.agendada",
                                      "removido": "eq.false"})


def _casar(item: dict, abertas: list[dict], vend_id: str | None) -> dict | None:
    chave = chave_carro(item["veiculo"])
    mesmas = [a for a in abertas if chave and chave_carro(a.get("veiculo")) == chave]
    do_vendedor = [a for a in mesmas if vend_id and a.get("vendedor_id") == vend_id]
    if do_vendedor:
        return do_vendedor[0]
    return mesmas[0] if len(mesmas) == 1 else None


def _venda_do_item(item: dict, vend_id: str | None) -> dict | None:
    from .vendas_grupo import nome_carro
    chave = chave_carro(item["veiculo"])
    if not chave:
        return None
    rows = db.select_all("vendas", {"select": "*", "removido": "eq.false",
                                    "status_venda": "in.(completa,aguardando_resumo)",
                                    "status_entrega": "neq.entregue", "order": "created_at.desc"})
    cands = [v for v in rows if chave in _ascii(f"{v.get('modelo')} {v.get('versao')} {nome_carro(v)}")]
    do_vendedor = [v for v in cands if vend_id and v.get("vendedor_id") == vend_id]
    if do_vendedor:
        return do_vendedor[0]
    return cands[0] if len(cands) == 1 else None


def _reabrir(item: dict, vend_id: str | None, em: datetime) -> dict | None:
    """Carro dado como entregue que volta à lista em até 3 dias: era engano, reabre."""
    desde = _iso(em - timedelta(days=REABRIR_DIAS))
    rows = db.select_all("entregas", {"select": "*", "origem": "eq.grupo", "status": "eq.entregue",
                                      "removido": "eq.false", "entregue_em": f"gte.{desde}"})
    alvo = _casar(item, rows, vend_id)
    if not alvo:
        return None
    db.update("entregas", {"status": "agendada", "entregue_em": None}, {"id": f"eq.{alvo['id']}"})
    if alvo.get("venda_id"):
        db.update("vendas", {"status_entrega": "pendente", "data_entrega_real": None}, {"id": f"eq.{alvo['venda_id']}"})
    return {**alvo, "status": "agendada", "entregue_em": None}


def processar(data: dict, historico: bool = False) -> dict:
    from .vendas_grupo import vendedor_por_nome
    texto = _texto(data.get("message") or {})
    if not texto or not eh_lista(texto):
        return {"ignored": "nao_e_lista"}
    em = _quando(data)
    ultima = _ultima_lista()
    if ultima and em <= ultima:
        return {"ignored": "lista_antiga"}
    itens = parse_lista(texto, em.astimezone(datas.TZ).date())
    if not itens:
        return {"ignored": "lista_vazia"}
    abertas = _abertas()
    vistas: set[str] = set()
    novas = 0
    for it in itens:
        vend = vendedor_por_nome(it["vendedor_nome"])
        vend_id = vend["id"] if vend else None
        campos = {**it, "vendedor_id": vend_id, "ultima_lista_em": _iso(em)}
        existente = _casar(it, [a for a in abertas if a["id"] not in vistas], vend_id) or _reabrir(it, vend_id, em)
        if existente:
            vistas.add(existente["id"])
            campos["vezes_na_lista"] = (existente.get("vezes_na_lista") or 0) + 1
            if (existente.get("data_entrega") != it["data_entrega"]) and existente.get("cobranca_status") == "proposta" \
                    and existente.get("cobranca_motivo") == "sem_data" and it["data_entrega"]:
                campos["cobranca_status"] = "cancelada"
            db.update("entregas", campos, {"id": f"eq.{existente['id']}"})
            entrega = {**existente, **campos}
        else:
            entrega = db.insert("entregas", {**campos, "origem": "grupo", "status": "agendada", "vezes_na_lista": 1,
                                             "primeira_vez_em": _iso(em), "historico": historico})
            novas += 1
        _ligar_venda(entrega, it, vend_id)
    entregues = 0
    abertas_ids = {a["id"] for a in abertas}
    parcial = abertas_ids and len(vistas & abertas_ids) < PARCIAL * len(abertas_ids)
    if not parcial:
        for a in abertas:
            if a["id"] not in vistas:  # sumiu da lista de qualquer pessoa = entregue (decisão do dono)
                dar_baixa(a, em)
                entregues += 1
    sujo["v"] = True
    return {"lista": len(itens), "novas": novas, "entregues": entregues, "parcial": bool(parcial)}


def _ligar_venda(entrega: dict, item: dict, vend_id: str | None) -> None:
    venda = None
    if entrega.get("venda_id"):
        r = db.select("vendas", {"select": "*", "id": f"eq.{entrega['venda_id']}", "limit": "1"})
        venda = r[0] if r else None
    if venda is None:
        venda = _venda_do_item(item, vend_id)
        if venda:
            db.update("entregas", {"venda_id": venda["id"]}, {"id": f"eq.{entrega['id']}"})
    if venda:  # a data da LISTA é a data de entrega da venda (não a do resumo)
        db.update("vendas", {"data_entrega_prevista": item["data_entrega"], "data_entrega_texto": item["data_texto"]},
                  {"id": f"eq.{venda['id']}"})
        from . import vendas_grupo
        vendas_grupo.sujo["v"] = True


def dar_baixa(entrega: dict, em: datetime) -> None:
    """Carro saiu do quadro = entregue."""
    db.update("entregas", {"status": "entregue", "entregue_em": _iso(em)}, {"id": f"eq.{entrega['id']}"})
    if entrega.get("venda_id"):
        db.update("vendas", {"status_entrega": "entregue",
                             "data_entrega_real": em.astimezone(datas.TZ).date().isoformat()},
                  {"id": f"eq.{entrega['venda_id']}"})
        from . import vendas_grupo
        vendas_grupo.sujo["v"] = True


# ===== prazos, avisos e cobrança =====
def codigo(e: dict) -> str:
    return e["id"][:4].upper()


def _carro(e: dict) -> str:
    return e.get("veiculo") or "carro"


def texto_cobranca(e: dict) -> str:
    return (f"Fala {e.get('vendedor_nome') or ''}! A entrega do {_carro(e)} está no quadro de entregas sem data "
            f"definida. Consegue combinar com o cliente e atualizar a lista no grupo de entregas? 🙏")


def _propor(e: dict, agora: datetime) -> bool:
    texto = texto_cobranca(e)
    cod = codigo(e)
    try:
        evolution.enviar_texto(settings.meu_numero,
                               f"📨 Entrega sem data — cobrança pro {e.get('vendedor_nome') or 'vendedor'} [#{cod}]\n"
                               f"{_carro(e)}\n\n\"{texto}\"\n\n"
                               f"Responda *ok {cod}* pra enviar · *não {cod}* pra descartar · "
                               f"ou *{cod}: texto novo* pra mandar outro texto.")
    except Exception:
        log.exception("falha propondo cobrança de entrega")
        return False
    cobrado = dict(e.get("cobrado") or {})
    cobrado["sem_data"] = True
    db.update("entregas", {"cobranca_status": "proposta", "cobranca_motivo": "sem_data", "cobranca_texto": texto,
                           "cobranca_proposta_em": _iso(agora), "cobrado": cobrado}, {"id": f"eq.{e['id']}"})
    return True


def _avisar_atraso(e: dict, agora: datetime) -> bool:
    try:
        evolution.enviar_texto(settings.meu_numero,
                               f"⚠️ Entrega atrasada [#{codigo(e)}]\n{_carro(e)} — {e.get('vendedor_nome') or '?'}\n"
                               f"Estava marcada pra {e['data_entrega'][8:10]}/{e['data_entrega'][5:7]}"
                               f"{' às ' + e['horario'] if e.get('horario') else ''} e continua no quadro de entregas.")
    except Exception:
        log.exception("falha avisando entrega atrasada")
        return False
    db.update("entregas", {"aviso_atraso_em": _iso(agora)}, {"id": f"eq.{e['id']}"})
    return True


def checar_prazos(agora: datetime | None = None) -> int:
    from . import vendas_grupo
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.astimezone(datas.TZ).date().isoformat()
    n = 0
    for e in _abertas():
        if e.get("historico") or e.get("cobranca_status") == "proposta":
            continue
        if not e.get("data_entrega") and "sem_data" not in (e.get("cobrado") or {}) \
                and _dt(e.get("primeira_vez_em")) and agora >= _dt(e["primeira_vez_em"]) + timedelta(days=SEM_DATA_DIAS):
            n += _propor(e, agora)
        elif e.get("data_entrega") and e["data_entrega"] < hoje and not e.get("aviso_atraso_em"):
            n += _avisar_atraso(e, agora)
    # venda com resumo há 1 dia e fora do quadro de entregas
    ligadas = {e["venda_id"] for e in db.select_all("entregas", {"select": "venda_id", "venda_id": "not.is.null",
                                                                  "removido": "eq.false"})}
    vendas = db.select_all("vendas", {"select": "*", "status_venda": "eq.completa", "historico": "eq.false",
                                      "removido": "eq.false", "revenda": "eq.false", "status_entrega": "neq.entregue"})
    for v in vendas:
        if v["id"] in ligadas or v.get("cobranca_status") == "proposta" or "sem_entrega" in (v.get("cobrado") or {}):
            continue
        if v.get("resumo_em") and agora >= _dt(v["resumo_em"]) + timedelta(days=SEM_ENTREGA_DIAS):
            n += vendas_grupo._propor(v, "sem_entrega", agora)
    if n:
        sujo["v"] = True
    return n


def _enviar(e: dict, msg: str) -> str:
    atual = db.select("entregas", {"select": "*", "id": f"eq.{e['id']}", "limit": "1"})[0]
    if atual.get("data_entrega") or atual["status"] != "agendada" or atual.get("removido"):
        db.update("entregas", {"cobranca_status": "cancelada"}, {"id": f"eq.{e['id']}"})
        return f"A entrega do {_carro(atual)} já tem data (ou já saiu do quadro) — não enviei."
    v = db.select("vendedores", {"select": "telefone,nome", "id": f"eq.{atual.get('vendedor_id')}", "limit": "1"}) \
        if atual.get("vendedor_id") else []
    tel = v[0].get("telefone") if v else None
    if not tel:
        return "Não sei quem é o vendedor dessa entrega (ou não tenho o telefone dele) — não enviei."
    evolution.enviar_por_coletor(tel, msg)
    db.update("entregas", {"cobranca_status": "enviada", "cobranca_texto": msg,
                           "cobranca_enviada_em": _iso(datetime.now(timezone.utc))}, {"id": f"eq.{e['id']}"})
    sujo["v"] = True
    return f"✅ Cobrança enviada pro {v[0]['nome']}."


# ===== agenda da manhã =====
def agenda_do_dia_texto(dia: str | None = None) -> str:
    dia = dia or datas.hoje_iso()
    rows = db.select_all("entregas", {"select": "*", "origem": "in.(grupo,planilha)", "status": "eq.agendada",
                                      "removido": "eq.false", "data_entrega": f"eq.{dia}"})
    if not rows:
        return "🚗 *Entregas de hoje:* nenhuma"
    from .leads import _ordem_horario
    rows.sort(key=lambda e: _ordem_horario(e.get("horario")))
    linhas = [f"🚗 *Entregas de hoje ({len(rows)}):*"]
    for e in rows:
        linhas.append(f"🕐 *{e.get('horario') or 'sem horário'}* — {_carro(e)} · {e.get('vendedor_nome') or '?'}"
                      + (f"\n   🔧 {e['observacao']}" if e.get("observacao") else ""))
    return "\n".join(linhas)
