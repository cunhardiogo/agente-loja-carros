-- 005_planilha_snapshot — o que o agente escreveu na planilha por lead (detecta edição da equipe)
alter table leads add column if not exists planilha_snap jsonb;
