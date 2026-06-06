import httpx

from .config import settings

_base = settings.supabase_url.rstrip("/") + "/rest/v1"
_headers = {
    "apikey": settings.supabase_service_role,
    "Authorization": f"Bearer {settings.supabase_service_role}",
    "Content-Type": "application/json",
}
_client = httpx.Client(base_url=_base, headers=_headers, verify=settings.verify_ssl, timeout=30)


def select(table: str, params: dict | None = None) -> list[dict]:
    r = _client.get(f"/{table}", params=params or {})
    r.raise_for_status()
    return r.json()


def insert(table: str, data: dict) -> dict:
    r = _client.post(f"/{table}", json=data, headers={"Prefer": "return=representation"})
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def update(table: str, data: dict, params: dict) -> list[dict]:
    r = _client.patch(f"/{table}", json=data, params=params, headers={"Prefer": "return=representation"})
    r.raise_for_status()
    return r.json()


def upsert(table: str, rows: list[dict], on_conflict: str) -> int:
    """Insere/atualiza em lote numa requisição (ON CONFLICT pela coluna on_conflict)."""
    if not rows:
        return 0
    r = _client.post(f"/{table}", params={"on_conflict": on_conflict}, json=rows,
                     headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    r.raise_for_status()
    return len(rows)
