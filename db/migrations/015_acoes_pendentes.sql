-- 015_acoes_pendentes — ação pedida pelo dono que precisa de "ok" antes (mover/criar cartão, tirar tarefa)
create table if not exists acoes_pendentes (
  id uuid primary key default gen_random_uuid(),
  tipo text not null,
  params jsonb not null,
  descricao text not null,
  status text not null default 'pendente',   -- pendente | feita | cancelada
  created_at timestamptz not null default now()
);
