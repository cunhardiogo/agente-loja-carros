-- 012_entregas_extras — checklist da preparação no Trello e pós-venda aprovado pelo dono
alter table entregas add column if not exists trello_checklist_id text;
alter table entregas add column if not exists checklist_itens jsonb;
alter table vendas add column if not exists posvenda_status text;       -- proposta | enviada | descartada
alter table vendas add column if not exists posvenda_texto text;
alter table vendas add column if not exists posvenda_proposta_em timestamptz;
alter table vendas add column if not exists posvenda_enviada_em timestamptz;
