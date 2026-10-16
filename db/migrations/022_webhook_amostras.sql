-- 022_webhook_amostras — guarda payloads de eventos novos da Evolution (ex.: messages.edited) pra conferir o formato
create table if not exists webhook_amostras (id bigserial primary key, evento text, corpo jsonb, created_at timestamptz not null default now());
