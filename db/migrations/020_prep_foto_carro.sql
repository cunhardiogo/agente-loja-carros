-- 020_prep_foto_carro — foto sem texto no grupo de preparação: carro/placa lidos da imagem pra ligar a mensagem seguinte
alter table prep_msgs add column if not exists carro text;
alter table prep_msgs add column if not exists autor text;
alter table prep_msgs add column if not exists em timestamptz;
