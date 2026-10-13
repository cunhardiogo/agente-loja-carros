-- 018_acesso_operacional — quem pediu cada ação (dono ou gerente com acesso operacional)
alter table acoes_pendentes add column if not exists numero text;
alter table acoes_log add column if not exists autor text;
insert into config_flags (nome, valor) values ('acesso_operacional', '5521994110597') on conflict (nome) do nothing;
