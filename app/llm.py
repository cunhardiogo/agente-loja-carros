import io

import httpx
from openai import OpenAI

from . import datas
from .config import settings
from .schemas import Extracao

_http = httpx.Client(verify=settings.verify_ssl, timeout=60)
_client = OpenAI(api_key=settings.openai_api_key, http_client=_http) if settings.openai_api_key else None

SYSTEM = """Você lê mensagens dos grupos de WhatsApp de uma loja de carros (Grupo SB) e extrai \
eventos de negócio de forma estruturada. As mensagens podem ser conversa livre OU formulários padronizados \
(com emojis e campos rotulados). Preencha o máximo de campos que a mensagem fornecer.

Tipos de evento:
- venda: "Resumo de Venda". Capture vendedor, cliente (nome/CPF/email/telefone/endereço/CEP), veículo \
(marca, modelo, versao, ano, cor, km, placa, em_estoque), datas (data_evento=data da venda, data_entrega), \
valores (tabela_preco, valor=valor vendido, desconto, over_valor, retorno, valor_total), \
pagamento (banco, valor_financiado, valor_pix, valor_avista, forma_pagamento), \
troca (troca_modelo, troca_placa, troca_valor), ipva (cliente/loja), beneficios, portal_venda (ex Webmotors).
- avaliacao: formulário "Avaliações" de um carro (pra troca/compra). Capture loja, modelo, combustivel, ano, km, placa, \
checklist (ar_condicionado, gelando, buzina, limpador, luz_painel, chave_reserva, revisado = true/false a partir de (X)Sim/(X)Não), \
revisao, pecas_qtd, pecas_obs, pneus, obs, fipe, valor_avaliacao. O campo "Modelo" é o carro avaliado (do cliente). O campo "Troca:" é o carro que o cliente QUER na troca → carro_interesse (ex 'kicks 2017').
- entrega_agendada: item da lista do grupo de ENTREGAS. Capture loja, data_entrega, horario, vendedor, \
veiculo_texto (ex 'ASX 2015 KPY-6D44'), placa, observacao. Se a mensagem tiver VÁRIAS entregas, extraia só a primeira \
(o sistema processa uma por vez).
- anuncio: carro novo entrando no estoque/anunciado (grupo de fotos). Capture marca, modelo, versao, ano, cor, km, valor (preço), placa.
- pagamento: avisa que uma venda foi paga. Capture cliente_nome (e status_pagamento).
- entrega: avisa que um carro JÁ foi entregue ao cliente. Capture cliente_nome.
- comparecimento: avisa se cliente compareceu/faltou. Capture cliente_nome e compareceu (true/false).
- nenhum: bate-papo, bom dia, figurinha, sem evento de negócio.

Regras:
- Sem evento claro → tipo_evento="nenhum", confianca alta.
- confianca = sua certeza (0 a 1). Dúvida = menor.
- Datas relativas → ISO YYYY-MM-DD usando a data de hoje. Datas dd/mm/aaaa → ISO.
- Valores numéricos em reais, sem "R$" nem pontos de milhar (ex 76900). Exceção: valor_pix pode ser texto.
- valor_entrada = SOMA de TODOS os valores de pix/sinal/entrada já pagos. Some todas as parcelas do Pix, EXCETO as explicitamente marcadas como "será depositado/a depositar/restante/devolvido". NÃO inclua financiamento nem valor da troca. Se "Banco: A vista", valor_entrada = valor total. Exemplos: "A vista — Pix: 10.000 + 66.900" → 76900; "Pix: 1.000 sinal + 70.900" → 71900; "Pix: 3.000 Sinal + 55.900 será depositado" → 3000; "Pix: 1.000 Sinal será devolvido na troca" → 0.
- Checkboxes "(X) Sim ( ) Não" → true; "( ) Sim (X) Não" → false.
- Nos formulários, copie os campos LITERALMENTE: 'Modelo:' → modelo e 'Versão:' → versao exatamente como escritos. NÃO reinterprete nem mova valores entre marca/modelo/versao (ex.: 'Modelo: Audi' / 'Versão: A3' → modelo='Audi', versao='A3').
- Nomes exatamente como aparecem; a resolução com o cadastro é feita depois.
- Responda SOMENTE com o objeto estruturado."""


def extrair(mensagem: str, grupo_nome: str, grupo_tipo: str | None, vendedores: list[dict]) -> Extracao:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY não configurada")

    nomes = ", ".join(
        f"{v['nome']} ({v['funcao']})" + (f" apelidos: {v['apelidos']}" if v.get("apelidos") else "")
        for v in vendedores
    )
    contexto = (
        f"Data de hoje: {datas.hoje_iso()}\n"
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


def transcrever_audio(audio_bytes: bytes, mimetype: str | None) -> str:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY não configurada")
    ext = "ogg" if "ogg" in (mimetype or "") else ("mp3" if "mp" in (mimetype or "") else "ogg")
    f = io.BytesIO(audio_bytes)
    f.name = f"audio.{ext}"
    r = _client.audio.transcriptions.create(model="whisper-1", file=f, language="pt")
    return (r.text or "").strip()


VISAO_SYSTEM = """Você lê imagens enviadas em grupos de uma loja de carros.
Transcreva TODO texto e números visíveis (documentos, prints de proposta, tabelas, placas, anúncios com preço).
Se for foto de um veículo, descreva marca/modelo/cor quando der pra identificar.
Responda apenas com o conteúdo lido, sem comentários seus."""


def ler_imagem(image_b64: str, mimetype: str | None) -> str:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY não configurada")
    data_uri = f"data:{mimetype or 'image/jpeg'};base64,{image_b64}"
    resp = _client.chat.completions.create(
        model=settings.openai_model_consulta,
        messages=[
            {"role": "system", "content": VISAO_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Leia esta imagem:"},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]},
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()
