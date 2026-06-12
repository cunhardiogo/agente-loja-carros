"""Supervisor autônomo (Fase 2): transforma o agente de registrador passivo em
vigia ativo. Roda regras de negócio (R1..R10) e de saúde do sistema (W1..W5),
persiste alertas com dedup, monta o radar proativo e gera insights por IA."""
import logging
from datetime import datetime, timedelta

from . import datas, db, evolution
from .config import settings

log = logging.getLogger("agente")

# ===== limiares (ajustáveis) =====
DIAS_A_ANUNCIAR = 5        # carro chegou e segue 'a_anunciar'
DIAS_ANUNCIADO = 30        # anunciado e não vende (encalhado)
DIAS_A_RECEBER = 7         # venda sem pagamento envelhecendo
DIAS_RESERVA = 5           # reserva parada sem virar venda
HORAS_SEM_CAPTURA = 5      # nenhuma mensagem captada (em horário comercial)
MIN_AGEND_SEMANA = 5       # captação fraca
TAXA_COMPARECIMENTO_MIN = 60.0


# ===== helpers =====
def _idade_dias(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    ag = datas.agora()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ag.tzinfo)
    return (ag - dt).total_seconds() / 86400


def _nome_veiculo(v: dict) -> str:
    return " ".join(x for x in (v.get("marca"), v.get("modelo"), v.get("versao")) if x) or "veículo"


def _alerta(tipo, chave, sev, titulo, detalhe=None, tabela=None, ent_id=None) -> dict:
    return {"tipo": tipo, "chave": chave, "severidade": sev, "titulo": titulo,
            "detalhe": detalhe, "entidade_tabela": tabela, "entidade_id": ent_id}


# ===== R1..R10 (regras de negócio) =====
def _r1_a_anunciar() -> list:
    rows = db.select_all("veiculos", {"select": "id,marca,modelo,versao,created_at", "status": "eq.a_anunciar"})
    out = []
    for v in rows:
        d = _idade_dias(v.get("created_at"))
        if d is not None and d >= DIAS_A_ANUNCIAR:
            out.append(_alerta("R1", f"veiculo:{v['id']}", "aviso",
                               f"Carro parado sem anunciar: {_nome_veiculo(v)}",
                               f"Chegou há {int(d)} dias e ainda está 'a anunciar'.", "veiculos", v["id"]))
    return out


def _r2_anunciado_encalhado() -> list:
    rows = db.select_all("veiculos", {"select": "id,marca,modelo,versao,data_anuncio,created_at", "status": "eq.anunciado"})
    out = []
    for v in rows:
        d = _idade_dias(v.get("data_anuncio") or v.get("created_at"))
        if d is not None and d >= DIAS_ANUNCIADO:
            out.append(_alerta("R2", f"veiculo:{v['id']}", "aviso",
                               f"Anúncio encalhado: {_nome_veiculo(v)}",
                               f"Anunciado há {int(d)} dias sem vender. Rever preço/fotos.", "veiculos", v["id"]))
    return out


def _r3_venda_sem_pagamento() -> list:
    rows = db.select_all("vendas", {
        "select": "id,cliente_nome,modelo,versao,created_at,valor_total,valor_venda,valor_entrada,status_pagamento,status_entrega"})
    out = []
    for v in rows:
        if v.get("status_pagamento") == "pago" or v.get("status_entrega") == "entregue":
            continue
        d = _idade_dias(v.get("created_at"))
        if d is None or d < DIAS_A_RECEBER:
            continue
        saldo = (v.get("valor_total") or v.get("valor_venda") or 0) - (v.get("valor_entrada") or 0)
        if saldo <= 0:
            continue
        out.append(_alerta("R3", f"venda:{v['id']}", "aviso",
                           f"A receber parado: {v.get('cliente_nome') or 'cliente'}",
                           f"Venda de {int(d)} dias com R$ {saldo:,.0f} a receber e sem entrega.".replace(",", "."),
                           "vendas", v["id"]))
    return out


def _r4_entrega_vencida() -> list:
    hoje = datas.hoje_iso()
    rows = db.select_all("vendas", {
        "select": "id,cliente_nome,modelo,versao,data_entrega_prevista",
        "status_entrega": "eq.pendente", "data_entrega_prevista": f"lt.{hoje}"})
    out = []
    for v in rows:
        if not v.get("data_entrega_prevista"):
            continue
        veic = " ".join(x for x in (v.get("modelo"), v.get("versao")) if x)
        out.append(_alerta("R4", f"venda:{v['id']}", "critico",
                           f"Entrega atrasada: {v.get('cliente_nome') or 'cliente'} ({veic})",
                           f"Prevista p/ {v['data_entrega_prevista']} e ainda pendente. Confirmar.", "vendas", v["id"]))
    return out


def _r5_venda_sem_vendedor() -> list:
    desde = (datas.agora() - timedelta(days=14)).isoformat()
    rows = db.select_all("vendas", {"select": "id,cliente_nome,created_at,vendedor_id",
                                    "vendedor_id": "is.null", "created_at": f"gte.{desde}"})
    return [_alerta("R5", f"venda:{v['id']}", "info",
                    f"Venda sem vendedor: {v.get('cliente_nome') or 'cliente'}",
                    "Venda registrada sem vendedor — completar p/ ranking/comissão.", "vendas", v["id"])
            for v in rows]


def _r6_reserva_parada() -> list:
    rows = db.select_all("agendamentos", {"select": "id,cliente_nome,data_agendada,resultado,observacoes",
                                          "origem": "eq.planilha"})
    out = []
    for a in rows:
        if not (a.get("resultado") or "").strip().lower().startswith("reservad"):
            continue
        d = _idade_dias(a.get("data_agendada"))
        if d is not None and d >= DIAS_RESERVA:
            out.append(_alerta("R6", f"agendamento:{a['id']}", "aviso",
                               f"Reserva parada: {a.get('cliente_nome') or 'cliente'}",
                               f"Reservado há {int(d)} dias sem virar venda. Cobrar fechamento.", "agendamentos", a["id"]))
    return out


def _semana_range():
    hoje = datas.hoje()
    ini = hoje - timedelta(days=hoje.weekday())
    return ini, hoje


def _r7_comparecimento_baixo() -> list:
    ini, hoje = _semana_range()
    rows = db.select_all("agendamentos", {"select": "compareceu,data_agendada", "origem": "eq.planilha",
                                          "data_agendada": f"gte.{ini.isoformat()}"})
    decididos = [r for r in rows if r.get("compareceu") is not None]
    if len(decididos) < 5:
        return []
    vieram = sum(1 for r in decididos if r.get("compareceu") is True)
    taxa = vieram / len(decididos) * 100
    if taxa >= TAXA_COMPARECIMENTO_MIN:
        return []
    return [_alerta("R7", f"comparecimento:{ini.isoformat()}", "aviso",
                    f"Comparecimento baixo: {taxa:.0f}% nesta semana",
                    f"{vieram}/{len(decididos)} compareceram. Revisar confirmação dos SDRs.", None, None)]


def _r8_captacao_fraca() -> list:
    ini, hoje = _semana_range()
    rows = db.select_all("agendamentos", {"select": "id", "origem": "eq.planilha",
                                          "data_agendada": f"gte.{ini.isoformat()}"})
    if hoje.weekday() < 2 or len(rows) >= MIN_AGEND_SEMANA:  # só cobra da quarta em diante
        return []
    return [_alerta("R8", f"captacao:{ini.isoformat()}", "aviso",
                    f"Captação fraca: só {len(rows)} agendamentos na semana",
                    f"Abaixo de {MIN_AGEND_SEMANA}. Reforçar geração de leads/agenda.", None, None)]


def _r9_sem_vendas() -> list:
    ini, hoje = _semana_range()
    rows = db.select_all("agendamentos", {"select": "resultado,data_agendada", "origem": "eq.planilha",
                                          "data_agendada": f"gte.{ini.isoformat()}"})
    vendidos = sum(1 for r in rows if (r.get("resultado") or "").strip().lower() == "vendido")
    if hoje.weekday() < 3 or vendidos > 0:  # só alerta da quinta em diante
        return []
    return [_alerta("R9", f"semvenda:{ini.isoformat()}", "critico",
                    "Sem vendas nesta semana",
                    "Nenhum carro marcado como vendido até agora. Atenção ao fechamento.", None, None)]


def _r10_avaliacao_sem_desfecho() -> list:
    desde = (datas.agora() - timedelta(days=10)).isoformat()
    avs = db.select_all("avaliacoes", {"select": "id,modelo,versao,created_at,carro_troca",
                                       "created_at": f"gte.{desde}"})
    # a avaliação é o carro do cliente (troca). Se ele fechou, a venda tem troca_modelo ~ esse carro.
    # Comparar com vendas.modelo (carro comprado) era errado e dava falso positivo eterno.
    vendas = db.select_all("vendas", {"select": "troca_modelo", "created_at": f"gte.{desde}"})
    trocas = " | ".join((v.get("troca_modelo") or "").strip().lower() for v in vendas if v.get("troca_modelo"))
    out = []
    for a in avs:
        d = _idade_dias(a.get("created_at"))
        if d is None or d < 4:
            continue
        modelo = (a.get("modelo") or a.get("carro_troca") or "").strip().lower()
        if modelo and modelo in trocas:  # esse carro já entrou como troca numa venda
            continue
        nome = " ".join(x for x in (a.get("modelo"), a.get("versao")) if x) or "veículo"
        out.append(_alerta("R10", f"avaliacao:{a['id']}", "info",
                           f"Avaliação sem desfecho: {nome}",
                           f"Avaliado há {int(d)} dias e o cliente não fechou. Retomar contato.", "avaliacoes", a["id"]))
    return out


def _r11_meta_em_risco() -> list:
    """Meta da loja em risco: ritmo abaixo do necessário passada a metade do mês."""
    import calendar
    from . import consulta
    hoje = datas.hoje()
    dias_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    if hoje.day < dias_mes // 2:  # só cobra da metade do mês em diante
        return []
    prog = consulta.progresso_metas()
    loja = next((m for m in prog.get("metas", []) if m.get("escopo") == "loja"), None)
    if not loja:
        return []
    esperado = hoje.day / dias_mes * 100  # % do mês decorrido
    out = []
    for rotulo, real_pct, alvo in (("vendas", loja.get("pct_vendas"), loja.get("meta_vendas")),
                                   ("faturamento", loja.get("pct_faturamento"), loja.get("meta_faturamento"))):
        if alvo and real_pct is not None and real_pct < esperado - 15:
            out.append(_alerta("R11", f"meta:{rotulo}:{hoje.strftime('%Y-%m')}", "aviso",
                               f"Meta de {rotulo} em risco: {real_pct:.0f}% no dia {hoje.day}/{dias_mes}",
                               f"Esperado ~{esperado:.0f}% do mês. Acelerar pra bater a meta.", None, None))
    return out


REGRAS = [_r1_a_anunciar, _r2_anunciado_encalhado, _r3_venda_sem_pagamento, _r4_entrega_vencida,
          _r5_venda_sem_vendedor, _r6_reserva_parada, _r7_comparecimento_baixo, _r8_captacao_fraca,
          _r9_sem_vendas, _r10_avaliacao_sem_desfecho, _r11_meta_em_risco]


# ===== W1..W5 (saúde do sistema) =====
def _w_watchdog() -> list:
    out = []
    # W1 assistente (lojasb) desconectado
    if settings.evolution_assist_instance:
        try:
            if evolution.estado(settings.evolution_assist_instance, settings.evolution_assist_apikey) != "open":
                out.append(_alerta("W1", "lojasb", "critico", "Assistente (lojasb) desconectado",
                                   "O número que fala com você caiu. Reconecte em evo.agenteintel.com.br/manager.", None, None))
        except Exception:
            log.exception("watchdog W1")
    # W2 coletor (diogo4895) desconectado → não capta grupos
    if settings.evolution_instance:
        try:
            if evolution.estado(settings.evolution_instance, settings.evolution_apikey) != "open":
                out.append(_alerta("W2", "coletor", "critico", "Coletor desconectado",
                                   "O número que escuta os grupos caiu. Sem ele nada é captado.", None, None))
        except Exception:
            log.exception("watchdog W2")
    # W3 sem nenhuma mensagem captada em horário comercial
    ag = datas.agora()
    if 9 <= ag.hour < 19 and ag.weekday() < 6:
        ult = db.select("eventos_brutos", {"select": "created_at", "order": "created_at.desc", "limit": "1"})
        idade = _idade_dias(ult[0]["created_at"]) if ult else None
        if idade is None or idade * 24 >= HORAS_SEM_CAPTURA:
            horas = "nunca" if idade is None else f"há {int(idade * 24)}h"
            out.append(_alerta("W3", f"captura:{ag.date().isoformat()}", "critico",
                               "Nenhuma mensagem captada dos grupos",
                               f"Última mensagem {horas}. Verifique groupsIgnore/conexão do coletor.", None, None))
    # W4 eventos travados em erro (reaper falhou)
    erros = db.select("eventos_brutos", {"select": "id", "status": "eq.erro", "limit": "50"})
    if erros:
        out.append(_alerta("W4", f"erros:{ag.date().isoformat()}", "aviso",
                           f"{len(erros)} mensagem(ns) com erro de processamento",
                           "Eventos que falharam mesmo após retry. Olhar logs/dados.", None, None))
    # W5 pendências de confirmação acumulando
    pend = db.select("eventos_brutos", {"select": "id", "status": "eq.pendente_confirmacao", "limit": "50"})
    if len(pend) >= 5:
        out.append(_alerta("W5", f"pendentes:{ag.date().isoformat()}", "aviso",
                           f"{len(pend)} registros aguardando sua confirmação",
                           "Responda as pendências (use 'pendências') p/ não perder dados.", None, None))
    # W6 token do Meta Ads expirando/expirado
    from . import meta_ads
    if meta_ads.configurado():
        st = meta_ads.token_status()
        exp = st.get("expira_em") or 0
        falta = (exp - ag.timestamp()) / 86400 if exp else None
        if not st.get("ok"):
            out.append(_alerta("W6", "meta_token", "critico", "Token do Meta Ads inválido",
                               "Renove o META_TOKEN — sem ele não dá pra puxar gasto/leads.", None, None))
        elif falta is not None and falta <= 7:
            out.append(_alerta("W6", f"meta_token:{ag.date().isoformat()}", "aviso",
                               f"Token do Meta Ads expira em {int(falta)} dia(s)",
                               "Renove o META_TOKEN antes de vencer.", None, None))
    return out


# ===== orquestração =====
def avaliar(com_watchdog: bool = True) -> list:
    alertas = []
    for regra in REGRAS:
        try:
            alertas += regra()
        except Exception:
            log.exception("erro na regra %s", getattr(regra, "__name__", "?"))
    if com_watchdog:
        try:
            alertas += _w_watchdog()
        except Exception:
            log.exception("erro no watchdog")
    return alertas


DIAS_SNOOZE = 3  # alerta resolvido não renasce por N dias (evita spam de enxugar gelo)


def _silenciados() -> set:
    """(tipo,chave) resolvidos há menos de DIAS_SNOOZE — não recriar ainda."""
    desde = (datas.agora() - timedelta(days=DIAS_SNOOZE)).isoformat()
    rows = db.select_all("alertas", {"select": "tipo,chave,resolved_at", "status": "eq.resolvido",
                                     "resolved_at": f"gte.{desde}"})
    return {(r["tipo"], r["chave"]) for r in rows}


def persistir(alertas: list) -> int:
    """Grava os alertas novos; o índice único (tipo,chave) where aberto evita repetir,
    e o snooze evita recriar o que o dono acabou de resolver."""
    silenciados = _silenciados()
    n = 0
    for a in alertas:
        if (a["tipo"], a["chave"]) in silenciados:
            continue
        if db.insert_lock("alertas", a) is not None:
            n += 1
    return n


_SEV_ICON = {"critico": "🔴", "aviso": "🟡", "info": "🔵"}
_SEV_ORDEM = {"critico": 0, "aviso": 1, "info": 2}


def abertos() -> list:
    rows = db.select_all("alertas", {"select": "id,tipo,severidade,titulo,detalhe,created_at",
                                     "status": "eq.aberto"})
    rows.sort(key=lambda r: (_SEV_ORDEM.get(r.get("severidade"), 9), r.get("created_at") or ""))
    return rows


def _formatar(rows: list, titulo: str) -> str:
    linhas = [titulo]
    for r in rows:
        ic = _SEV_ICON.get(r.get("severidade"), "•")
        linhas.append(f"{ic} {r['titulo']}" + (f"\n   {r['detalhe']}" if r.get("detalhe") else ""))
    return "\n".join(linhas)


def radar_texto() -> str:
    rows = abertos()
    if not rows:
        return "✅ Radar limpo: nenhum alerta aberto agora."
    return _formatar(rows, f"🛰️ *Radar* — {len(rows)} ponto(s) de atenção:")


def disparar_radar(forcar: bool = False) -> int:
    """Avalia, persiste e notifica. Manda os alertas ainda não notificados ao dono.
    `forcar=False` só manda se houver crítico novo (uso no tick); True manda o digest todo."""
    novos = persistir(avaliar())
    pend = db.select_all("alertas", {"select": "id,tipo,severidade,titulo,detalhe",
                                     "status": "eq.aberto", "notificado": "eq.false"})
    if not pend:
        return 0
    criticos = [p for p in pend if p.get("severidade") == "critico"]
    enviar = pend if forcar else criticos  # no tick, só crítico fura sem esperar o radar agendado
    if not enviar:
        return 0
    enviar.sort(key=lambda r: _SEV_ORDEM.get(r.get("severidade"), 9))
    titulo = "🛰️ *Radar* — atenção:" if forcar else "🔴 *Alerta crítico:*"
    try:
        evolution.enviar_relatorio(_formatar(enviar, titulo))
    except Exception:
        log.exception("erro enviando radar")
        return 0
    ids = [p["id"] for p in enviar]
    for i in range(0, len(ids), 100):
        db.update("alertas", {"notificado": True}, {"id": f"in.({','.join(ids[i:i+100])})"})
    return len(enviar)


def resolver_alerta(termo: str) -> dict:
    rows = abertos()
    t = (termo or "").strip().lower()
    alvo = next((r for r in rows if t and t in (r.get("titulo") or "").lower()), None)
    if not alvo:
        return {"erro": f"não achei alerta aberto com '{termo}'"}
    db.update("alertas", {"status": "resolvido", "resolved_at": datas.agora().isoformat()},
              {"id": f"eq.{alvo['id']}"})
    return {"ok": True, "resolvido": alvo["titulo"]}


# ===== notas =====
def anotar(texto: str, numero: str | None = None) -> dict:
    if not (texto or "").strip():
        return {"erro": "diga o que anotar"}
    db.insert("notas", {"numero": numero, "texto": texto.strip()})
    return {"ok": True, "anotado": texto.strip()}


def listar_notas() -> dict:
    rows = db.select_all("notas", {"select": "id,texto,created_at", "resolvida": "eq.false",
                                   "order": "created_at.asc"})
    return {"quantidade": len(rows), "notas": [{"texto": r["texto"]} for r in rows]}


def resolver_nota(termo: str) -> dict:
    rows = db.select_all("notas", {"select": "id,texto", "resolvida": "eq.false"})
    t = (termo or "").strip().lower()
    alvo = next((r for r in rows if t and t in (r.get("texto") or "").lower()), None)
    if not alvo:
        return {"erro": f"não achei nota com '{termo}'"}
    db.update("notas", {"resolvida": True}, {"id": f"eq.{alvo['id']}"})
    return {"ok": True, "concluida": alvo["texto"]}


# ===== insights por IA =====
def _metricas_insight(periodo: str) -> dict:
    from . import consulta
    p = "semana" if periodo == "semanal" else "hoje"
    return {
        "periodo": periodo,
        "vendidos": consulta.vendidos(p),
        "faturamento": consulta.resumo_vendas("semana" if periodo == "semanal" else "mes"),
        "a_receber": consulta.pendencias("pagamento").get("valor_total_a_receber"),
        "agendamentos": consulta.resumo_agendamentos("semana"),
        "ranking": consulta.ranking_vendedores("semana" if periodo == "semanal" else "mes").get("ranking"),
        "alertas_abertos": [a["titulo"] for a in abertos()],
    }


def gerar_insight(periodo: str) -> str:
    """Análise curta e acionável do dia/semana. Persiste em insights e devolve o texto."""
    from . import consulta
    if consulta._client is None:
        return ""
    data = datas.hoje_iso()
    try:
        metr = _metricas_insight(periodo)
        resp = consulta._client.chat.completions.create(
            model=settings.openai_model_consulta, temperature=0.3,
            messages=[{"role": "system", "content":
                       "Você é o analista de uma loja de carros. A partir dos números, escreva uma análise "
                       "MUITO curta (3 a 5 linhas, tom direto de WhatsApp) com: como foi o desempenho, o "
                       "principal ponto de atenção e 1 a 2 ações concretas pra amanhã/semana. Sem repetir "
                       "tabela de números crus; foque na leitura e na recomendação."},
                      {"role": "user", "content": f"Período: {periodo}. Dados: {metr}"}])
        texto = (resp.choices[0].message.content or "").strip()
    except Exception:
        log.exception("erro gerando insight")
        return ""
    if not texto:
        return ""
    try:
        db.delete("insights", {"periodo": f"eq.{periodo}", "data": f"eq.{data}"})
        db.insert("insights", {"periodo": periodo, "data": data, "conteudo": texto})
    except Exception:
        log.exception("erro salvando insight")
    return texto
