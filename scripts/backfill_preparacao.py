"""Importa o grupo PREPARAÇÃO (Evolution) a partir de uma data como histórico (sem Trello, sem cobrança).
Uso: python -m scripts.backfill_preparacao 2026-10-01"""
import sys
from datetime import datetime

from app import datas, preparacao_grupo
from app.config import settings
from scripts.backfill_leads import buscar

GRUPO = "120363185008258158@g.us"

if __name__ == "__main__":
    desde = datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-10-01").replace(tzinfo=datas.TZ)
    total: dict = {}
    for m in buscar(desde, GRUPO):
        r = preparacao_grupo.processar(m, settings.evolution_instance, settings.evolution_apikey, historico=True)
        for k, v in r.items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
    print(total)
