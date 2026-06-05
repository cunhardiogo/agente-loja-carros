from datetime import date

import httpx
from openai import OpenAI

from .config import settings
from .schemas import Extracao

_http = httpx.Client(verify=settings.verify_ssl, timeout=60)
_client = OpenAI(api_key=settings.openai_api_key, http_client=_http) if settings.openai_api_key else None

SYSTEM = """Você é um assistente que lê mensagens de grupos de WhatsApp de uma loja de carros (Grupo SB) \
e extrai eventos de negócio de forma estruturada. As mensagens são conversa livre, informais.

Eventos possíveis:
- venda: alguém fechou/vendeu um carro. Capture vendedor, cliente, veículo, valor, forma de pagamento.
- agendamento: SDR marcou uma visita/test drive. Capture cliente, sdr, vendedor, veículo, data/hora.
- comparecimento: informa se um cliente agendado compareceu ou faltou. Defina compareceu true/false.
- anuncio: carro novo colocado à venda/anunciado. Capture marca, modelo, ano, cor, valor.
- pagamento: informa que uma venda foi paga (status_pagamento).
- entrega: informa que um carro foi entregue ao cliente (status_entrega).
- nenhum: bate-papo, bom dia, figurinha, áudio, qualquer coisa sem evento de negócio.

Regras:
- Se a mensagem não contém um evento claro, use tipo_evento="nenhum" e confianca alta.
- confianca reflete sua certeza (0 a 1). Seja conservador: dúvida = confianca menor.
- Resolva datas relativas ("hoje", "amanhã", "sexta") para ISO YYYY-MM-DD usando a data de hoje informada.
- valor sempre em número (reais), sem "R$" nem pontos de milhar.
- Nomes: use exatamente como aparecem; a resolução com o cadastro é feita depois.
- Responda SOMENTE com o objeto estruturado."""


def extrair(mensagem: str, grupo_nome: str, grupo_tipo: str | None, vendedores: list[dict]) -> Extracao:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY não configurada")

    nomes = ", ".join(
        f"{v['nome']} ({v['funcao']})" + (f" apelidos: {v['apelidos']}" if v.get("apelidos") else "")
        for v in vendedores
    )
    contexto = (
        f"Data de hoje: {date.today().isoformat()}\n"
        f"Grupo: {grupo_nome} (tipo: {grupo_tipo})\n"
        f"Equipe conhecida: {nomes}\n\n"
        f"Mensagem:\n{mensagem}"
    )
    resp = _client.beta.chat.completions.parse(
        model=settings.openai_model_extracao,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": contexto}],
        response_format=Extracao,
        temperature=0,
    )
    return resp.choices[0].message.parsed
