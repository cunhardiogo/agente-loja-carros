from datetime import timedelta

from . import datas, db, evolution, llm
from .config import settings
from .schemas import Extracao, TipoEvento


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def grupo_por_jid(jid: str) -> dict | None:
    rows = db.select("grupos", {"jid": f"eq.{jid}", "ativo": "eq.true", "limit": "1"})
    return rows[0] if rows else None


def resolver_pessoa(nome: str | None, funcao: str | None = None) -> dict | None:
    if not nome:
        return None
    n = _norm(nome)
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    if funcao:
        vendedores = [v for v in vendedores if v["funcao"] == funcao] or vendedores
    for v in vendedores:
        apel = [_norm(a) for a in (v.get("apelidos") or [])]
        if _norm(v["nome"]) == n or n in apel:
            return v
    for v in vendedores:
        if n and (n in _norm(v["nome"]) or _norm(v["nome"]) in n):
            return v
    return None


def ja_processada(message_id: str | None) -> bool:
    if not message_id:
        return False
    return bool(db.select("eventos_brutos", {"message_id": f"eq.{message_id}", "limit": "1"}))


def _venda_duplicada(cliente: str | None) -> bool:
    if not cliente:
        return False
    desde = (datas.agora() - timedelta(hours=48)).isoformat()
    rows = db.select("vendas", {
        "select": "id", "cliente_nome": f"ilike.*{cliente}*",
        "created_at": f"gte.{desde}", "limit": "1",
    })
    return bool(rows)


def _venda_pendente(cliente: str | None, campo_status: str) -> dict | None:
    if not cliente:
        return None
    rows = db.select("vendas", {
        "select": "id,cliente_nome", campo_status: "eq.pendente",
        "cliente_nome": f"ilike.*{cliente}*", "order": "created_at.desc", "limit": "1",
    })
    return rows[0] if rows else None


def _agendamento_recente(cliente: str | None) -> dict | None:
    if not cliente:
        return None
    rows = db.select("agendamentos", {
        "select": "id,cliente_nome", "cliente_nome": f"ilike.*{cliente}*",
        "order": "data_agendada.desc.nullslast", "limit": "1",
    })
    return rows[0] if rows else None


# ===== ações (INSERT/UPDATE). Retornam (tabela, registro_id). =====
def aplicar(ext: Extracao) -> tuple[str | None, str | None]:
    t = ext.tipo_evento

    if t == TipoEvento.venda:
        if _venda_duplicada(ext.cliente_nome):
            return "duplicada", None
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        row = db.insert("vendas", {
            "vendedor_id": vend["id"] if vend else None,
            "cliente_nome": ext.cliente_nome,
            "valor_venda": ext.valor,
            "forma_pagamento": ext.forma_pagamento,
            "data_venda": ext.data_evento or datas.hoje_iso(),
            "status_pagamento": ext.status_pagamento or "pendente",
            "status_entrega": ext.status_entrega or "pendente",
            "data_entrega_prevista": ext.data_entrega,
            "observacoes": (ext.veiculo_descricao or "") + ((" | " + ext.resumo) if ext.resumo else ""),
        })
        return "vendas", row.get("id")

    if t == TipoEvento.agendamento:
        sdr = resolver_pessoa(ext.sdr_nome, "sdr")
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        row = db.insert("agendamentos", {
            "cliente_nome": ext.cliente_nome,
            "sdr_id": sdr["id"] if sdr else None,
            "vendedor_id": vend["id"] if vend else None,
            "data_agendada": ext.data_agendada,
            "observacoes": ext.resumo,
        })
        return "agendamentos", row.get("id")

    if t == TipoEvento.anuncio:
        row = db.insert("veiculos", {
            "marca": ext.marca, "modelo": ext.modelo, "ano": ext.ano, "cor": ext.cor,
            "preco_anuncio": ext.valor, "status": "anunciado",
            "data_anuncio": ext.data_evento or datas.hoje_iso(),
            "observacoes": ext.veiculo_descricao or ext.resumo,
        })
        return "veiculos", row.get("id")

    if t == TipoEvento.pagamento:
        v = _venda_pendente(ext.cliente_nome, "status_pagamento")
        if not v:
            return None, None
        db.update("vendas", {"status_pagamento": ext.status_pagamento or "pago"}, {"id": f"eq.{v['id']}"})
        return "vendas", v["id"]

    if t == TipoEvento.entrega:
        v = _venda_pendente(ext.cliente_nome, "status_entrega")
        if not v:
            return None, None
        db.update("vendas", {"status_entrega": "entregue", "data_entrega_real": datas.hoje_iso()},
                  {"id": f"eq.{v['id']}"})
        return "vendas", v["id"]

    if t == TipoEvento.comparecimento:
        a = _agendamento_recente(ext.cliente_nome)
        if not a:
            return None, None
        compareceu = ext.compareceu if ext.compareceu is not None else True
        db.update("agendamentos", {"compareceu": compareceu}, {"id": f"eq.{a['id']}"})
        return "agendamentos", a["id"]

    return None, None


def reextrair(mensagem_original: str, correcao: str) -> Extracao:
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    texto = f"Mensagem original: {mensagem_original}\nCorreção informada pelo usuário: {correcao}\n" \
            f"Gere o evento já com a correção aplicada."
    return llm.extrair(texto, "correção", None, vendedores)


def _pergunta_confirmacao(ext: Extracao) -> str:
    return (f"❓ Confirma este registro?\n"
            f"• Tipo: {ext.tipo_evento.value}\n"
            f"• {ext.resumo or ext.veiculo_descricao or '(sem resumo)'}\n\n"
            f"Responda: *sim* / *não* / *corrigir ...*")


def processar(grupo: dict, message_id: str | None, remetente: str | None,
              remetente_nome: str | None, texto: str, timestamp_msg: str | None) -> dict:
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    ext = llm.extrair(texto, grupo["nome"], grupo.get("tipo"), vendedores)

    tabela = registro_id = None
    status = "auto"

    if ext.tipo_evento != TipoEvento.nenhum:
        if ext.confianca < settings.confianca_minima:
            status = "pendente_confirmacao"
            evolution.notificar_dono(_pergunta_confirmacao(ext))
        else:
            tabela, registro_id = aplicar(ext)
            if tabela == "duplicada":
                tabela, status = None, "descartado"
            elif registro_id:
                status = "auto"
            else:
                status = "pendente_confirmacao"
                evolution.notificar_dono(_pergunta_confirmacao(ext))

    evento = db.insert("eventos_brutos", {
        "grupo_id": grupo["id"], "message_id": message_id,
        "remetente": remetente, "remetente_nome": remetente_nome,
        "mensagem_original": texto, "timestamp_msg": timestamp_msg,
        "tipo_evento": ext.tipo_evento.value, "dados_extraidos": ext.model_dump(mode="json"),
        "confianca": ext.confianca, "status": status,
        "registro_tabela": tabela, "registro_id": registro_id,
    })
    return {"evento_id": evento.get("id"), "tipo": ext.tipo_evento.value,
            "confianca": ext.confianca, "status": status, "registro_id": registro_id}
