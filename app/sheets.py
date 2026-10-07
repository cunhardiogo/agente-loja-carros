"""Planilha de controle (Google Sheets) — espelho dos leads do grupo de agendamento.
Escrita por conta de serviço (GOOGLE_SA_JSON) na planilha CONTROLE_SHEET_ID."""
import base64
import json
import logging

import httpx

from . import datas, db
from .config import settings

log = logging.getLogger("agente")

ABA_AGENDAMENTOS = "Agendamentos"
_API = "https://sheets.googleapis.com/v4/spreadsheets"
_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

CABECALHO = ["Recebido em", "SDR", "Tipo", "Cliente", "Telefone", "Data", "Horário", "Veículo", "Vendedor",
             "Canal", "Troca", "Oferta / entrada", "Observação", "Status", "Último retorno", "Retorno (texto)",
             "Cobrança", "Código"]


def configurado() -> bool:
    return bool(settings.google_sa_json and settings.controle_sheet_id)


def _credenciais():
    from google.oauth2 import service_account
    bruto = settings.google_sa_json.strip()
    if not bruto.startswith("{"):
        bruto = base64.b64decode(bruto).decode()
    return service_account.Credentials.from_service_account_info(json.loads(bruto), scopes=[_SCOPE])


def _token() -> str:
    from google.auth.transport.requests import Request
    cred = _credenciais()
    cred.refresh(Request())
    return cred.token


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return ""
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(datas.TZ).strftime("%d/%m/%Y %H:%M")


def _fmt_d(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else ""


def linhas_leads(rows: list[dict]) -> list[list[str]]:
    from .leads import STATUS_LABEL, TIPO_LABEL, codigo
    out = [CABECALHO]
    for l in rows:
        status = STATUS_LABEL.get(l["status"], l["status"])
        if l.get("nova_data"):
            status += f" ({_fmt_d(l['nova_data'])})"
        out.append([
            _fmt_dt(l["recebido_em"]), l.get("sdr") or "", TIPO_LABEL.get(l["tipo"], l["tipo"]),
            l.get("cliente_nome") or "", l.get("telefone") or "", _fmt_d(l.get("data_agendada")),
            l.get("horario") or "", l.get("veiculo") or "", l.get("vendedor_nome") or "", l.get("canal") or "",
            l.get("troca") or "", l.get("oferta_entrada") or "", l.get("observacao") or "", status,
            _fmt_dt(l.get("ultimo_retorno_em")), l.get("ultimo_retorno_texto") or "",
            l.get("cobranca_status") or "", codigo(l),
        ])
    return out


def _garantir_aba(c: httpx.Client, aba: str) -> None:
    meta = c.get(f"/{settings.controle_sheet_id}", params={"fields": "sheets.properties.title"}).json()
    if aba in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        return
    c.post(f"/{settings.controle_sheet_id}:batchUpdate",
           json={"requests": [{"addSheet": {"properties": {"title": aba, "gridProperties": {"frozenRowCount": 1}}}}]}
           ).raise_for_status()


def sincronizar_leads() -> int:
    """Reescreve a aba Agendamentos com todos os leads (não removidos), do mais novo pro mais antigo."""
    if not configurado():
        return 0
    rows = db.select_all("leads", {"select": "*", "removido": "eq.false", "order": "recebido_em.desc"})
    valores = linhas_leads(rows)
    with httpx.Client(base_url=_API, headers={"Authorization": f"Bearer {_token()}"},
                      verify=settings.verify_ssl, timeout=60) as c:
        _garantir_aba(c, ABA_AGENDAMENTOS)
        c.post(f"/{settings.controle_sheet_id}/values/{ABA_AGENDAMENTOS}!A:Z:clear").raise_for_status()
        c.put(f"/{settings.controle_sheet_id}/values/{ABA_AGENDAMENTOS}!A1",
              params={"valueInputOption": "RAW"}, json={"values": valores}).raise_for_status()
    return len(valores) - 1
