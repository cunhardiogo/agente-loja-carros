import base64
import logging

from . import evolution, llm

log = logging.getLogger("agente")


def _citada(msg: dict) -> str:
    """Texto da mensagem citada (reply), pra dar contexto a 'Não veio', 'pago', etc."""
    ctx = (msg.get("extendedTextMessage") or {}).get("contextInfo") or {}
    q = ctx.get("quotedMessage") or {}
    txt = q.get("conversation") or (q.get("extendedTextMessage") or {}).get("text") \
        or (q.get("imageMessage") or {}).get("caption") or ""
    txt = txt.strip()
    return f"[Em resposta a: {txt[:300]}]\n" if txt else ""


def conteudo_texto(instancia: str, apikey: str, data: dict) -> str | None:
    """Extrai texto de uma mensagem: texto direto, transcrição de áudio ou leitura de imagem."""
    msg = data.get("message") or {}

    if msg.get("conversation"):
        return msg["conversation"]
    if msg.get("extendedTextMessage"):
        return _citada(msg) + (msg["extendedTextMessage"].get("text") or "")

    if msg.get("audioMessage"):
        try:
            b64, mt = evolution.get_media_base64(instancia, apikey, data)
            if b64:
                return llm.transcrever_audio(base64.b64decode(b64), mt)
        except Exception:
            log.exception("falha transcrevendo áudio")
        return None

    if msg.get("imageMessage"):
        caption = (msg["imageMessage"].get("caption") or "").strip()
        leitura = ""
        try:
            b64, mt = evolution.get_media_base64(instancia, apikey, data)
            if b64:
                leitura = llm.ler_imagem(b64, mt)
        except Exception:
            log.exception("falha lendo imagem")
        conteudo = (caption + "\n" + leitura).strip()
        return conteudo or None

    return None
