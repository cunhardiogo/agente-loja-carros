# Catálogo de métricas — Grupo SB

Fonte única: `app/metricas.py`. API: `GET /api/metricas?periodo=mes&setores=vendas,leads` (token do dashboard).
Períodos: `hoje`, `ontem`, `semana`, `mes`, `mes_passado`, `30d`/`90d`, `ano`, `AAAA-MM`, ou `inicio`/`fim` (AAAA-MM-DD).
O agente responde as mesmas métricas pela ferramenta `metricas`. Cache de 5 min.

## Vendas (grupo VENDAS + lançamentos do dono)
| Métrica | O que é |
|---|---|
| vendidos, faturamento, ticket_medio | vendas que contam (sem reserva, queda e revenda) pela data da venda |
| vendas_sem_valor | vendas sem total no resumo (não entram no ticket) |
| revenda, faturamento_revenda | vendas para revenda (fora do ranking e do dinheiro) |
| reservas_abertas, reservas_no_periodo | reservas paradas hoje / feitas no período |
| caidas, taxa_queda_pct | vendas que caíram / (vendidas + caídas) |
| por_vendedor | vendas, faturamento e ticket por vendedor |
| por_canal, faturamento_por_canal | canal/portal padronizado (Tráfego, OLX, Webmotors, Indicação, Carteira…) |
| por_forma_pagamento, financiado_pct | Financiado / À vista / Consórcio |
| com_troca, com_troca_pct, valor_trocas | vendas com carro na troca |
| over_total, desconto_total, retorno_total | soma dos campos do resumo |
| por_faixa_preco, por_marca, por_dia_semana, serie_diaria | distribuição das vendas |
| resumo_completo_pct, sem_resumo, resumo_com_pendencia, campos_mais_faltando | disciplina do resumo de venda |
| tempo_aviso_ate_resumo_h | mediana de horas entre o aviso e o resumo |
| vindas_de_lead | vendas ligadas a um lead do agendamento |
| recebido_no_periodo, comprovantes_no_periodo | comprovantes lidos no grupo |
| a_receber | total, financiamento (banco) e à vista ainda a receber |

## Leads (grupo AGENDAMENTO SDR)
| Métrica | O que é |
|---|---|
| recebidos, por_tipo, por_sdr, por_status | leads que chegaram no período |
| por_canal | leads, vendidos e conversão por canal |
| convertidos, conversao_pct | lead que virou venda (status vendido ou venda ligada) |
| visitas_agendadas_passadas, compareceram, nao_vieram, comparecimento_pct | só visitas com data já passada |
| sem_retorno, retorno_mediano_min, retorno_ate_30min_pct | tempo até a primeira resposta no lead |
| remarcados, cancelados | |
| cobrancas_propostas, cobrancas_enviadas | cobranças de retorno |
| por_vendedor | leads, vendidos, conversão, sem retorno, retorno mediano |
| por_dia_semana, por_hora_chegada, serie_diaria | quando os leads chegam |

## Entregas (grupo ENTREGAS)
| Métrica | O que é |
|---|---|
| entregues, no_prazo_pct | entregas feitas no período; % até a data combinada |
| dias_venda_ate_entrega | mediana de dias entre a venda e a entrega |
| abertas, atrasadas, sem_data, proximos_7_dias | situação do quadro hoje |
| vendidos_sem_entrega_no_quadro | venda de outubro em diante sem entrega no quadro |
| por_vendedor, serie_diaria | |
| posvenda_propostos, posvenda_enviados | mensagens de pós-venda |

## Preparação (grupo PREPARAÇÃO + checklist do Trello)
| Métrica | O que é |
|---|---|
| tarefas_criadas, tarefas_concluidas, abertas, conclusao_pct | baixa vale tanto no grupo quanto no checklist do Trello |
| dias_para_concluir, idade_media_abertas_dias | |
| paradas_2_dias, paradas_7_dias | tarefas abertas há 2+ / 7+ dias |
| abertas_por_tipo, abertas_por_responsavel, carros_com_mais_pendencias | |
| problemas_relatados, problemas_por_autor | defeitos encontrados nos carros |
| sem_cartao_no_trello | tarefa que não achou o cartão do carro |

## Estoque (Autocerto + Trello)
| Métrica | O que é |
|---|---|
| autocerto.carros, valor_total, preco_medio | estoque anunciado |
| idade_media_dias, idade_mediana_dias, por_idade | dias desde o cadastro no Autocerto |
| valor_parado_mais_90_dias, mais_parados | dinheiro parado |
| por_faixa_preco, por_marca, entraram_no_periodo | |
| trello_por_lista | carros em cada etapa agora |
| dias_por_etapa | mediana/média de dias em Estoque, Trânsito, Pintura, Mecânico, Preparação/Foto, Vendido→entregar, Recall (saídas no período, histórico do Trello) |
| movimentos_no_periodo | entradas em cada lista no período |
| no_autocerto_sem_cartao, cartoes_sem_placa | qualidade do cadastro |

## Avaliações (grupo AVALIAÇÕES)
| Métrica | O que é |
|---|---|
| avaliacoes, com_valor, com_valor_pct | |
| minutos_ate_valor, valor_em_ate_5min_pct | velocidade do gestor em dar o valor |
| valor_medio_pct_fipe | valor dado ÷ FIPE |
| fechou_negocio, conversao_pct | avaliação cujo carro entrou numa venda |
| por_vendedor, valor_dado_por, por_marca, cliente_informou_interesse_pct | |

## Recall (grupo RECALL)
| Métrica | O que é |
|---|---|
| novos, abertos, resolvidos, abertos_por_status | |
| dias_para_resolver | mediana entre abrir e ficar pronto |
| dentro_da_garantia_pct | aberto até 90 dias da saída |
| por_vendedor, motivos_mais_comuns | |

## Marketing (Meta Ads)
gasto, leads_meta, conversas, custo_por_lead_meta, leads_trafego_no_agendamento, custo_por_agendamento, vendas_trafego, custo_por_venda.

## Comunicação (todas as conversas arquivadas)
mensagens, por_conversa, quem_mais_fala, por_tipo, por_hora, por_dia_semana, gerencia (volume e quem fala),
conversas_privadas (Felipe, Igor, Caio), tempo_resposta_mediano_min (Diogo × contato, por conversa).

## Equipe (placar por vendedor)
vendas, faturamento, ticket_medio, leads, conversao_leads_pct, retorno_mediano_min, leads_sem_retorno,
avaliacoes, avaliacoes_fechadas, entregas, recalls.

## Funil
leads → visitas que vieram → vendas → entregas.

## Ainda não medido (depende de próximos grupos ou dados)
- Margem por carro: o Autocerto não informa custo.
- Gerência e conversas privadas: só volume e tempo de resposta; decisões e combinados entram quando o grupo for tratado.
- Metas: existem no banco (`definir_meta`), mas precisam ser cadastradas pelo dono.
