import hmac
import logging
import os

from datetime import timedelta

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import confirmacao, consulta, datas, db, evolution, ingest, media, meta_ads, planilha, supervisor
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agente")

app = FastAPI(title="Agente Loja de Carros — Grupo SB")


def _token_ok(token: str) -> bool:
    """Comparação em tempo constante (evita timing attack) do token de cron/dashboard."""
    return bool(settings.dashboard_token) and hmac.compare_digest(token or "", settings.dashboard_token)


_rate = {"min": 0, "n": 0}


def _rate_limit_ok(limite: int = 300) -> bool:
    """Limita o webhook a `limite` req/min por processo (defesa simples contra flood)."""
    minuto = int(_time.time() // 60)
    if minuto != _rate["min"]:
        _rate["min"], _rate["n"] = minuto, 0
    _rate["n"] += 1
    return _rate["n"] <= limite


import threading
import time as _time
_ult_lembrete = {"t": 0.0}
_ult_planilha = {"t": 0.0}
_lojasb_ok = {"v": True}


def _tick() -> None:
    if _time.time() - _ult_lembrete["t"] <= 55:
        return
    _ult_lembrete["t"] = _time.time()
    _disparar_lembretes()
    _reaper_eventos()
    _radar()
    _checar_lojasb()
    _checar_relatorios()
    if _time.time() - _ult_planilha["t"] > 600:  # sync da planilha a cada ~10 min
        _ult_planilha["t"] = _time.time()
        try:
            planilha.sincronizar()
        except Exception:
            log.exception("erro sync planilha no loop")


def _loop_agendador() -> None:
    """Always-on (VPS): dispara lembretes/relatórios sozinho, sem depender de ping externo."""
    while True:
        _time.sleep(60)
        try:
            _tick()
        except Exception:
            log.exception("erro no loop agendador")


@app.on_event("startup")
def _start_bg() -> None:
    threading.Thread(target=_loop_agendador, daemon=True).start()


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


def _reaper_eventos() -> int:
    """Retoma eventos travados em 'processando' há >10min (app caiu no meio).
    Tenta reprocessar uma vez; se falhar, marca 'erro' e avisa o dono."""
    limite = (datas.agora() - timedelta(minutes=10)).isoformat()
    travados = db.select("eventos_brutos", {
        "select": "id,grupo_id,mensagem_original", "status": "eq.processando",
        "created_at": f"lte.{limite}", "limit": "20"})
    n = 0
    for ev in travados:
        try:
            ingest.reprocessar(ev)
            n += 1
        except Exception:
            log.exception("reaper: falha reprocessando %s", ev.get("id"))
            db.update("eventos_brutos", {"status": "erro"}, {"id": f"eq.{ev['id']}"})
            try:
                evolution.notificar_dono(
                    "⚠️ Não consegui processar uma mensagem de grupo (marquei como erro). "
                    f"Trecho: {(ev.get('mensagem_original') or '')[:200]}")
            except Exception:
                pass
    return n


_ult_radar = {"t": 0.0}


def _radar() -> None:
    """Supervisor proativo. A cada ~10min reavalia as regras e manda CRÍTICOS na hora.
    Nas janelas 10:30 e 16:30 manda o digest completo dos alertas abertos (1x por slot)."""
    if _time.time() - _ult_radar["t"] <= 600:
        return
    _ult_radar["t"] = _time.time()
    now = datas.agora()
    hhmm = now.strftime("%H:%M")
    hoje = now.date().isoformat()
    slot = "radar_manha" if "10:30" <= hhmm <= "11:29" else ("radar_tarde" if "16:30" <= hhmm <= "17:29" else None)
    try:
        if slot and not _ja_enviado(slot, hoje):
            supervisor.disparar_radar(forcar=True)   # digest completo
            _marcar_enviado(slot, hoje)
        else:
            supervisor.disparar_radar(forcar=False)  # só crítico novo fura na hora
    except Exception:
        log.exception("erro no radar")


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


def _ja_enviado(tipo: str, data: str) -> bool:
    return bool(db.select("relatorios_enviados", {"select": "tipo", "tipo": f"eq.{tipo}",
                                                  "data": f"eq.{data}", "limit": "1"}))


def _marcar_enviado(tipo: str, data: str) -> None:
    try:
        db.insert("relatorios_enviados", {"tipo": tipo, "data": data})
    except Exception:
        pass


def _jobs_relatorio(dow: int, hhmm: str) -> list:
    """Relatórios elegíveis AGORA. Cada um tem uma JANELA [inicio, fim]: fora dela
    não dispara (evita mandar a agenda da manhã às 23h quando o app ficou fora)."""
    # (tipo, fn, dias_da_semana, inicio, fim)
    JANELAS = [
        ("planejamento", _planejamento_semana_texto, {0}, "08:00", "11:59"),
        ("agenda", _agenda_manha_texto, {0, 1, 2, 3, 4, 5}, "09:00", "11:59"),
        ("fechamento", _resumo_diario_texto, {0, 1, 2, 3, 4}, "18:00", "21:59"),
        ("fechamento", _resumo_diario_texto, {5}, "15:00", "21:59"),
        ("semanal", _resumo_semanal_texto, {6}, "18:00", "21:59"),
    ]
    return [(tipo, fn) for tipo, fn, dias, ini, fim in JANELAS
            if dow in dias and ini <= hhmm <= fim]


def _checar_relatorios() -> None:
    """Dispara os relatórios na janela certa (independente do GitHub Actions)."""
    now = datas.agora()
    hhmm = now.strftime("%H:%M")
    dow = now.weekday()  # 0=segunda ... 6=domingo
    hoje = now.date().isoformat()
    for tipo, fn in _jobs_relatorio(dow, hhmm):
        if _ja_enviado(tipo, hoje):
            continue
        try:
            try:
                planilha.sincronizar()
            except Exception:
                pass
            texto = fn()
            # Meta Ads anexado ao fechamento e ao semanal
            if tipo in ("fechamento", "semanal"):
                try:
                    texto += _meta_ads_linha("semana" if tipo == "fechamento" else "semana")
                except Exception:
                    log.exception("erro anexando meta ads")
            # análise da IA anexada ao fechamento (diário) e ao semanal
            try:
                if tipo == "fechamento":
                    ins = supervisor.gerar_insight("diario")
                    if ins:
                        texto += f"\n\n🧠 *Leitura do dia:*\n{ins}"
                elif tipo == "semanal":
                    ins = supervisor.gerar_insight("semanal")
                    if ins:
                        texto += f"\n\n🧠 *Leitura da semana:*\n{ins}"
            except Exception:
                log.exception("erro anexando insight")
            evolution.enviar_relatorio(texto)
            _marcar_enviado(tipo, hoje)
        except Exception:
            log.exception("erro enviando relatório %s", tipo)


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    try:
        _tick()  # mesmo ciclo do loop interno (compartilham o throttle de 60s)
    except Exception:
        log.exception("erro no check periódico")
    return {"ok": True, "agora": datas.agora().strftime("%Y-%m-%d %H:%M:%S %Z")}


@app.post("/webhook/evolution")
async def webhook(request: Request, background: BackgroundTasks):
    # auth opcional por Bearer (só checa se WEBHOOK_TOKEN estiver configurado)
    if settings.webhook_token:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {settings.webhook_token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not _rate_limit_ok():
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    body = await request.json()
    # responde na hora; o trabalho pesado (mídia/IA/banco) roda em background num
    # threadpool. A idempotência por message_id protege contra o retry do Evolution.
    background.add_task(_handle_event, body)
    return {"ok": True}


def _handle_event(body: dict) -> dict:
    try:
        return _rotear_evento(body)
    except Exception as e:
        log.exception("erro processando webhook")
        return {"error": str(e)}


def _rotear_evento(body: dict) -> dict:
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
def _meta_ads_linha(periodo: str) -> str:
    if not meta_ads.configurado():
        return ""
    r = meta_ads.resumo(periodo)
    if r.get("erro"):
        return ""
    cpl = f" · CPL {_brl(r['custo_por_lead'])}" if r.get("custo_por_lead") else ""
    ro = meta_ads.roas("mes")
    roas_txt = f" · ROAS {ro['roas']}x" if not ro.get("erro") and ro.get("roas") is not None else ""
    return (f"\n\n📲 *Meta Ads (7d):* {_brl(r['gasto'])} gasto · {r['leads']} leads · "
            f"{r['conversas']} conversas{cpl}{roas_txt}")


def _ultimo_insight() -> str:
    rows = db.select("insights", {"select": "conteudo", "periodo": "eq.diario",
                                  "order": "data.desc", "limit": "1"})
    return rows[0]["conteudo"] if rows else ""


def _funil() -> dict:
    ag = consulta.resumo_agendamentos("mes")
    vendidos = consulta.vendidos("mes")["quantidade"]
    vl = consulta.lista_vendas("tudo")
    entregues = sum(1 for x in vl.get("vendas", []) if x.get("status_entrega") == "entregue")
    return {"agendados": ag["total"], "compareceram": ag["compareceram"],
            "vendidos": vendidos, "entregues": entregues}


def _metrics() -> dict:
    a_receber = consulta.pendencias("pagamento")
    a_entregar = consulta.pendencias("entrega")
    pendentes = confirmacao.pendentes_itens()
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
        # Fase 3 — supervisor no painel
        "radar": supervisor.abertos(),
        "insight": _ultimo_insight(),
        "pendentes_itens": pendentes,
        "notas": supervisor.listar_notas()["notas"],
        "funil": _funil(),
        # Fase 4 — metas, giro, recalls, Meta Ads
        "metas": consulta.progresso_metas(),
        "giro": consulta.giro_estoque(),
        "recalls": consulta.listar_recalls(),
        "meta_ads": meta_ads.resumo("semana") if meta_ads.configurado() else None,
        "meta_roas": meta_ads.roas("mes") if meta_ads.configurado() else None,
    }


@app.get("/api/metrics")
def api_metrics(token: str = ""):
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return _metrics()


@app.get("/api/meta_ads")
def api_meta_ads(periodo: str = "semana", token: str = ""):
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    if not meta_ads.configurado():
        return {"resumo": None, "roas": None}
    return {"resumo": meta_ads.resumo(periodo), "roas": meta_ads.roas(periodo),
            "campanhas": meta_ads.campanhas(periodo).get("campanhas", [])}


@app.post("/api/eventos/{evento_id}/confirmar")
def api_confirmar(evento_id: str, token: str = ""):
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return confirmacao.confirmar_id(evento_id)


@app.post("/api/eventos/{evento_id}/descartar")
def api_descartar(evento_id: str, token: str = ""):
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return confirmacao.descartar_id(evento_id)


@app.post("/api/alertas/resolver")
def api_resolver_alerta(titulo: str = "", token: str = ""):
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return supervisor.resolver_alerta(titulo)


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


def _buckets(ags: list) -> dict:
    from collections import Counter

    def b(s):
        s = (s or "").lower()
        if "vendido" in s:
            return "Vendidos"
        if "reserv" in s:
            return "Reservados"
        if "realizado" in s or "comparec" in s:
            return "Compareceram"
        if "cancel" in s or "falt" in s or "veio" in s:
            return "Cancelados/faltas"
        if "agendad" in s:
            return "Agendados"
        return "Outros"

    c = Counter(b(a.get("status")) for a in ags)
    ordem = ["Vendidos", "Reservados", "Compareceram", "Agendados", "Cancelados/faltas", "Outros"]
    return {k: c[k] for k in ordem if c.get(k)}


def _reservado_carro(it: dict) -> str:
    partes = [p.strip() for p in (it.get("observacoes") or "").split("·")]
    return partes[2] if len(partes) > 2 and partes[2] else (it.get("cliente_nome") or "—")


def _agenda_manha_texto() -> str:
    hoje = datas.hoje_iso()
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    ags = db.select_all("agendamentos", {"select": "cliente_nome,data_agendada,vendedor_id", "origem": "eq.planilha"})
    hoje_ags = sorted([a for a in ags if (a.get("data_agendada") or "")[:10] == hoje],
                      key=lambda a: a.get("data_agendada") or "")
    def _h(a):
        d = a.get("data_agendada") or ""
        return (d[11:16] + "h ") if len(d) >= 16 and d[11:16] != "00:00" else ""
    ag_txt = ", ".join(f"{a['cliente_nome']} {_h(a)}({nomes.get(a['vendedor_id'], '—')})".replace("  ", " ")
                       for a in hoje_ags) if hoje_ags else "nenhum"

    vendas = db.select_all("vendas", {"select": "cliente_nome,modelo,versao,status_entrega,data_entrega_prevista"})
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
    vendas = db.select_all("vendas", {"select": "cliente_nome,modelo,versao,valor_venda,vendedor_id,"
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

    L.append(f"📅 Agendamentos hoje: {ag['total']} · comparecimento {ag['taxa_comparecimento']}% (✅ {ag['compareceram']} · ❌ {ag['faltaram']})")
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
    if not _token_ok(token):
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
    conv = consulta.conversao("semana")

    L = [
        f"📈 *Resumo da semana* ({periodo})",
        f"🏆 Vendidos: {vend['quantidade']} · 💵 {_brl(fat['valor_total'])} · 📉 a receber {_brl(receber['valor_total_a_receber'])}",
    ]
    if ranking:
        L.append("🥇 " + " · ".join(f"{r['vendedor']} {r['quantidade']}" for r in ranking[:5]))

    tot = len(ags)
    bk = _buckets(ags)
    pct = lambda n: f"{round(n / tot * 100)}%" if tot else "0%"
    L.append(f"\n📅 *Agendamentos: {tot}* · comparecimento {ag['taxa_comparecimento']}% · conversão {conv['taxa_conversao']}%")
    if tot:
        L.append("   " + " · ".join(f"{k} {v} ({pct(v)})" for k, v in bk.items()))

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


def _planejamento_semana_texto() -> str:
    from datetime import datetime as _dt, timedelta
    hoje = datas.hoje()
    ini = hoje - timedelta(days=hoje.weekday())
    fim = ini + timedelta(days=6)
    ags = consulta.listar_agendamentos("semana")["agendamentos"]
    entregas = consulta.entregas_agendadas("semana")["entregas"]
    estoque = consulta.listar_carros("a_anunciar")
    vlist = consulta.lista_vendas("tudo")
    res = consulta.reservados("mes")
    receber = consulta.pendencias("pagamento")

    from collections import Counter
    abrev = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    cont = Counter()
    for a in ags:
        try:
            cont[_dt.fromisoformat(a["data"]).weekday()] += 1
        except Exception:
            pass
    pordia = " · ".join(f"{abrev[d]} {cont[d]}" for d in range(7) if cont[d])

    L = [f"🗓️ *Planejamento da semana* ({ini.strftime('%d/%m')} a {fim.strftime('%d/%m')})",
         f"📅 {len(ags)} agendamentos" + (f" — {pordia}" if pordia else "")]
    if entregas:
        L.append(f"🚗 {len(entregas)} entregas: " + ", ".join(
            f"{(e.get('veiculo') or '')[:18]} ({_dm(e.get('data_entrega'))})" for e in entregas[:8]))
    tarefas = []
    if estoque["quantidade"]:
        tarefas.append(f"anunciar {estoque['quantidade']} carro(s)")
    if vlist["a_entregar"]:
        tarefas.append(f"entregar {vlist['a_entregar']} carro(s)")
    if res["quantidade"]:
        tarefas.append(f"resolver {res['quantidade']} reservado(s)")
    if receber["valor_total_a_receber"]:
        tarefas.append(f"receber {_brl(receber['valor_total_a_receber'])}")
    if tarefas:
        L.append("🎯 *Pra fazer:* " + " · ".join(tarefas))
    return "\n".join(L)


@app.api_route("/cron/planejamento-semana", methods=["GET", "POST"])
def cron_planejamento(token: str = ""):
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        planilha.sincronizar()
    except Exception:
        log.exception("erro sync planilha no planejamento")
    texto = _planejamento_semana_texto()
    evolution.enviar_relatorio(texto)
    return {"enviado": True, "planejamento": texto}


@app.api_route("/cron/resumo-semanal", methods=["GET", "POST"])
def cron_semanal(token: str = ""):
    if not _token_ok(token):
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
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    return {"enviados": _disparar_lembretes()}


@app.api_route("/cron/agenda-manha", methods=["GET", "POST"])
def cron_agenda(token: str = ""):
    if not _token_ok(token):
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
    if not _token_ok(token):
        return JSONResponse({"erro": "não autorizado"}, status_code=401)
    try:
        planilha.sincronizar()  # garante planilha fresca antes do fechamento
    except Exception:
        log.exception("erro sync planilha no resumo")
    texto = _resumo_diario_texto()
    evolution.enviar_relatorio(texto)
    return {"enviado": True, "resumo": texto}
