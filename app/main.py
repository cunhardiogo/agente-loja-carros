import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import confirmacao, consulta, datas, db, evolution, ingest, media, planilha
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agente")

app = FastAPI(title="Agente Loja de Carros — Grupo SB")


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
    from_me = bool(key.get("fromMe"))

    eh_assistente = bool(settings.evolution_assist_instance) and instancia == settings.evolution_assist_instance
    apikey = settings.evolution_assist_apikey if eh_assistente else settings.evolution_apikey

    # texto direto OU transcrição de áudio OU leitura de imagem
    texto = media.conteudo_texto(instancia, apikey, data)
    if not texto:
        return {"ignored": "sem_conteudo"}

    # DM no número assistente -> consulta do dono (Fase 3)
    if not jid.endswith("@g.us"):
        if from_me:
            return {"ignored": "dm_propria"}  # evita reprocessar a resposta do bot
        numero = jid.split("@")[0]
        autorizado = settings.meu_numero and numero == settings.meu_numero
        if autorizado and (eh_assistente or not settings.evolution_assist_instance):
            return _consulta(texto, numero)
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


def _consulta(pergunta: str, numero: str):
    try:
        # primeiro tenta resolver uma confirmação pendente (sim/não/corrigir); senão, responde a pergunta
        resposta = confirmacao.tentar_resolver(pergunta) or consulta.responder(pergunta)
    except Exception:
        log.exception("erro na consulta")
        resposta = "Tive um problema ao consultar agora. Pode tentar de novo?"
    try:
        evolution.enviar_texto(numero, resposta)
    except Exception:
        log.exception("erro enviando resposta")
    return {"consulta": pergunta, "respondido": True}


# ===== Dashboard (servido pela própria Render) =====
def _metrics() -> dict:
    a_receber = consulta.pendencias("pagamento")
    a_entregar = consulta.pendencias("entrega")
    pendentes = db.select("eventos_brutos", {"select": "id", "status": "eq.pendente_confirmacao"})
    return {
        "vendidos_mes": consulta.vendidos("mes"),
        "vendas_mes": consulta.resumo_vendas("mes"),
        "ranking": consulta.ranking_vendedores("mes")["ranking"],
        "a_receber": {"quantidade": a_receber["quantidade"], "valor": a_receber["valor_total_a_receber"],
                      "itens": a_receber["itens"]},
        "a_entregar": {"quantidade": a_entregar["quantidade"], "itens": a_entregar["itens"]},
        "estoque": consulta.listar_carros("anunciado"),
        "agendamentos": consulta.resumo_agendamentos("mes"),
        "entregas": consulta.entregas_agendadas("mes"),
        "avaliacoes": consulta.listar_avaliacoes("mes"),
        "pendentes_confirmacao": len(pendentes),
    }


@app.get("/api/metrics")
def api_metrics(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return _metrics()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    caminho = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    with open(caminho, encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ===== Resumo diário (disparado por agendador externo) =====
def _brl(x) -> str:
    return f"R$ {(x or 0):,.0f}".replace(",", ".")


def _resumo_diario_texto() -> str:
    v = consulta.resumo_vendas("hoje")
    vend = consulta.vendidos("hoje")
    ag = consulta.resumo_agendamentos("hoje")
    receber = consulta.pendencias("pagamento")
    entregar = consulta.entregas_agendadas("hoje")
    pend = len(db.select("eventos_brutos", {"select": "id", "status": "eq.pendente_confirmacao"}))
    linhas = [
        f"📊 *Fechamento de hoje* ({datas.hoje().strftime('%d/%m')})",
        f"🏆 Vendidos hoje: {vend['quantidade']}",
        f"💵 Faturamento (c/ valor): {_brl(v['valor_total'])}",
        f"💰 A receber (total): {_brl(receber['valor_total_a_receber'])}",
        f"📦 Entregas hoje: {entregar['quantidade']} carro(s)",
        f"📅 Agendamentos hoje: {ag['total']} (✅ {ag['compareceram']} | ❌ {ag['faltaram']})",
    ]
    if pend:
        linhas.append(f"⚠️ {pend} pendente(s) de confirmação")
    return "\n".join(linhas)


@app.api_route("/cron/sync-planilha", methods=["GET", "POST"])
def cron_sync_planilha(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        return planilha.sincronizar()
    except Exception as e:
        log.exception("erro sync planilha")
        return JSONResponse({"erro": str(e)}, status_code=500)


@app.api_route("/cron/resumo-diario", methods=["GET", "POST"])
def cron_resumo(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        planilha.sincronizar()  # garante planilha fresca antes do fechamento
    except Exception:
        log.exception("erro sync planilha no resumo")
    texto = _resumo_diario_texto()
    evolution.notificar_dono(texto)
    return {"enviado": True, "resumo": texto}
