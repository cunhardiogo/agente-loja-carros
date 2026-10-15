"""Recupera mensagens de N horas que não passaram pelo agente (ex.: depois de desconexão do WhatsApp).
Uso: python -m scripts.repescar 20"""
import sys

from app import saude
from app.main import _rotear_evento

if __name__ == "__main__":
    print(saude.repescar(_rotear_evento, horas=float(sys.argv[1]) if len(sys.argv) > 1 else 2,
                         forcar="--forcar" in sys.argv), "mensagens recuperadas")
