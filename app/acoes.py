"""Ações que o dono pede por mensagem ao agente: corrigir lead, entrega, avaliação, placa da venda,
tarefas de preparação e o quadro do Trello. Planilha e descrição do grupo se atualizam sozinhas porque
são montadas a partir do banco. O que move ou apaga (mover/criar cartão, tirar tarefa) fica pendente
até o dono responder "ok"."""
import logging
import re
from datetime import datetime, timedelta, timezone

from . import db, trello
from .leads import _ascii, _iso

log = logging.getLogger("agente")

LISTAS = {"estoque": "estoque sb!", "transito": "carro em transito", "pintura": "pintura", "mecanico": "mecanico",
          "preparacao": "preparacao / foto", "foto": "preparacao / foto", "vendidos": "vendidos / para entregar",
          "recall": "recall", "finalizado": "finalizado"}
_SIM = {"ok", "sim", "s", "confirma", "confirmo", "pode", "pode fazer", "faz", "manda", "isso", "beleza", "blz"}
_NAO = {"nao", "n", "cancela", "deixa", "esquece", "para"}


def _lista(nome: str | None, padrao: str = "estoque sb!") -> str:
    a = _ascii(nome or "")
    return next((v for k, v in LISTAS.items() if k in a), padrao if not a else a)


def _unico(rows: list[dict], rotulo, descricao: str) -> tuple[dict | None, dict | None]:
    if not rows:
        return None, {"erro": f"não achei {descricao}"}
    if len(rows) > 1:
        return None, {"erro": f"achei {len(rows)} {descricao}s: " + "; ".join(rotulo(r) for r in rows[:6])
                              + ". Me diga qual (nome do cliente, placa ou data)."}
    return rows[0], None


def _bate(texto: str, *campos) -> bool:
    t = _ascii(texto)
    alvo = _ascii(" ".join(str(c or "") for c in campos)).replace("-", "")
    return all(p in alvo for p in t.replace("-", "").split())


def _sujo(*mods) -> None:
    for m in mods:
        m.sujo["v"] = True


# ===== correções diretas =====
def corrigir_lead(termo: str, vendedor: str | None = None, data: str | None = None, horario: str | None = None,
                  status: str | None = None, tipo: str | None = None, observacao: str | None = None,
                  telefone: str | None = None) -> dict:
    from . import leads
    from .sheets import _converter
    rows = [l for l in db.select_all("leads", {"select": "*", "removido": "eq.false", "order": "recebido_em.desc"})
            if _bate(termo, l.get("cliente_nome"), l.get("veiculo"), l.get("telefone"))][:10]
    l, err = _unico(rows, lambda r: f"{r.get('cliente_nome')} ({r.get('veiculo')}, {r.get('data_agendada')})", "lead")
    if err:
        return err
    upd = {}
    for col, v in (("Vendedor", vendedor), ("Data", data), ("Horário", horario), ("Status", status), ("Tipo", tipo),
                   ("Observação", observacao), ("Telefone", telefone)):
        if v:
            upd.update(_converter(col, v))
    if not upd:
        return {"erro": "não entendi o que mudar no lead"}
    db.update("leads", upd, {"id": f"eq.{l['id']}"})
    _sujo(leads)
    return {"ok": True, "lead": f"{l.get('cliente_nome')} — {l.get('veiculo')}", "alterado": upd}


def corrigir_entrega(termo: str, data: str | None = None, horario: str | None = None,
                     observacao: str | None = None, entregue: bool | None = None) -> dict:
    from . import entregas_grupo
    rows = [e for e in db.select_all("entregas", {"select": "*", "removido": "eq.false", "origem": "in.(grupo,planilha)",
                                                  "order": "created_at.desc"})
            if _bate(termo, e.get("veiculo"), e.get("vendedor_nome"))][:10]
    abertas = [e for e in rows if e["status"] == "agendada"] or rows
    e, err = _unico(abertas, lambda r: f"{r.get('veiculo')} ({r.get('vendedor_nome')}, {r.get('data_texto')})", "entrega")
    if err:
        return err
    upd = {}
    if data:
        d = entregas_grupo._data_entrega(data, datetime.now().date())
        upd.update({"data_entrega": d, "data_texto": data})
    if horario:
        upd["horario"] = horario
    if observacao:
        upd["observacao"] = observacao
    if not upd and entregue is None:
        return {"erro": "não entendi o que mudar na entrega"}
    if upd:
        db.update("entregas", upd, {"id": f"eq.{e['id']}"})
        if e.get("venda_id") and "data_entrega" in upd:
            db.update("vendas", {"data_entrega_prevista": upd["data_entrega"], "data_entrega_texto": data},
                      {"id": f"eq.{e['venda_id']}"})
    if entregue:
        entregas_grupo.dar_baixa(e, datetime.now(timezone.utc))
    _sujo(entregas_grupo)
    return {"ok": True, "entrega": e.get("veiculo"), "alterado": {**upd, **({"entregue": True} if entregue else {})}}


def valor_avaliacao(termo: str, valor: str) -> dict:
    from . import avaliacoes_grupo
    rows = [a for a in db.select_all("avaliacoes", {"select": "*", "origem": "eq.grupo", "order": "avaliado_em.desc"})
            if _bate(termo, a.get("modelo"), a.get("placa"), a.get("ano"))][:10]
    a, err = _unico(rows, lambda r: f"{r.get('modelo')} {r.get('ano') or ''} ({(r.get('avaliado_em') or '')[:10]})",
                    "avaliação")
    if err:
        return err
    db.update("avaliacoes", {"valor_texto": valor, "valor_avaliacao": avaliacoes_grupo.valor_numerico(valor),
                             "valor_por": "Diogo", "valor_em": _iso(datetime.now(timezone.utc))}, {"id": f"eq.{a['id']}"})
    _sujo(avaliacoes_grupo)
    return {"ok": True, "avaliacao": f"{a.get('modelo')} {a.get('ano') or ''}", "valor": valor}


def placa_venda(termo: str, placa: str) -> dict:
    """Corrige a placa de uma venda e o nome do cartão no Trello."""
    from . import vendas_grupo
    nova = vendas_grupo.placa_norm(placa)
    if not nova:
        return {"erro": f"'{placa}' não parece uma placa"}
    rows = [v for v in db.select_all("vendas", {"select": "*", "removido": "eq.false", "order": "created_at.desc"})
            if _bate(termo, v.get("modelo"), v.get("versao"), v.get("placa"), v.get("cliente_nome"),
                     vendas_grupo._nome_vendedor(v) if v.get("vendedor_id") else "")][:10]
    v, err = _unico(rows, lambda r: f"{vendas_grupo._carro(r)} ({r.get('cliente_nome') or '?'})", "venda")
    if err:
        return err
    db.update("vendas", {"placa": nova}, {"id": f"eq.{v['id']}"})
    cartao = None
    if v.get("trello_card_id") and trello.configurado():
        with trello._cliente() as c:
            card = c.get(f"/cards/{v['trello_card_id']}", params={"fields": "name"}).json()
            fmt = f"({nova[:3]}-{nova[3:]})"
            nome = re.sub(r"\([A-Za-z]{3}-?\d[A-Za-z0-9]\d{2}\)", fmt, card["name"]) if trello._placa(card["name"]) \
                else re.sub(r"(\b(?:19|20)\d{2}\b)", rf"\1 {fmt}", card["name"], count=1)
            if nome != card["name"]:
                c.put(f"/cards/{v['trello_card_id']}", params={"name": nome}).raise_for_status()
                cartao = nome
    _sujo(vendas_grupo)
    return {"ok": True, "venda": vendas_grupo._carro({**v, "placa": nova}), "cartao_trello": cartao}


def tarefa_preparacao(carro: str, acao: str, descricao: str) -> dict:
    """acao: adicionar | concluir | remover (remover pede confirmação)."""
    from . import preparacao_grupo as pg
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c)
        card = trello.achar_cartao(carro, cards, listas)
        if not card:
            return {"erro": f"não achei o cartão de '{carro}' no Trello"}
        if acao == "adicionar":
            t = db.insert("prep_tarefas", {"message_id": "dono", "carro_texto": carro, "card_id": card["id"],
                                           "card_nome": card["name"], "tipo": "outro", "descricao": descricao,
                                           "responsavel": None, "criado_em": _iso(datetime.now(timezone.utc))})
            db.update("prep_tarefas", {"checklist_item_id": trello.item_checklist(c, card["id"], descricao)},
                      {"id": f"eq.{t['id']}"})
            return {"ok": True, "cartao": card["name"], "adicionado": descricao}
    tarefas = [t for t in db.select_all("prep_tarefas", {"select": "*", "card_id": f"eq.{card['id']}",
                                                          "status": "eq.pendente"})
               if pg._parecida(t["descricao"].lstrip("⚠️ "), descricao) or _bate(descricao, t["descricao"])]
    t, err = _unico(tarefas, lambda r: r["descricao"], f"tarefa '{descricao}' em {card['name']}")
    if err:
        return err
    if acao == "concluir":
        pg._concluir(t, datetime.now(timezone.utc), False)
        return {"ok": True, "cartao": card["name"], "concluido": t["descricao"]}
    return _pedir("remover_tarefa", {"tarefa_id": t["id"]}, f"tirar '{t['descricao']}' do checklist do {card['name']}")


def mover_cartao(carro: str, lista: str) -> dict:
    destino = _lista(lista)
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c)
        if destino not in listas.values():
            return {"erro": f"não conheço a lista '{lista}'. Listas: {', '.join(sorted(set(listas.values())))}"}
        card = trello.achar_cartao(carro, cards, listas)
        if not card:
            return {"erro": f"não achei o cartão de '{carro}' no Trello"}
    return _pedir("mover_cartao", {"card_id": card["id"], "destino": destino},
                  f"mover '{card['name']}' de {listas.get(card['idList'])} para {destino}")


def criar_cartao(nome: str, lista: str = "estoque") -> dict:
    destino = _lista(lista)
    return _pedir("criar_cartao", {"nome": nome, "destino": destino}, f"criar o cartão '{nome}' em {destino}")


# ===== confirmação =====
def _pedir(tipo: str, params: dict, descricao: str) -> dict:
    db.update("acoes_pendentes", {"status": "cancelada"}, {"status": "eq.pendente"})  # só uma esperando por vez
    db.insert("acoes_pendentes", {"tipo": tipo, "params": params, "descricao": descricao})
    return {"precisa_confirmar": True, "acao": descricao,
            "instrucao": "Peça ao dono para responder 'ok' para confirmar ou 'não' para cancelar."}


def tentar_confirmar(texto: str) -> str | None:
    """'ok' / 'não' logo depois de um pedido de confirmação. None = não é resposta a uma ação pendente."""
    desde = _iso(datetime.now(timezone.utc) - timedelta(minutes=30))
    pend = db.select("acoes_pendentes", {"select": "*", "status": "eq.pendente", "created_at": f"gte.{desde}",
                                         "order": "created_at.desc", "limit": "1"})
    if not pend:
        return None
    r = _ascii(texto).strip(" .!")
    a = pend[0]
    if r in _NAO:
        db.update("acoes_pendentes", {"status": "cancelada"}, {"id": f"eq.{a['id']}"})
        return f"Ok, não fiz: {a['descricao']}."
    if r not in _SIM:
        return None
    try:
        _executar(a)
    except Exception as e:
        log.exception("falha executando ação pendente")
        return f"Não consegui {a['descricao']}: {e}"
    db.update("acoes_pendentes", {"status": "feita"}, {"id": f"eq.{a['id']}"})
    return f"✅ Feito: {a['descricao']}."


def _executar(a: dict) -> None:
    p = a["params"]
    if a["tipo"] == "remover_tarefa":
        t = db.select("prep_tarefas", {"select": "*", "id": f"eq.{p['tarefa_id']}"})[0]
        if t.get("card_id") and t.get("checklist_item_id"):
            with trello._cliente() as c:
                trello.remover_item(c, t["card_id"], t["checklist_item_id"])
        db.delete("prep_tarefas", {"id": f"eq.{t['id']}"})
        return
    with trello._cliente() as c:
        _, listas = trello.cartoes(c)
        id_lista = next(k for k, v in listas.items() if v == p["destino"])
        if a["tipo"] == "mover_cartao":
            c.put(f"/cards/{p['card_id']}", params={"idList": id_lista, "pos": "top"}).raise_for_status()
        elif a["tipo"] == "criar_cartao":
            c.post("/cards", params={"idList": id_lista, "name": p["nome"], "pos": "top"}).raise_for_status()
