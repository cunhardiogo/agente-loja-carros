"""Grupo PREPARAÇÃO: pedidos e "Missões" viram tarefas por carro (checklist "Preparação" no cartão do Trello),
✓ / "feito" fecha a tarefa, problema encontrado no carro fica registrado (comentário no cartão).
Tarefa parada há 2 dias → recado pro Felipe cobrar (com cópia pro dono)."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from . import datas, db, evolution, llm, trello
from .config import settings
from .leads import _ascii, _dt, _iso, _quando, _stanza, _texto

log = logging.getLogger("agente")

PARADA_DIAS = 2
FELIPE = "5521994110597"
PESSOAS = {"183863389221110@lid": "Daniel", "217982575837352@lid": "Caio", "248442551009535@lid": "Luiz Armando",
           "50427244589224@lid": "Felipe", "50852479930380@lid": "Igor"}

PROMPT = """Você lê mensagens do grupo de PREPARAÇÃO de carros de uma loja (deixar o carro pronto pra venda/entrega).
Responda SOMENTE um JSON {"itens": [...]}, cada item:
- "acao": "tarefa" (algo a fazer num carro ou pra loja), "feito" (avisa que algo foi feito), "problema" (defeito encontrado no carro: freio, embreagem, não liga, barulho...), "compra" (peça/item a comprar ou buscar), ou "info" (resto: conversa, combinação)
- "carro": o carro como foi escrito (ex "Civic 2014", "208 azul", "Versa branco"), ou null
- "descricao": curta, no infinitivo pra tarefa/compra ("Trocar pneu", "Levar para pintura", "Frisar os 4 pneus"); o defeito pra problema; o que foi feito pra feito
- "tipo": "pintura", "mecanica", "estetica" (lavagem, polimento, higienização), "peca_compra", "foto", "buscar_levar" ou "outro"
- "feito": true se o item já veio marcado como concluído (✓, ok, feito) — numa lista de Missões, cada linha com ✓ é feito=true
Uma mensagem de lista ("Missões: ...") gera um item por linha. Se a mensagem só conversa, devolva {"itens": []}.
Se vier foto, use-a só pra identificar o carro (modelo, cor, placa)."""


def _pessoa(lid: str | None, data: dict) -> str:
    if (data.get("key") or {}).get("fromMe"):
        return "Diogo"
    return PESSOAS.get(lid or "") or (data.get("pushName") or "?")


def _mencionado(data: dict) -> str | None:
    ctx = data.get("contextInfo") or {}
    for lid in ctx.get("mentionedJid") or []:
        if lid in PESSOAS:
            return PESSOAS[lid]
    return None


def extrair(texto: str, imagem_b64: str | None = None, mime: str | None = None) -> list[dict]:
    conteudo: list = [{"type": "text", "text": texto or "(foto sem texto)"}]
    if imagem_b64:
        conteudo.append({"type": "image_url", "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{imagem_b64}"}})
    resp = llm._client.chat.completions.create(
        model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": conteudo}])
    return json.loads(resp.choices[0].message.content or "{}").get("itens") or []


def _parecida(a: str, b: str) -> bool:
    return SequenceMatcher(None, _ascii(a), _ascii(b)).ratio() >= 0.72


def _tarefa_aberta(item: dict, card_id: str | None) -> dict | None:
    rows = db.select_all("prep_tarefas", {"select": "*", "status": "eq.pendente"})
    for t in rows:
        mesmo_carro = (card_id and t.get("card_id") == card_id) or \
            (not card_id and _ascii(t.get("carro_texto") or "") == _ascii(item.get("carro") or ""))
        if mesmo_carro and _parecida(t["descricao"], item["descricao"]):
            return t
    return None


def processar(data: dict, instancia: str, apikey: str, historico: bool = False) -> dict:
    key = data.get("key") or {}
    mid = key.get("id")
    msg = data.get("message") or {}
    if msg.get("protocolMessage"):
        return {"ignored": "protocolo"}
    texto = _texto(msg)
    img = None
    if msg.get("imageMessage"):
        try:
            img = evolution.get_media_base64(instancia, apikey, data)
        except Exception:
            img = None
    if not texto and not img:
        return {"ignored": "sem_conteudo"}
    if db.insert_lock("prep_msgs", {"message_id": mid}) is None:
        return {"ignored": "duplicada"}
    em = _quando(data)
    # resposta curta citando um pedido ("feito", "ok", "✓") fecha a tarefa daquele pedido
    stanza = _stanza(data)
    if stanza and re.fullmatch(r"\W*(ok|feito|pronto|resolvido|ja fiz|fiz|✓|✅|👍)\W*", _ascii(texto) or "ok"):
        n = 0
        for t in db.select("prep_tarefas", {"select": "*", "message_id": f"eq.{stanza}", "status": "eq.pendente"}):
            _concluir(t, em, historico)
            n += 1
        return {"feitas": n}
    try:
        itens = extrair(texto, *(img or (None, None)))
    except Exception:
        log.exception("falha extraindo preparação")
        return {"erro": "extracao"}
    autor = _pessoa(key.get("participant"), data)
    responsavel = _mencionado(data)
    resumo = {"tarefas": 0, "feitas": 0, "problemas": 0}
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c) if trello.configurado() else ([], {})
        for it in itens:
            if it.get("acao") == "info" or not it.get("descricao"):
                continue
            card = trello.achar_cartao(it.get("carro"), cards, listas)
            card_id, card_nome = (card["id"], card["name"]) if card else (None, None)
            if it["acao"] == "problema":
                row = db.insert_lock("prep_problemas", {"message_id": f"{mid}#{resumo['problemas']}", "carro_texto": it.get("carro"),
                                                        "card_id": card_id, "card_nome": card_nome,
                                                        "descricao": it["descricao"], "autor": autor, "em": _iso(em),
                                                        "historico": historico})
                if row and card_id and not historico:
                    try:
                        trello.comentar(c, card_id, f"⚠️ Problema relatado por {autor} no grupo de preparação "
                                                    f"({em.astimezone(datas.TZ):%d/%m}): {it['descricao']}")
                        db.update("prep_problemas", {"comentado_trello": True}, {"id": f"eq.{row['id']}"})
                    except Exception:
                        log.exception("falha comentando problema no Trello")
                resumo["problemas"] += 1
                continue
            aberta = _tarefa_aberta(it, card_id)
            if it["acao"] == "feito" or it.get("feito"):
                if aberta:
                    _concluir(aberta, em, historico, c)
                    resumo["feitas"] += 1
                continue
            if aberta:
                continue  # lista de Missões repostada: a tarefa já existe
            t = db.insert("prep_tarefas", {"message_id": mid, "carro_texto": it.get("carro"), "card_id": card_id,
                                           "card_nome": card_nome, "tipo": it.get("tipo") or "outro",
                                           "descricao": it["descricao"], "responsavel": responsavel,
                                           "criado_em": _iso(em), "historico": historico})
            if card_id and not historico:
                try:
                    item_id = trello.item_checklist(c, card_id, it["descricao"])
                    db.update("prep_tarefas", {"checklist_item_id": item_id}, {"id": f"eq.{t['id']}"})
                except Exception:
                    log.exception("falha criando item no Trello")
            resumo["tarefas"] += 1
    return resumo


def _concluir(t: dict, em: datetime, historico: bool, c=None) -> None:
    db.update("prep_tarefas", {"status": "feito", "feito_em": _iso(em)}, {"id": f"eq.{t['id']}"})
    if t.get("card_id") and t.get("checklist_item_id") and not historico:
        try:
            if c is None:
                with trello._cliente() as c2:
                    trello.concluir_item(c2, t["card_id"], t["checklist_item_id"])
            else:
                trello.concluir_item(c, t["card_id"], t["checklist_item_id"])
        except Exception:
            log.exception("falha marcando item no Trello")


# ===== tarefa parada → recado pro Felipe (cópia pro dono) =====
def _carro(t: dict) -> str:
    return re.sub(r"\s*-\s*[A-Za-zÀ-ú]+$", "", t.get("card_nome") or "") or t.get("carro_texto") or "geral"


def checar_paradas(agora: datetime | None = None) -> int:
    agora = agora or datetime.now(timezone.utc)
    hora = agora.astimezone(datas.TZ).hour
    if not 9 <= hora < 19:  # recado só em horário comercial
        return 0
    limite = _iso(agora - timedelta(days=PARADA_DIAS))
    paradas = db.select_all("prep_tarefas", {"select": "*", "status": "eq.pendente", "historico": "eq.false",
                                             "cobrado_em": "is.null", "criado_em": f"lte.{limite}"})
    if not paradas:
        return 0
    linhas = "\n".join(f"• {_carro(t)}: {t['descricao']}"
                       f"{' (' + t['responsavel'] + ')' if t.get('responsavel') else ''}"
                       f" — pedido em {_dt(t['criado_em']).astimezone(datas.TZ):%d/%m}" for t in paradas)
    texto = (f"Felipe, essas tarefas da preparação estão paradas há {PARADA_DIAS} dias ou mais. "
             f"Consegue cobrar quem está com elas?\n\n{linhas}")
    try:
        evolution.enviar_por_coletor(FELIPE, texto)
        evolution.enviar_texto(settings.meu_numero, f"📋 Mandei pro Felipe (preparação parada):\n\n{texto}")
    except Exception:
        log.exception("falha enviando recado de preparação")
        return 0
    for t in paradas:
        db.update("prep_tarefas", {"cobrado_em": _iso(agora)}, {"id": f"eq.{t['id']}"})
    return len(paradas)


def agenda_texto() -> str:
    rows = db.select_all("prep_tarefas", {"select": "*", "status": "eq.pendente", "historico": "eq.false"})
    if not rows:
        return ""
    por: dict[str, list] = {}
    for t in rows:
        por.setdefault(_carro(t), []).append(t["descricao"])
    return f"🔧 *Preparação em aberto ({len(rows)}):*\n" + "\n".join(f"• {c}: {', '.join(d)}" for c, d in por.items())
