"""Saúde da captura: repescagem de mensagens que não chegaram pelo webhook (desconexão, sincronização do
histórico, falha da IA), aviso de WhatsApp de captura desconectado e de OpenAI sem crédito."""
import logging
import time
from datetime import timedelta

import httpx

from . import datas, db, evolution, llm
from .config import settings
from .leads import _iso

log = logging.getLogger("agente")

REPESCA_HORAS = 2
_estado = {"conexao_ruim": 0, "conexao_avisada": 0.0, "ia_avisada": 0.0}


def _cliente() -> httpx.Client:
    return httpx.Client(base_url=settings.evolution_url.rstrip("/"), headers={"apikey": settings.evolution_apikey},
                        verify=settings.verify_ssl, timeout=60)


def mensagens_desde(c: httpx.Client, jid: str, desde_ts: float) -> list[dict]:
    out, page = [], 1
    while True:
        r = c.post(f"/chat/findMessages/{settings.evolution_instance}",
                   json={"where": {"key": {"remoteJid": jid}}, "limit": 100, "page": page})
        r.raise_for_status()
        d = r.json()["messages"]
        out += [m for m in d["records"] if int(m["messageTimestamp"]) >= desde_ts]
        if page >= d.get("pages", 1) or any(int(m["messageTimestamp"]) < desde_ts for m in d["records"]):
            return sorted(out, key=lambda m: int(m["messageTimestamp"]))
        page += 1


def repescar(rotear, horas: float = REPESCA_HORAS) -> int:
    """Passa pelo roteamento normal toda mensagem das últimas horas que ainda não está no arquivo.
    Os tratamentos são idempotentes (trava por message_id), então repassar é seguro."""
    desde = datas.agora() - timedelta(hours=horas)
    vistas = {r["message_id"] for r in db.select_all("mensagens", {"select": "message_id", "em": f"gte.{_iso(desde)}"})}
    n = 0
    with _cliente() as c:
        for g in db.select("grupos", {"select": "jid,nome", "ativo": "eq.true"}):
            try:
                ms = mensagens_desde(c, g["jid"], desde.timestamp())
            except Exception:
                log.exception("repescagem: falha lendo %s", g["nome"])
                continue
            for m in ms:
                if m["key"]["id"] in vistas:
                    continue
                try:
                    rotear({"instance": settings.evolution_instance, "event": "messages.upsert", "data": m})
                    n += 1
                except Exception:
                    log.exception("repescagem: falha processando %s", m["key"]["id"])
    if n:
        log.info("repescagem: %s mensagens recuperadas", n)
    return n


def checar_conexao() -> None:
    """WhatsApp de captura desconectado = nenhum grupo é lido. Avisa o dono (pelo número do assistente)."""
    try:
        with _cliente() as c:
            estado = c.get(f"/instance/connectionState/{settings.evolution_instance}").json()["instance"]["state"]
    except Exception:
        estado = "erro"
    if estado == "open":
        if _estado["conexao_avisada"]:
            _avisar("✅ O WhatsApp de captura (4895) reconectou. Já estou recuperando as mensagens que ficaram para trás.")
        _estado.update({"conexao_ruim": 0, "conexao_avisada": 0.0})
        return
    _estado["conexao_ruim"] += 1
    if _estado["conexao_ruim"] >= 2 and time.time() - _estado["conexao_avisada"] > 2 * 3600:
        _avisar("⚠️ O WhatsApp de captura (4895) está DESCONECTADO — nenhum grupo está sendo lido. "
                "Precisa ler o QR de novo (me peça que eu gero).")
        _estado["conexao_avisada"] = time.time()


def checar_ia() -> None:
    """Crédito da OpenAI acabou = o agente para de ler formulário, foto, comprovante e de responder."""
    try:
        llm._client.chat.completions.create(model=settings.openai_model_extracao, max_tokens=1,
                                            messages=[{"role": "user", "content": "ok"}])
        _estado["ia_avisada"] = 0.0
    except Exception as e:
        texto = str(e)
        if ("insufficient_quota" in texto or "credit" in texto) and time.time() - _estado["ia_avisada"] > 6 * 3600:
            _avisar("⚠️ A OpenAI está SEM CRÉDITO — o agente não consegue ler formulários, fotos e comprovantes "
                    "nem responder perguntas. Recarregue em platform.openai.com/settings/organization/billing.")
            _estado["ia_avisada"] = time.time()


def _avisar(texto: str) -> None:
    try:
        evolution.enviar_texto(settings.meu_numero, texto)
    except Exception:
        log.exception("falha avisando o dono")
