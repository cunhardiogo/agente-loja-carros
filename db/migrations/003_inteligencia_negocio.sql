-- 003_inteligencia_negocio — Fase 4: metas, recalls (e enum recall)
create table if not exists metas (
  id uuid primary key default gen_random_uuid(),
  escopo text not null default 'loja',     -- loja | vendedor
  vendedor_id uuid references vendedores(id) on delete cascade,
  mes date not null,                        -- 1º dia do mês de referência
  meta_vendas integer,
  meta_faturamento numeric(12,2),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_metas_mes on metas(mes);
drop trigger if exists trg_metas_updated on metas;
create trigger trg_metas_updated before update on metas for each row execute function set_updated_at();

create table if not exists recalls (
  id uuid primary key default gen_random_uuid(),
  cliente_nome text, veiculo text, placa text, motivo text,
  status text not null default 'aberto',   -- aberto | resolvido
  observacao text,
  created_at timestamptz not null default now()
);
create index if not exists idx_recalls_status on recalls(status);

-- grupo RECALL passa a gerar evento próprio (antes virava 'nenhum')
alter type evento_tipo add value if not exists 'recall';
