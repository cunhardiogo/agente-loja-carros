"""Importa o grupo AVALIAÇÕES (Evolution) a partir de uma data como histórico (sem aviso, sem cartão no Trello).
Uso: python -m scripts.backfill_avaliacoes 2026-10-01"""
import sys
from datetime import datetime

from app import avaliacoes_grupo, datas
from scripts.backfill_leads import buscar

GRUPO = "120363196256311293@g.us"

if __name__ == "__main__":
    desde = datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-10-01").replace(tzinfo=datas.TZ)
    total: dict = {}
    for m in buscar(desde, GRUPO):
        k = next(iter(avaliacoes_grupo.processar(m, historico=True)))
        total[k] = total.get(k, 0) + 1
    print(total, "| fecharam com venda:", avaliacoes_grupo.ligar_vendas())
