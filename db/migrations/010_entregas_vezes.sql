-- 010_entregas_vezes — em quantas listas a entrega já apareceu (cópia desatualizada não dá baixa)
alter table entregas add column if not exists vezes_na_lista integer not null default 0;
