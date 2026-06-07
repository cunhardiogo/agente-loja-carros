from enum import Enum

from pydantic import BaseModel, Field


class TipoEvento(str, Enum):
    venda = "venda"
    avaliacao = "avaliacao"
    agendamento = "agendamento"
    comparecimento = "comparecimento"
    anuncio = "anuncio"
    pagamento = "pagamento"
    entrega = "entrega"               # confirmação de que um carro foi entregue
    entrega_agendada = "entrega_agendada"  # item da lista do grupo de ENTREGAS
    nenhum = "nenhum"


class Extracao(BaseModel):
    """Extração estruturada de uma mensagem (texto livre ou formulário do grupo)."""

    tipo_evento: TipoEvento
    confianca: float = Field(ge=0, le=1)
    resumo: str | None = Field(None, description="Resumo curto em 1 linha")

    # pessoas
    vendedor_nome: str | None = None
    sdr_nome: str | None = None
    cliente_nome: str | None = None

    # veículo
    veiculo_descricao: str | None = None
    marca: str | None = None
    modelo: str | None = None
    versao: str | None = None
    ano: int | None = None
    cor: str | None = None
    km: int | None = None
    placa: str | None = None
    combustivel: str | None = None
    em_estoque: bool | None = None

    # datas
    data_evento: str | None = Field(None, description="Data do evento/venda em ISO YYYY-MM-DD")
    data_agendada: str | None = None
    data_entrega: str | None = Field(None, description="Data de entrega em ISO")
    horario: str | None = None
    compareceu: bool | None = None

    # valores / pagamento (venda)
    valor: float | None = Field(None, description="Valor principal/vendido em reais")
    tabela_preco: float | None = None
    desconto: float | None = None
    over_valor: str | None = None
    retorno: str | None = None
    forma_pagamento: str | None = None
    banco: str | None = None
    valor_entrada: float | None = Field(None, description="Soma do que o cliente JÁ pagou agora (à vista, sinal, entrada, pix já feito). NÃO incluir 'será depositado/a depositar/restante', financiamento nem troca.")
    valor_financiado: float | None = None
    valor_pix: str | None = Field(None, description="Pode ser texto, ex '10.000 + 66.900'")
    valor_avista: float | None = None
    debitos: float | None = None
    valor_total: float | None = None
    ipva: str | None = Field(None, description="Quem paga o IPVA: cliente ou loja")
    beneficios: str | None = None
    portal_venda: str | None = Field(None, description="Canal/portal da venda, ex Webmotors")
    status_pagamento: str | None = None
    status_entrega: str | None = None

    # troca (carro dado na troca, na venda)
    troca_modelo: str | None = None
    troca_placa: str | None = None
    troca_valor: float | None = None

    # cliente (dados cadastrais)
    cliente_cpf: str | None = None
    cliente_email: str | None = None
    cliente_telefone: str | None = None
    cliente_endereco: str | None = None
    cliente_cep: str | None = None

    # avaliação (formulário de avaliação de carro pra troca/compra)
    loja: str | None = None
    fipe: float | None = None
    valor_avaliacao: float | None = Field(None, description="Valor que a loja avaliou o carro do cliente")
    valor_pretendido: float | None = Field(None, description="Valor que o cliente pensa/pede pelo carro dele, se mencionado")
    carro_troca: str | None = Field(None, description="Carro avaliado/da troca, ex 'C4 Lounge 2014'")
    carro_interesse: str | None = Field(None, description="Na avaliação, o carro que o cliente quer na troca (campo 'Troca:'), ex 'kicks 2017'")
    ar_condicionado: bool | None = None
    gelando: bool | None = None
    buzina: bool | None = None
    limpador: bool | None = None
    luz_painel: bool | None = None
    chave_reserva: bool | None = None
    revisado: bool | None = None
    revisao: str | None = None
    pecas_qtd: int | None = None
    pecas_obs: str | None = None
    pneus: str | None = None
    obs: str | None = None

    # entrega agendada (lista do grupo de entregas)
    veiculo_texto: str | None = Field(None, description="Veículo como escrito, ex 'ASX 2015 KPY-6D44'")
    observacao: str | None = None
