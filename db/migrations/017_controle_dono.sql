-- 017_controle_dono — arquivo de todas as mensagens (busca), liga/desliga de automações, histórico p/ desfazer
create table if not exists mensagens (
  message_id text primary key,
  jid text not null,
  conversa text,                 -- nome do grupo / contato
  autor text,                    -- lid/número
  autor_nome text,
  from_me boolean not null default false,
  tipo text,                     -- texto | foto | video | audio | documento | sticker | outro
  texto text,
  citada text,                   -- message_id respondido
  em timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists idx_mensagens_jid_em on mensagens(jid, em desc);
create index if not exists idx_mensagens_em on mensagens(em desc);
create extension if not exists pg_trgm;
create index if not exists idx_mensagens_texto_trgm on mensagens using gin (texto gin_trgm_ops);

create table if not exists config_flags (
  nome text primary key,
  ativo boolean,
  valor text,
  updated_at timestamptz not null default now()
);
insert into config_flags (nome, ativo) values ('recados_preparacao', false) on conflict (nome) do nothing;

create table if not exists acoes_log (
  id uuid primary key default gen_random_uuid(),
  tabela text not null,
  registro_id text not null,
  antes jsonb,                   -- null = registro criado pela ação (desfazer = remover)
  depois jsonb,
  descricao text not null,
  desfeito boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists idx_acoes_log_created on acoes_log(created_at desc);
