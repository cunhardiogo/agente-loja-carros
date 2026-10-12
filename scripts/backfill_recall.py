"""Importa o grupo RECALL (Evolution) a partir de uma data. Uso: python -m scripts.backfill_recall 2026-10-01 [--historico]
Sem --historico os recalls entram vivos (Trello + aviso)."""
import sys
from datetime import datetime

from app import datas, recall_grupo
from scripts.backfill_leads import buscar

GRUPO = "120363414450144897@g.us"

if __name__ == "__main__":
    desde = datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-10-01").replace(tzinfo=datas.TZ)
    for m in buscar(desde, GRUPO):
        print(recall_grupo.processar(m, historico="--historico" in sys.argv))
