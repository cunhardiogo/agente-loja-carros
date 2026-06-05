import logging

from fastapi import FastAPI, Request

from . import db, ingest
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agente")

app = FastAPI(title="Agente Loja de Carros — Grupo SB")


def _extrair_texto(msg: dict) -> str | None:
    if not msg:
        return None
    if msg.get("conversation"):
        return msg["conversation"]
    if msg.get("extendedTextMessage"):
        return msg["extendedTextMessage"].get("text")
    if msg.get("imageMessage"):
        return msg["imageMessage"].get("caption")
    return None


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True}


@app.post("/webhook/evolution")
async def webhook(request: Request):
    body = await request.json()
    instancia = body.get("instance")
    data = body.get("data") or {}
    key = data.get("key") or {}
    jid = key.get("remoteJid", "")
    message_id = key.get("id")
    texto = _extrair_texto(data.get("message") or {})

    if not texto:
        return {"ignored": "sem_texto"}

    eh_assistente = bool(settings.evolution_assist_instance) and instancia == settings.evolution_assist_instance

    # DM do dono no número assistente -> consulta (Fase 3)
    if not jid.endswith("@g.us"):
        numero = jid.split("@")[0]
        autorizado = settings.meu_numero and numero == settings.meu_numero
        if autorizado and (eh_assistente or not settings.evolution_assist_instance):
            return await _consulta(texto)
        return {"ignored": "dm_nao_autorizado"}

    # Grupos só são ingeridos pelo coletor, nunca pelo assistente
    if eh_assistente:
        return {"ignored": "grupo_no_assistente"}

    grupo = ingest.grupo_por_jid(jid)
    if not grupo:
        return {"ignored": "grupo_nao_monitorado", "jid": jid}

    if ingest.ja_processada(message_id):
        return {"ignored": "duplicada", "message_id": message_id}

    try:
        res = ingest.processar(
            grupo=grupo,
            message_id=message_id,
            remetente=key.get("participant"),
            remetente_nome=data.get("pushName"),
            texto=texto,
            timestamp_msg=None,
        )
        log.info("evento %s", res)
        return res
    except Exception as e:
        log.exception("erro processando")
        return {"error": str(e)}


async def _consulta(pergunta: str):
    # Fase 3: traduzir pergunta -> consulta no Supabase -> resposta natural.
    return {"consulta": pergunta, "todo": "Q&A natural ainda não implementado (Fase 3)"}
