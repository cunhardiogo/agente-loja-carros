import json
from datetime import datetime, timedelta

import httpx
from openai import OpenAI

from . import datas, db
from .config import settings
from .ingest import resolver_pessoa

_http = httpx.Client(verify=settings.verify_ssl, timeout=60)
_client = OpenAI(api_key=settings.openai_api_key, http_client=_http) if settings.openai_api_key else None

_ctx = {"numero": None}  # quem está conversando (p/ lembretes irem pra pessoa certa)


# ===== helpers =====
def _range(periodo: str | None):
    hoje = datas.hoje()
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


def _resolve(periodo, data_inicio=None, data_fim=None):
    """Usa datas explícitas (ISO) se vierem; senão cai no período nomeado."""
    if data_inicio or data_fim:
        return (data_inicio or "1900-01-01"), (data_fim or "2999-12-31")
    return _range(periodo)


def _num(txt) -> float:
    """Extrai o primeiro número de um texto tipo 'R$ 2.000' ou '5'."""
    import re
    if txt is None:
        return 0.0
    m = re.findall(r"\d[\d.]*", str(txt).replace(",", "."))
    if not m:
        return 0.0
    try:
        return float(m[0].replace(".", "")) if len(m[0].replace(".", "")) > 0 else 0.0
    except ValueError:
        return 0.0


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
def resumo_vendas(periodo: str = "mes", vendedor: str | None = None,
                  data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    vid = None
    if vendedor:
        v = resolver_pessoa(vendedor, "vendedor")
        vid = v["id"] if v else None
        if vendedor and not vid:
            return {"erro": f"Vendedor '{vendedor}' não encontrado no cadastro."}
    rows = db.select_all("vendas", {"select": "valor_venda,data_venda,vendedor_id,over_valor"})
    rows = [r for r in rows if _dentro(r.get("data_venda"), ini, fim) and (not vid or r.get("vendedor_id") == vid)]
    total = sum((r.get("valor_venda") or 0) for r in rows)
    over = sum(_num(r.get("over_valor")) for r in rows)
    n = len(rows)
    return {
        "periodo": periodo, "vendedor": vendedor or "todos",
        "quantidade": n, "valor_total": total, "over_total": over,
        "ticket_medio": round(total / n, 2) if n else 0,
    }


def ranking_vendedores(periodo: str = "mes", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = db.select_all("vendas", {"select": "valor_venda,data_venda,vendedor_id"})
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
        rows = db.select_all("vendas", {
            "select": "cliente_nome,valor_venda,valor_total,valor_entrada,valor_financiado,troca_valor,"
                      "status_pagamento,status_entrega,modelo,versao",
            "order": "data_venda.asc.nullsfirst",
        })
        itens, total = [], 0
        for r in rows:
            # entregue ou marcado pago = quitado, não entra no a receber
            if r.get("status_entrega") == "entregue" or r.get("status_pagamento") == "pago":
                continue
            base = r.get("valor_total") or r.get("valor_venda") or 0
            saldo = base - (r.get("valor_entrada") or 0)  # já pago em conta abate
            if saldo <= 0:
                continue
            total += saldo
            itens.append({
                "cliente_nome": r.get("cliente_nome"),
                "veiculo": " ".join(x for x in (r.get("modelo"), r.get("versao")) if x),
                "valor_vendido": base, "ja_pago": r.get("valor_entrada") or 0,
                "financiado": r.get("valor_financiado") or 0, "troca": r.get("troca_valor") or 0,
                "a_receber": saldo,
            })
        return {"tipo": "pagamento", "quantidade": len(itens), "valor_total_a_receber": total, "itens": itens}
    rows = db.select_all("vendas", {
        "select": "cliente_nome,valor_venda,data_entrega_prevista,observacoes",
        "status_entrega": "eq.pendente",
        "order": "data_entrega_prevista.asc.nullsfirst",
    })
    return {"tipo": "entrega", "quantidade": len(rows), "itens": rows}


def resumo_agendamentos(periodo: str = "semana", compareceu: bool | None = None,
                        data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    rows = db.select_all("agendamentos", {"select": "cliente_nome,data_agendada,compareceu"})
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


def _range_ag(periodo: str | None):
    import calendar
    hoje = datas.hoje()
    p = (periodo or "hoje").lower()
    if p == "hoje":
        return hoje.isoformat(), hoje.isoformat()
    if p == "amanha":
        d = (hoje + timedelta(days=1)).isoformat()
        return d, d
    if p == "semana":
        ini = hoje - timedelta(days=hoje.weekday())
        return ini.isoformat(), (ini + timedelta(days=6)).isoformat()
    if p == "mes":
        ult = hoje.replace(day=calendar.monthrange(hoje.year, hoje.month)[1])
        return hoje.replace(day=1).isoformat(), ult.isoformat()
    return None, None


def listar_agendamentos(periodo: str = "hoje") -> dict:
    """Lista detalhada de agendamentos (cliente, horário, vendedor, status) num período."""
    ini, fim = _range_ag(periodo)
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = db.select_all("agendamentos", {"select": "cliente_nome,data_agendada,vendedor_id,resultado,compareceu",
                                      "origem": "eq.planilha"})
    rows = [r for r in rows if _dentro(r.get("data_agendada"), ini, fim)]
    rows.sort(key=lambda r: r.get("data_agendada") or "")
    itens = []
    for r in rows:
        d = r.get("data_agendada") or ""
        hora = d[11:16] if len(d) >= 16 and d[11:16] != "00:00" else ""
        comp = {True: "compareceu", False: "faltou"}.get(r.get("compareceu"), "")
        itens.append({"cliente": r.get("cliente_nome"), "data": d[:10], "hora": hora,
                      "vendedor": nomes.get(r.get("vendedor_id"), "—"),
                      "status": r.get("resultado") or comp})
    return {"periodo": periodo, "quantidade": len(itens), "agendamentos": itens}


def vendidos(periodo: str = "mes", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    """Quantos carros VENDIDOS no período, segundo a planilha (Status=VENDIDO)."""
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    rows = db.select_all("agendamentos", {"select": "cliente_nome,data_agendada,resultado,observacoes",
                                      "origem": "eq.planilha"})
    rows = [r for r in rows if (r.get("resultado") or "").strip().lower() == "vendido"
            and _dentro(r.get("data_agendada"), ini, fim)]
    return {"periodo": periodo, "quantidade": len(rows), "itens": rows}


def _range_futuro(periodo: str | None):
    import calendar
    hoje = datas.hoje()
    p = (periodo or "mes").lower()
    if p == "hoje":
        return hoje.isoformat(), hoje.isoformat()
    if p == "semana":
        return hoje.isoformat(), (hoje + timedelta(days=7)).isoformat()
    if p == "mes":
        ult = hoje.replace(day=calendar.monthrange(hoje.year, hoje.month)[1])
        return hoje.isoformat(), ult.isoformat()
    return hoje.isoformat(), (hoje + timedelta(days=365)).isoformat()


def reservados(periodo: str = "mes") -> dict:
    """Quantos carros RESERVADOS no período, segundo a planilha (Status=Reservado)."""
    ini, fim = _range(periodo)
    rows = db.select_all("agendamentos", {"select": "cliente_nome,data_agendada,resultado,observacoes",
                                      "origem": "eq.planilha"})
    rows = [r for r in rows if (r.get("resultado") or "").strip().lower().startswith("reservad")
            and _dentro(r.get("data_agendada"), ini, fim)]
    return {"periodo": periodo, "quantidade": len(rows), "itens": rows}


def entregas_agendadas(periodo: str = "mes") -> dict:
    ini, fim = _range_futuro(periodo)  # entregas são futuras: olha pra frente
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = db.select_all("entregas", {
        "select": "veiculo,data_entrega,horario,vendedor_id,observacao,status",
        "order": "data_entrega.asc.nullslast",
    })
    rows = [r for r in rows if _dentro(r.get("data_entrega"), ini, fim)]
    for r in rows:
        r["vendedor"] = nomes.get(r.pop("vendedor_id"), "—")
    return {"periodo": periodo, "quantidade": len(rows), "entregas": rows}


def lista_vendas(periodo: str = "tudo") -> dict:
    """Lista as vendas com status de entrega/pagamento (controle do que falta entregar)."""
    ini, fim = _range(periodo)
    rows = db.select_all("vendas", {
        "select": "cliente_nome,modelo,versao,placa,valor_venda,data_venda,"
                  "status_entrega,status_pagamento,data_entrega_prevista,data_entrega_real",
        "order": "data_venda.desc.nullslast",
    })
    if ini:
        rows = [r for r in rows if _dentro(r.get("data_venda"), ini, fim)]
    a_entregar = sum(1 for r in rows if r.get("status_entrega") != "entregue")
    return {"quantidade": len(rows), "a_entregar": a_entregar, "vendas": rows}


def listar_avaliacoes(periodo: str = "mes") -> dict:
    ini, fim = _range(periodo)
    rows = db.select_all("avaliacoes", {
        "select": "modelo,versao,ano,km,placa,fipe,valor_pretendido,valor_avaliacao,"
                  "carro_interesse,obs,created_at",
        "order": "created_at.desc",
    })
    rows = [r for r in rows if _dentro(r.get("created_at"), ini, fim)]
    return {"periodo": periodo, "quantidade": len(rows), "avaliacoes": rows}


def _match(rows, termo, campos):
    t = (termo or "").strip().lower()
    if not t:
        return None
    for r in rows:
        alvo = " ".join(str(r.get(c)) for c in campos if r.get(c)).lower()
        if t in alvo:
            return r
    return None


def marcar_entregue(cliente: str | None = None, veiculo: str | None = None) -> dict:
    rows = [r for r in db.select_all("vendas", {"select": "id,cliente_nome,modelo,versao,status_entrega"})
            if r.get("status_entrega") != "entregue"]
    r = _match(rows, cliente or veiculo, ["cliente_nome", "modelo", "versao"])
    if not r:
        return {"erro": f"não achei venda pendente com '{cliente or veiculo}'"}
    from . import datas
    db.update("vendas", {"status_entrega": "entregue", "status_pagamento": "pago",
                         "data_entrega_real": datas.hoje_iso()}, {"id": f"eq.{r['id']}"})
    return {"ok": True, "entregue": f"{r['cliente_nome']} — {r.get('modelo','')} {r.get('versao','') or ''}".strip()}


def marcar_pago(cliente: str | None = None, veiculo: str | None = None) -> dict:
    rows = [r for r in db.select_all("vendas", {"select": "id,cliente_nome,modelo,versao,status_pagamento"})
            if r.get("status_pagamento") != "pago"]
    r = _match(rows, cliente or veiculo, ["cliente_nome", "modelo", "versao"])
    if not r:
        return {"erro": f"não achei venda a receber com '{cliente or veiculo}'"}
    db.update("vendas", {"status_pagamento": "pago"}, {"id": f"eq.{r['id']}"})
    return {"ok": True, "pago": f"{r['cliente_nome']} — {r.get('modelo','')}".strip()}


def marcar_anunciado(veiculo: str) -> dict:
    rows = [r for r in db.select("veiculos", {"select": "id,marca,modelo,versao,status"})
            if r.get("status") == "a_anunciar"]
    r = _match(rows, veiculo, ["marca", "modelo", "versao"])
    if not r:
        return {"erro": f"não achei carro a anunciar com '{veiculo}'"}
    db.update("veiculos", {"status": "anunciado"}, {"id": f"eq.{r['id']}"})
    return {"ok": True, "anunciado": f"{r.get('marca','')} {r.get('modelo','')}".strip()}


def atualizar_venda(cliente: str | None = None, veiculo: str | None = None, **campos) -> dict:
    """Edita uma venda existente. Localiza por cliente ou veículo e altera os campos informados."""
    rows = db.select_all("vendas", {"select": "id,cliente_nome,modelo,versao"})
    r = _match(rows, cliente or veiculo, ["cliente_nome", "modelo", "versao"])
    if not r:
        return {"erro": f"não achei venda com '{cliente or veiculo}'"}
    permitidos = {"portal_venda", "valor_venda", "forma_pagamento", "banco", "valor_financiado",
                  "valor_entrada", "troca_valor", "status_pagamento", "status_entrega",
                  "data_venda", "data_entrega_prevista", "cliente_nome", "observacoes",
                  "marca", "modelo", "versao", "ano", "cor", "km", "placa"}
    set_ = {k: v for k, v in campos.items() if k in permitidos and v is not None}
    if campos.get("vendedor"):
        vend = resolver_pessoa(campos["vendedor"], "vendedor")
        if vend:
            set_["vendedor_id"] = vend["id"]
    if not set_:
        return {"erro": "não entendi o que mudar"}
    db.update("vendas", set_, {"id": f"eq.{r['id']}"})
    return {"ok": True, "venda": f"{r['cliente_nome']} — {r.get('modelo','')}".strip(), "alterado": set_}


def criar_lembrete(texto: str, quando: str) -> dict:
    """Cria um lembrete. 'quando' em ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM) no horário local."""
    numero = _ctx.get("numero") or settings.meu_numero
    q = (quando or "").strip()
    if not q:
        return {"erro": "diga quando devo lembrar"}
    if len(q) == 10:  # só data → assume 09:00
        q += "T09:00:00"
    if "T" in q and len(q) <= 19:  # sem fuso → assume São Paulo
        q += "-03:00"
    db.insert("lembretes", {"numero": numero, "texto": texto, "quando": q})
    return {"ok": True, "lembrete": texto, "quando": quando}


def _fmt_local(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(datas.TZ).strftime("%d/%m %H:%M")
    except Exception:
        return iso or ""


def listar_lembretes() -> dict:
    numero = _ctx.get("numero") or settings.meu_numero
    rows = db.select("lembretes", {"select": "texto,quando", "numero": f"eq.{numero}",
                                   "enviado": "eq.false", "order": "quando.asc"})
    itens = [{"texto": r["texto"], "quando": _fmt_local(r.get("quando"))} for r in rows]
    return {"quantidade": len(itens), "lembretes": itens}


def atualizar_carro(veiculo: str, preco_anuncio: float | None = None, status: str | None = None,
                    cor: str | None = None, km: int | None = None, ano: int | None = None) -> dict:
    """Edita um carro do estoque (preço, status, cor, km, ano). Localiza por marca/modelo/versão."""
    rows = db.select("veiculos", {"select": "id,marca,modelo,versao"})
    r = _match(rows, veiculo, ["marca", "modelo", "versao"])
    if not r:
        return {"erro": f"não achei carro com '{veiculo}'"}
    set_ = {k: v for k, v in {"preco_anuncio": preco_anuncio, "status": status, "cor": cor,
                              "km": km, "ano": ano}.items() if v is not None}
    if not set_:
        return {"erro": "não entendi o que mudar"}
    db.update("veiculos", set_, {"id": f"eq.{r['id']}"})
    return {"ok": True, "carro": f"{r.get('marca','')} {r.get('modelo','')}".strip(), "alterado": set_}


def vendas_por_canal(periodo: str = "mes", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    rows = [r for r in db.select_all("vendas", {"select": "portal_venda,valor_venda,data_venda"})
            if _dentro(r.get("data_venda"), ini, fim)]
    agg: dict = {}
    for r in rows:
        canal = (r.get("portal_venda") or "—").strip() or "—"
        a = agg.setdefault(canal, {"canal": canal, "quantidade": 0, "valor_total": 0})
        a["quantidade"] += 1
        a["valor_total"] += r.get("valor_venda") or 0
    return {"periodo": periodo, "canais": sorted(agg.values(), key=lambda x: x["valor_total"], reverse=True)}


def margem_avaliacoes(periodo: str = "mes", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    rows = [r for r in db.select_all("avaliacoes", {"select": "modelo,fipe,valor_avaliacao,valor_pretendido,created_at"})
            if _dentro(r.get("created_at"), ini, fim)]
    itens, difs = [], []
    for r in rows:
        fipe, aval, pret = r.get("fipe"), r.get("valor_avaliacao"), r.get("valor_pretendido")
        abaixo = (fipe - aval) if (fipe and aval) else None
        if abaixo is not None:
            difs.append(abaixo)
        itens.append({"modelo": r.get("modelo"), "fipe": fipe, "avaliado": aval,
                      "cliente_pediu": pret, "abaixo_da_fipe": abaixo})
    return {"periodo": periodo, "quantidade": len(itens),
            "media_abaixo_da_fipe": round(sum(difs) / len(difs)) if difs else 0, "itens": itens}


def conversao(periodo: str = "mes", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    """Taxa de conversão: dos agendamentos da planilha no período, quantos viraram VENDIDO."""
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    rows = [r for r in db.select_all("agendamentos", {"select": "resultado,data_agendada", "origem": "eq.planilha"})
            if _dentro(r.get("data_agendada"), ini, fim)]
    total = len(rows)
    vendidos_n = sum(1 for r in rows if (r.get("resultado") or "").strip().lower() == "vendido")
    return {"periodo": periodo, "agendamentos": total, "vendidos": vendidos_n,
            "taxa_conversao": round(vendidos_n / total * 100, 1) if total else 0}


def historico_cliente(nome: str) -> dict:
    n = (nome or "").strip()
    if not n:
        return {"erro": "diga o nome do cliente"}
    ag = db.select_all("agendamentos", {"select": "cliente_nome,data_agendada,resultado,observacoes",
                                    "cliente_nome": db.ilike(n), "origem": "eq.planilha"})
    vd = db.select_all("vendas", {"select": "cliente_nome,modelo,versao,valor_venda,data_venda,"
                              "status_entrega,status_pagamento,portal_venda", "cliente_nome": db.ilike(n)})
    return {"cliente": n, "agendamentos": ag, "vendas": vd,
            "encontrou": bool(ag or vd)}


def listar_pendencias() -> dict:
    from . import confirmacao
    return {"texto": confirmacao.listar_pendencias()}


DISPATCH = {
    "listar_pendencias": listar_pendencias,
    "vendidos": vendidos,
    "reservados": reservados,
    "listar_agendamentos": listar_agendamentos,
    "vendas_por_canal": vendas_por_canal,
    "margem_avaliacoes": margem_avaliacoes,
    "conversao": conversao,
    "historico_cliente": historico_cliente,
    "marcar_entregue": marcar_entregue,
    "marcar_pago": marcar_pago,
    "marcar_anunciado": marcar_anunciado,
    "atualizar_venda": atualizar_venda,
    "atualizar_carro": atualizar_carro,
    "criar_lembrete": criar_lembrete,
    "listar_lembretes": listar_lembretes,
    "resumo_vendas": resumo_vendas,
    "ranking_vendedores": ranking_vendedores,
    "listar_carros": listar_carros,
    "pendencias": pendencias,
    "resumo_agendamentos": resumo_agendamentos,
    "entregas_agendadas": entregas_agendadas,
    "listar_avaliacoes": listar_avaliacoes,
}

_PERIODO = {"type": "string", "enum": ["hoje", "ontem", "semana", "mes", "tudo"]}
_DI = {"type": "string", "description": "Data início ISO YYYY-MM-DD (opcional, p/ período livre como 'maio' ou 'semana passada')"}
_DF = {"type": "string", "description": "Data fim ISO YYYY-MM-DD (opcional)"}

TOOLS = [
    {"type": "function", "function": {
        "name": "listar_pendencias",
        "description": "Lista os eventos aguardando confirmação (fila de pendências), com o código de cada um.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "vendidos",
        "description": "Quantos carros foram VENDIDOS no período (contagem confiável da planilha).",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "reservados",
        "description": "Quantos carros estão RESERVADOS no período (planilha, Status=Reservado).",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO}},
    }},
    {"type": "function", "function": {
        "name": "marcar_entregue",
        "description": "AÇÃO: marcar uma venda como entregue (e paga). Use quando o dono disser que entregou um carro.",
        "parameters": {"type": "object", "properties": {
            "cliente": {"type": "string"}, "veiculo": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "marcar_pago",
        "description": "AÇÃO: marcar uma venda como paga/recebida. Use quando o dono disser que recebeu o pagamento.",
        "parameters": {"type": "object", "properties": {
            "cliente": {"type": "string"}, "veiculo": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "marcar_anunciado",
        "description": "AÇÃO: marcar um carro como anunciado (sai da lista 'a anunciar'). Use quando o dono disser que anunciou/publicou.",
        "parameters": {"type": "object", "properties": {"veiculo": {"type": "string"}}, "required": ["veiculo"]},
    }},
    {"type": "function", "function": {
        "name": "atualizar_venda",
        "description": "AÇÃO: corrigir/editar dados de uma venda existente (canal, valor, vendedor, forma de pagamento, financiado, troca, datas, cliente, status). Ex.: 'a venda do Denilson foi pelo tráfego', 'a do João foi 95 mil', 'a venda do C4 foi o Carlos'.",
        "parameters": {"type": "object", "properties": {
            "cliente": {"type": "string", "description": "nome do cliente p/ localizar"},
            "veiculo": {"type": "string", "description": "modelo/versão p/ localizar"},
            "portal_venda": {"type": "string"}, "valor_venda": {"type": "number"}, "vendedor": {"type": "string"},
            "forma_pagamento": {"type": "string"}, "banco": {"type": "string"},
            "valor_financiado": {"type": "number"}, "valor_entrada": {"type": "number"}, "troca_valor": {"type": "number"},
            "status_pagamento": {"type": "string", "enum": ["pendente", "parcial", "pago"]},
            "status_entrega": {"type": "string", "enum": ["pendente", "entregue"]},
            "data_venda": {"type": "string"}, "data_entrega_prevista": {"type": "string"},
            "cliente_nome": {"type": "string", "description": "novo nome do cliente (se for corrigir o nome)"},
            "observacoes": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "atualizar_carro",
        "description": "AÇÃO: editar um carro do estoque (preço, status, cor, km, ano). Ex.: 'muda o preço do Corolla pra 135 mil', 'o 208 é prata'.",
        "parameters": {"type": "object", "properties": {
            "veiculo": {"type": "string"}, "preco_anuncio": {"type": "number"},
            "status": {"type": "string", "enum": ["a_anunciar", "anunciado", "reservado", "vendido", "entregue", "inativo"]},
            "cor": {"type": "string"}, "km": {"type": "integer"}, "ano": {"type": "integer"}}, "required": ["veiculo"]},
    }},
    {"type": "function", "function": {
        "name": "criar_lembrete",
        "description": "AÇÃO: criar um lembrete. Use quando o dono pedir p/ ser lembrado. Calcule 'quando' em ISO a partir da DATA DE HOJE (ex.: 'amanhã 10h', 'sexta 14h', 'daqui 2h').",
        "parameters": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "o que lembrar"},
            "quando": {"type": "string", "description": "ISO YYYY-MM-DDTHH:MM (horário local)"}}, "required": ["texto", "quando"]},
    }},
    {"type": "function", "function": {
        "name": "listar_lembretes",
        "description": "Lista os lembretes pendentes do dono.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "resumo_vendas",
        "description": "Faturamento, qtd, ticket médio e over total num período. Pode filtrar por vendedor.",
        "parameters": {"type": "object", "properties": {
            "periodo": _PERIODO, "vendedor": {"type": "string", "description": "Nome do vendedor (opcional)"},
            "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "ranking_vendedores",
        "description": "Ranking dos vendedores por valor vendido num período.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "vendas_por_canal",
        "description": "Vendas agrupadas por canal/portal (Webmotors, OLX, Instagram, Indicação...). Qual canal vende mais.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "margem_avaliacoes",
        "description": "Avaliações no período: FIPE x valor avaliado x valor que o cliente pediu, e média de quanto abaixo da FIPE compramos.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "conversao",
        "description": "Taxa de conversão: dos agendamentos no período, quantos viraram venda (%).",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "historico_cliente",
        "description": "Histórico de um cliente: agendamentos (com resultado) e vendas dele. Use p/ 'histórico do cliente X'.",
        "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]},
    }},
    {"type": "function", "function": {
        "name": "listar_carros",
        "description": "Lista carros do estoque por status, com preços. Use 'a_anunciar' p/ os que faltam anunciar.",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["a_anunciar", "anunciado", "reservado", "vendido", "entregue"]}}},
    }},
    {"type": "function", "function": {
        "name": "pendencias",
        "description": "Carros/vendas pendentes: 'pagamento' (a receber) ou 'entrega' (a entregar).",
        "parameters": {"type": "object", "properties": {
            "tipo": {"type": "string", "enum": ["pagamento", "entrega"]}}, "required": ["tipo"]},
    }},
    {"type": "function", "function": {
        "name": "resumo_agendamentos",
        "description": "RESUMO de agendamentos num período: total e taxa de comparecimento (quantos vieram/faltaram).",
        "parameters": {"type": "object", "properties": {
            "periodo": _PERIODO, "compareceu": {"type": "boolean"}, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "listar_agendamentos",
        "description": "LISTA detalhada dos agendamentos (cliente, horário, vendedor, status). Use para 'quais os agendamentos de hoje/amanhã/essa semana'.",
        "parameters": {"type": "object", "properties": {
            "periodo": {"type": "string", "enum": ["hoje", "amanha", "semana", "mes"]}}},
    }},
    {"type": "function", "function": {
        "name": "entregas_agendadas",
        "description": "Entregas agendadas (lista do grupo de entregas) num período, com veículo, horário e vendedor.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO}},
    }},
    {"type": "function", "function": {
        "name": "listar_avaliacoes",
        "description": "Avaliações de carros (troca) num período, com FIPE e valor avaliado.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO}},
    }},
]

SYSTEM = """Você é o assistente da Loja SB (revenda de carros) respondendo o DONO no WhatsApp.
Use SEMPRE as ferramentas para buscar dados reais — nunca invente números.

CONTAGEM x VALOR: para CONTAR vendidos use 'vendidos' (fonte oficial = planilha). Para FATURAMENTO/ticket/over use 'resumo_vendas'.
SEMPRE que falar de vendas/faturamento/financeiro, informe os DOIS juntos: FATURAMENTO (resumo_vendas) E A RECEBER (pendencias pagamento).

PERÍODOS: quando o usuário não disser, assuma o mês atual. Para períodos livres ("semana passada", "dia 5", "em maio", "últimos 7 dias"), \
calcule data_inicio/data_fim em ISO a partir da DATA DE HOJE informada e passe nas ferramentas que aceitam (vendidos, resumo_vendas, ranking_vendedores, vendas_por_canal, margem_avaliacoes, conversao, resumo_agendamentos).

ANÁLISES: você tem vendas_por_canal (qual portal vende mais), margem_avaliacoes (FIPE x avaliado), conversao (agendou→vendeu %), historico_cliente (jornada de um cliente).

AÇÕES quando o dono pedir: marcar_entregue, marcar_pago, marcar_anunciado, e EDITAR registros: atualizar_venda \
(corrigir canal/valor/vendedor/pagamento/datas/cliente de uma venda — ex 'a venda do Denilson foi pelo tráfego', 'a do João foi 95 mil') \
e atualizar_carro (preço/status/cor/km do estoque). Confirme em 1 linha o que mudou (ou que não encontrou). Nunca invente se a ferramenta der erro. \
LEMBRETES: criar_lembrete quando o dono pedir p/ ser lembrado (calcule 'quando' em ISO a partir da DATA DE HOJE); listar_lembretes p/ ver os pendentes.
OBS: agendamento, comparecimento, vendido e reservado vêm da PLANILHA — não dá pra editar por aqui; nesse caso oriente a corrigir na planilha.

ESTILO: curto e direto, em português, valores como R$ 95.000, listas em linhas curtas com emojis discretos. \
Quando fizer sentido, acrescente UM insight curto (ex.: quem está puxando o mês, alerta de comparecimento/entrega atrasada) — sem encher. \
Use o histórico da conversa para entender perguntas curtas de continuação ("e do Carlos?", "e esse mês?")."""


def _system_dinamico() -> str:
    h = datas.agora()
    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    return SYSTEM + f"\n\nDATA E HORA DE HOJE (horário de Brasília): {h.strftime('%Y-%m-%d %H:%M')} ({dias[h.weekday()]})."


def carregar_historico(numero: str, limite: int = 6) -> list:
    rows = db.select("conversas", {"select": "papel,conteudo", "numero": f"eq.{numero}",
                                   "order": "created_at.desc", "limit": str(limite)})
    rows.reverse()
    return [{"role": r["papel"], "content": r["conteudo"]} for r in rows]


def salvar_conversa(numero: str, papel: str, conteudo: str) -> None:
    try:
        db.insert("conversas", {"numero": numero, "papel": papel, "conteudo": conteudo})
    except Exception:
        pass


def responder(pergunta: str, historico: list | None = None, numero: str | None = None) -> str:
    if _client is None:
        return "IA não configurada (falta OPENAI_API_KEY)."
    _ctx["numero"] = numero
    messages = [{"role": "system", "content": _system_dinamico()}]
    messages += historico or []
    messages.append({"role": "user", "content": pergunta})
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
