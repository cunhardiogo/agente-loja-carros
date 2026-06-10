import httpx

from .config import settings

_base = settings.supabase_url.rstrip("/") + "/rest/v1"
_headers = {
    "apikey": settings.supabase_service_role,
    "Authorization": f"Bearer {settings.supabase_service_role}",
    "Content-Type": "application/json",
}
_client = httpx.Client(base_url=_base, headers=_headers, verify=settings.verify_ssl, timeout=30)


def _esc(valor) -> str:
    """Escapa um valor para filtro PostgREST: envolve em aspas (neutraliza ,.:()*)
    e escapa \\ e " internos. Use via ilike()/eq_text() para evitar injeção de filtro."""
    return str(valor).replace("\\", "\\\\").replace('"', '\\"')


def ilike(valor: str) -> str:
    """Valor de filtro `ilike.*termo*` seguro (termo entre aspas, wildcards fora)."""
    return f'ilike."*{_esc(valor)}*"'


def eq_text(valor) -> str:
    """Valor de filtro `eq."..."` seguro para texto vindo de usuário/extração."""
    return f'eq."{_esc(valor)}"'


def select(table: str, params: dict | None = None) -> list[dict]:
    r = _client.get(f"/{table}", params=params or {})
    r.raise_for_status()
    return r.json()


def select_all(table: str, params: dict | None = None, page: int = 1000) -> list[dict]:
    """Como select(), mas pagina via header Range até esgotar (PostgREST limita ~1000/req)."""
    base = dict(params or {})
    out: list[dict] = []
    offset = 0
    while True:
        r = _client.get(f"/{table}", params=base,
                        headers={"Range-Unit": "items", "Range": f"{offset}-{offset + page - 1}"})
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return out


def insert(table: str, data: dict) -> dict:
    r = _client.post(f"/{table}", json=data, headers={"Prefer": "return=representation"})
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}


def update(table: str, data: dict, params: dict) -> list[dict]:
    r = _client.patch(f"/{table}", json=data, params=params, headers={"Prefer": "return=representation"})
    r.raise_for_status()
    return r.json()


def delete(table: str, params: dict) -> None:
    r = _client.delete(f"/{table}", params=params)
    r.raise_for_status()


def upsert(table: str, rows: list[dict], on_conflict: str) -> int:
    """Insere/atualiza em lote numa requisição (ON CONFLICT pela coluna on_conflict)."""
    if not rows:
        return 0
    r = _client.post(f"/{table}", params={"on_conflict": on_conflict}, json=rows,
                     headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    r.raise_for_status()
    return len(rows)
