"""Grupo PREPARAÇÃO: pedidos e "Missões" viram tarefas por carro (checklist "Preparação" no cartão do Trello),
✓ / "feito" fecha a tarefa, problema encontrado no carro vira item do checklist com ⚠️; mensagem editada acerta o checklist.
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
RECADOS_ATIVOS = True  # liga/desliga de verdade: flag recados_preparacao (desligada desde 12/10)
FELIPE = "5521994110597"
PESSOAS = {"183863389221110@lid": "Daniel", "217982575837352@lid": "Caio", "248442551009535@lid": "Luiz Armando",
           "50427244589224@lid": "Felipe", "50852479930380@lid": "Igor"}

PROMPT = """Você lê mensagens do grupo de PREPARAÇÃO de carros de uma loja (deixar o carro pronto pra venda/entrega).
Responda SOMENTE um JSON {"itens": [...]}, cada item:
- "acao": "tarefa" (algo a fazer num carro ou pra loja), "feito" (avisa que algo foi feito — "volante ok", "Kwid lavado"; "tudo ok no Kwid" = descricao "tudo"), "pronto_entrega" (o carro está pronto pra entregar ao cliente), "problema" (defeito encontrado no carro: freio, embreagem, não liga, barulho...), "compra" (peça/item a comprar ou buscar), ou "info" (resto: conversa, combinação)
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


FOTO_MIN = 10
_OK_FIM = re.compile(r"(?i)(\s*[-–—:]\s*(ok|feito|feita|pronto|pronta|resolvido)|\s*(✓|✅|✔️?))\s*$")


def placa_norm_txt(texto: str | None) -> str | None:
    from .vendas_grupo import placa_norm
    return placa_norm(re.sub(r"\s", "", texto or "")) or placa_norm(texto)


def eh_resumo_repostado(texto: str | None) -> bool:
    return bool(texto) and "pendencias de hoje" in _ascii(texto.splitlines()[0] if texto else "")


def baixa_por_resumo(texto: str, em: datetime, historico: bool = False) -> dict:
    """Resumo das 8h devolvido com '- ok' nos itens: cada linha marcada dá baixa na tarefa daquele carro."""
    pend = db.select_all("prep_tarefas", {"select": "*", "status": "eq.pendente", "historico": "eq.false"})
    carro_atual, feitas = None, 0
    for linha in texto.splitlines():
        s = linha.strip()
        if s.startswith("*") and s.endswith("*") and len(s) > 2:
            carro_atual = s.strip("*").strip()
            continue
        if not s.startswith("•") or not carro_atual or not _OK_FIM.search(s):
            continue
        desc = _OK_FIM.sub("", s.lstrip("• ")).strip()
        desc = re.sub(r"\s*\(\d+ dias?\)\s*$", "", desc).strip()
        alvo = next((t for t in pend if _carro(t) == carro_atual and t["descricao"].strip() == desc), None) or \
            next((t for t in pend if _carro(t) == carro_atual and _parecida(t["descricao"], desc)), None)
        if alvo:
            _concluir(alvo, em, historico)
            pend.remove(alvo)
            feitas += 1
    return {"feitas": feitas, "via": "resumo"}


def identificar_carro(imagem_b64: str, mime: str | None = None) -> str | None:
    """Placa (e modelo, se der) que aparece na foto. None se não der pra ler."""
    resp = llm._client.chat.completions.create(
        model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": "Identifique o carro da foto. Responda SOMENTE JSON "
                   '{"placa": "ABC1D23" ou null, "carro": "marca e modelo" ou null}. Leia a placa com cuidado.'},
                  {"role": "user", "content": [{"type": "image_url", "image_url": {
                      "url": f"data:{mime or 'image/jpeg'};base64,{imagem_b64}"}}]}])
    r = json.loads(resp.choices[0].message.content or "{}")
    return " ".join(x for x in (r.get("carro"), r.get("placa")) if x) or None


def _carro_da_foto(autor: str, em: datetime) -> str | None:
    r = db.select("prep_msgs", {"select": "carro,em", "autor": f"eq.{autor}", "carro": "not.is.null",
                                "em": f"gte.{_iso(em - timedelta(minutes=FOTO_MIN))}", "order": "em.desc", "limit": "1"})
    return r[0]["carro"] if r and _dt(r[0]["em"]) <= em else None


_LADOS = [("diant", "tras"), ("direit", "esquerd"), ("motorist", "carona")]


def _parecida(a: str, b: str) -> bool:
    """Mesma tarefa escrita diferente. Lados opostos (dianteiro/traseiro, direito/esquerdo...) nunca são a mesma."""
    x, y = _ascii(a), _ascii(b)
    for l1, l2 in _LADOS:
        if (l1 in x and l2 in y and l1 not in y) or (l2 in x and l1 in y and l2 not in y):
            return False
    return SequenceMatcher(None, x, y).ratio() >= 0.72


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
    pm = msg.get("protocolMessage")
    if pm:
        return _edicao(pm, data) if pm.get("editedMessage") else {"ignored": "protocolo"}
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
    autor = _pessoa(key.get("participant"), data)
    if img and not texto:  # foto sem texto: lê placa/carro da imagem; a próxima mensagem da pessoa fala desse carro
        try:
            carro = identificar_carro(*img)
        except Exception:
            log.exception("falha lendo a placa da foto")
            db.delete("prep_msgs", {"message_id": f"eq.{mid}"})  # libera pra repescagem tentar de novo
            return {"erro": "foto"}
        if carro:
            db.update("prep_msgs", {"carro": carro, "autor": autor, "em": _iso(em)}, {"message_id": f"eq.{mid}"})
        return {"foto": carro}
    # resposta curta citando um pedido ("feito", "ok", "✓") fecha a tarefa daquele pedido
    stanza = _stanza(data)
    if stanza and re.fullmatch(r"\W*(ok|feito|pronto|resolvido|ja fiz|fiz|✓|✅|👍)\W*", _ascii(texto) or "ok"):
        n = 0
        for t in db.select("prep_tarefas", {"select": "*", "message_id": f"eq.{stanza}", "status": "eq.pendente"}):
            _concluir(t, em, historico)
            n += 1
        return {"feitas": n}
    if eh_resumo_repostado(texto):  # o resumo das 8h copiado com "- ok" nos itens feitos: lido linha a linha
        return baixa_por_resumo(texto, em, historico)
    try:
        itens = extrair(texto, *(img or (None, None)))
        placa_foto = None
        if img and any(not placa_norm_txt(it.get("carro")) for it in itens):
            placa_foto = identificar_carro(*img)  # leitura dedicada da placa (a extração geral às vezes só descreve o carro)
    except Exception:
        log.exception("falha extraindo preparação")
        db.delete("prep_msgs", {"message_id": f"eq.{mid}"})  # libera pra repescagem tentar de novo
        return {"erro": "extracao"}
    placas_txt = set(re.findall(r"\b[A-Za-z]{3}-?\d[A-Za-z0-9]\d{2}\b", texto or ""))
    if len(placas_txt) == 1:  # placa escrita no texto vale mais que a descrição da foto ("BYD branco")
        placa_txt = placas_txt.pop()
        itens = [it if placa_norm_txt(it.get("carro")) else {**it, "carro": placa_txt} for it in itens]
    if placa_foto and placa_norm_txt(placa_foto):
        itens = [it if placa_norm_txt(it.get("carro")) else {**it, "carro": placa_foto} for it in itens]
    responsavel = _mencionado(data)
    resumo = {"tarefas": 0, "feitas": 0, "problemas": 0}
    if any(not it.get("carro") for it in itens):  # "Volante desalinhado" depois da foto da placa: mesmo carro
        da_foto = _carro_da_foto(autor, em)
        if da_foto:
            itens = [it if it.get("carro") else {**it, "carro": da_foto} for it in itens]
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c) if trello.configurado() else ([], {})
        eh_lista = bool(re.search(r"\bmiss(ao|oes)\b", _ascii(texto.splitlines()[0] if texto else "")))
        vigentes = [r for it in itens
                    if (r := _aplicar(it, mid, em, autor, responsavel, historico, c, cards, listas, resumo, eh_lista))]
        if eh_lista and not historico:
            resumo["feitas"] += _baixa_por_lista(vigentes, em, c)
    return resumo


def _baixa_por_lista(vigentes: list[tuple], em: datetime, c) -> int:
    """Nova lista de Missões: item de lista anterior DO MESMO CARRO que não aparece mais = feito.
    Carro que não está na lista nova não é mexido."""
    cards = {card for card, _ in vigentes if card}
    n = 0
    for card in cards:
        atuais = [d for cd, d in vigentes if cd == card]
        for t in db.select_all("prep_tarefas", {"select": "*", "card_id": f"eq.{card}", "status": "eq.pendente",
                                                 "lista": "eq.true", "criado_em": f"lt.{_iso(em)}"}):
            if not any(_parecida(t["descricao"].lstrip("⚠️ "), d.lstrip("⚠️ ")) for d in atuais):
                _concluir(t, em, False, c)
                n += 1
    return n



def _feita_por_palavra(it: dict, card_id: str | None) -> list[dict]:
    """'Idea: volante ok' → a tarefa '⚠️ Volante desalinhado' do mesmo carro. 'tudo ok' → todas as do carro."""
    if not card_id:
        return []
    pend = db.select_all("prep_tarefas", {"select": "*", "card_id": f"eq.{card_id}", "status": "eq.pendente"})
    desc = _ascii(it.get("descricao") or "")
    if re.search(r"\b(tudo|todos|todas|completo)\b", desc):
        return pend
    vazias = {"feito", "feita", "pronto", "pronta", "resolvido", "trocado", "trocada", "colocado", "limpo", "carro"}
    toks = {t for t in re.findall(r"[a-z0-9]{4,}", desc) if t not in vazias}
    achadas = [t for t in pend if toks & set(re.findall(r"[a-z0-9]{4,}", _ascii(t["descricao"])))]
    return achadas if len(achadas) == 1 else []


def _aplicar(it, mid, em, autor, responsavel, historico, c, cards, listas, resumo, lista=False) -> tuple | None:
    """Um item extraído → tarefa (com item no checklist do cartão). Devolve (card_id, descrição) do item."""
    if it.get("acao") == "info" or not it.get("descricao") and it.get("acao") != "pronto_entrega":
        return None
    card = trello.achar_cartao(it.get("carro"), cards, listas)
    card_id, card_nome = (card["id"], card["name"]) if card else (None, None)
    if it["acao"] == "pronto_entrega":  # "Kwid pronto pra entregar": todos os checklists do cartão ficam ok
        if card_id and not historico:
            from .entregas_grupo import marcar_pronto
            resumo["feitas"] += marcar_pronto(card_id, c)
        return None
    if it["acao"] == "problema":  # defeito também é algo a resolver: vira item do checklist com ⚠️
        db.insert_lock("prep_problemas", {"message_id": f"{mid}#{resumo['problemas']}#{_ascii(it['descricao'])[:30]}",
                                          "carro_texto": it.get("carro"), "card_id": card_id, "card_nome": card_nome,
                                          "descricao": it["descricao"], "autor": autor, "em": _iso(em),
                                          "historico": historico})
        resumo["problemas"] += 1
        it = {**it, "descricao": f"⚠️ {it['descricao']}", "tipo": "problema"}
    aberta = _tarefa_aberta(it, card_id)
    if it["acao"] == "feito" or it.get("feito"):
        for t in ([aberta] if aberta else _feita_por_palavra(it, card_id)):
            _concluir(t, em, historico, c)
            resumo["feitas"] += 1
        return card_id, it["descricao"]
    if aberta:
        if lista and not aberta.get("lista"):
            db.update("prep_tarefas", {"lista": True}, {"id": f"eq.{aberta['id']}"})
        return card_id, it["descricao"]  # lista de Missões repostada: a tarefa já existe
    t = db.insert("prep_tarefas", {"message_id": mid, "carro_texto": it.get("carro"), "card_id": card_id,
                                   "card_nome": card_nome, "tipo": it.get("tipo") or "outro",
                                   "descricao": it["descricao"], "responsavel": responsavel,
                                   "criado_em": _iso(em), "historico": historico, "lista": lista})
    if card_id and not historico:
        try:
            item_id = trello.item_checklist(c, card_id, it["descricao"])
            db.update("prep_tarefas", {"checklist_item_id": item_id}, {"id": f"eq.{t['id']}"})
        except Exception:
            log.exception("falha criando item no Trello")
    resumo["tarefas"] += 1
    return card_id, it["descricao"]


def _edicao(pm: dict, data: dict) -> dict:
    """Mensagem editada: relê o texto e acerta o checklist — item novo entra, item que sumiu sai
    (se ainda não foi feito). Item com texto alterado sai e entra com o texto novo."""
    alvo = (pm.get("key") or {}).get("id")
    texto = _texto(pm.get("editedMessage") or {})
    antigas = db.select_all("prep_tarefas", {"select": "*", "message_id": f"eq.{alvo}"}) if alvo else []
    if not texto or not alvo:
        return {"ignored": "edicao_sem_texto"}
    if not antigas:  # a mensagem original não tinha virado tarefa ("Fiesta" e depois editou com os itens): trata como nova
        try:
            itens = extrair(texto)
        except Exception:
            log.exception("falha extraindo edição da preparação")
            return {"erro": "extracao"}
        resumo = {"tarefas": 0, "feitas": 0, "problemas": 0}
        with trello._cliente() as c:
            cards, listas = trello.cartoes(c)
            for it in itens:
                _aplicar(it, alvo, _quando(data), _pessoa((data.get("key") or {}).get("participant"), data), None, False,
                         c, cards, listas, resumo)
        return resumo
    try:
        itens = extrair(texto)
    except Exception:
        log.exception("falha extraindo edição da preparação")
        return {"erro": "extracao"}
    em = _dt(antigas[0]["criado_em"])
    carro_original = next((t.get("card_nome") or t.get("carro_texto") for t in antigas
                           if t.get("card_nome") or t.get("carro_texto")), None)
    if carro_original:  # legenda de foto não diz o carro: item novo vai pro carro da mensagem original
        itens = [it if it.get("carro") else {**it, "carro": carro_original} for it in itens]
    resumo = {"tarefas": 0, "feitas": 0, "problemas": 0, "removidas": 0}
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c)
        vigentes = [r for it in itens if (r := _aplicar(it, alvo, em, _pessoa(None, data), antigas[0].get("responsavel"),
                                                        antigas[0].get("historico", False), c, cards, listas, resumo))]
        for t in antigas:
            if t["status"] != "pendente":
                continue
            ainda = any((card_id == t.get("card_id") or not card_id) and _parecida(desc, t["descricao"])
                        for card_id, desc in vigentes)
            if ainda:
                continue
            if t.get("card_id") and t.get("checklist_item_id"):
                try:
                    trello.remover_item(c, t["card_id"], t["checklist_item_id"])
                except Exception:
                    log.exception("falha removendo item do Trello")
            db.delete("prep_tarefas", {"id": f"eq.{t['id']}"})
            resumo["removidas"] += 1
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


def sincronizar_trello() -> dict:
    """Quem marca o item como feito direto no checklist do Trello também conta: a tarefa vira 'feito'.
    Item apagado do cartão (ou cartão apagado) vira 'removida'."""
    pend = db.select_all("prep_tarefas", {"select": "id,card_id,checklist_item_id", "status": "eq.pendente",
                                          "checklist_item_id": "not.is.null"})
    por_card: dict[str, list] = {}
    for t in pend:
        por_card.setdefault(t["card_id"], []).append(t)
    feitas = removidas = 0
    agora = _iso(datetime.now(timezone.utc))
    with trello._cliente() as c:
        for card_id, ts in por_card.items():
            r = c.get(f"/cards/{card_id}/checklists", params={"fields": "name", "checkItem_fields": "state"})
            if r.status_code == 404:
                itens = {}
            elif r.is_success:
                itens = {i["id"]: i["state"] for k in r.json() for i in k.get("checkItems", [])}
            else:
                continue
            for t in ts:
                estado = itens.get(t["checklist_item_id"])
                if estado == "complete":
                    db.update("prep_tarefas", {"status": "feito", "feito_em": agora}, {"id": f"eq.{t['id']}"})
                    feitas += 1
                elif estado is None:
                    db.update("prep_tarefas", {"status": "removida"}, {"id": f"eq.{t['id']}"})
                    removidas += 1
    return {"feitas": feitas, "removidas": removidas}


# ===== tarefa parada → recado pro Felipe (cópia pro dono) =====
def _carro(t: dict) -> str:
    return re.sub(r"\s*-\s*[A-Za-zÀ-ú]+$", "", t.get("card_nome") or "") or t.get("carro_texto") or "geral"


def checar_paradas(agora: datetime | None = None) -> int:
    if not RECADOS_ATIVOS:
        return 0
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


GRUPO = "120363185008258158@g.us"


def resumo_grupo() -> str:
    """Mensagem das 8h no grupo: tudo que está pendente, por carro. Substitui o recado pro Felipe."""
    rows = db.select_all("prep_tarefas", {"select": "*", "status": "eq.pendente", "historico": "eq.false",
                                          "order": "criado_em.asc"})
    if not rows:
        return ""
    por: dict[str, list] = {}
    for t in rows:
        por.setdefault(_carro(t), []).append(t)
    hoje = datetime.now(timezone.utc)
    blocos = []
    for carro, ts in por.items():
        itens = "\n".join(f"• {t['descricao']}" + (f" ({(hoje - _dt(t['criado_em'])).days} dias)"
                                                     if (hoje - _dt(t["criado_em"])).days >= 2 else "") for t in ts)
        blocos.append(f"*{carro}*\n{itens}")
    return (f"🔧 *Preparação — pendências de hoje ({datas.hoje():%d/%m})*\n\n" + "\n\n".join(blocos)
            + "\n\nRespondam o que já foi feito (ex.: _Idea: volante ok_) que eu dou baixa no Trello.")


def agenda_texto() -> str:
    rows = db.select_all("prep_tarefas", {"select": "*", "status": "eq.pendente", "historico": "eq.false"})
    if not rows:
        return ""
    por: dict[str, list] = {}
    for t in rows:
        por.setdefault(_carro(t), []).append(t["descricao"])
    return f"🔧 *Preparação em aberto ({len(rows)}):*\n" + "\n".join(f"• {c}: {', '.join(d)}" for c, d in por.items())
