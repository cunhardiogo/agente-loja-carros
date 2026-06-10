# CONTEXTO — Agente Loja SB (handoff completo)

Documento único de contexto pra entender e evoluir o agente. Todo o código está neste repo
(público): https://github.com/cunhardiogo/agente-loja-carros

## 🎯 Objetivo
Cérebro operacional de uma **loja de carros (Grupo SB / Soberano / Brutus)** dentro do WhatsApp:
escuta os grupos da equipe, entende vendas/pagamentos/entregas/avaliações/agendamentos com IA,
estrutura tudo no Supabase, e dá visão e respostas por WhatsApp e por dashboard — sem a equipe
preencher sistema.

## 🏗️ Arquitetura
- **App**: FastAPI (Python 3.12). Roda na **VPS do dono** (Ubuntu 20.04, Docker Swarm + Traefik),
  sempre ligado, ao lado da Evolution. Serviço swarm `agente_agente` na rede overlay `agenteintelnet`.
  Exposto pelo Traefik em `https://agente.agenteintel.com.br`.
- **Imagem**: `ghcr.io/cunhardiogo/agente-loja-carros:latest` (buildada por GitHub Actions a cada push —
  `.github/workflows/docker-build.yml`).
- **Banco**: Supabase (Postgres). **IA**: OpenAI (gpt-4.1-mini extração, gpt-4.1 conversa).
  **WhatsApp**: Evolution API (`https://evo.agenteintel.com.br`).
- **2 números**: `diogo4895` (coletor — fica nos grupos só ESCUTANDO; é o nº pessoal do dono 5521980994895)
  e `lojasb` (assistente — fala com o dono no privado; nº 5516994539586).
- Webhook Evolution → app interno `http://agente_agente:8000/webhook/evolution`. App → Evolution interno
  `http://evolution:8080`.
- **Deploy de mudança**: commit → GitHub Actions builda a imagem → na VPS:
  `docker service update --image ghcr.io/cunhardiogo/agente-loja-carros:latest --force agente_agente`.

## 📁 Estrutura
- `app/main.py` — FastAPI: webhook, roteamento grupo/DM, endpoints /cron/*, /dashboard, /api/metrics,
  agendador interno (`_loop_agendador`/`_tick`: lembretes, relatórios, checagem lojasb, sync planilha),
  builders de relatório (agenda manhã, fechamento, semanal, planejamento).
- `app/ingest.py` — pipeline de ingestão: `processar` (split de entregas), `aplicar` (INSERT/UPDATE por
  tipo), matchers (`_venda_pendente` casa por cliente OU veículo, `_agendamento_recente`), dedup,
  confirmação, `parse_pix` (calcula entrada).
- `app/llm.py` — `SYSTEM` (prompt de extração), `extrair` (structured output → schema Extracao),
  `transcrever_audio` (whisper), `ler_imagem` (visão).
- `app/consulta.py` — agente conversacional: `SYSTEM` (prompt), `responder` (function calling com memória),
  ~21 ferramentas (consulta/ação/edição/lembretes), `carregar_historico`/`salvar_conversa`.
- `app/media.py` — extrai texto de texto/áudio/imagem + contexto de **reply** (`_citada`).
- `app/planilha.py` — sync da planilha Google (xlsx, todas as abas <Mes>26), upsert por ref_externa,
  espelha exclusões, preserva comparecimento do grupo.
- `app/confirmacao.py` — loop sim/não/corrigir das pendências.
- `app/evolution.py` — envio (lojasb/coletor), get_media, estado, alertas.
- `app/schemas.py` — `Extracao` (Pydantic) + `TipoEvento`.
- `app/datas.py` — agora()/hoje() em America/Sao_Paulo.
- `app/config.py` — Settings (env vars).
- `db/schema.sql` + `db/seed.sql` — schema e seed.
- `Dockerfile`, `CONTEXTO.md` (este), `.github/workflows/*` (build + crons manuais).

## 👥 Grupos monitorados (JIDs)
| JID | Nome | tipo |
|---|---|---|
| 120363394210533119@g.us | VENDAS - GRUPO SB | vendas |
| 120363196256311293@g.us | AVALIAÇÕES SOBERANO / BRUTUS | avaliacoes |
| 120363378250723351@g.us | ENTREGAS: BRUTUS / SOBERANO | entregas |
| 120363417524289707@g.us | Fotos - GRUPO SB | estoque |
| 120363418933621858@g.us | Agendamento SDR SB | agendamentos |
| 120363401771669249@g.us | TRÁFEGO - GRUPO SB | trafego |
| 120363422349822318@g.us | Marketing Grupo SB | marketing |
| 120363414450144897@g.us | RECALL GRUPO SB | recall |
| 120363408838873964@g.us | GERÊNCIA SB | geral |

## 👤 Equipe
- Vendedores: Carlos, Claudia, Diogo, Vinicius (apelido Vini), Yan (apelido Ian)
- SDRs: Mario, Renata

## 🗄️ Modelo de dados (tabelas Supabase)
- `vendas` — venda completa: cliente_nome/cpf/email/telefone/endereco/cep, marca/modelo/versao/ano/cor/km/placa,
  em_estoque, tabela_preco, valor_venda, desconto, over_valor, retorno, banco, valor_entrada (já pago),
  valor_financiado, valor_pix(texto), valor_avista, troca_modelo/placa/valor, debitos, valor_total, ipva,
  beneficios, portal_venda, forma_pagamento, data_venda, data_entrega_prevista/real,
  status_pagamento(pendente|parcial|pago), status_entrega(pendente|entregue), observacoes.
- `avaliacoes` — loja, modelo, versao, combustivel, ano, km, placa, checklist (ar_condicionado, gelando,
  buzina, limpador, luz_painel, chave_reserva, revisado), revisao, pecas_qtd, pecas_obs, pneus, obs, fipe,
  valor_avaliacao, valor_pretendido (o que o cliente pede), carro_troca, carro_interesse (o que ele quer).
- `entregas` — loja, data_entrega, horario, vendedor_id, veiculo, placa, observacao, status, ref_externa.
- `veiculos` — estoque: marca/modelo/versao/ano/cor/km/placa, preco_anuncio, preco_custo, link_anuncio,
  status(a_anunciar|anunciado|reservado|vendido|entregue|inativo), data_anuncio, observacoes.
- `agendamentos` — (FONTE = PLANILHA) cliente_nome, sdr_id, vendedor_id, veiculo_id, data_agendada,
  compareceu(bool|null), resultado(status cru da planilha), origem='planilha', ref_externa, observacoes.
- `vendedores` — nome, apelidos[], funcao(vendedor|sdr|gerente), telefone, ativo.
- `eventos_brutos` — auditoria de TODA mensagem: grupo, message_id, remetente, mensagem_original,
  tipo_evento, dados_extraidos(jsonb), confianca, status(auto|pendente_confirmacao|confirmado|descartado|ignorado_planilha),
  registro_tabela/id.
- `lembretes` — numero, texto, quando(timestamptz), enviado.
- `conversas` — numero, papel(user|assistant), conteudo (memória do chat).
- `relatorios_enviados` — (tipo, data) PK — evita relatório duplicado.

## 🔄 Tipos de evento (extração)
venda · avaliacao · entrega_agendada (lista 🎁 ENTREGAS) · anuncio (carro chegando) ·
anuncio_publicado ("anunciei o X") · pagamento (casa por carro/cliente) · entrega ("Onix entregue") ·
comparecimento ("Não veio" via reply) · agendamento (visita — vem da planilha, ignorado) · nenhum.

## 📊 Relatórios (disparados pelo agendador interno da VPS, fuso BRT)
- Planejamento: seg 08:00 · Agenda: seg–sáb 09:00 · Fechamento: seg–sex 18:00 e sáb 15:00 ·
  Semanal: dom 18:00. Vão pro dono + NUMEROS_RELATORIO.

## ⏰ Lembretes / 🔔 Alertas
- Lembretes vencidos disparados a cada ~60s pelo loop interno.
- Se `lojasb` desconectar (groupsIgnore/queda), alerta o 2º número pelo coletor.

## 📋 Fonte planilha (agendamentos/comparecimento/vendido/reservado)
Google Sheets (uma aba por mês `<Mes>26`). Sync via xlsx export + Apps Script onEdit (realtime) +
loop interno (10 min). Status na planilha → compareceu/vendido/reservado. É a FONTE OFICIAL de
agendamentos (grupos não criam agendamento).

## ⚙️ Variáveis de ambiente (valores reais ficam no stack do Portainer, NÃO no repo)
SUPABASE_URL, SUPABASE_SERVICE_ROLE, OPENAI_API_KEY, OPENAI_MODEL_EXTRACAO, OPENAI_MODEL_CONSULTA,
EVOLUTION_URL (http://evolution:8080), EVOLUTION_INSTANCE (diogo4895), EVOLUTION_APIKEY,
EVOLUTION_ASSIST_INSTANCE (lojasb), EVOLUTION_ASSIST_APIKEY, MEU_NUMERO (5521980994895),
NUMEROS_RELATORIO (5521994110597), DASHBOARD_TOKEN, CONFIANCA_MINIMA (0.8), VERIFY_SSL (true),
planilha_sheet_id (default no config), agendamento_via_grupo (false).

## ⚠️ Pendências / ideias futuras
- **Meta Ads** (token + act_ID) pra cruzar gasto/leads/ROAS com vendas do canal Tráfego — não conectado.
- Metas por vendedor/loja + comparativos (mês vs mês).
- Tempo de giro do estoque, margem real (precisa custo).
- Conversa mais proativa (alertas: carro encalhado, venda sem pagamento, entrega não confirmada).
- Captura estruturada do grupo RECALL (hoje vira "nenhum").
- Rotacionar chaves (service_role/OpenAI/Evolution passaram por chat).

## 🧠 Prompts
- Extração: `app/llm.py` → `SYSTEM`.
- Conversa: `app/consulta.py` → `SYSTEM` (+ `_system_dinamico` injeta data/hora).
Ler direto no código pra ter a versão exata.
