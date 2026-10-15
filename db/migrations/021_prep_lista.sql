-- 021_prep_lista — tarefa que veio de uma lista de Missões: some da lista seguinte do mesmo carro = feita
alter table prep_tarefas add column if not exists lista boolean not null default false;
