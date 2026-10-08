"""Planilha de controle (Google Sheets): abas Agendamentos e Vendas (nos dois sentidos — a equipe edita,
inclui e apaga linhas) e Pagamentos (só saída).
Escrita na planilha CONTROLE_SHEET_ID por conta de serviço (GOOGLE_SA_JSON) ou por
cliente OAuth com refresh token (GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN)."""
import base64
import json
import logging
import re

import httpx

from . import datas, db
from .config import settings

log = logging.getLogger("agente")

ABA_AGENDAMENTOS = "Agendamentos"
# a planilha começa em outubro/2026: o que for anterior fica só no banco
DESDE = "2026-10-01"
_API = "https://sheets.googleapis.com/v4/spreadsheets"
_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

CABECALHO = ["Recebido em", "SDR", "Tipo", "Cliente", "Telefone", "Data", "Horário", "Veículo", "Vendedor",
             "Canal", "Troca", "Oferta / entrada", "Observação", "Status", "Último retorno", "Retorno (texto)",
             "Cobrança", "Código", "ID"]


def configurado() -> bool:
    return bool(settings.controle_sheet_id and (settings.google_sa_json or settings.google_refresh_token))


def _credenciais():
    if settings.google_refresh_token:
        from google.oauth2.credentials import Credentials
        return Credentials(None, refresh_token=settings.google_refresh_token,
                           client_id=settings.google_client_id, client_secret=settings.google_client_secret,
                           token_uri="https://oauth2.googleapis.com/token", scopes=[_SCOPE])
    from google.oauth2 import service_account
    bruto = settings.google_sa_json.strip()
    if not bruto.startswith("{"):
        bruto = base64.b64decode(bruto).decode()
    return service_account.Credentials.from_service_account_info(json.loads(bruto), scopes=[_SCOPE])


def _token() -> str:
    import requests
    from google.auth.transport.requests import Request
    sessao = requests.Session()
    sessao.verify = settings.verify_ssl
    cred = _credenciais()
    cred.refresh(Request(session=sessao))
    return cred.token


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return ""
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(datas.TZ).strftime("%d/%m/%Y %H:%M")


def _fmt_d(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else ""


def _num(v) -> str:
    if v is None:
        return ""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ===== aba Agendamentos =====
# colunas que a equipe pode editar na planilha → campo do lead
EDITAVEIS = {"SDR": "sdr", "Tipo": "tipo", "Cliente": "cliente_nome", "Telefone": "telefone",
             "Data": "data_agendada", "Horário": "horario", "Veículo": "veiculo", "Vendedor": "vendedor_nome",
             "Canal": "canal", "Troca": "troca", "Oferta / entrada": "oferta_entrada",
             "Observação": "observacao", "Status": "status"}


def _linha(l: dict) -> list[str]:
    from .leads import STATUS_LABEL, TIPO_LABEL, codigo
    status = STATUS_LABEL.get(l["status"], l["status"])
    if l.get("nova_data"):
        status += f" ({_fmt_d(l['nova_data'])})"
    return [
        _fmt_dt(l["recebido_em"]), l.get("sdr") or "", TIPO_LABEL.get(l["tipo"], l["tipo"]),
        l.get("cliente_nome") or "", l.get("telefone") or "", _fmt_d(l.get("data_agendada")),
        l.get("horario") or "", l.get("veiculo") or "", l.get("vendedor_nome") or "", l.get("canal") or "",
        l.get("troca") or "", l.get("oferta_entrada") or "", l.get("observacao") or "", status,
        _fmt_dt(l.get("ultimo_retorno_em")), l.get("ultimo_retorno_texto") or "",
        l.get("cobranca_status") or "", codigo(l), l["id"],
    ]


def _snap(linha: list[str]) -> dict:
    return {col: linha[CABECALHO.index(col)] for col in EDITAVEIS}


def linhas_leads(rows: list[dict]) -> list[list[str]]:
    return [CABECALHO] + [_linha(l) for l in rows]


def _converter(col: str, valor: str) -> dict:
    """Valor digitado na planilha → campos do lead."""
    from .leads import STATUS_LABEL, TIPO_LABEL, _ascii, _data, _vendedor, classificar_resposta
    v = valor.strip()
    campo = EDITAVEIS[col]
    if col == "Tipo":
        rev = {_ascii(lbl): k for k, lbl in TIPO_LABEL.items()}
        a = _ascii(v)
        tipo = rev.get(a) or ("turno" if "turno" in a else "negociacao_telefone" if "telefone" in a or "negoc" in a
                              else "visita")
        return {"tipo": tipo}
    if col == "Data":
        d = _data(v, datas.hoje())
        return {"data_agendada": d.isoformat() if d else None}
    if col == "Vendedor":
        vend = _vendedor(v)
        return {"vendedor_nome": v or None, "vendedor_id": vend["id"] if vend else None}
    if col == "Status":
        if not v:
            return {"status": "aguardando_retorno", "nova_data": None}
        rev = {_ascii(lbl): k for k, lbl in STATUS_LABEL.items()}
        base = _ascii(v.split("(")[0]).strip()
        status, nova = (rev[base], None) if base in rev else classificar_resposta(v)
        if status == "remarcado" or "(" in v:
            d = _data(v, datas.hoje())
            nova = d.isoformat() if d else nova
        return {"status": status, "nova_data": nova if status == "remarcado" else None,
                "status_em": datas.agora().isoformat()}
    return {campo: v or None}


# ===== motor comum =====
def _ler(c: httpx.Client, aba: str = ABA_AGENDAMENTOS) -> list[dict]:
    r = c.get(f"/{settings.controle_sheet_id}/values/{aba}!A:AZ")
    r.raise_for_status()
    vals = r.json().get("values", [])
    if not vals:
        return []
    cab = vals[0]
    return [{cab[i]: (linha[i] if i < len(linha) else "") for i in range(len(cab))} for linha in vals[1:]]


def _importar(planilha: list[dict], vis: dict[str, dict], editaveis: dict, converter, tabela: str,
              codigo, chaves_novo: tuple, novo_base) -> int:
    """Aplica no banco o que a equipe mudou/incluiu/apagou numa aba. Retorna nº de mudanças."""
    por_codigo: dict[str, list] = {}
    for r in vis.values():
        por_codigo.setdefault(codigo(r), []).append(r)
    vistos, n = set(), 0
    for linha in planilha:
        rid = (linha.get("ID") or "").strip()
        if not rid and len(por_codigo.get((linha.get("Código") or "").strip().upper(), [])) == 1:
            rid = por_codigo[linha["Código"].strip().upper()][0]["id"]
        if rid in vis:
            vistos.add(rid)
            snap = vis[rid].get("planilha_snap") or {}
            if not snap:
                continue
            upd = {}
            for col in editaveis:
                if col in linha and (linha[col] or "").strip() != (snap.get(col) or "").strip():
                    upd.update(converter(col, linha[col] or ""))
            if upd:
                db.update(tabela, upd, {"id": f"eq.{rid}"})
                n += 1
        elif not rid and any((linha.get(k) or "").strip() for k in chaves_novo):
            novo = novo_base()
            for col in editaveis:
                if (linha.get(col) or "").strip():
                    novo.update(converter(col, linha[col]))
            db.insert(tabela, novo)
            n += 1
    for rid, r in vis.items():
        if r.get("planilha_snap") and rid not in vistos:
            db.update(tabela, {"removido": True}, {"id": f"eq.{rid}"})
            n += 1
    return n


def importar_edicoes(planilha: list[dict], leads_vis: dict[str, dict]) -> int:
    from uuid import uuid4
    from .leads import codigo
    return _importar(planilha, leads_vis, EDITAVEIS, _converter, "leads", codigo, ("Cliente", "Telefone", "Veículo"),
                     lambda: {"message_id": f"manual:{uuid4()}", "recebido_em": datas.agora().isoformat(),
                              "tipo": "visita", "fila": "planilha", "historico": True})


def _garantir_aba(c: httpx.Client, aba: str) -> None:
    meta = c.get(f"/{settings.controle_sheet_id}", params={"fields": "sheets.properties.title"}).json()
    if aba in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        return
    c.post(f"/{settings.controle_sheet_id}:batchUpdate",
           json={"requests": [{"addSheet": {"properties": {"title": aba, "gridProperties": {"frozenRowCount": 1}}}}]}
           ).raise_for_status()


def _escrever_se_mudou(c: httpx.Client, aba: str, cabecalho: list[str], planilha: list[dict],
                       valores: list[list[str]]) -> None:
    atual = [[linha.get(col, "") for col in cabecalho] for linha in planilha]
    if [cabecalho] + atual != valores:
        c.post(f"/{settings.controle_sheet_id}/values/{aba}!A:AZ:clear").raise_for_status()
        c.put(f"/{settings.controle_sheet_id}/values/{aba}!A1",
              params={"valueInputOption": "RAW"}, json={"values": valores}).raise_for_status()


def _gravar_snaps(tabela: str, rows: list[dict], valores: list[list[str]], cabecalho: list[str], editaveis) -> None:
    for r, linha in zip(rows, valores[1:]):
        snap = {col: linha[cabecalho.index(col)] for col in editaveis}
        if r.get("planilha_snap") != snap:
            db.update(tabela, {"planilha_snap": snap}, {"id": f"eq.{r['id']}"})


def _cliente() -> httpx.Client:
    return httpx.Client(base_url=_API, headers={"Authorization": f"Bearer {_token()}"},
                        verify=settings.verify_ssl, timeout=60)


def _visiveis() -> list[dict]:
    return db.select_all("leads", {"select": "*", "removido": "eq.false", "order": "recebido_em.desc",
                                   "data_agendada": f"gte.{DESDE}"})


def sincronizar_leads(c: httpx.Client | None = None) -> int:
    """Lê a aba Agendamentos, aplica no banco as edições da equipe e reescreve com os leads agendados a
    partir de DESDE (mais novo primeiro) — só reescreve se algo mudou."""
    if not configurado():
        return 0
    if c is None:
        with _cliente() as c2:
            return sincronizar_leads(c2)
    _garantir_aba(c, ABA_AGENDAMENTOS)
    planilha = _ler(c, ABA_AGENDAMENTOS)
    if importar_edicoes(planilha, {l["id"]: l for l in _visiveis()}):
        from . import leads
        leads.sujo["v"] = True
    rows = _visiveis()
    valores = linhas_leads(rows)
    _escrever_se_mudou(c, ABA_AGENDAMENTOS, CABECALHO, planilha, valores)
    _gravar_snaps("leads", rows, valores, CABECALHO, EDITAVEIS)
    return len(valores) - 1


# ===== aba Vendas (dois sentidos) =====
ABA_VENDAS = "Vendas"
CAB_VENDAS = ["Data venda", "Vendedor", "Carro", "Versão", "Ano", "Cor", "Placa", "Status", "Tabela", "Vendido",
              "Desconto", "Over", "Total", "Banco", "Financiado", "Pix / pagamento", "Troca", "Valor troca", "Revenda", "Cliente", "CPF",
              "Telefone", "E-mail", "Portal", "Entrega prevista", "Entregue em", "Pago (comprovantes)", "Situação pgto", "Docs",
              "Pendências", "Observação", "Cobrança", "Código", "ID"]
EDIT_VENDAS = {"Data venda": "data_venda", "Vendedor": "vendedor_id", "Carro": "modelo", "Versão": "versao",
               "Ano": "ano", "Cor": "cor", "Placa": "placa", "Status": "status_venda", "Tabela": "tabela_preco",
               "Vendido": "valor_venda", "Desconto": "desconto", "Over": "over_valor", "Total": "valor_total",
               "Banco": "banco", "Financiado": "valor_financiado", "Pix / pagamento": "valor_pix",
               "Troca": "troca_modelo", "Valor troca": "troca_valor", "Revenda": "revenda", "Cliente": "cliente_nome", "CPF": "cliente_cpf",
               "Telefone": "cliente_telefone", "E-mail": "cliente_email", "Portal": "portal_venda",
               "Entrega prevista": "data_entrega_prevista", "Observação": "observacoes"}
_DOC_ROTULO = {"cnh": "CNH", "documento_identidade": "RG", "comprovante_residencia": "Residência", "contrato": "Contrato"}


def _linha_venda(v: dict, nomes: dict) -> list[str]:
    from .vendas_grupo import STATUS_LABEL, codigo
    docs = v.get("docs") or {}
    docs_txt = " · ".join(lbl for k, lbl in _DOC_ROTULO.items() if docs.get(k))
    if docs.get("comprovantes"):
        docs_txt = " · ".join(x for x in (docs_txt, f"{docs['comprovantes']} comprovante(s)") if x)
    return [
        _fmt_d(v.get("data_venda")), nomes.get(v.get("vendedor_id"), ""), v.get("modelo") or "",
        v.get("versao") or "", str(v.get("ano") or ""), v.get("cor") or "", v.get("placa") or "",
        STATUS_LABEL.get(v.get("status_venda"), v.get("status_venda") or ""), _num(v.get("tabela_preco")),
        _num(v.get("valor_venda")), _num(v.get("desconto")), v.get("over_valor") or "", _num(v.get("valor_total")),
        v.get("banco") or "", _num(v.get("valor_financiado")), v.get("valor_pix") or "", v.get("troca_modelo") or "",
        _num(v.get("troca_valor")), "Sim" if v.get("revenda") else "", v.get("cliente_nome") or "", v.get("cliente_cpf") or "", v.get("cliente_telefone") or "",
        v.get("cliente_email") or "", v.get("portal_venda") or "",
        _fmt_d(v.get("data_entrega_prevista")) or (v.get("data_entrega_texto") or ""), _fmt_d(v.get("data_entrega_real")),
        _num(v.get("valor_pago")) if v.get("valor_pago") else "", v.get("status_pagamento") or "", docs_txt,
        ", ".join(v.get("pendencias") or []), v.get("observacoes") or "", v.get("cobranca_status") or "",
        codigo(v), v["id"],
    ]


def _converter_venda(col: str, valor: str) -> dict:
    from .leads import _ascii, _data
    from .vendas_grupo import STATUS_LABEL, _valor, placa_norm, vendedor_por_nome
    v = valor.strip()
    campo = EDIT_VENDAS[col]
    if col in ("Data venda", "Entrega prevista"):
        d = _data(v, datas.hoje())
        out = {campo: d.isoformat() if d else None}
        if col == "Entrega prevista":
            out["data_entrega_texto"] = v or None
        return out
    if col == "Vendedor":
        vend = vendedor_por_nome(v)
        return {"vendedor_id": vend["id"] if vend else None}
    if col == "Status":
        rev = {_ascii(lbl): k for k, lbl in STATUS_LABEL.items()}
        a = _ascii(v)
        return {"status_venda": rev.get(a) or ("reservado" if "reserv" in a else "desistiu" if re.search(r"desist|caiu|cancel", a)
                                               else "aguardando_resumo" if "aguard" in a else "completa")}
    if col == "Ano":
        return {"ano": int(v) if v.isdigit() else None}
    if col == "Placa":
        return {"placa": placa_norm(v) or v or None}
    if col == "Revenda":
        return {"revenda": _ascii(v).startswith(("s", "x"))}
    if campo in ("tabela_preco", "valor_venda", "desconto", "valor_total", "valor_financiado", "troca_valor"):
        return {campo: _valor(v)}
    return {campo: v or None}


def _vendas_visiveis() -> list[dict]:
    rows = db.select_all("vendas", {"select": "*", "removido": "eq.false", "origem": "in.(grupo,planilha)",
                                    "order": "data_venda.desc.nullsfirst,created_at.desc"})
    return [v for v in rows if (v.get("data_venda") or v["created_at"][:10]) >= DESDE]


def sincronizar_vendas(c: httpx.Client) -> int:
    from uuid import uuid4
    from . import vendas_grupo
    _garantir_aba(c, ABA_VENDAS)
    planilha = _ler(c, ABA_VENDAS)
    vis = {v["id"]: v for v in _vendas_visiveis()}
    if _importar(planilha, vis, EDIT_VENDAS, _converter_venda, "vendas", vendas_grupo.codigo,
                 ("Carro", "Placa", "Cliente"),
                 lambda: {"origem": "planilha", "status_venda": "completa", "historico": True,
                          "aviso_message_id": f"manual:{uuid4()}"}):
        for v in _vendas_visiveis():  # a equipe pode ter completado o resumo pela planilha
            pend = vendas_grupo.pendencias_venda(v)
            if pend != (v.get("pendencias") or []):
                db.update("vendas", {"pendencias": pend}, {"id": f"eq.{v['id']}"})
        vendas_grupo.sujo["v"] = True
    rows = _vendas_visiveis()
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    valores = [CAB_VENDAS] + [_linha_venda(v, nomes) for v in rows]
    _escrever_se_mudou(c, ABA_VENDAS, CAB_VENDAS, planilha, valores)
    _gravar_snaps("vendas", rows, valores, CAB_VENDAS, EDIT_VENDAS)
    return len(rows)


# ===== aba Pagamentos (só saída) =====
ABA_PAGAMENTOS = "Pagamentos"
CAB_PAGAMENTOS = ["Recebido em", "Pago em", "Valor", "Forma", "Banco", "Pagador", "Recebedor", "ID transação",
                  "Venda", "Arquivo"]


def sincronizar_pagamentos(c: httpx.Client) -> int:
    _garantir_aba(c, ABA_PAGAMENTOS)
    planilha = _ler(c, ABA_PAGAMENTOS)
    pags = db.select_all("pagamentos", {"select": "*", "recebido_em": f"gte.{DESDE}", "order": "recebido_em.desc"})
    vendas = {v["id"]: v for v in db.select_all("vendas", {"select": "id,modelo,placa"})}
    valores = [CAB_PAGAMENTOS]
    for p in pags:
        v = vendas.get(p.get("venda_id"))
        venda = " ".join(x for x in (v.get("modelo"), v.get("placa")) if x) if v else "sem venda ligada"
        valores.append([_fmt_dt(p["recebido_em"]), _fmt_dt(p.get("pago_em")), _num(p.get("valor")),
                        p.get("tipo") or "", p.get("banco") or "", p.get("pagador") or "", p.get("recebedor") or "",
                        p.get("id_transacao") or "", venda, p.get("arquivo") or ""])
    _escrever_se_mudou(c, ABA_PAGAMENTOS, CAB_PAGAMENTOS, planilha, valores)
    return len(pags)


# ===== aba Entregas (dois sentidos) =====
ABA_ENTREGAS = "Entregas"
CAB_ENTREGAS = ["Dia", "Horário", "Vendedor", "Carro", "O que fazer", "Loja", "Status", "Entregue em", "Venda",
                "Cobrança", "Código", "ID"]
EDIT_ENTREGAS = {"Dia": "data_entrega", "Horário": "horario", "Vendedor": "vendedor_nome", "Carro": "veiculo",
                 "O que fazer": "observacao", "Loja": "loja", "Status": "status"}


def _linha_entrega(e: dict, vendas: dict) -> list[str]:
    from .entregas_grupo import STATUS_LABEL, codigo
    v = vendas.get(e.get("venda_id"))
    venda = " ".join(x for x in (v.get("modelo"), v.get("placa")) if x) if v else ""
    return [_fmt_d(e.get("data_entrega")) or (e.get("data_texto") or ""), e.get("horario") or "",
            e.get("vendedor_nome") or "", e.get("veiculo") or "", e.get("observacao") or "", e.get("loja") or "",
            STATUS_LABEL.get(e.get("status"), e.get("status") or ""), _fmt_dt(e.get("entregue_em")), venda,
            e.get("cobranca_status") or "", codigo(e), e["id"]]


def _converter_entrega(col: str, valor: str) -> dict:
    from .leads import _ascii
    from .entregas_grupo import _data_entrega
    from .vendas_grupo import vendedor_por_nome
    v = valor.strip()
    if col == "Dia":
        return {"data_entrega": _data_entrega(v, datas.hoje()), "data_texto": v or None}
    if col == "Vendedor":
        vend = vendedor_por_nome(v)
        return {"vendedor_nome": v or None, "vendedor_id": vend["id"] if vend else None}
    if col == "Status":
        entregue = _ascii(v).startswith("entreg")
        return {"status": "entregue" if entregue else "agendada",
                "entregue_em": datas.agora().isoformat() if entregue else None}
    return {EDIT_ENTREGAS[col]: v or None}


def _entregas_visiveis() -> list[dict]:
    rows = db.select_all("entregas", {"select": "*", "removido": "eq.false", "origem": "in.(grupo,planilha)",
                                      "order": "status.asc,data_entrega.asc.nullsfirst"})
    return [e for e in rows if (e.get("primeira_vez_em") or e["created_at"])[:10] >= DESDE]


def sincronizar_entregas(c: httpx.Client) -> int:
    from uuid import uuid4
    from . import entregas_grupo, vendas_grupo
    _garantir_aba(c, ABA_ENTREGAS)
    planilha = _ler(c, ABA_ENTREGAS)
    vis = {e["id"]: e for e in _entregas_visiveis()}
    if _importar(planilha, vis, EDIT_ENTREGAS, _converter_entrega, "entregas", entregas_grupo.codigo,
                 ("Carro",), lambda: {"origem": "planilha", "status": "agendada", "historico": True,
                                      "primeira_vez_em": datas.agora().isoformat(), "ref_externa": f"manual:{uuid4()}"}):
        # edição da equipe também vale para a venda ligada (data de entrega e entregue)
        for e in _entregas_visiveis():
            if not e.get("venda_id"):
                continue
            upd = {"data_entrega_prevista": e.get("data_entrega")}
            if e["status"] == "entregue":
                upd.update({"status_entrega": "entregue", "data_entrega_real": (e.get("entregue_em") or "")[:10] or None})
            db.update("vendas", upd, {"id": f"eq.{e['venda_id']}"})
        vendas_grupo.sujo["v"] = True
    rows = _entregas_visiveis()
    vendas = {v["id"]: v for v in db.select_all("vendas", {"select": "id,modelo,placa"})}
    valores = [CAB_ENTREGAS] + [_linha_entrega(e, vendas) for e in rows]
    _escrever_se_mudou(c, ABA_ENTREGAS, CAB_ENTREGAS, planilha, valores)
    _gravar_snaps("entregas", rows, valores, CAB_ENTREGAS, EDIT_ENTREGAS)
    return len(rows)


def sincronizar_tudo() -> dict:
    if not configurado():
        return {}
    with _cliente() as c:
        return {"agendamentos": sincronizar_leads(c), "vendas": sincronizar_vendas(c),
                "entregas": sincronizar_entregas(c), "pagamentos": sincronizar_pagamentos(c)}
