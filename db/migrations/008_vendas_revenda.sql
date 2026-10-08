-- 008_vendas_revenda — venda para revenda/repasse: fora do ranking, listada abaixo do traçado na descrição
alter table vendas add column if not exists revenda boolean not null default false;
