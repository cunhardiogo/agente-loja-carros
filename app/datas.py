from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")


def agora() -> datetime:
    return datetime.now(TZ)


def hoje() -> date:
    return agora().date()


def hoje_iso() -> str:
    return hoje().isoformat()
