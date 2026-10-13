-- 019_cartao_aviso — venda sem placa que não achou cartão no Trello: avisa o dono uma vez (não cria cartão)
alter table vendas add column if not exists cartao_aviso_em timestamptz;
