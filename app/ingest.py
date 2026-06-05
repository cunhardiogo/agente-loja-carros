from datetime import datetime, timedelta

from . import db, evolution, llm
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


def _venda_duplicada(veiculo_desc: str | None, cliente: str | None) -> dict | None:
    """Dedup simples: venda do mesmo cliente nas últimas 48h."""
    if not cliente:
        return None
    desde = (datetime.utcnow() - timedelta(hours=48)).isoformat()
    rows = db.select("vendas", {
        "select": "id,cliente_nome,created_at",
        "cliente_nome": f"ilike.{cliente}",
        "created_at": f"gte.{desde}",
        "limit": "1",
    })
    return rows[0] if rows else None


def _gravar_dominio(ext: Extracao) -> tuple[str | None, str | None]:
    """Insere na tabela de domínio conforme o tipo. Retorna (tabela, id)."""
    if ext.tipo_evento == TipoEvento.venda:
        if _venda_duplicada(ext.veiculo_descricao, ext.cliente_nome):
            return None, None
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        row = db.insert("vendas", {
            "vendedor_id": vend["id"] if vend else None,
            "cliente_nome": ext.cliente_nome,
            "valor_venda": ext.valor,
            "forma_pagamento": ext.forma_pagamento,
            "data_venda": ext.data_evento,
            "status_pagamento": ext.status_pagamento or "pendente",
            "status_entrega": ext.status_entrega or "pendente",
            "observacoes": (ext.veiculo_descricao or "") + ((" | " + ext.resumo) if ext.resumo else ""),
        })
        return "vendas", row.get("id")

    if ext.tipo_evento == TipoEvento.agendamento:
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

    if ext.tipo_evento == TipoEvento.anuncio:
        row = db.insert("veiculos", {
            "marca": ext.marca,
            "modelo": ext.modelo,
            "ano": ext.ano,
            "cor": ext.cor,
            "preco_anuncio": ext.valor,
            "status": "anunciado",
            "data_anuncio": ext.data_evento,
            "observacoes": ext.veiculo_descricao or ext.resumo,
        })
        return "veiculos", row.get("id")

    # comparecimento / pagamento / entrega: atualizam registros existentes — Fase 2/5.
    return None, None


def processar(grupo: dict, message_id: str | None, remetente: str | None,
              remetente_nome: str | None, texto: str, timestamp_msg: str | None) -> dict:
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    ext = llm.extrair(texto, grupo["nome"], grupo.get("tipo"), vendedores)

    tabela = registro_id = None
    status = "auto"

    if ext.tipo_evento != TipoEvento.nenhum:
        if ext.confianca >= settings.confianca_minima:
            tabela, registro_id = _gravar_dominio(ext)
            status = "auto" if registro_id else "descartado"
        else:
            status = "pendente_confirmacao"
            evolution.notificar_dono(
                f"❓ Confirmar evento ({ext.tipo_evento.value}, {int(ext.confianca*100)}%):\n"
                f"{ext.resumo or texto}\n\nResponda: sim / não / corrigir"
            )

    evento = db.insert("eventos_brutos", {
        "grupo_id": grupo["id"],
        "message_id": message_id,
        "remetente": remetente,
        "remetente_nome": remetente_nome,
        "mensagem_original": texto,
        "timestamp_msg": timestamp_msg,
        "tipo_evento": ext.tipo_evento.value,
        "dados_extraidos": ext.model_dump(mode="json"),
        "confianca": ext.confianca,
        "status": status,
        "registro_tabela": tabela,
        "registro_id": registro_id,
    })
    return {"evento_id": evento.get("id"), "tipo": ext.tipo_evento.value,
            "confianca": ext.confianca, "status": status, "registro_id": registro_id}
