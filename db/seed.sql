-- Seed inicial — Grupo SB

-- Grupos monitorados
insert into grupos (jid, nome, tipo) values
  ('120363422349822318@g.us', 'Marketing Grupo SB',            'marketing'),
  ('120363401771669249@g.us', 'TRÁFEGO - GRUPO SB',            'trafego'),
  ('120363408838873964@g.us', 'GERÊNCIA SB',                   'geral'),
  ('120363414450144897@g.us', 'RECALL GRUPO SB',               'recall'),
  ('120363418933621858@g.us', 'Agendamento SDR SB',            'agendamentos'),
  ('120363394210533119@g.us', 'VENDAS - GRUPO SB',             'vendas'),
  ('120363417524289707@g.us', 'Fotos - GRUPO SB',              'estoque'),
  ('120363378250723351@g.us', 'ENTREGAS: BRUTUS / SOBERANO',   'entregas')
on conflict (jid) do update set nome = excluded.nome, tipo = excluded.tipo;

-- Vendedores
insert into vendedores (nome, funcao) values
  ('Claudia',  'vendedor'),
  ('Carlos',   'vendedor'),
  ('Yan',      'vendedor'),
  ('Vinicius', 'vendedor'),
  ('Diogo',    'vendedor')
on conflict do nothing;

-- SDRs (marcam visita)
insert into vendedores (nome, funcao) values
  ('Mario',  'sdr'),
  ('Renata', 'sdr')
on conflict do nothing;
