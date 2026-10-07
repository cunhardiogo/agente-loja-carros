-- 006_vendas_grupo — grupo VENDAS: aviso curto cria a venda, resumo completa, reserva, comprovantes
alter table vendas add column if not exists status_venda text not null default 'completa'; -- aguardando_resumo | completa | reservado | desistiu
alter table vendas add column if not exists origem text;                 -- grupo | llm
alter table vendas add column if not exists autor text;                  -- participant (@lid) de quem avisou
alter table vendas add column if not exists aviso_message_id text;
alter table vendas add column if not exists resumo_message_id text;
alter table vendas add column if not exists aviso_em timestamptz;
alter table vendas add column if not exists resumo_em timestamptz;
alter table vendas add column if not exists reservado_em timestamptz;
alter table vendas add column if not exists texto_resumo text;
alter table vendas add column if not exists data_entrega_texto text;
alter table vendas add column if not exists pendencias text[];
alter table vendas add column if not exists docs jsonb not null default '{}'::jsonb;
alter table vendas add column if not exists valor_pago numeric(12,2) not null default 0;
alter table vendas add column if not exists contexto_em timestamptz;     -- última mensagem que citou o carro
alter table vendas add column if not exists contexto_autor text;
alter table vendas add column if not exists cobranca_status text;        -- proposta | enviada | descartada | cancelada
alter table vendas add column if not exists cobranca_motivo text;        -- resumo | pendencias | reserva
alter table vendas add column if not exists cobranca_texto text;
alter table vendas add column if not exists cobranca_proposta_em timestamptz;
alter table vendas add column if not exists cobranca_enviada_em timestamptz;
alter table vendas add column if not exists cobrado jsonb not null default '{}'::jsonb; -- motivos já cobrados
alter table vendas add column if not exists lead_id uuid references leads(id) on delete set null;
alter table vendas add column if not exists historico boolean not null default false;
alter table vendas add column if not exists removido boolean not null default false;
alter table vendas add column if not exists planilha_snap jsonb;
create unique index if not exists uq_vendas_aviso_msg on vendas(aviso_message_id) where aviso_message_id is not null;
create unique index if not exists uq_vendas_resumo_msg on vendas(resumo_message_id) where resumo_message_id is not null;
create index if not exists idx_vendas_status_venda on vendas(status_venda);

create table if not exists pagamentos (
  id uuid primary key default gen_random_uuid(),
  venda_id uuid references vendas(id) on delete set null,
  message_id text unique not null,
  id_transacao text,
  valor numeric(12,2),
  pago_em timestamptz,
  tipo text,                -- pix | ted | cartao | boleto | outro
  banco text, pagador text, recebedor text,
  arquivo text, autor text,
  recebido_em timestamptz not null,
  historico boolean not null default false,
  created_at timestamptz not null default now()
);
create unique index if not exists uq_pagamentos_transacao on pagamentos(id_transacao) where id_transacao is not null;
create index if not exists idx_pagamentos_venda on pagamentos(venda_id);

-- participant @lid de cada pessoa nos grupos (identifica o remetente sem nome)
alter table vendedores add column if not exists lid text;
