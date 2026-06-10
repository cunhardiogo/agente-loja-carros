# Agente Loja de Carros — Grupo SB

Agente de IA que lê os grupos de WhatsApp da loja (via Evolution API), extrai eventos de
negócio (vendas, agendamentos, anúncios, entregas, pagamentos, comparecimento) com IA e
grava estruturado no Supabase. Consulta por WhatsApp + dashboard.

## Stack
FastAPI (Render) · Evolution API (VPS) · Supabase (Postgres) · OpenAI · Dashboard React (depois)

## Rodar local
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
# preencher OPENAI_API_KEY e MEU_NUMERO no .env
uvicorn app.main:app --reload
```

## Endpoints
- `GET  /health` — healthcheck
- `POST /webhook/evolution` — recebe mensagens do Evolution (configurar webhook na instância)

## Configurar webhook no Evolution
Apontar a instância `diogo4895` para `https://<seu-app>.onrender.com/webhook/evolution`,
evento `messages.upsert`.

## Fluxo de ingestão
1. mensagem de grupo monitorado chega no webhook
2. idempotência por `message_id`
3. IA (`app/llm.py`) classifica + extrai → `Extracao`
4. confiança ≥ 0.8 → grava na tabela de domínio; senão → pede confirmação no seu WhatsApp
5. tudo é logado em `eventos_brutos` (auditoria/correção)

## Banco
Schema em `db/schema.sql`, seed em `db/seed.sql`. Já aplicados no projeto Supabase.

## Status das fases
- [x] Fase 0 — Infra (schema + seed + scaffold)
- [x] Fase 1 — Ingestão vendas/estoque/agendamento (IA structured output)
- [x] Fase 2 — comparecimento/pagamento/entrega (UPDATE) + loop de confirmação (sim/não/corrigir)
- [x] Fase 3 — Agente de consulta (Q&A WhatsApp, function calling)
- [x] Fase 4 — Mídia: ouve áudio (Whisper) e lê imagem/documento (visão)
- [x] Fase 5 — Dashboard (servido em /dashboard, protegido por DASHBOARD_TOKEN)

## Dashboard
`https://<app>.onrender.com/dashboard?token=<DASHBOARD_TOKEN>` — KPIs, ranking, a receber/entregar,
comparecimento e estoque. Requer env var `DASHBOARD_TOKEN` configurada na Render.

## Timezone
Datas usam America/Sao_Paulo (`app/datas.py`), não o UTC do servidor.

## Segurança / segredos
- Nenhum segredo no repo: todas as chaves vivem nas env vars do Portainer
  (`SUPABASE_SERVICE_ROLE`, `OPENAI_API_KEY`, `EVOLUTION_APIKEY`, `DASHBOARD_TOKEN`,
  `WEBHOOK_TOKEN`). Números e id da planilha em `CONTEXTO_PRIVADO.md` (fora do git).
- Endpoints `/cron/*` e `/api/metrics` exigem `DASHBOARD_TOKEN` (comparação em tempo
  constante). O webhook aceita `WEBHOOK_TOKEN` opcional via `Authorization: Bearer` e
  tem rate limit de 300 req/min.
- **Rotação de chaves**: como o repo é público, rotacione periodicamente
  `SUPABASE_SERVICE_ROLE`, `OPENAI_API_KEY` e `EVOLUTION_APIKEY` (gerar nova no painel
  de origem → atualizar no Portainer → `docker service update --force agente_agente`).
  Qualquer chave que tenha passado por chat/log deve ser rotacionada na primeira
  oportunidade.
