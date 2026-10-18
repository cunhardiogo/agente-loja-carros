create table if not exists sdr_produtividade (
    id uuid primary key default gen_random_uuid(),
    data date not null,
    sdr text not null,
    leads int,
    agendamentos int,
    negociacao int,
    vendas int,
    message_id text,
    texto text,
    created_at timestamptz not null default now(),
    unique (data, sdr)
);

insert into grupos (jid, nome, tipo, ativo)
select '120363413734430943@g.us', 'Sdr''s', 'arquivo', true
where not exists (select 1 from grupos where jid = '120363413734430943@g.us');
