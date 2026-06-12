import logging

import httpx

from .config import settings

log = logging.getLogger("agente")
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


def get_media_base64(instance: str, apikey: str, message_data: dict) -> tuple[str | None, str | None]:
    """Baixa a mídia (áudio/imagem) de uma mensagem e retorna (base64, mimetype)."""
    with httpx.Client(base_url=_base, headers={"apikey": apikey, "Content-Type": "application/json"},
                      verify=settings.verify_ssl, timeout=60) as c:
        for body in ({"message": message_data, "convertToMp4": False},
                     {"message": {"key": message_data.get("key")}}):
            r = c.post(f"/chat/getBase64FromMediaMessage/{instance}", json=body)
            if r.status_code < 400:
                j = r.json()
                return j.get("base64"), j.get("mimetype")
        return None, None


def notificar_dono(texto: str) -> None:
    if settings.meu_numero:
        try:
            enviar_texto(settings.meu_numero, texto)
        except Exception:
            pass


def _destinatarios() -> list[str]:
    nums = [settings.meu_numero] + [n.strip() for n in (settings.numeros_relatorio or "").split(",")]
    return [n for n in dict.fromkeys(nums) if n]  # remove vazios e duplicados


def estado(instance: str, apikey: str) -> str | None:
    with httpx.Client(base_url=_base, headers={"apikey": apikey}, verify=settings.verify_ssl, timeout=20) as c:
        r = c.get(f"/instance/connectionState/{instance}")
        r.raise_for_status()
        return (r.json().get("instance") or {}).get("state")


def enviar_por_coletor(numero: str, texto: str) -> dict:
    return _send(settings.evolution_instance, settings.evolution_apikey, numero, texto)


def numeros_alerta() -> list[str]:
    return [n.strip() for n in (settings.numeros_relatorio or "").split(",") if n.strip()]


def enviar_relatorio(texto: str) -> int:
    """Envia a todos os destinatários. Loga e conta falhas (antes engolia em silêncio)."""
    falhas = 0
    for numero in _destinatarios():
        try:
            enviar_texto(numero, texto)
        except Exception:
            falhas += 1
            log.exception("falha enviando relatório para %s", numero)
    return falhas
