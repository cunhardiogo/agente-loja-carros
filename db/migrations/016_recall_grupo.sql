-- 016_recall_grupo — grupo RECALL: formulário/texto vira recall, agendamento e "pronto" pelo grupo, Trello e aviso
alter table recalls add column if not exists message_id text;
alter table recalls add column if not exists autor text;
alter table recalls add column if not exists vendedor_id uuid references vendedores(id);
alter table recalls add column if not exists venda_id uuid references vendas(id);
alter table recalls add column if not exists data_saida date;
alter table recalls add column if not exists km_saida integer;
alter table recalls add column if not exists km_atual integer;
alter table recalls add column if not exists agendado_para timestamptz;
alter table recalls add column if not exists agendado_texto text;
alter table recalls add column if not exists pronto_em timestamptz;
alter table recalls add column if not exists aviso_em timestamptz;
alter table recalls add column if not exists trello_card_id text;
alter table recalls add column if not exists checklist jsonb not null default '{}'::jsonb;  -- motivo → id do item
alter table recalls add column if not exists texto_original text;
alter table recalls add column if not exists historico boolean not null default false;
alter table recalls add column if not exists origem text;                                    -- grupo | dono
alter table recalls add column if not exists aberto_em timestamptz;
alter table recalls add column if not exists updated_at timestamptz not null default now();
create unique index if not exists uq_recalls_message_id on recalls(message_id);

-- cada mensagem do grupo (formulário, resposta) aponta pro recall — respostas em cadeia acham o recall
create table if not exists recall_msgs (
  message_id text primary key,
  recall_id uuid references recalls(id) on delete cascade,
  created_at timestamptz not null default now()
);
