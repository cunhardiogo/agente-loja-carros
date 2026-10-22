"""Grupo RECALL: formulário (ou texto solto) de carro vendido que volta vira um recall; resposta com data
agenda; "pronto" no grupo encerra. No Trello o cartão vai pra RECALL com checklist dos motivos e volta pro
FINALIZADO quando fica pronto. Recall sem data há 2 dias → aviso pro dono."""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from . import datas, db, evolution, llm, trello
from .config import settings
from .leads import _ascii, _dt, _iso, _quando, _stanza, _texto

log = logging.getLogger("agente")

AVISO_DIAS = 2
AVISOS_ATIVOS = True
GARANTIA_DIAS = 90
RECALL = "recall"
_CURTA = re.compile(r"\W*(ok|blz|beleza|certo|show|top|👍|🙏)?\W*")

sujo = {"v": True}

PROMPT = """Você lê mensagens do grupo de RECALL de uma loja de carros seminovos (carro vendido que volta pra
consertar defeito / garantia). Hoje é {hoje}.
Recalls abertos:
{abertos}
Responda SOMENTE um JSON:
{{"tipo": "novo" (relata carro vendido que precisa voltar: formulário Veículo/Placa/Motivo ou texto solto com carro + defeito)
        | "agendado" (diz quando o carro vem / foi marcado)
        | "na_loja" (carro chegou ou ficou na loja)
        | "pronto" (carro pronto, resolvido, cliente pode buscar, já devolvido)
        | "info" (resto: conversa, pergunta, combinação),
 "recall": número do recall aberto de que a mensagem fala, ou null,
 "veiculo": carro como escrito, "placa": placa ou null,
 "data_saida": "AAAA-MM-DD" (data em que o carro saiu da loja) ou null,
 "km_saida": inteiro ou null, "km_atual": inteiro ou null,
 "motivos": ["cada defeito, curto"], "obs": texto ou null,
 "na_loja": true se o carro já está/ficou na loja,
 "quando": "AAAA-MM-DDTHH:MM" se a mensagem marca dia (sem hora → T09:00), senão null,
 "quando_texto": o dia/hora como foi escrito, ou null}}
Regras:
- Carro com defeito/serviço a fazer que NÃO está na lista de abertos é "novo", mesmo em texto solto, mesmo
  perguntando quando trazer ou já trazendo o dia (aí preencha "quando" também).
- Mensagem que só traz dia/hora pra um recall aberto (ou respondendo a ele) é "agendado".
- Datas no formato brasileiro: 10/09 = 10 de setembro. Sem ano: o mais próximo de hoje.
- Mensagem curta continuando a conversa ("Vazando óleo", "e o amortecedor") fala do carro das mensagens
  anteriores: use-as só para preencher "veiculo"/"placa"/"recall"; o tipo vem da mensagem atual.
- "motivos" são defeitos do carro. Opinião ou comentário ("golpe", "manda trazer", "nunca vi isso") não é
  motivo: é "info"."""


def _rotulo(r: dict) -> str:
    return " ".join(x for x in (r.get("veiculo"), _placa_fmt(r.get("placa"))) if x) or "carro?"


def _placa_fmt(p: str | None) -> str:
    return f"{p[:3]}-{p[3:]}" if p and len(p) == 7 else (p or "")


def extrair(texto: str, citada: str | None, abertos: list[dict], anteriores: str | None = None) -> dict:
    lista = "\n".join(f"{i}. {_rotulo(r)} — {r.get('motivo') or ''}" for i, r in enumerate(abertos, 1)) or "(nenhum)"
    conteudo = texto if not citada else f"[respondendo a: {citada}]\n{texto}"
    if anteriores:
        conteudo = f"[mensagens anteriores no grupo]\n{anteriores}\n[mensagem atual]\n{conteudo}"
    resp = llm._client.chat.completions.create(
        model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": PROMPT.format(hoje=datas.hoje_iso(), abertos=lista)},
                  {"role": "user", "content": conteudo}])
    return json.loads(resp.choices[0].message.content or "{}")


def _anteriores(em: datetime, mid: str | None) -> str:
    """Últimas mensagens do grupo nos 15 min antes desta (pra saber de que carro fala uma mensagem curta)."""
    rows = db.select("mensagens", {"select": "message_id,autor_nome,texto", "jid": f"eq.{GRUPO}",
                                   "texto": "not.is.null", "em": f"gte.{_iso(em - timedelta(minutes=15))}",
                                   "and": f"(em.lt.{_iso(em)})", "order": "em.desc", "limit": "6"})
    return "\n".join(f"{m.get('autor_nome') or '?'}: {m['texto'][:300]}" for m in reversed(rows)
                     if m["message_id"] != mid and not m["texto"].startswith("🔁"))


def _mesmo_carro(veiculo: str | None, abertos: list[dict]) -> dict | None:
    v = _ascii(veiculo or "").split()
    achados = [r for r in abertos if v and r.get("veiculo") and _ascii(r["veiculo"]).split()[:1] == v[:1]]
    return achados[0] if len(achados) == 1 else None


def _abertos() -> list[dict]:
    return db.select_all("recalls", {"select": "*", "origem": "eq.grupo", "status": "in.(aberto,agendado,na_loja)",
                                     "order": "aberto_em.asc"})


def _do_stanza(stanza: str | None) -> dict | None:
    if not stanza:
        return None
    r = db.select("recalls", {"select": "*", "message_id": f"eq.{stanza}", "limit": "1"})
    if r:
        return r[0]
    m = db.select("recall_msgs", {"select": "recall_id", "message_id": f"eq.{stanza}", "limit": "1"})
    if m and m[0].get("recall_id"):
        r = db.select("recalls", {"select": "*", "id": f"eq.{m[0]['recall_id']}", "limit": "1"})
        return r[0] if r else None
    return None


def _quando_dt(v: str | None) -> str | None:
    try:
        return _iso(datetime.fromisoformat(v).replace(tzinfo=datas.TZ)) if v else None
    except ValueError:
        return None


def _data(v: str | None) -> str | None:
    try:
        return date.fromisoformat(v).isoformat() if v else None
    except ValueError:
        return None


def _motivos(r: dict) -> list[str]:
    return [m.strip() for m in (r.get("motivo") or "").split(" / ") if m.strip()]


# ===== processamento =====
def processar(data: dict, historico: bool = False) -> dict:
    from .vendas_grupo import placa_norm, vendedor_por_lid
    key = data.get("key") or {}
    msg = data.get("message") or {}
    pm = msg.get("protocolMessage")
    if pm:  # formulário editado: acrescenta motivo / corrige placa, km, data
        alvo = db.select("recalls", {"select": "*", "message_id": f"eq.{(pm.get('key') or {}).get('id')}", "limit": "1"})
        texto = _texto(pm.get("editedMessage") or {})
        if not alvo or not texto:
            return {"ignored": "protocolo"}
        try:
            it = extrair(texto, None, [])
        except Exception:
            log.exception("falha extraindo edição de recall")
            return {"erro": "extracao"}
        _novo_ou_soma(alvo[0], it, alvo[0].get("historico", False))
        return {"editado": alvo[0]["id"]}
    texto = _texto(msg)
    mid = key.get("id")
    if not texto or _CURTA.fullmatch(_ascii(texto)):
        return {"ignored": "sem_conteudo"}
    if db.insert_lock("recall_msgs", {"message_id": mid}) is None:
        return {"ignored": "duplicada"}
    em = _quando(data)
    citado = _do_stanza(_stanza(data))
    abertos = _abertos()
    try:
        it = extrair(texto, _rotulo(citado) + " — " + (citado.get("motivo") or "") if citado else None, abertos,
                     None if citado else _anteriores(em, mid))
    except Exception:
        log.exception("falha extraindo recall")
        db.delete("recall_msgs", {"message_id": f"eq.{mid}"})  # repescagem tenta de novo
        return {"erro": "extracao"}
    tipo = it.get("tipo") or "info"
    alvo = citado
    n = it.get("recall")
    if alvo is None and isinstance(n, int) and 1 <= n <= len(abertos):
        alvo = abertos[n - 1]
    placa = placa_norm(it.get("placa"))
    if tipo == "agendado" and alvo is None and it.get("motivos"):
        tipo = it["tipo"] = "novo"  # "Toro Ranch, segunda 28/09, polimento": abre já agendado
    if tipo == "novo":
        mesmo = next((r for r in abertos if placa and r.get("placa") == placa), None) \
            or _mesmo_carro(it.get("veiculo"), [r for r in abertos if not (placa and r.get("placa"))])
        if not mesmo and alvo is None and not placa and not it.get("veiculo"):
            return {"ignored": "defeito_sem_carro"}  # não abre recall sem saber de que carro é
        if mesmo:  # carro com recall aberto: o motivo novo soma no mesmo
            alvo = mesmo
        elif alvo is None:
            vend = vendedor_por_lid(key.get("participant"))
            alvo = _criar(it, placa, mid, em, key.get("participant"), vend, texto, historico)
            db.update("recall_msgs", {"recall_id": alvo["id"]}, {"message_id": f"eq.{mid}"})
            _aplicar_estado(alvo, it, em, historico)
            sujo["v"] = True
            return {"recall": alvo["id"], "carro": _rotulo(alvo)}
    if alvo is None:
        return {"ignored": "conversa"}
    db.update("recall_msgs", {"recall_id": alvo["id"]}, {"message_id": f"eq.{mid}"})
    if tipo == "novo":
        _novo_ou_soma(alvo, it, historico)
    _aplicar_estado(alvo, it, em, historico)
    sujo["v"] = True
    return {"atualizado": alvo["id"], "tipo": tipo}


def _criar(it, placa, mid, em, autor, vend, texto, historico) -> dict:
    venda = None
    if placa:
        v = db.select("vendas", {"select": "id,vendedor_id,data_entrega_prevista", "placa": f"eq.{placa}",
                                 "removido": "eq.false", "limit": "1"})
        venda = v[0] if v else None
    row = {"message_id": mid, "origem": "grupo", "autor": autor, "historico": historico,
           "vendedor_id": (vend or {}).get("id") or (venda or {}).get("vendedor_id"),
           "venda_id": (venda or {}).get("id"), "veiculo": it.get("veiculo"), "placa": placa,
           "data_saida": _data(it.get("data_saida")), "km_saida": it.get("km_saida"), "km_atual": it.get("km_atual"),
           "motivo": " / ".join(it.get("motivos") or []) or None, "observacao": it.get("obs"),
           "texto_original": texto, "status": "aberto", "aberto_em": _iso(em)}
    r = db.insert_lock("recalls", row) or db.select("recalls", {"select": "*", "message_id": f"eq.{mid}"})[0]
    if not historico:
        _trello_abrir(r)
    return r


def _novo_ou_soma(r: dict, it: dict, historico: bool) -> None:
    from .vendas_grupo import placa_norm
    atuais = _motivos(r)
    novos = [m for m in it.get("motivos") or [] if all(_ascii(m) != _ascii(a) for a in atuais)]
    upd = {k: v for k, v in (("placa", placa_norm(it.get("placa"))), ("data_saida", _data(it.get("data_saida"))),
                             ("km_saida", it.get("km_saida")), ("km_atual", it.get("km_atual")),
                             ("veiculo", it.get("veiculo"))) if v and not r.get(k)}
    if novos:
        upd["motivo"] = " / ".join(atuais + novos)
    if upd:
        db.update("recalls", upd, {"id": f"eq.{r['id']}"})
        r.update(upd)
        sujo["v"] = True
    if novos and not historico:
        _trello_abrir(r, novos)


def _aplicar_estado(r: dict, it: dict, em: datetime, historico: bool) -> None:
    tipo = it.get("tipo")
    upd: dict = {}
    quando = _quando_dt(it.get("quando"))
    if quando and r.get("status") in ("aberto", "agendado"):
        upd.update({"agendado_para": quando, "agendado_texto": it.get("quando_texto"), "status": "agendado"})
    if (tipo == "na_loja" or it.get("na_loja")) and r.get("status") != "pronto":
        upd["status"] = "na_loja"
    if tipo == "pronto":
        upd.update({"status": "pronto", "pronto_em": _iso(em)})
    if not upd:
        return
    db.update("recalls", upd, {"id": f"eq.{r['id']}"})
    r.update(upd)
    if historico or not trello.configurado() or not r.get("trello_card_id"):
        return
    try:
        with trello._cliente() as c:
            if upd.get("status") == "pronto":
                _trello_fechar(c, r)
            elif "agendado_para" in upd:
                c.put(f"/cards/{r['trello_card_id']}", params={"due": quando}).raise_for_status()
    except Exception:
        log.exception("falha atualizando cartão do recall")


# ===== Trello =====
def _trello_abrir(r: dict, motivos: list[str] | None = None) -> None:
    """Cartão do carro (achado pela placa ou modelo, inclusive no FINALIZADO) vai pra RECALL com checklist."""
    if not trello.configurado():
        return
    try:
        with trello._cliente() as c:
            cards, listas = trello.cartoes(c)
            id_recall = next(k for k, v in listas.items() if v == RECALL)
            card = next((cd for cd in cards if cd["id"] == r.get("trello_card_id")), None) \
                or trello.achar_cartao(_placa_fmt(r.get("placa")) or None, cards, listas, todas=True) \
                or trello.achar_cartao(r.get("veiculo"), [cd for cd in cards if listas.get(cd["idList"]) in
                                                          (trello.FINALIZADO, RECALL, trello.VENDIDOS)], listas, todas=True)
            if card is None:
                from .vendas_grupo import _nome_vendedor
                vend = _nome_vendedor(r) if r.get("vendedor_id") else None
                card = c.post("/cards", params={"idList": id_recall, "pos": "top", "name": trello.nome_cartao(
                    {"modelo": r.get("veiculo"), "placa": r.get("placa")}, vend)}).json()
            elif card["idList"] != id_recall:
                c.put(f"/cards/{card['id']}", params={"idList": id_recall, "pos": "top"}).raise_for_status()
            itens = dict(r.get("checklist") or {})
            for m in motivos if motivos is not None else _motivos(r):
                if m not in itens:
                    itens[m] = trello.item_checklist(c, card["id"], m, nome="Recall")
        db.update("recalls", {"trello_card_id": card["id"], "checklist": itens}, {"id": f"eq.{r['id']}"})
        r.update({"trello_card_id": card["id"], "checklist": itens})
    except Exception:
        log.exception("falha abrindo recall no Trello")


def _trello_fechar(c, r: dict) -> None:
    _, listas = trello.cartoes(c)
    fin = next(k for k, v in listas.items() if v == trello.FINALIZADO)
    for item in (r.get("checklist") or {}).values():
        try:
            trello.concluir_item(c, r["trello_card_id"], item)
        except Exception:
            log.exception("falha concluindo item do recall")
    c.put(f"/cards/{r['trello_card_id']}", params={"idList": fin, "pos": "top", "dueComplete": "true"}).raise_for_status()


# ===== aviso pro dono e agenda =====
def checar(agora: datetime | None = None) -> int:
    if not AVISOS_ATIVOS:
        return 0
    agora = agora or datetime.now(timezone.utc)
    if not 9 <= agora.astimezone(datas.TZ).hour < 19:
        return 0
    parados = db.select_all("recalls", {"select": "*", "origem": "eq.grupo", "status": "eq.aberto",
                                        "historico": "eq.false", "aviso_em": "is.null",
                                        "aberto_em": f"lte.{_iso(agora - timedelta(days=AVISO_DIAS))}"})
    if not parados:
        return 0
    linhas = "\n".join(f"• {_rotulo(r)}: {r.get('motivo') or '?'} — aberto em "
                       f"{_dt(r['aberto_em']).astimezone(datas.TZ):%d/%m}" for r in parados)
    try:
        evolution.enviar_texto(settings.meu_numero,
                               f"🔁 Recall sem data marcada há {AVISO_DIAS} dias ou mais:\n\n{linhas}")
    except Exception:
        log.exception("falha avisando recall parado")
        return 0
    for r in parados:
        db.update("recalls", {"aviso_em": _iso(agora)}, {"id": f"eq.{r['id']}"})
    return len(parados)


def garantia_ate(r: dict) -> date | None:
    d = r.get("data_saida")
    return date.fromisoformat(d) + timedelta(days=GARANTIA_DIAS) if d else None


GRUPO = "120363414450144897@g.us"


def resumo_grupo() -> str:
    """Mensagem das 8h no grupo de recall: todos os abertos, cobrando o próximo passo de cada um."""
    rows = _abertos()
    if not rows:
        return ""
    agora = datetime.now(timezone.utc)
    linhas = []
    for r in rows:
        dias = (agora - _dt(r["aberto_em"])).days
        if r["status"] == "agendado" and r.get("agendado_para"):
            situacao = f"agendado {_dt(r['agendado_para']).astimezone(datas.TZ):%d/%m %H:%M} — confirmar com o cliente"
        elif r["status"] == "na_loja":
            situacao = "na loja — quando fica pronto?"
        else:
            situacao = "SEM DATA — quando o cliente traz?"
        linhas.append(f"*{_rotulo(r)}* ({dias} dias)\n{r.get('motivo') or '?'}\n👉 {situacao}")
    return (f"🔁 *Recall em aberto ({len(rows)}) — {datas.hoje():%d/%m}*\n\n" + "\n\n".join(linhas)
            + "\n\nQuando ficar pronto, avisem aqui (\"pronto\") respondendo ao recall.")


def agenda_texto(hoje: str | None = None) -> str:
    hoje = hoje or datas.hoje_iso()
    rows = _abertos()
    if not rows:
        return ""
    linhas = []
    for r in rows:
        if r["status"] == "agendado" and r.get("agendado_para"):
            q = _dt(r["agendado_para"]).astimezone(datas.TZ)
            quando = f"hoje {q:%H:%M}" if q.date().isoformat() == hoje else f"{q:%d/%m %H:%M}"
        else:
            quando = {"na_loja": "na loja", "aberto": "sem data"}.get(r["status"], r["status"])
        linhas.append(f"• {_rotulo(r)} ({quando}): {r.get('motivo') or '?'}")
    return f"🔁 *Recall em aberto ({len(rows)}):*\n" + "\n".join(linhas)
