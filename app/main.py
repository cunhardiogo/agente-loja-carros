import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import confirmacao, consulta, datas, db, evolution, ingest, media, planilha
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agente")

app = FastAPI(title="Agente Loja de Carros — Grupo SB")


import time as _time
_ult_lembrete = {"t": 0.0}
_lojasb_ok = {"v": True}


def _checar_lojasb() -> None:
    if not settings.evolution_assist_instance:
        return
    try:
        st = evolution.estado(settings.evolution_assist_instance, settings.evolution_assist_apikey)
    except Exception:
        return
    ok = (st == "open")
    if _lojasb_ok["v"] and not ok:  # caiu
        for n in evolution.numeros_alerta():
            try:
                evolution.enviar_por_coletor(n, "🚨 ALERTA: o agente (lojasb) DESCONECTOU do WhatsApp. "
                                             "Reconecte em evo.agenteintel.com.br/manager — sem isso ele não envia relatórios nem lembretes.")
            except Exception:
                log.exception("erro alertando queda lojasb")
    elif ok and not _lojasb_ok["v"]:  # voltou
        for n in evolution.numeros_alerta():
            try:
                evolution.enviar_por_coletor(n, "✅ Agente (lojasb) reconectado. Tudo normal.")
            except Exception:
                pass
    _lojasb_ok["v"] = ok


def _disparar_lembretes() -> int:
    agora = datas.agora().isoformat()
    rows = db.select("lembretes", {"select": "id,numero,texto", "enviado": "eq.false",
                                   "quando": f"lte.{agora}"})
    n = 0
    for r in rows:
        try:
            evolution.enviar_texto(r["numero"], "⏰ Lembrete: " + r["texto"])
            db.update("lembretes", {"enviado": True}, {"id": f"eq.{r['id']}"})
            n += 1
        except Exception:
            log.exception("erro enviando lembrete")
    return n


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    try:  # aproveita o ping do UptimeRobot (5 min): dispara lembretes + checa conexão do lojasb
        if _time.time() - _ult_lembrete["t"] > 60:
            _ult_lembrete["t"] = _time.time()
            _disparar_lembretes()
            _checar_lojasb()
    except Exception:
        log.exception("erro no check periódico")
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
        resposta = confirmacao.tentar_resolver(pergunta)
        if resposta is None:
            historico = consulta.carregar_historico(numero)
            resposta = consulta.responder(pergunta, historico, numero)
    except Exception:
        log.exception("erro na consulta")
        resposta = "Tive um problema ao consultar agora. Pode tentar de novo?"
    consulta.salvar_conversa(numero, "user", pergunta)
    consulta.salvar_conversa(numero, "assistant", resposta)
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
        "reservados_mes": consulta.reservados("mes"),
        "vendas_mes": consulta.resumo_vendas("mes"),
        "ranking": consulta.ranking_vendedores("mes")["ranking"],
        "a_receber": {"quantidade": a_receber["quantidade"], "valor": a_receber["valor_total_a_receber"],
                      "itens": a_receber["itens"]},
        "a_entregar": {"quantidade": a_entregar["quantidade"], "itens": a_entregar["itens"]},
        "estoque": consulta.listar_carros("a_anunciar"),
        "agendamentos": consulta.resumo_agendamentos("mes"),
        "entregas": consulta.entregas_agendadas("mes"),
        "avaliacoes": consulta.listar_avaliacoes("mes"),
        "vendas_lista": consulta.lista_vendas("tudo"),
        "canais": consulta.vendas_por_canal("mes"),
        "conversao": consulta.conversao("mes"),
        "margem": consulta.margem_avaliacoes("mes"),
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


# ===== Relatórios diários (disparados por agendador externo) =====
def _brl(x) -> str:
    return f"R$ {(x or 0):,.0f}".replace(",", ".")


def _dm(s: str | None) -> str:
    return f"{s[8:10]}/{s[5:7]}" if s and len(s) >= 10 else ""


def _carro(v: dict) -> str:
    return " ".join(x for x in (v.get("modelo"), v.get("versao")) if x) or "carro"


def _reservado_carro(it: dict) -> str:
    partes = [p.strip() for p in (it.get("observacoes") or "").split("·")]
    return partes[2] if len(partes) > 2 and partes[2] else (it.get("cliente_nome") or "—")


def _agenda_manha_texto() -> str:
    hoje = datas.hoje_iso()
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    ags = db.select("agendamentos", {"select": "cliente_nome,data_agendada,vendedor_id", "origem": "eq.planilha"})
    hoje_ags = sorted([a for a in ags if (a.get("data_agendada") or "")[:10] == hoje],
                      key=lambda a: a.get("data_agendada") or "")
    def _h(a):
        d = a.get("data_agendada") or ""
        return (d[11:16] + "h ") if len(d) >= 16 and d[11:16] != "00:00" else ""
    ag_txt = ", ".join(f"{a['cliente_nome']} {_h(a)}({nomes.get(a['vendedor_id'], '—')})".replace("  ", " ")
                       for a in hoje_ags) if hoje_ags else "nenhum"

    vendas = db.select("vendas", {"select": "cliente_nome,modelo,versao,status_entrega,data_entrega_prevista"})
    pend = [v for v in vendas if v.get("status_entrega") != "entregue"]
    hoje_ent = [v for v in pend if (v.get("data_entrega_prevista") or "")[:10] == hoje]
    atras = [v for v in pend if v.get("data_entrega_prevista") and v["data_entrega_prevista"][:10] < hoje]
    ent_txt = ", ".join(f"{_carro(v)} ({v.get('cliente_nome') or ''})" for v in hoje_ent) if hoje_ent else "nenhuma"
    atr_txt = ", ".join(f"{_carro(v)} ({v.get('cliente_nome') or ''}, {_dm(v['data_entrega_prevista'])})"
                        for v in atras) if atras else "nenhuma"

    res = consulta.reservados("mes")
    res_txt = str(res["quantidade"])
    if res["quantidade"]:
        res_txt += " (" + ", ".join(_reservado_carro(i) for i in res["itens"]) + ")"

    linhas = [
        f"☀️ *Bom dia! Agenda de hoje* ({datas.hoje().strftime('%d/%m')})",
        f"📅 Agendamentos: {len(hoje_ags)} — {ag_txt}",
        f"🚗 Entregas marcadas hoje: {ent_txt}",
        f"⚠️ Atrasadas p/ entregar: {atr_txt}",
        f"🅿️ Reservados aguardando: {res_txt}",
    ]
    if hoje_ent or atras:
        linhas.append("\n👉 Já entregou alguma? Responde \"entreguei o [carro]\" que eu atualizo.")
    return "\n".join(linhas)


def _resumo_diario_texto() -> str:
    hoje = datas.hoje_iso()
    vh = consulta.vendidos("hoje")
    vm = consulta.vendidos("mes")
    fat = consulta.resumo_vendas("mes")
    receber = consulta.pendencias("pagamento")
    ag = consulta.resumo_agendamentos("hoje")
    res = consulta.reservados("mes")

    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    vendas = db.select("vendas", {"select": "cliente_nome,modelo,versao,valor_venda,vendedor_id,"
                                  "data_venda,status_entrega,data_entrega_real"})
    vendas_hoje = [v for v in vendas if (v.get("data_venda") or "")[:10] == hoje]
    feitas = [v for v in vendas if v.get("status_entrega") == "entregue" and (v.get("data_entrega_real") or "")[:10] == hoje]
    pendentes = [v for v in vendas if v.get("status_entrega") != "entregue"]

    L = [
        f"📊 *Fechamento de hoje* ({datas.hoje().strftime('%d/%m')})",
        f"🏆 Vendidos hoje: {vh['quantidade']} · no mês: {vm['quantidade']}",
        f"💵 Faturamento do mês: {_brl(fat['valor_total'])} · 📉 a receber {_brl(receber['valor_total_a_receber'])}",
    ]
    if vendas_hoje:
        L.append("🚗 *Vendas de hoje:*")
        for v in vendas_hoje:
            L.append(f"- {_carro(v)} {_brl(v.get('valor_venda'))} ({nomes.get(v.get('vendedor_id'), '—')})")

    feitas_txt = f"{len(feitas)} feita" + ("s" if len(feitas) != 1 else "")
    if feitas:
        feitas_txt += " (" + ", ".join(_carro(v) for v in feitas) + ")"
    L.append(f"📦 Entregas: {feitas_txt} · {len(pendentes)} pendentes")

    ags_hoje = consulta.listar_agendamentos("hoje")["agendamentos"]
    faltou = [a["cliente"] for a in ags_hoje if a.get("status") and
              ("falt" in a["status"].lower() or "cancel" in a["status"].lower() or "veio" in a["status"].lower())]
    linha = f"📅 Agendamentos hoje: {ag['total']} (✅ {ag['compareceram']} · ❌ {ag['faltaram']})"
    if faltou:
        linha += " — faltaram: " + ", ".join(faltou)
    L.append(linha)
    L.append(f"🅿️ Reservados: {res['quantidade']}")

    av = consulta.listar_avaliacoes("hoje")
    if av["quantidade"]:
        L.append("🔍 Avaliações: " + ", ".join(f"{a.get('modelo', '')} {_brl(a.get('valor_avaliacao'))}" for a in av["avaliacoes"]))

    if receber["itens"]:
        top = max(receber["itens"], key=lambda x: x["a_receber"])
        L.append(f"💸 Maior pendência: {top['cliente_nome']} {_brl(top['a_receber'])}")

    am = consulta.listar_agendamentos("amanha")["agendamentos"]
    if am:
        L.append(f"🔜 Amanhã: {len(am)} agendamento(s)")
    return "\n".join(L)


@app.api_route("/cron/sync-planilha", methods=["GET", "POST"])
def cron_sync_planilha(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        return planilha.sincronizar()
    except Exception as e:
        log.exception("erro sync planilha")
        return JSONResponse({"erro": str(e)}, status_code=500)


def _resumo_semanal_texto() -> str:
    from datetime import timedelta
    hoje = datas.hoje()
    ini = hoje - timedelta(days=hoje.weekday())
    periodo = f"{ini.strftime('%d/%m')} a {hoje.strftime('%d/%m')}"

    vend = consulta.vendidos("semana")
    fat = consulta.resumo_vendas("semana")
    ranking = consulta.ranking_vendedores("semana")["ranking"]
    receber = consulta.pendencias("pagamento")
    ag = consulta.resumo_agendamentos("semana")
    res = consulta.reservados("mes")
    vlist = consulta.lista_vendas("tudo")
    estoque = consulta.listar_carros("a_anunciar")

    ags = consulta.listar_agendamentos("semana")["agendamentos"]
    entregas = consulta.entregas_agendadas("semana")["entregas"]

    L = [
        f"📈 *Resumo da semana* ({periodo})",
        f"🏆 Vendidos: {vend['quantidade']} · 💵 {_brl(fat['valor_total'])} · 📉 a receber {_brl(receber['valor_total_a_receber'])}",
    ]
    if ranking:
        L.append("🥇 " + " · ".join(f"{r['vendedor']} {r['quantidade']}" for r in ranking[:5]))
    L.append(f"📅 Comparecimento: {ag['taxa_comparecimento']}% (✅ {ag['compareceram']} · ❌ {ag['faltaram']})")

    L.append(f"\n📅 *Agendamentos da semana ({len(ags)}):*")
    for a in ags[:15]:
        h = f" {a['hora']}" if a.get("hora") else ""
        st = f" – {a['status']}" if a.get("status") else ""
        L.append(f"- {a['cliente']} ({_dm(a['data'])}{h}, {a['vendedor']}){st}")
    if len(ags) > 15:
        L.append(f"... +{len(ags) - 15} na lista")

    if entregas:
        L.append("\n🚗 *Entregas agendadas:*")
        for e in entregas:
            L.append(f"- {e.get('veiculo', '—')} ({_dm(e.get('data_entrega'))}, {(e.get('horario') or '')[:5]}, {e.get('vendedor', '')})")

    if receber["itens"]:
        L.append(f"\n💰 *A receber ({_brl(receber['valor_total_a_receber'])}):*")
        for it in receber["itens"]:
            L.append(f"- {it['cliente_nome']} – {it.get('veiculo', '')}: {_brl(it['a_receber'])}")

    focos = []
    if res["quantidade"]:
        focos.append(f"resolver {res['quantidade']} reservado(s)")
    if vlist["a_entregar"]:
        focos.append(f"entregar {vlist['a_entregar']} carro(s)")
    if estoque["quantidade"]:
        focos.append(f"anunciar {estoque['quantidade']} carro(s)")
    if ag["total"] < 5 or ag["taxa_comparecimento"] < 60:
        focos.append("melhorar captação/comparecimento")
    if focos:
        L.append("\n🎯 *Focos:* " + " · ".join(focos))
    return "\n".join(L)


@app.api_route("/cron/resumo-semanal", methods=["GET", "POST"])
def cron_semanal(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        planilha.sincronizar()
    except Exception:
        log.exception("erro sync planilha no semanal")
    texto = _resumo_semanal_texto()
    evolution.enviar_relatorio(texto)
    return {"enviado": True, "resumo": texto}


@app.api_route("/cron/lembretes", methods=["GET", "POST"])
def cron_lembretes(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return {"enviados": _disparar_lembretes()}


@app.api_route("/cron/agenda-manha", methods=["GET", "POST"])
def cron_agenda(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        planilha.sincronizar()
    except Exception:
        log.exception("erro sync planilha na agenda")
    texto = _agenda_manha_texto()
    evolution.enviar_relatorio(texto)
    return {"enviado": True, "agenda": texto}


@app.api_route("/cron/resumo-diario", methods=["GET", "POST"])
def cron_resumo(token: str = ""):
    if not settings.dashboard_token or token != settings.dashboard_token:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        planilha.sincronizar()  # garante planilha fresca antes do fechamento
    except Exception:
        log.exception("erro sync planilha no resumo")
    texto = _resumo_diario_texto()
    evolution.enviar_relatorio(texto)
    return {"enviado": True, "resumo": texto}
