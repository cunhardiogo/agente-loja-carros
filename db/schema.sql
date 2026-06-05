-- Agente Loja de Carros (Grupo SB) — schema
-- Aplicado via Supabase Management API. Backend usa service_role; dashboard lê via FastAPI.

-- ===== Tipos =====
do $$ begin
  create type vendedor_funcao as enum ('vendedor','sdr','gerente');
exception when duplicate_object then null; end $$;

do $$ begin
  create type veiculo_status as enum ('anunciado','reservado','vendido','entregue','inativo');
exception when duplicate_object then null; end $$;

do $$ begin
  create type pagamento_status as enum ('pendente','parcial','pago');
exception when duplicate_object then null; end $$;

do $$ begin
  create type entrega_status as enum ('pendente','entregue');
exception when duplicate_object then null; end $$;

do $$ begin
  create type evento_tipo as enum ('venda','agendamento','comparecimento','anuncio','pagamento','entrega','nenhum');
exception when duplicate_object then null; end $$;

do $$ begin
  create type evento_status as enum ('auto','pendente_confirmacao','confirmado','descartado');
exception when duplicate_object then null; end $$;

-- ===== Função updated_at =====
create or replace function set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

-- ===== grupos =====
create table if not exists grupos (
  id uuid primary key default gen_random_uuid(),
  jid text unique not null,
  nome text not null,
  tipo text,                 -- marketing|trafego|geral|recall|agendamentos|vendas|estoque|entregas
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- ===== vendedores (inclui SDRs) =====
create table if not exists vendedores (
  id uuid primary key default gen_random_uuid(),
  nome text not null,
  apelidos text[] not null default '{}',  -- matching no texto livre: ["JP","Joãozinho"]
  funcao vendedor_funcao not null default 'vendedor',
  telefone text,
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- ===== veiculos (estoque / anúncios) =====
create table if not exists veiculos (
  id uuid primary key default gen_random_uuid(),
  marca text,
  modelo text,
  ano integer,
  versao text,
  cor text,
  km integer,
  placa text,
  preco_anuncio numeric(12,2),
  preco_custo numeric(12,2),
  link_anuncio text,
  status veiculo_status not null default 'anunciado',
  data_anuncio date,
  observacoes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger trg_veiculos_updated before update on veiculos
  for each row execute function set_updated_at();

-- ===== vendas =====
create table if not exists vendas (
  id uuid primary key default gen_random_uuid(),
  veiculo_id uuid references veiculos(id) on delete set null,
  vendedor_id uuid references vendedores(id) on delete set null,
  cliente_nome text,
  valor_venda numeric(12,2),
  forma_pagamento text,
  data_venda date,
  status_pagamento pagamento_status not null default 'pendente',
  status_entrega entrega_status not null default 'pendente',
  data_entrega_prevista date,
  data_entrega_real date,
  observacoes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger trg_vendas_updated before update on vendas
  for each row execute function set_updated_at();

-- ===== agendamentos =====
create table if not exists agendamentos (
  id uuid primary key default gen_random_uuid(),
  cliente_nome text,
  telefone text,
  sdr_id uuid references vendedores(id) on delete set null,       -- quem marcou (Mario/Renata)
  vendedor_id uuid references vendedores(id) on delete set null,  -- pra qual vendedor
  veiculo_id uuid references veiculos(id) on delete set null,
  data_agendada timestamptz,
  compareceu boolean,        -- null = ainda não definido
  origem text,
  observacoes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger trg_agendamentos_updated before update on agendamentos
  for each row execute function set_updated_at();

-- ===== eventos_brutos (auditoria + confirmação da IA) =====
create table if not exists eventos_brutos (
  id uuid primary key default gen_random_uuid(),
  grupo_id uuid references grupos(id) on delete set null,
  message_id text,           -- id da msg no Evolution (idempotência)
  remetente text,            -- jid do autor
  remetente_nome text,
  mensagem_original text,
  timestamp_msg timestamptz,
  tipo_evento evento_tipo,
  dados_extraidos jsonb,
  confianca numeric(3,2),
  status evento_status not null default 'auto',
  registro_tabela text,      -- vendas|agendamentos|veiculos
  registro_id uuid,
  created_at timestamptz not null default now()
);
create unique index if not exists uq_eventos_message_id
  on eventos_brutos(message_id) where message_id is not null;

-- ===== índices de consulta =====
create index if not exists idx_vendas_vendedor   on vendas(vendedor_id);
create index if not exists idx_vendas_pagamento  on vendas(status_pagamento);
create index if not exists idx_vendas_entrega    on vendas(status_entrega);
create index if not exists idx_vendas_data       on vendas(data_venda);
create index if not exists idx_agend_data        on agendamentos(data_agendada);
create index if not exists idx_agend_compareceu  on agendamentos(compareceu);
create index if not exists idx_veiculos_status   on veiculos(status);
create index if not exists idx_eventos_status    on eventos_brutos(status);
create index if not exists idx_eventos_grupo     on eventos_brutos(grupo_id);
