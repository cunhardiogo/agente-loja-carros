# Agente de IA — Gestão de Loja de Carros

## Decisões travadas (2026-06-05)
- Interface: **WhatsApp privado + Dashboard web** (ambos)
- Captura dos grupos: **Evolution API** (número/chip via QR)
- Formato das mensagens: **conversa livre** (IA estrutura)
- Modo: plano completo aprovado antes de construir

## Arquitetura
Grupos WhatsApp → Evolution API (webhook) → n8n → IA Extratora (msg→JSON) → Supabase.
Consulta: você pergunta no WhatsApp → IA de Consulta → query Supabase → resposta natural.
Dashboard React/Vite lê Supabase.

## Stack (DECISÃO: Python, não n8n)
Motivo: o núcleo é lógica (resolução de entidades no texto livre, dedup 48h, máquina de estado
da confirmação, Q&A seguro). Em n8n viraria spaghetti de Function nodes; em Python é código testável.
n8n só ganharia se as mensagens fossem semi-estruturadas — mas escolhemos conversa livre.

- Captura: **Evolution API** — JÁ RODANDO na VPS sempre-online do usuário (não precisa hospedar)
- Agente: **FastAPI** na **Render** (web service persistente) — endpoints /webhook (ingestão) + /consulta (Q&A) + cron resumo diário
- Validação da extração: **Pydantic**
- IA extração (alto volume): GPT-4.1-mini ou Claude Haiku 4.5 (structured output)
- IA consulta: GPT-4.1 ou Claude Sonnet
- Banco: Supabase (Postgres) via supabase-py
- Dashboard: React + Vite + Recharts + Tailwind (Vercel ou Render Static)

Topologia:
  VPS do usuário: Evolution API (sessão WhatsApp) → webhook → Render(FastAPI)
  Render: FastAPI (ingestão + Q&A + cron) ; Supabase: banco ; Vercel: dashboard

## Modelo de dados (Supabase)
- grupos: id, evolution_group_jid, nome, tipo(vendas|estoque|agendamentos|geral), ativo
- vendedores: id, nome, apelidos[], telefone, ativo  (apelidos[] = matching no texto livre)
- veiculos: id, marca, modelo, ano, versao, cor, km, placa, preco_anuncio, preco_custo,
  link_anuncio, status(anunciado|reservado|vendido|entregue), data_anuncio
- vendas: id, veiculo_id, vendedor_id, cliente_nome, valor_venda, forma_pagamento, data_venda,
  status_pagamento(pago|pendente), status_entrega(pendente|entregue),
  data_entrega_prevista, data_entrega_real, observacoes
- agendamentos: id, cliente_nome, telefone, vendedor_id, veiculo_id, data_agendada,
  compareceu(true|false|null), origem, observacoes
- eventos_brutos: id, grupo_id, remetente, mensagem_original, timestamp, tipo_evento,
  dados_extraidos(jsonb), confianca, status(auto|pendente_confirmacao|confirmado|descartado), registro_id

Derivações:
- "carros a pagar" = vendas.status_pagamento='pendente'
- "carros a entregar" = vendas.status_entrega='pendente'

## Pipeline de ingestão (3 passos por mensagem)
1. Classificar + extrair (IA recebe msg + contexto vendedores/veiculos ativos → JSON com tipo_evento, confianca, campos)
2. Resolver entidades (casa "Corolla branco"→veiculo_id, "João/JP"→vendedor_id via apelidos[]; datas relativas→absolutas)
3. Decidir por confiança:
   - >=0.8 → grava direto (status=auto)
   - media/baixa → pendente_confirmacao, agente pergunta no privado (sim/não/corrigir)
Dedup: venda mesmo veiculo+cliente em 48h → update, não duplica.

## Agente de consulta (WhatsApp)
Q&A natural read-only. Resumo diário opcional via cron n8n (19h).

## Dashboard
Páginas: Visão Geral (KPIs) · Vendas por Vendedor · Estoque/Anúncios · Agendamentos (comparecimento) · Auditoria (eventos pendentes).

## Fases
- 0 Infra: Supabase + schema + scaffold FastAPI (render.yaml) + plugar webhook do Evolution da VPS
- 1 Ingestão vendas/estoque
- 2 Agendamentos + comparecimento
- 3 Agente consulta WhatsApp + resumo diário
- 4 Dashboard
- 5 Refino (dedup, prompts, relatórios)

## Pendências pra iniciar Fase 0
1. Chip dedicado (recomendado) vs número pessoal — RISCO DE BAN no número pessoal
2. Projeto Supabase (criar via MCP)
3. Evolution API: JÁ RODANDO na VPS do usuário — falta URL base + API key + nome da instância
4. Nomes dos 5+ grupos (JIDs) + vendedores e apelidos (seed)
5. Chave de IA (OpenAI ou Anthropic)
6. Conta Render (deploy do FastAPI via repo Git)
