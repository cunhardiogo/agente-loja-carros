-- 023: quando o agente põe o cartão no FINALIZADO; se alguém tirar de lá depois, a venda é reaberta (não foi entregue)
alter table vendas add column if not exists finalizado_pelo_agente_em timestamptz;
