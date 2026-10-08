-- 011_trello — cartão do quadro ESTOQUE ligado à venda
alter table vendas add column if not exists trello_card_id text;
