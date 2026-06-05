import json
from datetime import date, timedelta

import httpx
from openai import OpenAI

from . import db
from .config import settings
from .ingest import resolver_pessoa

_http = httpx.Client(verify=settings.verify_ssl, timeout=60)
_client = OpenAI(api_key=settings.openai_api_key, http_client=_http) if settings.openai_api_key else None


# ===== helpers =====
def _range(periodo: str | None):
    hoje = date.today()
    p = (periodo or "mes").lower()
    if p == "hoje":
        return hoje.isoformat(), hoje.isoformat()
    if p == "ontem":
        d = (hoje - timedelta(days=1)).isoformat()
        return d, d
    if p == "semana":
        ini = hoje - timedelta(days=hoje.weekday())
        return ini.isoformat(), hoje.isoformat()
    if p == "mes":
        return hoje.replace(day=1).isoformat(), hoje.isoformat()
    return None, None  # tudo


def _dentro(valor_data: str | None, ini: str | None, fim: str | None) -> bool:
    if ini is None:
        return True
    if not valor_data:
        return False
    d = valor_data[:10]
    return ini <= d <= fim


def _nome_vendedor(vid, cache):
    if not vid:
        return "—"
    return cache.get(vid, "—")


# ===== ferramentas de consulta =====
def resumo_vendas(periodo: str = "mes", vendedor: str | None = None) -> dict:
    ini, fim = _range(periodo)
    vid = None
    if vendedor:
        v = resolver_pessoa(vendedor, "vendedor")
        vid = v["id"] if v else None
        if vendedor and not vid:
            return {"erro": f"Vendedor '{vendedor}' não encontrado no cadastro."}
    rows = db.select("vendas", {"select": "valor_venda,data_venda,vendedor_id"})
    rows = [r for r in rows if _dentro(r.get("data_venda"), ini, fim) and (not vid or r.get("vendedor_id") == vid)]
    total = sum((r.get("valor_venda") or 0) for r in rows)
    n = len(rows)
    return {
        "periodo": periodo, "vendedor": vendedor or "todos",
        "quantidade": n, "valor_total": total,
        "ticket_medio": round(total / n, 2) if n else 0,
    }


def ranking_vendedores(periodo: str = "mes") -> dict:
    ini, fim = _range(periodo)
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = db.select("vendas", {"select": "valor_venda,data_venda,vendedor_id"})
    rows = [r for r in rows if _dentro(r.get("data_venda"), ini, fim)]
    agg: dict = {}
    for r in rows:
        vid = r.get("vendedor_id")
        a = agg.setdefault(vid, {"vendedor": _nome_vendedor(vid, nomes), "quantidade": 0, "valor_total": 0})
        a["quantidade"] += 1
        a["valor_total"] += r.get("valor_venda") or 0
    ranking = sorted(agg.values(), key=lambda x: x["valor_total"], reverse=True)
    return {"periodo": periodo, "ranking": ranking}


def listar_carros(status: str = "anunciado") -> dict:
    rows = db.select("veiculos", {
        "select": "marca,modelo,ano,cor,preco_anuncio",
        "status": f"eq.{status}",
        "order": "preco_anuncio.desc.nullslast",
        "limit": "40",
    })
    return {"status": status, "quantidade": len(rows), "carros": rows}


def pendencias(tipo: str) -> dict:
    if tipo == "pagamento":
        rows = db.select("vendas", {
            "select": "cliente_nome,valor_venda,data_venda,observacoes",
            "status_pagamento": "eq.pendente",
            "order": "data_venda.asc.nullsfirst",
        })
        total = sum((r.get("valor_venda") or 0) for r in rows)
        return {"tipo": "pagamento", "quantidade": len(rows), "valor_total_a_receber": total, "itens": rows}
    rows = db.select("vendas", {
        "select": "cliente_nome,valor_venda,data_entrega_prevista,observacoes",
        "status_entrega": "eq.pendente",
        "order": "data_entrega_prevista.asc.nullsfirst",
    })
    return {"tipo": "entrega", "quantidade": len(rows), "itens": rows}


def resumo_agendamentos(periodo: str = "semana", compareceu: bool | None = None) -> dict:
    ini, fim = _range(periodo)
    rows = db.select("agendamentos", {"select": "cliente_nome,data_agendada,compareceu"})
    rows = [r for r in rows if _dentro(r.get("data_agendada"), ini, fim)]
    total = len(rows)
    vieram = sum(1 for r in rows if r.get("compareceu") is True)
    faltaram = sum(1 for r in rows if r.get("compareceu") is False)
    sem_info = sum(1 for r in rows if r.get("compareceu") is None)
    if compareceu is not None:
        rows = [r for r in rows if r.get("compareceu") is compareceu]
    return {
        "periodo": periodo, "total": total, "compareceram": vieram,
        "faltaram": faltaram, "sem_info": sem_info,
        "taxa_comparecimento": round(vieram / total * 100, 1) if total else 0,
        "itens": rows,
    }


DISPATCH = {
    "resumo_vendas": resumo_vendas,
    "ranking_vendedores": ranking_vendedores,
    "listar_carros": listar_carros,
    "pendencias": pendencias,
    "resumo_agendamentos": resumo_agendamentos,
}

_PERIODO = {"type": "string", "enum": ["hoje", "ontem", "semana", "mes", "tudo"]}

TOOLS = [
    {"type": "function", "function": {
        "name": "resumo_vendas",
        "description": "Total de vendas, valor somado e ticket médio num período. Pode filtrar por um vendedor.",
        "parameters": {"type": "object", "properties": {
            "periodo": _PERIODO, "vendedor": {"type": "string", "description": "Nome do vendedor (opcional)"}}},
    }},
    {"type": "function", "function": {
        "name": "ranking_vendedores",
        "description": "Ranking dos vendedores por valor vendido num período (quantas vendas e quanto cada um).",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO}},
    }},
    {"type": "function", "function": {
        "name": "listar_carros",
        "description": "Lista carros do estoque por status, com preços.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["anunciado", "reservado", "vendido", "entregue"]}}},
    }},
    {"type": "function", "function": {
        "name": "pendencias",
        "description": "Carros/vendas pendentes: 'pagamento' (a receber) ou 'entrega' (a entregar).",
        "parameters": {"type": "object", "properties": {
            "tipo": {"type": "string", "enum": ["pagamento", "entrega"]}}, "required": ["tipo"]},
    }},
    {"type": "function", "function": {
        "name": "resumo_agendamentos",
        "description": "Agendamentos num período e taxa de comparecimento. compareceu=false lista quem faltou.",
        "parameters": {"type": "object", "properties": {
            "periodo": _PERIODO, "compareceu": {"type": "boolean"}}},
    }},
]

SYSTEM = """Você é o assistente da Loja SB (revenda de carros) respondendo o DONO no WhatsApp.
Use SEMPRE as ferramentas para buscar dados reais — nunca invente números.
Responda curto e direto, em português, formatando valores em reais como R$ 95.000.
Para rankings/listas, use linhas curtas com emojis discretos. Se não houver dados, diga que não encontrou nada no período.
Quando o usuário não especificar o período, assuma o mês atual."""


def responder(pergunta: str) -> str:
    if _client is None:
        return "IA não configurada (falta OPENAI_API_KEY)."
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": pergunta}]
    for _ in range(5):
        resp = _client.chat.completions.create(
            model=settings.openai_model_consulta, messages=messages, tools=TOOLS,
            tool_choice="auto", temperature=0,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or "Não consegui montar a resposta."
        messages.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                resultado = DISPATCH[tc.function.name](**args)
            except Exception as e:
                resultado = {"erro": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(resultado, default=str, ensure_ascii=False)})
    return "Consulta ficou complexa demais. Tenta reformular?"
