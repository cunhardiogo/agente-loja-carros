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
- [~] Fase 1 — Ingestão vendas/estoque/agendamento (MVP feito; falta OPENAI_API_KEY pra testar)
- [ ] Fase 2 — comparecimento/pagamento/entrega (update de registros) + loop de confirmação (sim/não/corrigir)
- [ ] Fase 3 — Agente de consulta (Q&A WhatsApp) + resumo diário
- [ ] Fase 4 — Dashboard React
- [ ] Fase 5 — Refino (dedup avançado, prompts, relatórios)
