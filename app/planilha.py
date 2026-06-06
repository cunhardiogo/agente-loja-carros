import csv
import hashlib
import io
import logging
from datetime import datetime

import httpx

from . import db
from .config import settings
from .ingest import resolver_pessoa

log = logging.getLogger("agente")

# Status da planilha -> compareceu
_STATUS_COMPARECEU = {
    "REALIZADO": True, "VENDIDO": True, "COMPARECEU": True, "FINALIZADO": True,
    "CANCELADO": False, "NAO COMPARECEU": False, "NÃO COMPARECEU": False, "FALTOU": False,
    "AGENDADO": None, "REMARCADO": None,
}


def _parse_data(data_str: str, hora_str: str) -> str | None:
    data_str = (data_str or "").strip()
    if not data_str:
        return None
    d = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(data_str, fmt)
            break
        except ValueError:
            continue
    if d is None:
        return None
    try:
        h = datetime.strptime((hora_str or "").strip(), "%H:%M")
        d = d.replace(hour=h.hour, minute=h.minute)
    except ValueError:
        pass
    return d.isoformat()


def _ref(cliente: str, data_str: str, vendedor: str) -> str:
    base = f"{cliente}|{data_str}|{vendedor}".strip().lower()
    return "plan_" + hashlib.md5(base.encode()).hexdigest()[:16]


def sincronizar() -> dict:
    r = httpx.get(settings.planilha_csv_url, timeout=60, verify=settings.verify_ssl, follow_redirects=True)
    r.raise_for_status()
    if "text/csv" not in (r.headers.get("content-type") or ""):
        raise RuntimeError("Planilha não retornou CSV — verifique se está compartilhada por link (Leitor).")

    rows = list(csv.reader(io.StringIO(r.text)))
    novos = atualizados = ignorados = 0

    for row in rows[1:]:
        if len(row) < 6:
            ignorados += 1
            continue
        cliente = (row[1] or "").strip()
        vendedor_nome = (row[2] or "").strip()
        data_str = (row[3] or "").strip()
        hora_str = (row[4] or "").strip()
        status = (row[5] or "").strip()
        veiculo = (row[6].strip() if len(row) > 6 else "")
        canal = (row[7].strip() if len(row) > 7 else "")

        if not cliente or not data_str:
            ignorados += 1
            continue

        ref = _ref(cliente, data_str, vendedor_nome)
        vend = resolver_pessoa(vendedor_nome, "vendedor")
        registro = {
            "cliente_nome": cliente,
            "vendedor_id": vend["id"] if vend else None,
            "data_agendada": _parse_data(data_str, hora_str),
            "compareceu": _STATUS_COMPARECEU.get(status.upper()),
            "origem": "planilha",
            "ref_externa": ref,
            "observacoes": " · ".join([p for p in (status, veiculo, canal) if p]),
        }
        if db.select("agendamentos", {"select": "id", "ref_externa": f"eq.{ref}", "limit": "1"}):
            db.update("agendamentos", registro, {"ref_externa": f"eq.{ref}"})
            atualizados += 1
        else:
            db.insert("agendamentos", registro)
            novos += 1

    return {"novos": novos, "atualizados": atualizados, "ignorados": ignorados, "linhas": len(rows) - 1}
