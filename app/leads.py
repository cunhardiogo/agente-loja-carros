"""Grupo Agendamento SDR: formulários viram leads, respostas citando o formulário viram
status/retorno, e lead sem retorno gera aviso (30 min) e cobrança aprovada pelo dono (1 h)."""
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from . import datas, db, evolution, llm
from .config import settings

log = logging.getLogger("agente")

AVISO_MIN = 30
COBRANCA_MIN = 60
NOTA_JANELA_MIN = 20

TIPO_LABEL = {"visita": "Visita", "negociacao_telefone": "Negociação telefone", "turno": "Turno"}
STATUS_LABEL = {
    "aguardando_retorno": "Aguardando retorno", "retorno": "Retornou", "confirmado": "Confirmado",
    "negociando": "Negociando", "compareceu": "Compareceu", "vendido": "Vendido",
    "reservado": "Reservado", "nao_veio": "Não veio", "cancelado": "Cancelado", "remarcado": "Remarcado",
}

_LABELS = re.compile(r"^\W*(loja|localizacao|atendente|cliente|data|horario|veiculo|vendedor|canal de venda)\s*:")

# o agendador avisa a planilha quando algo mudou
sujo = {"v": True}


def _ascii(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return s.encode("ascii", "ignore").decode().lower()


def _limpa(s: str) -> str:
    return s.replace("*", "").replace("_", "").strip()


def _texto(msg: dict) -> str:
    return (msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
            or (msg.get("imageMessage") or {}).get("caption")
            or (msg.get("videoMessage") or {}).get("caption") or "").strip()


def _stanza(data: dict) -> str | None:
    ctx = data.get("contextInfo") or {}
    if not ctx.get("stanzaId"):
        msg = data.get("message") or {}
        for k in ("extendedTextMessage", "imageMessage", "videoMessage", "audioMessage"):
            ctx = (msg.get(k) or {}).get("contextInfo") or {}
            if ctx.get("stanzaId"):
                break
    return ctx.get("stanzaId")


def _quando(data: dict) -> datetime:
    ts = data.get("messageTimestamp")
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _hhmm(iso: str | None) -> str:
    d = _dt(iso)
    return d.astimezone(datas.TZ).strftime("%d/%m %H:%M") if d else ""


# ===== parser do formulário =====
def eh_formulario(texto: str) -> bool:
    for linha in texto.splitlines():
        if linha.strip():
            return _ascii(_limpa(linha)).lstrip(" ☝👆").startswith("agendamento")
    return False


def _data(valor: str, ref: date) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", valor or "")
    if not m:
        return None
    d, mes, ano = int(m.group(1)), int(m.group(2)), m.group(3)
    ano = int(ano) + (2000 if ano and len(ano) == 2 else 0) if ano else ref.year
    try:
        return date(ano, mes, d)
    except ValueError:
        return None


def _telefone(valor: str) -> str | None:
    dig = re.sub(r"\D", "", valor or "")
    if len(dig) in (10, 11):
        return "55" + dig
    if len(dig) in (12, 13) and dig.startswith("55"):
        return dig
    return None


def parse_formulario(texto: str, ref: date | None = None) -> dict:
    ref = ref or datas.hoje()
    linhas = [_limpa(l) for l in texto.splitlines()]
    campos: dict = {}
    filas = []
    for i, linha in enumerate(linhas):
        a = _ascii(linha).strip()
        if (a.startswith("(") and "fila" in a) or a.startswith("turno"):
            filas.append(linha.strip("() "))
        m = _LABELS.match(a)
        if not m or m.group(1) in campos:
            continue
        rotulo = m.group(1)
        valor = linha.split(":", 1)[1].strip(" *_") if ":" in linha else ""
        campos[rotulo] = valor
        if rotulo == "cliente":
            tel = _telefone(valor)
            if tel:  # telefone na mesma linha do nome
                campos["telefone"] = tel
                campos[rotulo] = re.sub(r"[\d()\s-]{8,}$", "", valor).strip()
            else:
                for prox in linhas[i + 1:i + 3]:
                    if _LABELS.match(_ascii(prox).strip()):
                        break
                    tel = _telefone(prox)
                    if tel:
                        campos["telefone"] = tel
                        break

    a_todo = _ascii(texto)
    horario = campos.get("horario") or ""
    if "turno" in a_todo:
        tipo = "turno"
    elif "fila" in a_todo or _ascii(horario).startswith("ligac"):
        tipo = "negociacao_telefone"
    else:
        tipo = "visita"
    vendedor = campos.get("vendedor") or ""
    if not re.search(r"[a-zA-ZÀ-ú]", vendedor):
        vendedor = ""
    return {
        "tipo": tipo, "fila": " / ".join(filas) or None,
        "loja": campos.get("loja") or None, "localizacao": campos.get("localizacao") or None,
        "sdr": campos.get("atendente") or None,
        "cliente_nome": campos.get("cliente") or None, "telefone": campos.get("telefone"),
        "data_agendada": (d.isoformat() if (d := _data(campos.get("data", ""), ref)) else None),
        "horario": horario or None, "veiculo": campos.get("veiculo") or None,
        "vendedor_nome": vendedor or None, "canal": campos.get("canal de venda") or None,
    }


def _vendedor(nome: str | None) -> dict | None:
    if not nome:
        return None
    n = _ascii(nome).split()[0] if _ascii(nome).split() else ""
    for v in db.select("vendedores", {"select": "id,nome,apelidos,telefone", "ativo": "eq.true"}):
        nomes = [_ascii(v["nome"])] + [_ascii(a) for a in (v.get("apelidos") or [])]
        if n in nomes:
            return v
    return None


# ===== respostas =====
def classificar_resposta(texto: str, ref: date | None = None) -> tuple[str, str | None]:
    n = _ascii(texto)
    if re.search(r"vendid|vendeu|fechou|fechad", n):
        return "vendido", None
    if re.search(r"nao (veio|vem|compareceu|apareceu)|faltou|no ?show|nao tem previsao", n):
        return "nao_veio", None
    if re.search(r"cancel|desist", n):
        return "cancelado", None
    if "reserv" in n:
        return "reservado", None
    if "negoci" in n:
        return "negociando", None
    if re.search(r"\b(veio|compareceu|esteve|chegou)\b", n):
        return "compareceu", None
    if "confirm" in n:
        return "confirmado", None
    d = _data(n, ref or datas.hoje())
    if d or "remarc" in n:
        return "remarcado", d.isoformat() if d else None
    return "retorno", None


# ===== processamento =====
def processar(data: dict, historico: bool = False) -> dict:
    key = data.get("key") or {}
    msg = data.get("message") or {}
    pm = msg.get("protocolMessage")
    if pm:
        return _protocolo(pm, historico)
    mid = key.get("id")
    autor = key.get("participant") or ("eu" if key.get("fromMe") else None)
    nome = data.get("pushName") or ""
    em = _quando(data)
    texto = _texto(msg)
    stanza = _stanza(data)

    if texto and eh_formulario(texto):
        return _criar_lead(texto, mid, autor, em, historico)
    if stanza:
        lead = _lead_por_msg(stanza)
        if lead:
            return _registrar_retorno(lead, texto or "[mídia]", nome or autor, em, mid, historico)
        return {"ignored": "resposta_sem_lead"}
    if texto and autor:
        return _anexar_nota(texto, mid, autor, em)
    return {"ignored": "sem_texto"}


def _criar_lead(texto, mid, autor, em, historico) -> dict:
    p = parse_formulario(texto, em.astimezone(datas.TZ).date())
    v = _vendedor(p["vendedor_nome"])
    row = {**p, "message_id": mid, "autor": autor, "recebido_em": _iso(em), "historico": historico,
           "vendedor_id": v["id"] if v else None, "texto_original": texto}
    criado = db.insert_lock("leads", row)
    if criado is None:
        return {"ignored": "lead_duplicado", "message_id": mid}
    sujo["v"] = True
    return {"lead": criado.get("id"), "cliente": p["cliente_nome"], "tipo": p["tipo"]}


def _lead_por_msg(message_id: str) -> dict | None:
    rows = db.select("leads", {"message_id": f"eq.{message_id}", "limit": "1"})
    if rows:
        return rows[0]
    nota = db.select("lead_notas_msg", {"message_id": f"eq.{message_id}", "limit": "1"})
    if nota:
        rows = db.select("leads", {"id": f"eq.{nota[0]['lead_id']}", "limit": "1"})
        return rows[0] if rows else None
    return None


def _registrar_retorno(lead, texto, autor_nome, em, mid, historico) -> dict:
    status, nova = classificar_resposta(texto, em.astimezone(datas.TZ).date())
    if db.insert_lock("lead_eventos", {"lead_id": lead["id"], "message_id": mid, "em": _iso(em),
                                       "autor": autor_nome, "texto": texto, "status": status}) is None:
        return {"ignored": "retorno_duplicado"}
    upd = {"ultimo_retorno_em": _iso(em), "ultimo_retorno_texto": texto[:500], "ultimo_retorno_por": autor_nome}
    if status != "retorno" or lead["status"] == "aguardando_retorno":
        upd.update({"status": status, "status_em": _iso(em)})
    if nova:
        upd["nova_data"] = nova
    if lead.get("cobranca_status") == "proposta":
        upd["cobranca_status"] = "cancelada"
        if not historico:
            evolution.notificar_dono(f"✅ {lead.get('vendedor_nome') or 'O vendedor'} respondeu o lead "
                                     f"{lead.get('cliente_nome')} — cobrança cancelada.")
    db.update("leads", upd, {"id": f"eq.{lead['id']}"})
    sujo["v"] = True
    return {"retorno": lead["id"], "status": status}


def _anexar_nota(texto, mid, autor, em) -> dict:
    """Mensagem solta do mesmo SDR logo após o formulário = observação do lead."""
    desde = _iso(em - timedelta(minutes=NOTA_JANELA_MIN))
    rows = db.select("leads", {"select": "id,observacao", "autor": f"eq.{autor}",
                               "recebido_em": f"gte.{desde}", "order": "recebido_em.desc", "limit": "1"})
    if not rows:
        return {"ignored": "nota_sem_lead"}
    lead = rows[0]
    if db.insert_lock("lead_notas_msg", {"message_id": mid, "lead_id": lead["id"]}) is None:
        return {"ignored": "nota_duplicada"}
    limpa = _limpa(texto).lstrip("☝👆🏼🏽🏻 ").strip()
    obs = "\n".join(x for x in (lead.get("observacao"), limpa) if x)
    upd = {"observacao": obs}
    try:
        upd.update(llm.extrair_nota_lead(obs))
    except Exception:
        log.exception("falha extraindo nota do lead")
    db.update("leads", upd, {"id": f"eq.{lead['id']}"})
    sujo["v"] = True
    return {"nota": lead["id"]}


def _protocolo(pm: dict, historico: bool) -> dict:
    alvo = (pm.get("key") or {}).get("id")
    rows = db.select("leads", {"message_id": f"eq.{alvo}", "limit": "1"}) if alvo else []
    if not rows:
        return {"ignored": "protocolo_sem_lead"}
    lead = rows[0]
    editada = pm.get("editedMessage")
    if editada:
        texto = _texto(editada)
        if texto and eh_formulario(texto):
            ref = (_dt(lead["recebido_em"]) or datetime.now(timezone.utc)).astimezone(datas.TZ).date()
            p = parse_formulario(texto, ref)
            v = _vendedor(p["vendedor_nome"])
            db.update("leads", {**p, "vendedor_id": v["id"] if v else None, "texto_original": texto},
                      {"id": f"eq.{lead['id']}"})
            sujo["v"] = True
            return {"editado": lead["id"]}
        return {"ignored": "edicao_sem_formulario"}
    if pm.get("type") in (0, "REVOKE"):
        db.update("leads", {"removido": True}, {"id": f"eq.{lead['id']}"})
        sujo["v"] = True
        return {"removido": lead["id"]}
    return {"ignored": "protocolo"}


# ===== prazos e cobrança =====
def codigo(lead: dict) -> str:
    return lead["id"][:4].upper()


def _resumo_lead(l: dict) -> str:
    return (f"{l.get('cliente_nome') or '?'} — {l.get('veiculo') or '?'}\n"
            f"Vendedor: {l.get('vendedor_nome') or '?'} · SDR: {l.get('sdr') or '?'} · "
            f"{TIPO_LABEL.get(l['tipo'], l['tipo'])}\nPassado em {_hhmm(l['recebido_em'])}")


def texto_cobranca(l: dict) -> str:
    hora = _dt(l["recebido_em"]).astimezone(datas.TZ).strftime("%H:%M")
    return (f"Fala {l.get('vendedor_nome') or ''}! O lead do {l.get('cliente_nome') or 'cliente'} "
            f"({l.get('veiculo') or 'veículo'}) que {l.get('sdr') or 'o SDR'} passou às {hora} ainda está "
            f"sem retorno no grupo de agendamento. Já conseguiu falar com ele? Responde lá citando o agendamento 🙏")


def checar_prazos(agora: datetime | None = None) -> int:
    agora = agora or datetime.now(timezone.utc)
    # só negociação por telefone: é o lead em que o vendedor precisa entrar em contato
    abertos = db.select_all("leads", {"select": "*", "status": "eq.aguardando_retorno", "historico": "eq.false",
                                      "removido": "eq.false", "ultimo_retorno_em": "is.null",
                                      "tipo": "eq.negociacao_telefone"})
    n = 0
    for l in abertos:
        idade = agora - _dt(l["recebido_em"])
        if idade >= timedelta(minutes=COBRANCA_MIN) and not l.get("cobranca_status"):
            texto = texto_cobranca(l)
            cod = codigo(l)
            try:
                evolution.enviar_texto(settings.meu_numero,
                                       f"📨 Cobrança pronta pro {l.get('vendedor_nome') or 'vendedor'} [#{cod}]\n"
                                       f"{_resumo_lead(l)}\n\n\"{texto}\"\n\n"
                                       f"Responda *ok {cod}* pra enviar · *não {cod}* pra descartar · "
                                       f"ou *{cod}: texto novo* pra mandar outro texto.")
            except Exception:
                log.exception("falha propondo cobrança")
                continue
            db.update("leads", {"cobranca_status": "proposta", "cobranca_texto": texto,
                                "cobranca_proposta_em": _iso(agora),
                                "aviso_dono_em": l.get("aviso_dono_em") or _iso(agora)},
                      {"id": f"eq.{l['id']}"})
            n += 1
        elif idade >= timedelta(minutes=AVISO_MIN) and not l.get("aviso_dono_em"):
            try:
                evolution.enviar_texto(settings.meu_numero,
                                       f"⏰ Lead sem retorno há {AVISO_MIN} min [#{codigo(l)}]\n{_resumo_lead(l)}")
            except Exception:
                log.exception("falha avisando lead sem retorno")
                continue
            db.update("leads", {"aviso_dono_em": _iso(agora)}, {"id": f"eq.{l['id']}"})
            n += 1
    if n:
        sujo["v"] = True
    return n


def tentar_resolver(texto: str) -> str | None:
    """Aprovação de cobrança pelo dono (leads e vendas juntos) — ver cobrancas.py."""
    from . import cobrancas
    return cobrancas.tentar_resolver(texto)

def _enviar_cobranca(lead: dict, msg: str) -> str:
    atual = db.select("leads", {"id": f"eq.{lead['id']}", "limit": "1"})[0]
    if atual.get("ultimo_retorno_em") or atual.get("status") != "aguardando_retorno":
        db.update("leads", {"cobranca_status": "cancelada"}, {"id": f"eq.{lead['id']}"})
        return f"Esse lead já teve retorno ({STATUS_LABEL.get(atual.get('status'), atual.get('status'))}) — não enviei."
    v = db.select("vendedores", {"select": "telefone", "id": f"eq.{lead.get('vendedor_id')}", "limit": "1"}) \
        if lead.get("vendedor_id") else []
    tel = v[0].get("telefone") if v else None
    if not tel:
        return f"Não tenho o telefone de {lead.get('vendedor_nome') or 'esse vendedor'} — não enviei."
    evolution.enviar_por_coletor(tel, msg)
    db.update("leads", {"cobranca_status": "enviada", "cobranca_texto": msg,
                        "cobranca_enviada_em": _iso(datetime.now(timezone.utc))}, {"id": f"eq.{lead['id']}"})
    sujo["v"] = True
    return f"✅ Cobrança enviada pro {lead.get('vendedor_nome')}."


# ===== agenda da manhã =====
def _ordem_horario(h: str | None) -> int:
    a = _ascii(h)
    m = re.search(r"(\d{1,2})\s*(?:h|:)?\s*(\d{2})?", a)
    if m and int(m.group(1)) < 24:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    if "manh" in a:
        return 9 * 60
    if "tarde" in a:
        return 14 * 60
    return 24 * 60


def agenda_do_dia_texto(dia: str | None = None) -> str:
    """Leads com data agendada pro dia (já com as edições da planilha), um resumo por lead."""
    dia = dia or datas.hoje_iso()
    rows = db.select_all("leads", {"select": "*", "removido": "eq.false", "data_agendada": f"eq.{dia}"})
    rows.sort(key=lambda l: _ordem_horario(l.get("horario")))
    if not rows:
        return "📅 *Leads agendados pra hoje:* nenhum"
    linhas = [f"📅 *Leads agendados pra hoje ({len(rows)}):*"]
    for l in rows:
        extra = " · ".join(x for x in (
            f"troca: {l['troca']}" if l.get("troca") else "",
            l.get("oferta_entrada") or "",
            (l.get("observacao") or "").replace("\n", " · ")[:140] if not (l.get("troca") or l.get("oferta_entrada")) else "",
        ) if x)
        status = STATUS_LABEL.get(l["status"], l["status"])
        linhas.append(
            f"\n🕐 *{l.get('horario') or 'sem horário'}* — {l.get('cliente_nome') or '?'} · {l.get('veiculo') or '?'} [#{codigo(l)}]\n"
            f"   👤 {l.get('vendedor_nome') or 'sem vendedor'} · SDR {l.get('sdr') or '?'} · "
            f"{TIPO_LABEL.get(l['tipo'], l['tipo'])} · {l.get('canal') or '?'}\n"
            + (f"   📝 {extra}\n" if extra else "")
            + f"   📌 {status}" + (f" · 📞 {l['telefone']}" if l.get("telefone") else ""))
    return "\n".join(linhas)
