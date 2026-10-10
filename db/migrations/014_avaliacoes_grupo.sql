-- 014_avaliacoes_grupo — formulário do grupo AVALIAÇÕES + valor do gestor (literal) + desfecho com a venda
alter table avaliacoes add column if not exists message_id text;
alter table avaliacoes add column if not exists origem text;                 -- grupo | llm
alter table avaliacoes add column if not exists autor text;
alter table avaliacoes add column if not exists avaliado_em timestamptz;     -- quando o formulário foi postado
alter table avaliacoes add column if not exists combustivel_txt text;
alter table avaliacoes add column if not exists pecas_pintadas text;
alter table avaliacoes add column if not exists pecas_trocar text;
alter table avaliacoes add column if not exists valor_texto text;            -- exatamente o que o gestor escreveu
alter table avaliacoes add column if not exists valor_por text;
alter table avaliacoes add column if not exists valor_em timestamptz;
alter table avaliacoes add column if not exists aviso_em timestamptz;
alter table avaliacoes add column if not exists status text not null default 'aberta';  -- aberta | fechou
alter table avaliacoes add column if not exists venda_id uuid references vendas(id) on delete set null;
alter table avaliacoes add column if not exists trello_card_id text;
alter table avaliacoes add column if not exists historico boolean not null default false;
alter table avaliacoes add column if not exists texto_original text;
create unique index if not exists uq_avaliacoes_msg on avaliacoes(message_id) where message_id is not null;
