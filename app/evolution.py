import httpx

from .config import settings

_base = settings.evolution_url.rstrip("/")


def _send(instance: str, apikey: str, numero: str, texto: str) -> dict:
    with httpx.Client(
        base_url=_base,
        headers={"apikey": apikey, "Content-Type": "application/json"},
        verify=settings.verify_ssl,
        timeout=30,
    ) as c:
        r = c.post(f"/message/sendText/{instance}", json={"number": numero, "text": texto})
        r.raise_for_status()
        return r.json()


def _assistente() -> tuple[str, str]:
    """Instância que fala com o dono. Usa o assistente se configurado; senão cai no coletor (testes)."""
    if settings.evolution_assist_instance:
        return settings.evolution_assist_instance, (settings.evolution_assist_apikey or settings.evolution_apikey)
    return settings.evolution_instance, settings.evolution_apikey


def enviar_texto(numero: str, texto: str) -> dict:
    inst, key = _assistente()
    return _send(inst, key, numero, texto)


def notificar_dono(texto: str) -> None:
    if settings.meu_numero:
        try:
            enviar_texto(settings.meu_numero, texto)
        except Exception:
            pass
