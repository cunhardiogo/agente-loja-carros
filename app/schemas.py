from enum import Enum

from pydantic import BaseModel, Field


class TipoEvento(str, Enum):
    venda = "venda"
    agendamento = "agendamento"
    comparecimento = "comparecimento"
    anuncio = "anuncio"
    pagamento = "pagamento"
    entrega = "entrega"
    nenhum = "nenhum"


class Extracao(BaseModel):
    """Resultado estruturado da IA pra uma mensagem de grupo."""

    tipo_evento: TipoEvento
    confianca: float = Field(ge=0, le=1, description="Certeza de 0 a 1 da classificação/extração")
    resumo: str | None = Field(None, description="Resumo curto do evento em 1 linha")

    vendedor_nome: str | None = None
    sdr_nome: str | None = None
    cliente_nome: str | None = None

    veiculo_descricao: str | None = Field(None, description="Como o carro foi citado, ex: 'Corolla branco 2021'")
    marca: str | None = None
    modelo: str | None = None
    ano: int | None = None
    cor: str | None = None

    valor: float | None = Field(None, description="Valor em reais, só número")
    forma_pagamento: str | None = None

    data_evento: str | None = Field(None, description="Data do evento em ISO YYYY-MM-DD")
    data_agendada: str | None = Field(None, description="Data/hora do agendamento em ISO")
    compareceu: bool | None = None

    status_pagamento: str | None = Field(None, description="pendente|parcial|pago")
    status_entrega: str | None = Field(None, description="pendente|entregue")
