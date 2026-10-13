"""Carrega no arquivo de mensagens o histórico de todos os grupos/conversas monitorados (Evolution).
Uso: python -m scripts.backfill_arquivo 2026-08-01"""
import sys
from datetime import datetime

from app import arquivo, datas, db
from scripts.backfill_leads import buscar

if __name__ == "__main__":
    desde = datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-08-01").replace(tzinfo=datas.TZ)
    for g in db.select("grupos", {"select": "jid,nome"}):
        linhas = [r for m in buscar(desde, g["jid"]) if (r := arquivo.linha(m, g["nome"]))]
        for i in range(0, len(linhas), 200):
            db.upsert("mensagens", linhas[i:i + 200], on_conflict="message_id")
        print(g["nome"], len(linhas))
