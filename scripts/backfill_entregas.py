"""Importa as listas do grupo ENTREGAS (Evolution) a partir de uma data, em ordem.
Uso: python -m scripts.backfill_entregas 2026-10-01"""
import sys
from datetime import datetime

from app import datas, entregas_grupo
from scripts.backfill_leads import buscar

GRUPO = "120363378250723351@g.us"


def main(desde_iso: str) -> None:
    msgs = buscar(datetime.fromisoformat(desde_iso).replace(tzinfo=datas.TZ), GRUPO)
    print(f"{len(msgs)} mensagens desde {desde_iso}")
    for m in msgs:
        r = entregas_grupo.processar(m, historico=True)
        if "lista" in r:
            print(" ", datetime.fromtimestamp(int(m["messageTimestamp"]), datas.TZ).strftime("%d/%m %H:%M"), r)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-10-01")
