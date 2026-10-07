-- 007_documentos_venda — documentos do grupo VENDAS (só o tipo; nada do conteúdo), ligáveis depois à venda
create table if not exists documentos (
  id uuid primary key default gen_random_uuid(),
  message_id text unique not null,
  venda_id uuid references vendas(id) on delete set null,
  tipo text not null,           -- cnh | documento_identidade | comprovante_residencia | contrato
  arquivo text, autor text,
  recebido_em timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists idx_documentos_venda on documentos(venda_id);
