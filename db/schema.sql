-- ============================================================================
-- Agente Loja SB — SCHEMA (fonte da verdade)
-- Snapshot completo e fiel do estado de produção.
-- Migrations incrementais ficam em db/migrations/ e são aplicadas EM ORDEM.
-- Rodar este arquivo num Postgres limpo recria o sistema do zero.
-- ============================================================================

-- ===== Tipos (enums) =====
do $$ begin create type vendedor_funcao as enum ('vendedor','sdr','gerente'); exception when duplicate_object then null; end $$;
do $$ begin create type veiculo_status as enum ('a_anunciar','anunciado','reservado','vendido','entregue','inativo'); exception when duplicate_object then null; end $$;
do $$ begin create type pagamento_status as enum ('pendente','parcial','pago'); exception when duplicate_object then null; end $$;
do $$ begin create type entrega_status as enum ('pendente','entregue'); exception when duplicate_object then null; end $$;
do $$ begin create type evento_tipo as enum ('venda','avaliacao','agendamento','comparecimento','anuncio','anuncio_publicado','pagamento','entrega','entrega_agendada','recall','nenhum'); exception when duplicate_object then null; end $$;
do $$ begin create type evento_status as enum ('processando','auto','pendente_confirmacao','confirmado','descartado','ignorado_planilha','erro'); exception when duplicate_object then null; end $$;

create or replace function set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end; $$ language plpgsql;

-- ===== grupos =====
create table if not exists grupos (
  id uuid primary key default gen_random_uuid(),
  jid text unique not null,
  nome text not null,
  tipo text,
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- ===== vendedores =====
create table if not exists vendedores (
  id uuid primary key default gen_random_uuid(),
  nome text not null,
  apelidos text[] not null default '{}',
  funcao vendedor_funcao not null default 'vendedor',
  telefone text,
  ativo boolean not null default true,
  created_at timestamptz not null default now()
);

-- ===== veiculos (estoque) =====
create table if not exists veiculos (
  id uuid primary key default gen_random_uuid(),
  marca text, modelo text, versao text, ano integer, cor text, km integer, placa text,
  preco_anuncio numeric(12,2), preco_custo numeric(12,2), link_anuncio text,
  status veiculo_status not null default 'a_anunciar',
  data_anuncio date, observacoes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
drop trigger if exists trg_veiculos_updated on veiculos;
create trigger trg_veiculos_updated before update on veiculos for each row execute function set_updated_at();

-- ===== vendas =====
create table if not exists vendas (
  id uuid primary key default gen_random_uuid(),
  veiculo_id uuid references veiculos(id) on delete set null,
  vendedor_id uuid references vendedores(id) on delete set null,
  cliente_nome text,
  marca text, modelo text, versao text, ano integer, cor text, km integer, placa text, em_estoque boolean,
  valor_venda numeric(12,2), tabela_preco numeric(12,2), desconto numeric(12,2),
  over_valor text, retorno text,
  forma_pagamento text, banco text,
  valor_entrada numeric(12,2), valor_financiado numeric(12,2), valor_pix text, valor_avista numeric(12,2),
  troca_modelo text, troca_placa text, troca_valor numeric(12,2),
  debitos numeric(12,2), valor_total numeric(12,2), ipva text, beneficios text, portal_venda text,
  cliente_cpf text, cliente_email text, cliente_telefone text, cliente_endereco text, cliente_cep text,
  data_venda date, data_entrega_prevista date, data_entrega_real date,
  status_pagamento pagamento_status not null default 'pendente',
  status_entrega entrega_status not null default 'pendente',
  observacoes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
drop trigger if exists trg_vendas_updated on vendas;
create trigger trg_vendas_updated before update on vendas for each row execute function set_updated_at();
create index if not exists idx_vendas_vendedor  on vendas(vendedor_id);
create index if not exists idx_vendas_pagamento on vendas(status_pagamento);
create index if not exists idx_vendas_entrega   on vendas(status_entrega);
create index if not exists idx_vendas_data      on vendas(data_venda);
create index if not exists idx_vendas_created   on vendas(created_at);

-- ===== avaliacoes =====
create table if not exists avaliacoes (
  id uuid primary key default gen_random_uuid(),
  loja text, modelo text, versao text, combustivel text, ano integer, km integer, placa text,
  ar_condicionado boolean, gelando boolean, buzina boolean, limpador boolean,
  luz_painel boolean, chave_reserva boolean, revisado boolean,
  revisao text, pecas_qtd integer, pecas_obs text, pneus text, obs text,
  fipe numeric(12,2), valor_avaliacao numeric(12,2), valor_pretendido numeric(12,2),
  carro_troca text, carro_interesse text,
  vendedor_id uuid references vendedores(id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists idx_avaliacoes_created on avaliacoes(created_at);

-- ===== entregas (lista do grupo de ENTREGAS) =====
create table if not exists entregas (
  id uuid primary key default gen_random_uuid(),
  loja text, data_entrega date, horario text,
  vendedor_id uuid references vendedores(id) on delete set null,
  veiculo text, placa text, observacao text,
  status text not null default 'agendada',
  ref_externa text,
  created_at timestamptz not null default now()
);
create unique index if not exists uq_entregas_ref on entregas(ref_externa) where ref_externa is not null;

-- ===== agendamentos (FONTE = PLANILHA) =====
create table if not exists agendamentos (
  id uuid primary key default gen_random_uuid(),
  cliente_nome text, telefone text,
  sdr_id uuid references vendedores(id) on delete set null,
  vendedor_id uuid references vendedores(id) on delete set null,
  veiculo_id uuid references veiculos(id) on delete set null,
  data_agendada timestamptz, compareceu boolean,
  resultado text, origem text, observacoes text, ref_externa text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
drop trigger if exists trg_agendamentos_updated on agendamentos;
create trigger trg_agendamentos_updated before update on agendamentos for each row execute function set_updated_at();
create unique index if not exists uq_agend_ref on agendamentos(ref_externa);
create index if not exists idx_agend_data       on agendamentos(data_agendada);
create index if not exists idx_agend_compareceu on agendamentos(compareceu);

-- ===== eventos_brutos (auditoria de toda mensagem) =====
create table if not exists eventos_brutos (
  id uuid primary key default gen_random_uuid(),
  grupo_id uuid references grupos(id) on delete set null,
  message_id text, remetente text, remetente_nome text, mensagem_original text, timestamp_msg timestamptz,
  tipo_evento evento_tipo, dados_extraidos jsonb, confianca numeric(3,2),
  status evento_status not null default 'auto',
  registro_tabela text, registro_id uuid,
  created_at timestamptz not null default now()
);
create unique index if not exists uq_eventos_message_id on eventos_brutos(message_id) where message_id is not null;
create index if not exists idx_eventos_status  on eventos_brutos(status);
create index if not exists idx_eventos_grupo   on eventos_brutos(grupo_id);
create index if not exists idx_eventos_created on eventos_brutos(created_at);

-- ===== lembretes =====
create table if not exists lembretes (
  id uuid primary key default gen_random_uuid(),
  numero text not null, texto text not null, quando timestamptz not null,
  enviado boolean not null default false, created_at timestamptz not null default now()
);
create index if not exists idx_lembretes_due on lembretes(enviado, quando);

-- ===== conversas (memória do chat) =====
create table if not exists conversas (
  id uuid primary key default gen_random_uuid(),
  numero text not null, papel text not null, conteudo text not null,
  created_at timestamptz not null default now()
);
create index if not exists idx_conversas_num on conversas(numero, created_at desc);

-- ===== relatorios_enviados (evita relatório duplicado) =====
create table if not exists relatorios_enviados (
  tipo text not null, data date not null, created_at timestamptz default now(),
  primary key (tipo, data)
);

-- ===== alertas (supervisor: rule engine + watchdog) =====
create table if not exists alertas (
  id uuid primary key default gen_random_uuid(),
  tipo text not null, chave text not null,
  severidade text not null default 'aviso',
  titulo text not null, detalhe text,
  entidade_tabela text, entidade_id uuid,
  status text not null default 'aberto',
  notificado boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  resolved_at timestamptz
);
create unique index if not exists uq_alertas_aberto on alertas(tipo, chave) where status = 'aberto';
create index if not exists idx_alertas_status on alertas(status);
create index if not exists idx_alertas_notificar on alertas(notificado) where status = 'aberto';
drop trigger if exists trg_alertas_updated on alertas;
create trigger trg_alertas_updated before update on alertas for each row execute function set_updated_at();

-- ===== insights (análise gerada por IA) =====
create table if not exists insights (
  id uuid primary key default gen_random_uuid(),
  periodo text not null, data date not null,
  conteudo text not null, dados jsonb,
  created_at timestamptz not null default now()
);
create unique index if not exists uq_insights on insights(periodo, data);

-- ===== notas (anotações livres do dono) =====
create table if not exists notas (
  id uuid primary key default gen_random_uuid(),
  numero text, texto text not null,
  resolvida boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists idx_notas_abertas on notas(resolvida, created_at);

-- ===== metas (por loja ou vendedor, por mês) =====
create table if not exists metas (
  id uuid primary key default gen_random_uuid(),
  escopo text not null default 'loja',
  vendedor_id uuid references vendedores(id) on delete cascade,
  mes date not null,
  meta_vendas integer, meta_faturamento numeric(12,2),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_metas_mes on metas(mes);
drop trigger if exists trg_metas_updated on metas;
create trigger trg_metas_updated before update on metas for each row execute function set_updated_at();

-- ===== recalls (grupo RECALL) =====
create table if not exists recalls (
  id uuid primary key default gen_random_uuid(),
  cliente_nome text, veiculo text, placa text, motivo text,
  status text not null default 'aberto', observacao text,
  created_at timestamptz not null default now()
);
create index if not exists idx_recalls_status on recalls(status);
