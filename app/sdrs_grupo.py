"""Grupo Sdr's: tudo é arquivado; o relatório de produtividade diário de cada SDR vira número
("Produtividade 15/10/2026 / Leads - 57 / Agendamentos -03 / Negociação -05 / Vendas - 00")."""
import logging
import re
from datetime import date, datetime

from . import datas, db
from .leads import _ascii

log = logging.getLogger("agente")

GRUPO = "120363413734430943@g.us"
_DATA = re.compile(r"(?i)produtividade\D{0,10}(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
_CAMPOS = {"leads": r"leads", "agendamentos": r"agendamentos?", "negociacao": r"negocia\w*", "vendas": r"vendas?"}


def ler(texto: str | None, hoje: date | None = None) -> dict | None:
    t = _ascii(texto or "")
    m = _DATA.search(t)
    if not m:
        return None
    hoje = hoje or datas.hoje()
    ano = int(m.group(3)) if m.group(3) else hoje.year
    try:
        d = date(ano + 2000 if ano < 100 else ano, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    out = {"data": d.isoformat()}
    for campo, rx in _CAMPOS.items():
        v = re.search(rf"(?i)\b{rx}\s*[-:=]?\s*(\d+)", t[m.end():])
        out[campo] = int(v.group(1)) if v else None
    return out if any(out[c] is not None for c in _CAMPOS) else None


def processar(row: dict) -> None:
    """row = linha do arquivo (arquivo.linha). Edição do relatório substitui o do mesmo dia."""
    rel = ler(row.get("texto"))
    if not rel or not row.get("autor_nome"):
        return
    mid = row["citada"] if row.get("tipo") == "edicao" and row.get("citada") else row["message_id"]
    try:
        ja = db.select("sdr_produtividade", {"select": "message_id", "data": f"eq.{rel['data']}",
                                             "sdr": f"eq.{row['autor_nome']}"})
        if ja and ja[0]["message_id"] != mid and row.get("em"):  # data copiada do relatório anterior: vale o dia do envio
            rel["data"] = datetime.fromisoformat(row["em"]).astimezone(datas.TZ).date().isoformat()
        db.upsert("sdr_produtividade", [{**rel, "sdr": row["autor_nome"], "message_id": mid,
                                         "texto": row.get("texto")}], on_conflict="data,sdr")
    except Exception:
        log.exception("produtividade SDR")


def comparar(ini: str, fim: str, leads_rows: list[dict]) -> list[dict]:
    """O que cada SDR reportou x o que o grupo de agendamento registrou (agendamentos por SDR)."""
    rel = db.select_all("sdr_produtividade", {"select": "*", "data": f"gte.{ini}", "and": f"(data.lte.{fim})"})
    out = {}
    for r in rel:
        s = out.setdefault(_ascii(r["sdr"]), {"sdr": r["sdr"], "dias_reportados": 0, "leads": 0, "agendamentos": 0,
                                              "negociacao": 0, "vendas": 0, "formularios_no_grupo_agendamento": 0})
        s["dias_reportados"] += 1
        for c in _CAMPOS:
            s[c] += r.get(c) or 0
    for lead in leads_rows:
        if lead.get("sdr") and _ascii(lead["sdr"]) in out:
            out[_ascii(lead["sdr"])]["formularios_no_grupo_agendamento"] += 1
    return sorted(out.values(), key=lambda s: -s["agendamentos"])
