import json
import logging
from datetime import datetime, timedelta

import httpx
from openai import OpenAI

from . import datas, db
from .config import settings
from .ingest import resolver_pessoa

log = logging.getLogger("agente")
_http = httpx.Client(verify=settings.verify_ssl, timeout=60)
_kwargs = {"api_key": settings.openai_api_key, "http_client": _http}
if settings.openai_base_url:  # LLM plugável: endpoint compatível-OpenAI
    _kwargs["base_url"] = settings.openai_base_url
_client = OpenAI(**_kwargs) if settings.openai_api_key else None

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
    rows = _vendas_validas("valor_venda,data_venda,vendedor_id,over_valor")
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
    rows = [r for r in _vendas_validas("valor_venda,data_venda,vendedor_id") if _dentro(r.get("data_venda"), ini, fim)]
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
    """Quantos carros VENDIDOS no período, segundo o grupo de vendas (conta no aviso; resumo é pendência)."""
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = [r for r in _vendas_validas("modelo,versao,placa,data_venda,vendedor_id,status_venda,cliente_nome")
            if _dentro(r.get("data_venda"), ini, fim)]
    itens = [{"carro": " ".join(x for x in (r.get("modelo"), r.get("versao"), r.get("placa")) if x),
              "vendedor": nomes.get(r.get("vendedor_id")), "data": r.get("data_venda"),
              "cliente": r.get("cliente_nome"), "resumo": r["status_venda"] == "completa"} for r in rows]
    return {"periodo": periodo, "quantidade": len(rows), "itens": itens}


def _vendas_validas(select: str, incluir_revenda: bool = False) -> list[dict]:
    """Vendas que contam: não removidas, nem reserva, nem desistência (revenda só quando pedida)."""
    params = {"select": select, "removido": "eq.false", "status_venda": "in.(completa,aguardando_resumo)"}
    if not incluir_revenda:
        params["revenda"] = "eq.false"
    return db.select_all("vendas", params)


def vendas_pendentes() -> dict:
    """Vendas do grupo com algo pendente: sem resumo, resumo incompleto, reserva em aberto."""
    from .vendas_grupo import codigo, prazo_resumo
    from .leads import _dt
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = db.select_all("vendas", {"select": "*", "removido": "eq.false", "origem": "eq.grupo",
                                    "status_venda": "in.(aguardando_resumo,completa,reservado)"})
    carro = lambda r: " ".join(x for x in (r.get("modelo"), r.get("versao"), r.get("placa")) if x)
    return {
        "sem_resumo": [{"codigo": codigo(r), "carro": carro(r), "vendedor": nomes.get(r.get("vendedor_id")),
                        "avisada_em": _fmt_local(r.get("aviso_em")),
                        "prazo": prazo_resumo(_dt(r["aviso_em"])).strftime("%d/%m %H:%M") if r.get("aviso_em") else None}
                       for r in rows if r["status_venda"] == "aguardando_resumo"],
        "resumo_incompleto": [{"codigo": codigo(r), "carro": carro(r), "vendedor": nomes.get(r.get("vendedor_id")),
                               "faltando": r.get("pendencias")}
                              for r in rows if r["status_venda"] == "completa" and r.get("pendencias")],
        "reservas": [{"codigo": codigo(r), "carro": carro(r), "vendedor": nomes.get(r.get("vendedor_id")),
                      "desde": _fmt_local(r.get("reservado_em"))} for r in rows if r["status_venda"] == "reservado"],
    }


def a_receber_vendas(periodo: str = "tudo", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    """Quanto falta receber das vendas do grupo (outubro/2026 em diante): total − troca − comprovantes.
    Separa o que ainda vem do banco (financiado) do que falta do cliente."""
    from .sheets import DESDE
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = [r for r in _vendas_validas("*") if (r.get("data_venda") or "") >= DESDE
            and _dentro(r.get("data_venda"), ini, fim)]
    itens, sem_valor = [], []
    for r in rows:
        carro = " ".join(x for x in (r.get("modelo"), r.get("versao"), r.get("placa")) if x)
        total = r.get("valor_total") or r.get("valor_venda")
        if not total:
            sem_valor.append(carro)
            continue
        troca = r.get("troca_valor") or 0
        pago = r.get("valor_pago") or 0
        falta = round(max(total - troca - pago, 0), 2)
        if falta <= 0:
            continue
        financ = min(r.get("valor_financiado") or 0, falta)
        itens.append({"carro": carro, "cliente": r.get("cliente_nome"), "vendedor": nomes.get(r.get("vendedor_id")),
                      "revenda": r.get("revenda"), "total": total, "troca": troca, "ja_pago_comprovado": pago,
                      "falta": falta, "falta_financiamento": financ, "falta_a_vista": round(falta - financ, 2)})
    itens.sort(key=lambda x: -x["falta"])
    return {"total_a_receber": round(sum(i["falta"] for i in itens), 2),
            "financiamento_banco": round(sum(i["falta_financiamento"] for i in itens), 2),
            "a_vista": round(sum(i["falta_a_vista"] for i in itens), 2),
            "vendas": itens, "vendas_sem_valor_no_resumo": sem_valor,
            "observacao": "Pago = só o que tem comprovante no grupo de vendas. Troca abate do total. "
                          "Consórcio conta como à vista (carta de crédito paga à vista)."}


def marcar_venda_caiu(termo: str) -> dict:
    """Venda que caiu: sai da contagem, do ranking e da descrição do grupo."""
    t = db.ilike(termo)
    rows = db.select("vendas", {"select": "id,modelo,versao,placa,cliente_nome", "removido": "eq.false",
                                "status_venda": "in.(completa,aguardando_resumo,reservado)",
                                "or": f"(modelo.{t},versao.{t},placa.{t},cliente_nome.{t})"})
    if len(rows) != 1:
        return {"erro": "nenhuma venda encontrada" if not rows else "mais de uma venda encontrada, especifique",
                "encontradas": [" ".join(x for x in (r.get("modelo"), r.get("versao"), r.get("placa")) if x) for r in rows]}
    db.update("vendas", {"status_venda": "desistiu"}, {"id": f"eq.{rows[0]['id']}"})
    from . import vendas_grupo
    vendas_grupo.sujo["v"] = True
    return {"ok": True, "venda": " ".join(x for x in (rows[0].get("modelo"), rows[0].get("versao"), rows[0].get("placa")) if x)}


def buscar_venda(termo: str) -> dict:
    """Uma venda por carro, placa ou cliente, com pagamentos (comprovantes) e documentos recebidos."""
    t = db.ilike(termo)
    rows = db.select("vendas", {"select": "*", "removido": "eq.false", "order": "created_at.desc", "limit": "8",
                                "or": f"(modelo.{t},versao.{t},placa.{t},cliente_nome.{t})"})
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    out = []
    for r in rows:
        pags = db.select("pagamentos", {"select": "valor,pago_em,tipo,banco,id_transacao", "venda_id": f"eq.{r['id']}"})
        out.append({k: r.get(k) for k in ("modelo", "versao", "ano", "cor", "placa", "status_venda", "data_venda",
                                          "tabela_preco", "valor_venda", "desconto", "over_valor", "valor_total",
                                          "banco", "valor_financiado", "valor_pix", "troca_modelo", "troca_valor",
                                          "cliente_nome", "cliente_telefone", "portal_venda",
                                          "data_entrega_prevista", "data_entrega_texto", "valor_pago",
                                          "status_pagamento", "docs", "pendencias", "observacoes")}
                   | {"vendedor": nomes.get(r.get("vendedor_id")), "pagamentos": pags})
    return {"encontradas": len(out), "vendas": out}


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
    """Quadro do grupo de entregas: o que ainda não foi entregue (com data no período) e as sem data."""
    ini, fim = _range_futuro(periodo)  # entregas são futuras: olha pra frente
    rows = db.select_all("entregas", {
        "select": "veiculo,data_entrega,data_texto,horario,vendedor_nome,observacao,status",
        "origem": "in.(grupo,planilha)", "status": "eq.agendada", "removido": "eq.false",
        "order": "data_entrega.asc.nullslast",
    })
    com_data = [r for r in rows if r.get("data_entrega") and _dentro(r["data_entrega"], ini, fim)]
    atrasadas = [r for r in rows if r.get("data_entrega") and r["data_entrega"] < datas.hoje_iso()]
    sem_data = [r for r in rows if not r.get("data_entrega")]
    return {"periodo": periodo, "quantidade": len(com_data), "entregas": com_data,
            "atrasadas_ainda_no_quadro": atrasadas, "sem_data": sem_data}


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


def _matches_venda(rows, cliente, veiculo):
    """Casa por cliente E veículo (cada um opcional). Diferente do match antigo (OR
    no primeiro hit), retorna TODAS as vendas que batem — pra exigir unicidade."""
    c = (cliente or "").strip().lower()
    v = (veiculo or "").strip().lower()
    out = []
    for r in rows:
        nome = (r.get("cliente_nome") or "").lower()
        carro = f"{r.get('modelo', '') or ''} {r.get('versao', '') or ''}".lower()
        if (c or v) and (c in nome if c else True) and (v in carro if v else True):
            out.append(r)
    return out


def _venda_unica(rows, cliente, veiculo, descricao):
    """Devolve (venda, None) se única; (None, msg_erro) se nenhuma ou ambígua."""
    m = _matches_venda(rows, cliente, veiculo)
    termo = " ".join(x for x in (cliente, veiculo) if x)
    if not m:
        return None, {"erro": f"não achei {descricao} com '{termo}'"}
    if len(m) > 1:
        nomes = ", ".join(f"{x.get('cliente_nome') or '?'} ({x.get('modelo') or '?'})" for x in m[:6])
        return None, {"erro": f"achei {len(m)} {descricao}s com '{termo}': {nomes}. "
                              "Diga cliente E carro pra eu não mexer na errada."}
    return m[0], None


def marcar_entregue(cliente: str | None = None, veiculo: str | None = None) -> dict:
    rows = [r for r in db.select_all("vendas", {"select": "id,cliente_nome,modelo,versao,status_entrega,"
                                                "valor_total,valor_venda,valor_entrada"})
            if r.get("status_entrega") != "entregue"]
    r, err = _venda_unica(rows, cliente, veiculo, "venda pendente")
    if err:
        return err
    from . import datas
    dados = {"status_entrega": "entregue", "data_entrega_real": datas.hoje_iso()}
    saldo = (r.get("valor_total") or r.get("valor_venda") or 0) - (r.get("valor_entrada") or 0)
    if saldo <= 0:  # só quita automático se não há saldo a receber (I3)
        dados["status_pagamento"] = "pago"
    db.update("vendas", dados, {"id": f"eq.{r['id']}"})
    msg = f"{r['cliente_nome']} — {r.get('modelo', '')} {r.get('versao', '') or ''}".strip()
    if saldo > 0:
        return {"ok": True, "entregue": msg, "atencao": f"ainda há R$ {saldo:.0f} a receber — não marquei como pago"}
    return {"ok": True, "entregue": msg}


def marcar_pago(cliente: str | None = None, veiculo: str | None = None) -> dict:
    rows = [r for r in db.select_all("vendas", {"select": "id,cliente_nome,modelo,versao,status_pagamento"})
            if r.get("status_pagamento") != "pago"]
    r, err = _venda_unica(rows, cliente, veiculo, "venda a receber")
    if err:
        return err
    db.update("vendas", {"status_pagamento": "pago"}, {"id": f"eq.{r['id']}"})
    return {"ok": True, "pago": f"{r['cliente_nome']} — {r.get('modelo','')}".strip()}


def marcar_anunciado(veiculo: str) -> dict:
    rows = [r for r in db.select_all("veiculos", {"select": "id,marca,modelo,versao,status"})
            if r.get("status") == "a_anunciar"]
    m = [r for r in rows if _match_carro(r, veiculo)]
    if not m:
        return {"erro": f"não achei carro a anunciar com '{veiculo}'"}
    if len(m) > 1:
        nomes = ", ".join(f"{x.get('marca','')} {x.get('modelo','')}".strip() for x in m[:6])
        return {"erro": f"achei {len(m)} carros com '{veiculo}': {nomes}. Seja mais específico."}
    db.update("veiculos", {"status": "anunciado"}, {"id": f"eq.{m[0]['id']}"})
    return {"ok": True, "anunciado": f"{m[0].get('marca','')} {m[0].get('modelo','')}".strip()}


def _match_carro(r, termo):
    t = (termo or "").strip().lower()
    alvo = f"{r.get('marca','') or ''} {r.get('modelo','') or ''} {r.get('versao','') or ''}".lower()
    return bool(t) and t in alvo


def atualizar_venda(cliente: str | None = None, veiculo: str | None = None, **campos) -> dict:
    """Edita uma venda existente. Localiza por cliente E/OU veículo (exige unicidade)."""
    rows = db.select_all("vendas", {"select": "id,cliente_nome,modelo,versao"})
    r, err = _venda_unica(rows, cliente, veiculo, "venda")
    if err:
        return err
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
    rows = db.select_all("veiculos", {"select": "id,marca,modelo,versao"})
    m = [r for r in rows if _match_carro(r, veiculo)]
    if not m:
        return {"erro": f"não achei carro com '{veiculo}'"}
    if len(m) > 1:
        nomes = ", ".join(f"{x.get('marca','')} {x.get('modelo','')} {x.get('versao','') or ''}".strip() for x in m[:6])
        return {"erro": f"achei {len(m)} carros com '{veiculo}': {nomes}. Seja mais específico (com versão/ano)."}
    r = m[0]
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
                                    "cliente_nome": db.ilike_simples(n), "origem": "eq.planilha"})
    vd = db.select_all("vendas", {"select": "cliente_nome,modelo,versao,valor_venda,data_venda,"
                              "status_entrega,status_pagamento,portal_venda", "cliente_nome": db.ilike_simples(n)})
    return {"cliente": n, "agendamentos": ag, "vendas": vd,
            "encontrou": bool(ag or vd)}


def listar_pendencias() -> dict:
    from . import confirmacao
    return {"texto": confirmacao.listar_pendencias()}


def radar() -> dict:
    from . import supervisor
    return {"texto": supervisor.radar_texto()}


def resolver_alerta(termo: str) -> dict:
    from . import supervisor
    return supervisor.resolver_alerta(termo)


def anotar(texto: str) -> dict:
    from . import supervisor
    return supervisor.anotar(texto, _ctx.get("numero"))


def listar_notas() -> dict:
    from . import supervisor
    return supervisor.listar_notas()


def resolver_nota(termo: str) -> dict:
    from . import supervisor
    return supervisor.resolver_nota(termo)


def giro_estoque() -> dict:
    """Tempo que os carros estão parados no estoque (a anunciar/anunciado/reservado)."""
    rows = db.select_all("veiculos", {"select": "marca,modelo,versao,status,created_at,data_anuncio",
                                      "status": "in.(a_anunciar,anunciado,reservado)"})
    hoje = datas.agora()

    def idade(r):
        base = r.get("data_anuncio") or r.get("created_at")
        if not base:
            return None
        try:
            dt = datetime.fromisoformat(base.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=hoje.tzinfo)
        return (hoje - dt).days

    faixas = {"0-15": 0, "16-30": 0, "31-60": 0, "60+": 0}
    itens = []
    for r in rows:
        d = idade(r)
        if d is None:
            continue
        k = "0-15" if d <= 15 else "16-30" if d <= 30 else "31-60" if d <= 60 else "60+"
        faixas[k] += 1
        itens.append({"carro": " ".join(x for x in (r.get("marca"), r.get("modelo"), r.get("versao")) if x),
                      "dias": d, "status": r.get("status")})
    itens.sort(key=lambda x: x["dias"], reverse=True)
    dias = [i["dias"] for i in itens]
    return {"em_estoque": len(itens), "tempo_medio_dias": round(sum(dias) / len(dias)) if dias else 0,
            "faixas": faixas, "mais_parados": itens[:10]}


def margem_estoque(periodo: str = "mes") -> dict:
    """Margem real (valor de venda − custo) das vendas ligadas a um carro do estoque."""
    ini, fim = _resolve(periodo)
    vendas = [v for v in db.select_all("vendas", {"select": "valor_venda,data_venda,veiculo_id"})
              if _dentro(v.get("data_venda"), ini, fim)]
    custos = {x["id"]: x.get("preco_custo")
              for x in db.select_all("veiculos", {"select": "id,preco_custo"})}
    total = com_custo = 0
    for v in vendas:
        custo = custos.get(v.get("veiculo_id"))
        if custo and v.get("valor_venda"):
            total += v["valor_venda"] - custo
            com_custo += 1
    return {"periodo": periodo, "vendas": len(vendas), "com_custo_conhecido": com_custo,
            "margem_total": round(total), "margem_media": round(total / com_custo) if com_custo else 0}


def definir_meta(meta_vendas: int | None = None, meta_faturamento: float | None = None,
                 vendedor: str | None = None, mes: str | None = None) -> dict:
    """Define/atualiza a meta do mês (da loja ou de um vendedor)."""
    mref = (mes or datas.hoje_iso())[:7] + "-01"
    escopo = "vendedor" if vendedor else "loja"
    vid = None
    if vendedor:
        v = resolver_pessoa(vendedor, "vendedor")
        if not v:
            return {"erro": f"vendedor '{vendedor}' não encontrado"}
        vid = v["id"]
    filtro = {"select": "id", "escopo": f"eq.{escopo}", "mes": f"eq.{mref}", "limit": "1",
              "vendedor_id": f"eq.{vid}" if vid else "is.null"}
    dados = {"escopo": escopo, "vendedor_id": vid, "mes": mref,
             "meta_vendas": meta_vendas, "meta_faturamento": meta_faturamento}
    existe = db.select("metas", filtro)
    if existe:
        db.update("metas", dados, {"id": f"eq.{existe[0]['id']}"})
    else:
        db.insert("metas", dados)
    return {"ok": True, "escopo": escopo, "vendedor": vendedor, "mes": mref,
            "meta_vendas": meta_vendas, "meta_faturamento": meta_faturamento}


def progresso_metas(mes: str | None = None) -> dict:
    """Quanto já foi realizado das metas do mês (loja e vendedores)."""
    mref = (mes or datas.hoje_iso())[:7] + "-01"
    metas = db.select_all("metas", {"select": "escopo,vendedor_id,meta_vendas,meta_faturamento",
                                    "mes": f"eq.{mref}"})
    if not metas:
        return {"mes": mref, "metas": [], "aviso": "nenhuma meta definida — use definir_meta"}
    fat = resumo_vendas("mes").get("valor_total", 0)
    vdd = vendidos("mes")["quantidade"]
    rank = {r["vendedor"]: r for r in ranking_vendedores("mes")["ranking"]}
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}

    def pct(real, meta):
        return round(real / meta * 100) if meta else None

    out = []
    for m in metas:
        if m["escopo"] == "loja":
            out.append({"escopo": "loja", "meta_vendas": m.get("meta_vendas"), "vendas": vdd,
                        "pct_vendas": pct(vdd, m.get("meta_vendas")),
                        "meta_faturamento": m.get("meta_faturamento"), "faturamento": fat,
                        "pct_faturamento": pct(fat, m.get("meta_faturamento"))})
        else:
            nome = nomes.get(m["vendedor_id"], "—")
            r = rank.get(nome, {})
            out.append({"escopo": "vendedor", "vendedor": nome,
                        "meta_vendas": m.get("meta_vendas"), "vendas": r.get("quantidade", 0),
                        "pct_vendas": pct(r.get("quantidade", 0), m.get("meta_vendas")),
                        "meta_faturamento": m.get("meta_faturamento"), "faturamento": r.get("valor_total", 0),
                        "pct_faturamento": pct(r.get("valor_total", 0), m.get("meta_faturamento"))})
    return {"mes": mref, "metas": out}


def listar_recalls() -> dict:
    rows = db.select_all("recalls", {"select": "cliente_nome,veiculo,placa,motivo,created_at",
                                     "status": "eq.aberto", "order": "created_at.desc"})
    return {"quantidade": len(rows), "recalls": rows}


def meta_ads_resumo(periodo: str = "semana") -> dict:
    from . import meta_ads
    return meta_ads.resumo(periodo)


def meta_ads_roas(periodo: str = "mes") -> dict:
    from . import meta_ads
    return meta_ads.roas(periodo)


def _dias_desde(iso: str | None):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    ag = datas.agora()
    if d.tzinfo is None:
        d = d.replace(tzinfo=ag.tzinfo)
    return (ag - d).days


def lista_ligar_hoje() -> dict:
    """Quem precisa de contato HOJE (assistido — só informa, não liga sozinho):
    faltas de ontem, reservas paradas, entregas atrasadas e a-receber envelhecendo.
    Cada item traz o telefone pra facilitar o contato."""
    hoje = datas.hoje()
    ontem = (hoje - timedelta(days=1)).isoformat()
    itens = []

    ags = db.select_all("agendamentos", {"select": "cliente_nome,telefone,data_agendada,compareceu,resultado",
                                         "origem": "eq.planilha"})
    for a in ags:
        dia = (a.get("data_agendada") or "")[:10]
        res = (a.get("resultado") or "").strip().lower()
        if a.get("compareceu") is False and dia == ontem:
            itens.append({"prioridade": 1, "cliente": a.get("cliente_nome"), "telefone": a.get("telefone"),
                          "motivo": "faltou ontem", "acao": "remarcar visita"})
        elif res.startswith("reservad"):
            d = _dias_desde(a.get("data_agendada"))
            if d is not None and d >= 5:
                itens.append({"prioridade": 2, "cliente": a.get("cliente_nome"), "telefone": a.get("telefone"),
                              "motivo": f"reservou há {d}d e não fechou", "acao": "cobrar decisão"})

    vendas = db.select_all("vendas", {"select": "cliente_nome,cliente_telefone,modelo,versao,created_at,"
                                      "data_entrega_prevista,valor_total,valor_venda,valor_entrada,"
                                      "status_pagamento,status_entrega"})
    for v in vendas:
        carro = " ".join(x for x in (v.get("modelo"), v.get("versao")) if x)
        if v.get("status_entrega") == "pendente" and v.get("data_entrega_prevista") and \
                v["data_entrega_prevista"][:10] < hoje.isoformat():
            itens.append({"prioridade": 1, "cliente": v.get("cliente_nome"), "telefone": v.get("cliente_telefone"),
                          "motivo": f"entrega do {carro} atrasada", "acao": "remarcar entrega"})
        elif v.get("status_pagamento") != "pago" and v.get("status_entrega") != "entregue":
            saldo = (v.get("valor_total") or v.get("valor_venda") or 0) - (v.get("valor_entrada") or 0)
            d = _dias_desde(v.get("created_at"))
            if saldo > 0 and d is not None and d >= 7:
                itens.append({"prioridade": 3, "cliente": v.get("cliente_nome"), "telefone": v.get("cliente_telefone"),
                              "motivo": f"falta R$ {saldo:.0f} do {carro} ({d}d)", "acao": "cobrar pagamento"})

    itens.sort(key=lambda x: x["prioridade"])
    return {"quantidade": len(itens), "itens": itens}


def mensagem_cobranca(cliente: str | None = None, veiculo: str | None = None) -> dict:
    """Monta uma mensagem pronta de cobrança pro cliente (assistido: você revisa e encaminha)."""
    rows = []
    for v in db.select_all("vendas", {"select": "cliente_nome,modelo,versao,valor_total,valor_venda,"
                                      "valor_entrada,status_pagamento,status_entrega"}):
        if v.get("status_pagamento") == "pago" or v.get("status_entrega") == "entregue":
            continue
        saldo = (v.get("valor_total") or v.get("valor_venda") or 0) - (v.get("valor_entrada") or 0)
        if saldo > 0:
            v["_saldo"] = saldo
            rows.append(v)
    r, err = _venda_unica(rows, cliente, veiculo, "venda a receber")
    if err:
        return err
    nome = (r.get("cliente_nome") or "").split()[0] or "tudo bem"
    carro = " ".join(x for x in (r.get("modelo"), r.get("versao")) if x) or "seu carro"
    saldo = r["_saldo"]
    saldo_fmt = f"{saldo:,.0f}".replace(",", ".")  # formata só o número (não o texto)
    msg = (f"Oi {nome}, tudo bem? 😊\n\n"
           f"Passando pra alinhar o restante da compra do {carro}. "
           f"Ainda ficou um saldo de R$ {saldo_fmt} pra gente concluir tudo certinho.\n\n"
           f"Pode me dizer como prefere acertar? Qualquer dúvida, é só chamar por aqui!")
    return {"cliente": r.get("cliente_nome"), "veiculo": carro, "saldo": round(saldo),
            "mensagem": msg}


# ===== leads do grupo de agendamento =====
_ABERTOS = ("aguardando_retorno", "retorno", "confirmado", "negociando", "remarcado", "compareceu", "reservado")


def _lead_curto(l: dict) -> dict:
    from .leads import STATUS_LABEL, TIPO_LABEL, codigo
    return {"codigo": codigo(l), "cliente": l.get("cliente_nome"), "telefone": l.get("telefone"),
            "veiculo": l.get("veiculo"), "vendedor": l.get("vendedor_nome"), "sdr": l.get("sdr"),
            "tipo": TIPO_LABEL.get(l["tipo"], l["tipo"]), "canal": l.get("canal"),
            "data": l.get("data_agendada"), "horario": l.get("horario"),
            "status": STATUS_LABEL.get(l["status"], l["status"]), "nova_data": l.get("nova_data"),
            "recebido_em": _fmt_local(l.get("recebido_em")),
            "ultimo_retorno": l.get("ultimo_retorno_texto"), "troca": l.get("troca"),
            "oferta_entrada": l.get("oferta_entrada"), "observacao": l.get("observacao"),
            "cobranca": l.get("cobranca_status")}


def leads_abertos(vendedor: str | None = None, sdr: str | None = None) -> dict:
    rows = db.select_all("leads", {"select": "*", "removido": "eq.false",
                                   "status": f"in.({','.join(_ABERTOS)})", "order": "recebido_em.desc"})
    if vendedor:
        rows = [r for r in rows if _sem_acento(vendedor) in _sem_acento(r.get("vendedor_nome"))]
    if sdr:
        rows = [r for r in rows if _sem_acento(sdr) in _sem_acento(r.get("sdr"))]
    return {"total": len(rows), "leads": [_lead_curto(r) for r in rows[:40]]}


def buscar_lead(termo: str) -> dict:
    t = db.ilike(termo)
    rows = db.select("leads", {"select": "*", "removido": "eq.false", "order": "recebido_em.desc", "limit": "15",
                               "or": f"(cliente_nome.{t},veiculo.{t},telefone.{t})"})
    out = []
    for r in rows:
        d = _lead_curto(r)
        d["respostas"] = [f"{e.get('autor')}: {e.get('texto')}" for e in
                          db.select("lead_eventos", {"lead_id": f"eq.{r['id']}", "order": "em.asc"})]
        out.append(d)
    return {"encontrados": len(out), "leads": out}


def resumo_leads(periodo: str = "mes", data_inicio: str | None = None, data_fim: str | None = None) -> dict:
    from collections import Counter
    from .leads import STATUS_LABEL, TIPO_LABEL
    ini, fim = _resolve(periodo, data_inicio, data_fim)
    params = {"select": "*", "removido": "eq.false"}
    rows = db.select_all("leads", params)
    rows = [r for r in rows if _dentro(_fmt_data_sp(r["recebido_em"]), ini, fim)]
    tempos = []
    for r in rows:
        if r.get("ultimo_retorno_em"):
            ev = db.select("lead_eventos", {"select": "em", "lead_id": f"eq.{r['id']}", "order": "em.asc", "limit": "1"})
            if ev:
                tempos.append((datetime.fromisoformat(ev[0]["em"]) - datetime.fromisoformat(r["recebido_em"])).total_seconds() / 60)
    tempos.sort()
    vendidos = [r for r in rows if r["status"] == "vendido"]
    return {
        "periodo": [ini, fim], "total": len(rows),
        "por_tipo": dict(Counter(TIPO_LABEL.get(r["tipo"], r["tipo"]) for r in rows)),
        "por_sdr": dict(Counter(r.get("sdr") or "?" for r in rows)),
        "por_canal": dict(Counter((r.get("canal") or "?").strip().title() for r in rows)),
        "por_vendedor": dict(Counter(r.get("vendedor_nome") or "?" for r in rows)),
        "por_status": dict(Counter(STATUS_LABEL.get(r["status"], r["status"]) for r in rows)),
        "vendidos": len(vendidos),
        "vendidos_por_vendedor": dict(Counter(r.get("vendedor_nome") or "?" for r in vendidos)),
        "sem_retorno": sum(1 for r in rows if not r.get("ultimo_retorno_em")),
        "tempo_retorno_mediano_min": round(tempos[len(tempos) // 2]) if tempos else None,
        "cobrancas_enviadas": sum(1 for r in rows if r.get("cobranca_status") == "enviada"),
    }


def _sem_acento(s: str | None) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _fmt_data_sp(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(datas.TZ).date().isoformat()


DISPATCH = {
    "a_receber_vendas": a_receber_vendas,
    "marcar_venda_caiu": marcar_venda_caiu,
    "vendas_pendentes": vendas_pendentes,
    "buscar_venda": buscar_venda,
    "leads_abertos": leads_abertos,
    "buscar_lead": buscar_lead,
    "resumo_leads": resumo_leads,
    "lista_ligar_hoje": lista_ligar_hoje,
    "mensagem_cobranca": mensagem_cobranca,
    "giro_estoque": giro_estoque,
    "margem_estoque": margem_estoque,
    "definir_meta": definir_meta,
    "progresso_metas": progresso_metas,
    "listar_recalls": listar_recalls,
    "meta_ads_resumo": meta_ads_resumo,
    "meta_ads_roas": meta_ads_roas,
    "listar_pendencias": listar_pendencias,
    "radar": radar,
    "resolver_alerta": resolver_alerta,
    "anotar": anotar,
    "listar_notas": listar_notas,
    "resolver_nota": resolver_nota,
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
        "name": "a_receber_vendas",
        "description": "Quanto falta receber das vendas (de outubro/2026 em diante, sem revenda): total, quanto vem de financiamento bancário e quanto é à vista (consórcio conta como à vista), venda por venda. Pago = comprovantes postados no grupo de vendas.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "marcar_venda_caiu",
        "description": "Marca que uma venda caiu (cliente desistiu): ela sai da contagem de vendidos, do ranking e da descrição do grupo. Use quando o dono disser que a venda X caiu/cancelou.",
        "parameters": {"type": "object", "properties": {"termo": {"type": "string", "description": "Carro, placa ou cliente"}}, "required": ["termo"]},
    }},
    {"type": "function", "function": {
        "name": "vendas_pendentes",
        "description": "Vendas do grupo com pendência: avisadas sem Resumo de Venda (com prazo), resumos incompletos (campos faltando) e reservas em aberto.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "buscar_venda",
        "description": "Procura uma venda por carro, placa ou cliente e mostra tudo: valores, forma de pagamento, troca, portal, entrega, pagamentos já comprovados (valor, data, id da transação), documentos recebidos e pendências.",
        "parameters": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]},
    }},
    {"type": "function", "function": {
        "name": "leads_abertos",
        "description": "Leads do grupo de agendamento ainda sem desfecho (aguardando retorno, confirmado, negociando, remarcado...). Filtra por vendedor ou SDR.",
        "parameters": {"type": "object", "properties": {
            "vendedor": {"type": "string", "description": "Nome do vendedor (opcional)"},
            "sdr": {"type": "string", "description": "Nome da SDR/atendente (opcional)"}}},
    }},
    {"type": "function", "function": {
        "name": "buscar_lead",
        "description": "Procura um lead do grupo de agendamento por nome do cliente, carro ou telefone e mostra tudo dele (observação, troca, oferta, respostas do vendedor).",
        "parameters": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]},
    }},
    {"type": "function", "function": {
        "name": "resumo_leads",
        "description": "Números do grupo de agendamento num período: total, por tipo/SDR/canal/vendedor/status, vendidos, sem retorno, tempo de retorno, cobranças.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO, "data_inicio": _DI, "data_fim": _DF}},
    }},
    {"type": "function", "function": {
        "name": "listar_pendencias",
        "description": "Lista os eventos aguardando confirmação (fila de pendências), com o código de cada um.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "radar",
        "description": "Mostra os alertas abertos do supervisor (carro encalhado, a receber parado, entrega atrasada, "
                       "comparecimento baixo, sistema fora, etc.). Use quando perguntarem 'o que preciso resolver?', "
                       "'tem algum alerta?', 'como está a operação?'.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "resolver_alerta",
        "description": "Marca um alerta do radar como resolvido. Passe um trecho do título do alerta.",
        "parameters": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]},
    }},
    {"type": "function", "function": {
        "name": "anotar",
        "description": "Salva uma anotação livre do dono (ex: 'anota pra ligar pro fornecedor', 'lembra de cobrar o Carlos'). "
                       "Diferente de lembrete: não tem horário, fica numa lista de pendências pessoais.",
        "parameters": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]},
    }},
    {"type": "function", "function": {
        "name": "listar_notas",
        "description": "Lista as anotações livres em aberto.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "resolver_nota",
        "description": "Marca uma anotação como concluída. Passe um trecho do texto da nota.",
        "parameters": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]},
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
    {"type": "function", "function": {
        "name": "lista_ligar_hoje",
        "description": "Lista de clientes pra contatar hoje (faltas de ontem, reservas paradas, entregas atrasadas, a receber), com telefone e o motivo. Você liga — o agente só organiza.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "mensagem_cobranca",
        "description": "Monta uma mensagem pronta de cobrança pra um cliente que tem saldo a receber, pra você revisar e encaminhar.",
        "parameters": {"type": "object", "properties": {
            "cliente": {"type": "string", "description": "Nome do cliente"},
            "veiculo": {"type": "string", "description": "Carro (opcional, p/ desambiguar)"}}},
    }},
    {"type": "function", "function": {
        "name": "giro_estoque",
        "description": "Tempo de giro do estoque: quantos dias cada carro está parado (a anunciar/anunciado/reservado), média e os mais parados.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "margem_estoque",
        "description": "Margem real (venda − custo) das vendas ligadas a um carro do estoque, num período.",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO}},
    }},
    {"type": "function", "function": {
        "name": "definir_meta",
        "description": "Define ou atualiza a meta do mês — da loja ou de um vendedor (meta de vendas e/ou faturamento).",
        "parameters": {"type": "object", "properties": {
            "meta_vendas": {"type": "integer", "description": "Qtd de carros alvo no mês"},
            "meta_faturamento": {"type": "number", "description": "Faturamento alvo em R$"},
            "vendedor": {"type": "string", "description": "Nome do vendedor (vazio = meta da loja)"},
            "mes": {"type": "string", "description": "Mês ISO YYYY-MM (vazio = mês atual)"}}},
    }},
    {"type": "function", "function": {
        "name": "progresso_metas",
        "description": "Quanto já foi realizado das metas do mês (loja e vendedores), em % e valores.",
        "parameters": {"type": "object", "properties": {"mes": {"type": "string", "description": "Mês ISO YYYY-MM (opcional)"}}},
    }},
    {"type": "function", "function": {
        "name": "listar_recalls",
        "description": "Recalls/retornos de clientes em aberto (grupo RECALL): cliente, veículo e motivo.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "meta_ads_resumo",
        "description": "Resumo do Meta Ads no período: gasto, impressões, cliques, leads, conversas no WhatsApp e custo por lead.",
        "parameters": {"type": "object", "properties": {"periodo": {"type": "string", "enum": ["hoje", "ontem", "semana", "mes", "7d", "30d"]}}},
    }},
    {"type": "function", "function": {
        "name": "meta_ads_roas",
        "description": "Cruza o gasto do Meta Ads com o faturamento/vendas do canal Tráfego (ROAS e custo por venda).",
        "parameters": {"type": "object", "properties": {"periodo": _PERIODO}},
    }},
]

SYSTEM = """Você é o assistente da Loja SB (revenda de carros) respondendo o DONO no WhatsApp.
Use SEMPRE as ferramentas para buscar dados reais — nunca invente números.

CONTAGEM x VALOR: para CONTAR vendidos use 'vendidos' (fonte oficial = grupo de VENDAS; a venda conta no aviso "vendido", o Resumo de Venda é pendência). Para FATURAMENTO/ticket/over use 'resumo_vendas'.
SEMPRE que falar de vendas/faturamento/financeiro, informe os DOIS juntos: FATURAMENTO (resumo_vendas) E A RECEBER (a_receber_vendas). Para 'quanto falta receber' use SEMPRE a_receber_vendas.

PERÍODOS: quando o usuário não disser, assuma o mês atual. Para períodos livres ("semana passada", "dia 5", "em maio", "últimos 7 dias"), \
calcule data_inicio/data_fim em ISO a partir da DATA DE HOJE informada e passe nas ferramentas que aceitam (vendidos, resumo_vendas, ranking_vendedores, vendas_por_canal, margem_avaliacoes, conversao, resumo_agendamentos).

ANÁLISES: você tem vendas_por_canal (qual portal vende mais), margem_avaliacoes (FIPE x avaliado), conversao (agendou→vendeu %), historico_cliente (jornada de um cliente).

AÇÕES quando o dono pedir: marcar_entregue, marcar_pago, marcar_anunciado, e EDITAR registros: atualizar_venda \
(corrigir canal/valor/vendedor/pagamento/datas/cliente de uma venda — ex 'a venda do Denilson foi pelo tráfego', 'a do João foi 95 mil') \
e atualizar_carro (preço/status/cor/km do estoque). Confirme em 1 linha o que mudou (ou que não encontrou). Nunca invente se a ferramenta der erro. \
LEMBRETES: criar_lembrete quando o dono pedir p/ ser lembrado (calcule 'quando' em ISO a partir da DATA DE HOJE); listar_lembretes p/ ver os pendentes.
SUPERVISOR: você é proativo. radar mostra os alertas abertos (carro encalhado, a receber parado, entrega atrasada, comparecimento baixo, sistema fora) — use quando perguntarem 'o que preciso resolver?'/'como está a operação?', e resolver_alerta p/ baixar um. anotar salva recado livre sem horário; listar_notas/resolver_nota gerenciam. pendências (listar_pendencias) mostra o que aguarda sua confirmação.
SECRETÁRIA (assistido — você organiza, o DONO contata): lista_ligar_hoje dá quem ligar hoje (faltas de ontem, reservas paradas, entrega atrasada, a receber) com telefone — use em 'quem preciso contatar?'/'tem alguém pra ligar?'. mensagem_cobranca monta um texto pronto e educado de cobrança pra um cliente com saldo (ex 'manda uma cobrança pro João'); ENTREGUE o texto pro dono copiar/encaminhar e deixe claro que ele revisa e envia — você NUNCA manda direto pro cliente.
LEADS (grupo Agendamento SDR — fonte oficial de visitas e negociações por telefone): use leads_abertos, buscar_lead e resumo_leads para qualquer pergunta sobre agendamentos, leads, SDRs, retorno de vendedor e cobranças. Tipos: Visita (horário na loja), Negociação telefone, Turno (horário e vendedor definidos).
COBRANÇAS A VENDEDORES: o agente ENVIA sim, pelo número do dono, depois da aprovação. Se o dono pedir para mandar mensagem/cobrança a um vendedor, NÃO diga que não envia: explique que basta responder à cobrança pendente com 'ok CÓDIGO', 'não CÓDIGO' ou 'texto novo: <mensagem>'.
VENDAS do grupo: vendas_pendentes (sem resumo, resumo incompleto, reservas) e buscar_venda (detalhe de uma venda com comprovantes de pagamento).

ESTILO: curto e direto, em português, valores como R$ 95.000, listas em linhas curtas com emojis discretos. \
Quando fizer sentido, acrescente UM insight curto (ex.: quem está puxando o mês, alerta de comparecimento/entrega atrasada) — sem encher. \
Use o histórico da conversa para entender perguntas curtas de continuação ("e do Carlos?", "e esse mês?")."""


def _system_dinamico() -> str:
    h = datas.agora()
    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    return SYSTEM + f"\n\nDATA E HORA DE HOJE (horário de Brasília): {h.strftime('%Y-%m-%d %H:%M')} ({dias[h.weekday()]})."


_MEM_TURNOS = 20      # mensagens recentes mantidas na íntegra
_MEM_TRUNC = 2000     # corte por mensagem (evita estourar contexto com dump de tool/imagem)
_MEM_COMPACTAR = 30   # acima disso, comprime as antigas num resumo rolante


def _trunc(s: str | None) -> str:
    s = s or ""
    return s if len(s) <= _MEM_TRUNC else s[:_MEM_TRUNC] + " …"


def carregar_historico(numero: str, limite: int = _MEM_TURNOS) -> list:
    rows = db.select("conversas", {"select": "papel,conteudo", "numero": f"eq.{numero}",
                                   "papel": "in.(user,assistant)",
                                   "order": "created_at.desc", "limit": str(limite)})
    rows.reverse()
    return [{"role": r["papel"], "content": _trunc(r["conteudo"])} for r in rows]


def carregar_resumo(numero: str) -> str:
    rows = db.select("conversas", {"select": "conteudo", "numero": f"eq.{numero}",
                                   "papel": "eq.resumo", "order": "created_at.desc", "limit": "1"})
    return rows[0]["conteudo"] if rows else ""


def salvar_conversa(numero: str, papel: str, conteudo: str) -> None:
    try:
        db.insert("conversas", {"numero": numero, "papel": papel, "conteudo": _trunc(conteudo)})
    except Exception:
        pass
    if papel == "assistant":  # fim de um turno → tenta compactar a memória antiga
        try:
            _compactar_memoria(numero)
        except Exception:
            log.exception("erro compactando memória")


def _compactar_memoria(numero: str) -> None:
    if _client is None:
        return
    msgs = db.select_all("conversas", {"select": "id,papel,conteudo,created_at", "numero": f"eq.{numero}",
                                       "papel": "in.(user,assistant)", "order": "created_at.asc"})
    if len(msgs) <= _MEM_COMPACTAR:
        return
    antigas = msgs[:-_MEM_TURNOS]  # tudo além das 20 recentes vira resumo
    if not antigas:
        return
    prev = carregar_resumo(numero)
    texto = "\n".join(f"{m['papel']}: {_trunc(m['conteudo'])}" for m in antigas)
    resp = _client.chat.completions.create(
        model=settings.openai_model_consulta, temperature=0,
        messages=[{"role": "system", "content": "Resuma de forma compacta o contexto desta conversa entre o "
                   "dono da loja e o assistente, preservando fatos, números, decisões e pendências citadas. "
                   "Máximo 1200 caracteres."},
                  {"role": "user", "content": (f"Resumo anterior:\n{prev}\n\n" if prev else "") + "Mensagens:\n" + texto}])
    novo = resp.choices[0].message.content or prev
    db.delete("conversas", {"numero": f"eq.{numero}", "papel": "eq.resumo"})
    db.insert("conversas", {"numero": numero, "papel": "resumo", "conteudo": novo[:_MEM_TRUNC]})
    ids = [m["id"] for m in antigas]
    for i in range(0, len(ids), 100):
        lote = ids[i:i + 100]
        db.delete("conversas", {"numero": f"eq.{numero}", "id": f"in.({','.join(lote)})"})


def responder(pergunta: str, historico: list | None = None, numero: str | None = None) -> str:
    if _client is None:
        return "IA não configurada (falta OPENAI_API_KEY)."
    _ctx["numero"] = numero
    sistema = _system_dinamico()
    resumo = carregar_resumo(numero) if numero else ""
    if resumo:
        sistema += f"\n\nRESUMO DA CONVERSA ATÉ AQUI:\n{resumo}"
    messages = [{"role": "system", "content": sistema}]
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
