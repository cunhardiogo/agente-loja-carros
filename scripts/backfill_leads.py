"""Importa o histórico do grupo Agendamento SDR (Evolution) para a tabela leads.
Uso: python -m scripts.backfill_leads 2026-09-25
Leads importados ficam historico=true: não geram aviso nem cobrança."""
import sys
from datetime import datetime

import httpx

from app import datas, leads
from app.config import settings

GRUPO = "120363418933621858@g.us"


def buscar(desde: datetime, grupo: str = GRUPO) -> list[dict]:
    todas, page = {}, 1
    with httpx.Client(base_url=settings.evolution_url.rstrip("/"), verify=settings.verify_ssl, timeout=120,
                      headers={"apikey": settings.evolution_apikey}) as c:
        while True:
            r = c.post(f"/chat/findMessages/{settings.evolution_instance}",
                       json={"where": {"key": {"remoteJid": grupo}}, "limit": 250, "page": page})
            r.raise_for_status()
            d = r.json()["messages"]
            for m in d["records"]:
                todas[m["key"]["id"]] = m
            antigas = [m for m in d["records"] if int(m["messageTimestamp"]) < desde.timestamp()]
            if page >= d.get("pages", 1) or antigas:
                break
            page += 1
    msgs = [m for m in todas.values() if int(m["messageTimestamp"]) >= desde.timestamp()]
    return sorted(msgs, key=lambda m: int(m["messageTimestamp"]))


def main(desde_iso: str) -> None:
    desde = datetime.fromisoformat(desde_iso).replace(tzinfo=datas.TZ)
    msgs = buscar(desde)
    print(f"{len(msgs)} mensagens desde {desde_iso}")
    contagem: dict[str, int] = {}
    for m in msgs:
        res = leads.processar(m, historico=True)
        k = next(iter(res))
        contagem[k] = contagem.get(k, 0) + 1
    print(contagem)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-09-25")
