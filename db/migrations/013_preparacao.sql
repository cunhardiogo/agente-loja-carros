-- 013_preparacao — grupo PREPARAÇÃO: tarefas por carro (checklist no Trello), problemas registrados no carro
create table if not exists prep_tarefas (
  id uuid primary key default gen_random_uuid(),
  message_id text,
  carro_texto text, card_id text, card_nome text,
  tipo text,                     -- pintura | mecanica | estetica | peca_compra | foto | buscar_levar | outro
  descricao text not null,
  responsavel text,
  status text not null default 'pendente',   -- pendente | feito
  criado_em timestamptz not null,
  feito_em timestamptz,
  checklist_item_id text,
  cobrado_em timestamptz,
  historico boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists idx_prep_tarefas_status on prep_tarefas(status);

create table if not exists prep_problemas (
  id uuid primary key default gen_random_uuid(),
  message_id text unique,
  carro_texto text, card_id text, card_nome text,
  descricao text not null,
  autor text,
  em timestamptz not null,
  comentado_trello boolean not null default false,
  historico boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists prep_msgs (message_id text primary key, created_at timestamptz not null default now());
