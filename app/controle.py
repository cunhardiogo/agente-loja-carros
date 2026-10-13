"""Mais ações do dono por mensagem: criar e apagar registros, consultar/mexer no Trello, liga/desliga
de automações, descrição do grupo de vendas e mensagem enviada pelo número dele. O que apaga ou manda
mensagem fica pendente até o "ok" (acoes._pedir)."""
import re
from datetime import datetime, timezone

from . import db, trello
from .acoes import _alterar, _bate, _criado, _lista, _marcar_sujo, _pedir, _unico
from .leads import _ascii, _iso


# ===== criar registros =====
def _venda_de(termo: str) -> tuple[dict | None, dict | None]:
    from . import vendas_grupo
    rows = [v for v in db.select_all("vendas", {"select": "*", "removido": "eq.false", "order": "created_at.desc"})
            if _bate(termo, v.get("modelo"), v.get("versao"), v.get("placa"), v.get("cliente_nome"),
                     vendas_grupo._nome_vendedor(v) if v.get("vendedor_id") else "")][:10]
    return _unico(rows, lambda r: f"{vendas_grupo._carro(r)} ({r.get('cliente_nome') or '?'}, {r.get('data_venda')})",
                  "venda")


def _dia(data: str | None):
    from . import datas
    from .leads import _data
    return _data(data, datas.hoje()) if data else datas.hoje()


def criar_venda(vendedor: str, carro: str, ano: int | None = None, placa: str | None = None,
                cliente: str | None = None, valor: str | None = None, data: str | None = None,
                reserva: bool = False, revenda: bool = False, forma_pagamento: str | None = None,
                canal: str | None = None) -> dict:
    from . import datas, vendas_grupo
    vend = vendas_grupo.vendedor_por_nome(vendedor)
    if not vend:
        return {"erro": f"não conheço o vendedor '{vendedor}'"}
    d = _dia(data)
    if not d:
        return {"erro": f"não entendi a data '{data}'"}
    row = {"vendedor_id": vend["id"], "modelo": carro, "ano": ano, "placa": vendas_grupo.placa_norm(placa),
           "cliente_nome": cliente, "valor_total": vendas_grupo._valor(valor) if valor else None,
           "forma_pagamento": forma_pagamento, "portal_venda": canal, "revenda": revenda, "origem": "planilha",
           "status_venda": "reservado" if reserva else "aguardando_resumo", "historico": False}
    if reserva:
        row["reservado_em"] = _iso(datetime(d.year, d.month, d.day, 12, tzinfo=datas.TZ))
    else:
        row["data_venda"] = d.isoformat()
    row["pendencias"] = vendas_grupo.pendencias_venda(row)
    v = db.insert("vendas", row)
    desc = " ".join(str(x) for x in ("reserva" if reserva else "venda", carro, ano or "", "—", vend["nome"]) if x != "")
    _criado("vendas", v, f"criar {desc}")
    vendas_grupo.sujo["v"] = True
    return {"ok": True, "criado": desc, "pendencias": row["pendencias"]}


def criar_lead(cliente: str, vendedor: str | None = None, carro: str | None = None, telefone: str | None = None,
               data: str | None = None, horario: str | None = None, tipo: str = "Visita",
               observacao: str | None = None) -> dict:
    from . import datas, leads
    from .sheets import _converter
    agora = datetime.now(timezone.utc)
    row = {"message_id": f"dono-{agora.timestamp():.0f}", "autor": "dono", "cliente_nome": cliente,
           "veiculo": carro, "recebido_em": _iso(agora), "historico": False, "data_agendada": datas.hoje_iso()}
    for col, v in (("Vendedor", vendedor), ("Data", data), ("Horário", horario), ("Tipo", tipo),
                   ("Observação", observacao), ("Telefone", telefone)):
        if v:
            row.update(_converter(col, v))
    l = db.insert("leads", row)
    _criado("leads", l, f"criar lead {cliente} ({carro or 'sem carro'})")
    leads.sujo["v"] = True
    return {"ok": True, "lead": cliente,
            "dados": {k: l.get(k) for k in ("vendedor_nome", "data_agendada", "horario", "tipo", "status")}}


def criar_entrega(carro: str, vendedor: str | None = None, data: str | None = None, horario: str | None = None,
                  observacao: str | None = None, placa: str | None = None) -> dict:
    from . import entregas_grupo, vendas_grupo
    vend = vendas_grupo.vendedor_por_nome(vendedor) if vendedor else None
    row = {"veiculo": carro, "placa": vendas_grupo.placa_norm(placa), "vendedor_id": (vend or {}).get("id"),
           "vendedor_nome": (vend or {}).get("nome") or vendedor, "horario": horario, "observacao": observacao,
           "data_texto": data, "origem": "planilha", "status": "agendada", "historico": False,
           "data_entrega": entregas_grupo._data_entrega(data, datetime.now().date()) if data else None}
    e = db.insert("entregas", row)
    _criado("entregas", e, f"criar entrega {carro}")
    entregas_grupo.sujo["v"] = True
    return {"ok": True, "entrega": carro, "data": row["data_entrega"]}


def registrar_pagamento(venda: str, valor: str, data: str | None = None, forma: str | None = None) -> dict:
    from . import datas, vendas_grupo
    v, err = _venda_de(venda)
    if err:
        return err
    d = _dia(data)
    if not d:
        return {"erro": f"não entendi a data '{data}'"}
    p = db.insert("pagamentos", {"venda_id": v["id"], "valor": vendas_grupo._valor(valor), "tipo": forma,
                                 "pago_em": _iso(datetime(d.year, d.month, d.day, 12, tzinfo=datas.TZ)),
                                 "autor": "dono", "recebido_em": _iso(datetime.now(timezone.utc)), "historico": False})
    vendas_grupo.recalcular(v["id"])
    _criado("pagamentos", p, f"pagamento {valor} na venda {vendas_grupo._carro(v)}")
    vendas_grupo.sujo["v"] = True
    return {"ok": True, "venda": vendas_grupo._carro(v), "valor": p["valor"]}


def criar_avaliacao(carro: str, ano: int | None = None, km: str | None = None, fipe: str | None = None,
                    valor: str | None = None, vendedor: str | None = None, placa: str | None = None,
                    cliente_quer: str | None = None) -> dict:
    from . import avaliacoes_grupo, vendas_grupo
    vend = vendas_grupo.vendedor_por_nome(vendedor) if vendedor else None
    agora = datetime.now(timezone.utc)
    km_n = vendas_grupo._valor(km) if km else None
    row = {"message_id": f"dono-{agora.timestamp():.0f}", "origem": "grupo", "autor": "dono", "modelo": carro,
           "ano": ano, "km": int(km_n) if km_n else None, "fipe": vendas_grupo._valor(fipe) if fipe else None,
           "placa": vendas_grupo.placa_norm(placa), "vendedor_id": (vend or {}).get("id"),
           "carro_interesse": cliente_quer, "avaliado_em": _iso(agora), "historico": True, "aviso_em": _iso(agora)}
    if valor:
        row.update({"valor_texto": valor, "valor_avaliacao": avaliacoes_grupo.valor_numerico(valor),
                    "valor_por": "Diogo", "valor_em": _iso(agora)})
    a = db.insert("avaliacoes", row)
    _criado("avaliacoes", a, f"criar avaliação {carro} {ano or ''}".strip())
    avaliacoes_grupo.sujo["v"] = True
    return {"ok": True, "avaliacao": f"{carro} {ano or ''}".strip()}


# ===== apagar (pede ok) =====
def apagar(tipo: str, termo: str) -> dict:
    """tipo: venda | lead | entrega | avaliacao | recall | pagamento."""
    from . import recall_grupo, vendas_grupo
    t = _ascii(tipo)
    if t.startswith("pag") or t.startswith("compro"):
        v, err = _venda_de(termo)
        if err:
            return err
        pags = db.select("pagamentos", {"select": "*", "venda_id": f"eq.{v['id']}", "order": "pago_em.desc"})
        r, err = _unico(pags, lambda x: f"R$ {x.get('valor')} em {(x.get('pago_em') or '')[:10]}", "pagamento")
        if err:
            return err
        return _pedir("apagar", {"tabela": "pagamentos", "id": r["id"]},
                      f"apagar o pagamento de R$ {r.get('valor')} ({(r.get('pago_em') or '')[:10]}) "
                      f"da venda {vendas_grupo._carro(v)}")
    if t.startswith("vend") or t.startswith("reserv"):
        r, err = _venda_de(termo)
        tabela, rot = "vendas", f"a venda {vendas_grupo._carro(r or {})} ({(r or {}).get('cliente_nome') or '?'})"
    elif t.startswith("lead") or t.startswith("agend"):
        rows = [l for l in db.select_all("leads", {"select": "*", "removido": "eq.false", "order": "recebido_em.desc"})
                if _bate(termo, l.get("cliente_nome"), l.get("veiculo"), l.get("telefone"))][:10]
        r, err = _unico(rows, lambda x: f"{x.get('cliente_nome')} ({x.get('veiculo')}, {x.get('data_agendada')})", "lead")
        tabela, rot = "leads", f"o lead {(r or {}).get('cliente_nome')} ({(r or {}).get('veiculo')})"
    elif t.startswith("entreg"):
        rows = [e for e in db.select_all("entregas", {"select": "*", "removido": "eq.false", "order": "created_at.desc"})
                if _bate(termo, e.get("veiculo"), e.get("vendedor_nome"), e.get("placa"))][:10]
        r, err = _unico(rows, lambda x: f"{x.get('veiculo')} ({x.get('vendedor_nome')}, {x.get('data_texto')})", "entrega")
        tabela, rot = "entregas", f"a entrega {(r or {}).get('veiculo')}"
    elif t.startswith("avali"):
        rows = [a for a in db.select_all("avaliacoes", {"select": "*", "origem": "eq.grupo", "order": "avaliado_em.desc"})
                if a.get("status") != "removida" and _bate(termo, a.get("modelo"), a.get("placa"), a.get("ano"))][:10]
        r, err = _unico(rows, lambda x: f"{x.get('modelo')} {x.get('ano') or ''} ({(x.get('avaliado_em') or '')[:10]})",
                        "avaliação")
        tabela, rot = "avaliacoes", f"a avaliação {(r or {}).get('modelo')} {(r or {}).get('ano') or ''}"
    elif t.startswith("recall"):
        rows = [x for x in recall_grupo._abertos() if _bate(termo, x.get("veiculo"), x.get("placa"))]
        r, err = _unico(rows, recall_grupo._rotulo, "recall")
        tabela, rot = "recalls", f"o recall {recall_grupo._rotulo(r or {})}"
    else:
        return {"erro": "tipo deve ser venda, lead, entrega, avaliação, recall ou pagamento"}
    if err:
        return err
    return _pedir("apagar", {"tabela": tabela, "id": r["id"]}, f"apagar {rot}")


def executar_apagar(p: dict) -> None:
    tabela, rid = p["tabela"], p["id"]
    row = db.select(tabela, {"select": "*", "id": f"eq.{rid}"})[0]
    if tabela == "pagamentos":
        db.delete("pagamentos", {"id": f"eq.{rid}"})
        if row.get("venda_id"):
            from .vendas_grupo import recalcular
            recalcular(row["venda_id"])
    else:
        upd = {"status": "cancelado"} if tabela == "recalls" else \
            {"status": "removida"} if tabela == "avaliacoes" else {"removido": True}
        nome = row.get("veiculo") or row.get("modelo") or row.get("cliente_nome") or ""
        _alterar(tabela, row, upd, f"apagar {tabela.rstrip('s')} {nome}".strip())
    _marcar_sujo(tabela)


# ===== Trello =====
def _dias_no_quadro(card_id: str) -> int:
    criado = datetime.fromtimestamp(int(card_id[:8], 16), timezone.utc)  # o id do Trello carrega a data de criação
    return (datetime.now(timezone.utc) - criado).days


def _quadro(c) -> tuple[list[dict], dict]:
    from .config import settings
    cards = c.get(f"/boards/{settings.trello_board}/cards", params={"fields": "name,idList,due,badges"}).json()
    listas = {l["id"]: _ascii(l["name"]).strip() for l in c.get(f"/boards/{settings.trello_board}/lists").json()}
    return cards, listas


def consultar_trello(lista: str | None = None, carro: str | None = None) -> dict:
    """Sem nada: quantos carros por lista. Com lista: os carros dela. Com carro: o cartão com checklists."""
    with trello._cliente() as c:
        cards, listas = _quadro(c)
        if carro:
            card = trello.achar_cartao(carro, cards, listas, todas=True)
            if not card:
                return {"erro": f"não achei um cartão único para '{carro}'"}
            chks = c.get(f"/cards/{card['id']}/checklists").json()
            return {"cartao": card["name"], "lista": listas.get(card["idList"]), "prazo": card.get("due"),
                    "dias_no_quadro": _dias_no_quadro(card["id"]),
                    "checklists": {k["name"]: [("✅ " if i["state"] == "complete" else "⬜ ") + i["name"]
                                               for i in k["checkItems"]] for k in chks}}
    if lista:
        destino = _lista(lista)
        sel = [cd for cd in cards if listas.get(cd["idList"]) == destino]
        return {"lista": destino, "quantidade": len(sel), "carros": [
            {"cartao": cd["name"], "dias_no_quadro": _dias_no_quadro(cd["id"]), "prazo": cd.get("due"),
             "checklist": f"{cd['badges'].get('checkItemsChecked', 0)}/{cd['badges']['checkItems']}"
             if cd["badges"].get("checkItems") else None} for cd in sel]}
    por: dict[str, int] = {}
    for cd in cards:
        nome = listas.get(cd["idList"], "?")
        por[nome] = por.get(nome, 0) + 1
    return {"carros_por_lista": por}


def _um_cartao(c, carro: str) -> dict | None:
    cards, listas = trello.cartoes(c)
    return trello.achar_cartao(carro, cards, listas, todas=True)


def renomear_cartao(carro: str, nome: str) -> dict:
    with trello._cliente() as c:
        card = _um_cartao(c, carro)
        if not card:
            return {"erro": f"não achei um cartão único para '{carro}'"}
        c.put(f"/cards/{card['id']}", params={"name": nome}).raise_for_status()
    return {"ok": True, "antes": card["name"], "agora": nome}


def prazo_cartao(carro: str, data: str | None = None, horario: str | None = None) -> dict:
    """Sem data: tira o prazo."""
    from . import datas
    due = ""
    if data:
        d = _dia(data)
        m = re.match(r"(\d{1,2})(?:\D+(\d{2}))?", horario or "9")
        if not d or not m:
            return {"erro": "não entendi a data/horário"}
        due = _iso(datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2) or 0), tzinfo=datas.TZ))
    with trello._cliente() as c:
        card = _um_cartao(c, carro)
        if not card:
            return {"erro": f"não achei um cartão único para '{carro}'"}
        c.put(f"/cards/{card['id']}", params={"due": due or "null"}).raise_for_status()
    return {"ok": True, "cartao": card["name"], "prazo": due or "sem prazo"}


# ===== automações e descrição =====
def automacoes() -> dict:
    from . import flags
    return {"automacoes": flags.situacao()}


def automacao(nome: str, ligar: bool) -> dict:
    from . import flags
    palavras = [p for p in re.split(r"[\s_]+", _ascii(nome)) if len(p) >= 3]
    achados = [n for n, (_, d) in flags.AUTOMACOES.items()
               if _ascii(nome).replace(" ", "_") == n or (palavras and all(p in f"{n} {_ascii(d)}" for p in palavras))]
    if len(achados) != 1:
        return {"erro": "qual automação? diga uma das opções", "opcoes": flags.situacao()}
    flags.definir(achados[0], ativo=ligar)
    return {"ok": True, "automacao": achados[0], "o_que_faz": flags.AUTOMACOES[achados[0]][1], "ligada": ligar}


def descricao_vendas(acao: str = "ver", texto: str | None = None) -> dict:
    """ver | recado (texto fixo no fim da descrição) | limpar_recado."""
    from . import flags, vendas_grupo
    if acao in ("recado", "limpar_recado"):
        flags.definir("descricao_vendas_texto", valor=(texto or "") if acao == "recado" else "")
        vendas_grupo.atualizar_descricao(forcar=True)
    return {"descricao": vendas_grupo.texto_ranking(), "atualiza_sozinha": flags.ativo("descricao_vendas")}


# ===== mandar mensagem pelo número do dono (pede ok) =====
def enviar_mensagem(para: str, texto: str) -> dict:
    """para: alguém da equipe, nome de grupo, ou número."""
    from . import vendas_grupo
    num = re.sub(r"\D", "", para or "")
    if len(num) >= 10:
        destino, nome = (num if num.startswith("55") else f"55{num}"), para
    else:
        v = vendas_grupo.vendedor_por_nome(para)
        if v and v.get("telefone"):
            destino, nome = v["telefone"], v["nome"]
        else:
            a = _ascii(para)
            gs = [g for g in db.select("grupos", {"select": "jid,nome"}) if a and a in _ascii(g["nome"])]
            if len(gs) != 1:
                return {"erro": f"não sei pra quem é '{para}'",
                        "grupos": [g["nome"] for g in db.select("grupos", {"select": "nome"})]}
            destino, nome = gs[0]["jid"], gs[0]["nome"]
    return _pedir("enviar_mensagem", {"destino": destino, "texto": texto},
                  f"mandar pelo seu número para {nome}:\n\n{texto}")
