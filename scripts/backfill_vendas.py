"""Importa o histórico do grupo VENDAS (Evolution) a partir de uma data.
Uso: python -m scripts.backfill_vendas 2026-10-01
Vendas importadas ficam historico=true: não geram cobrança."""
import sys
from datetime import datetime

from app import datas, vendas_grupo
from app.config import settings
from scripts.backfill_leads import buscar

GRUPO = "120363394210533119@g.us"


def main(desde_iso: str) -> None:
    desde = datetime.fromisoformat(desde_iso).replace(tzinfo=datas.TZ)
    msgs = buscar(desde, GRUPO)
    print(f"{len(msgs)} mensagens desde {desde_iso}")
    contagem: dict[str, int] = {}
    for m in msgs:
        try:
            res = vendas_grupo.processar(m, settings.evolution_instance, settings.evolution_apikey, historico=True)
        except Exception as e:
            res = {"erro": str(e)[:80]}
        k = next(iter(res))
        contagem[k] = contagem.get(k, 0) + 1
        if k in ("erro",):
            print("  erro:", res)
    print(contagem)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-10-01")
