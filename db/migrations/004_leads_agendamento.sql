-- 004_leads_agendamento — grupo Agendamento SDR vira fonte oficial de visitas e negociações
create table if not exists leads (
  id uuid primary key default gen_random_uuid(),
  message_id text unique not null,           -- id da mensagem do formulário no grupo
  recebido_em timestamptz not null,
  sdr text,
  tipo text not null,                        -- visita | negociacao_telefone | turno
  fila text,                                 -- rótulo cru: 'Fila Negociação', 'TURNO MANHÃ'...
  loja text, localizacao text,
  cliente_nome text, telefone text,
  data_agendada date, horario text,
  veiculo text,
  vendedor_id uuid references vendedores(id) on delete set null,
  vendedor_nome text,
  canal text,
  troca text, oferta_entrada text, observacao text,
  status text not null default 'aguardando_retorno',
  status_em timestamptz,
  nova_data date,                            -- quando remarcado
  ultimo_retorno_em timestamptz, ultimo_retorno_texto text, ultimo_retorno_por text,
  aviso_dono_em timestamptz,                 -- aviso de 30 min
  cobranca_status text,                      -- proposta | enviada | descartada | cancelada
  cobranca_texto text,
  cobranca_proposta_em timestamptz, cobranca_enviada_em timestamptz,
  historico boolean not null default false,  -- importado do passado: não gera aviso/cobrança
  removido boolean not null default false,   -- SDR apagou a mensagem
  texto_original text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_leads_recebido on leads(recebido_em);
create index if not exists idx_leads_status on leads(status);
create index if not exists idx_leads_vendedor on leads(vendedor_id);
create index if not exists idx_leads_cobranca on leads(cobranca_status);
drop trigger if exists trg_leads_updated on leads;
create trigger trg_leads_updated before update on leads for each row execute function set_updated_at();

create table if not exists lead_eventos (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references leads(id) on delete cascade,
  message_id text unique,
  em timestamptz not null,
  autor text,
  texto text,
  status text,                               -- status que a resposta indicou (ou 'retorno')
  created_at timestamptz not null default now()
);
create index if not exists idx_lead_eventos_lead on lead_eventos(lead_id);

-- notas "☝🏼" que vêm logo após o formulário
create table if not exists lead_notas_msg (
  message_id text primary key,
  lead_id uuid not null references leads(id) on delete cascade,
  created_at timestamptz not null default now()
);

-- autor (participant @lid) do formulário: liga a nota "☝🏼" ao lead certo
alter table leads add column if not exists autor text;
