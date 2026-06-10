-- 001_baseline_sync — alinha a produção ao db/schema.sql (idempotente)
-- Valores de enum que o código já usa mas faltavam no banco + novos (processando/erro p/ webhook async).
alter type evento_status add value if not exists 'processando';
alter type evento_status add value if not exists 'ignorado_planilha';
alter type evento_status add value if not exists 'erro';

-- Índices que faltavam para as consultas/relatórios mais frequentes.
create index if not exists idx_vendas_created    on vendas(created_at);
create index if not exists idx_eventos_created   on eventos_brutos(created_at);
create index if not exists idx_avaliacoes_created on avaliacoes(created_at);
create index if not exists idx_conversas_num     on conversas(numero, created_at desc);
