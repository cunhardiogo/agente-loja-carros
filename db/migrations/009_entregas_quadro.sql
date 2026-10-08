-- 009_entregas_quadro — grupo ENTREGAS como quadro: lista repostada = situação atual; sumiu = entregue
alter table entregas add column if not exists venda_id uuid references vendas(id) on delete set null;
alter table entregas add column if not exists origem text;                 -- grupo | planilha
alter table entregas add column if not exists data_texto text;             -- como veio ("sem data /10", "XX/10")
alter table entregas add column if not exists primeira_vez_em timestamptz; -- quando apareceu no quadro
alter table entregas add column if not exists ultima_lista_em timestamptz; -- última lista em que estava
alter table entregas add column if not exists entregue_em timestamptz;
alter table entregas add column if not exists vendedor_nome text;
alter table entregas add column if not exists aviso_atraso_em timestamptz;
alter table entregas add column if not exists cobranca_status text;
alter table entregas add column if not exists cobranca_motivo text;
alter table entregas add column if not exists cobranca_texto text;
alter table entregas add column if not exists cobranca_proposta_em timestamptz;
alter table entregas add column if not exists cobranca_enviada_em timestamptz;
alter table entregas add column if not exists cobrado jsonb not null default '{}'::jsonb;
alter table entregas add column if not exists historico boolean not null default false;
alter table entregas add column if not exists removido boolean not null default false;
alter table entregas add column if not exists planilha_snap jsonb;
create index if not exists idx_entregas_status on entregas(status);
create index if not exists idx_entregas_venda on entregas(venda_id);
