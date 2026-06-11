-- 002_supervisor — Fase 2: alertas (rule engine + watchdog), insights (análise IA) e notas (anotações)
create table if not exists alertas (
  id uuid primary key default gen_random_uuid(),
  tipo text not null,                 -- R1..R10 / W1..W5
  chave text not null,                -- chave de dedup (ex: veiculo:<id>)
  severidade text not null default 'aviso',  -- info | aviso | critico
  titulo text not null,
  detalhe text,
  entidade_tabela text,
  entidade_id uuid,
  status text not null default 'aberto',     -- aberto | resolvido | silenciado
  notificado boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  resolved_at timestamptz
);
-- só um alerta ABERTO por (tipo, chave) → não repete o mesmo problema
create unique index if not exists uq_alertas_aberto on alertas(tipo, chave) where status = 'aberto';
create index if not exists idx_alertas_status on alertas(status);
create index if not exists idx_alertas_notificar on alertas(notificado) where status = 'aberto';
drop trigger if exists trg_alertas_updated on alertas;
create trigger trg_alertas_updated before update on alertas for each row execute function set_updated_at();

create table if not exists insights (
  id uuid primary key default gen_random_uuid(),
  periodo text not null,              -- diario | semanal
  data date not null,
  conteudo text not null,
  dados jsonb,
  created_at timestamptz not null default now()
);
create unique index if not exists uq_insights on insights(periodo, data);

create table if not exists notas (
  id uuid primary key default gen_random_uuid(),
  numero text,
  texto text not null,
  resolvida boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists idx_notas_abertas on notas(resolvida, created_at);
