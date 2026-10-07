import io

import httpx
from openai import OpenAI

from . import datas
from .config import settings
from .schemas import Extracao

_http = httpx.Client(verify=settings.verify_ssl, timeout=60)
_kwargs = {"api_key": settings.openai_api_key, "http_client": _http}
if settings.openai_base_url:  # LLM plugável: endpoint compatível-OpenAI
    _kwargs["base_url"] = settings.openai_base_url
_client = OpenAI(**_kwargs) if settings.openai_api_key else None

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
revisao, pecas_qtd, pecas_obs, pneus, obs, fipe, valor_avaliacao (valor que a loja avaliou), valor_pretendido (valor que o cliente pensa/pede pelo carro, se citado). O campo "Modelo" é o carro avaliado (do cliente) → preencha modelo (marca+modelo) e versao (motorização/versão, ex 'crv lx 2.0' → modelo 'CRV', versao 'LX 2.0'). O campo "Troca:" é o carro que o cliente QUER na troca → carro_interesse (ex 'kicks 2017').
- entrega_agendada: SÓ a lista do grupo de ENTREGAS (começa com "🎁 ENTREGAS"), agendando a ENTREGA de um carro JÁ VENDIDO. Capture loja, data_entrega, horario, vendedor, \
veiculo_texto (ex 'ASX 2015 KPY-6D44'), placa, observacao. Se tiver VÁRIAS, extraia só a primeira.
- agendamento: marcar uma VISITA/test drive de um cliente (ex começa com "*AGENDAMENTO*", ou "marquei o cliente X pra sexta"). NÃO confunda com entrega_agendada. (Agendamento vem da planilha, então só classifique — não precisa de todos os campos.)
- anuncio: carro novo CHEGANDO no estoque (grupo de fotos), ainda a anunciar. Capture marca, modelo, versao, ano, cor, km, valor (preço), placa.
- anuncio_publicado: avisa que um carro JÁ foi anunciado/publicado (ex 'anunciei o Corolla', 'subi o anúncio do 208'). Capture modelo/veiculo_descricao pra localizar.
- pagamento: avisa que uma venda foi paga (ex 'pagamento do HRV', 'Onix quitado'). Capture cliente_nome E o veículo (modelo/veiculo_descricao/placa) pra localizar a venda.
- entrega: avisa que um carro JÁ foi entregue (ex 'Onix entregue', 'entreguei o do João'). Capture cliente_nome E o veículo (modelo/veiculo_descricao/placa).
- comparecimento: avisa se um cliente compareceu ou faltou na visita (ex 'Não veio', 'Veio mas não fechou', 'compareceu'). compareceu=false se NÃO veio/faltou; true se veio/compareceu. Capture cliente_nome — se a mensagem for resposta a outra (contexto '[Em resposta a: ...]'), pegue o cliente/carro dali.
- recall: SÓ no grupo RECALL — cliente chamado pra revisão/retorno/garantia, ou retorno pós-venda (ex 'chamar o cliente do Onix pra revisão', 'recall do airbag', 'cliente voltou reclamando de barulho'). Capture cliente_nome, veículo (modelo/placa/veiculo_texto) e motivo.
- nenhum: bate-papo, instruções operacionais, bom dia, figurinha, sem evento de negócio.

Regras:
- Sem evento claro → tipo_evento="nenhum", confianca alta.
- confianca = sua certeza (0 a 1). Dúvida = menor.
- Datas relativas → ISO YYYY-MM-DD usando a data de hoje. Datas dd/mm/aaaa → ISO.
- Valores numéricos em reais, sem "R$" nem pontos de milhar (ex 76900). Exceção: valor_pix pode ser texto.
- valor_entrada = SOMA de TODOS os valores de pix/sinal/entrada já pagos. Some todas as parcelas do Pix, EXCETO as explicitamente marcadas como "será depositado/a depositar/restante/devolvido". NÃO inclua financiamento nem valor da troca. Se "Banco: A vista", valor_entrada = valor total. Exemplos: "A vista — Pix: 10.000 + 66.900" → 76900; "Pix: 1.000 sinal + 70.900" → 71900; "Pix: 3.000 Sinal + 55.900 será depositado" → 3000; "Pix: 1.000 Sinal será devolvido na troca" → 0.
- Checkboxes "(X) Sim ( ) Não" → true; "( ) Sim (X) Não" → false.
- Nos formulários, copie os campos LITERALMENTE: 'Modelo:' → modelo e 'Versão:' → versao exatamente como escritos. NÃO reinterprete nem mova valores entre marca/modelo/versao (ex.: 'Modelo: Audi' / 'Versão: A3' → modelo='Audi', versao='A3').
- Grupo tipo "conversa" = conversa PRIVADA do dono com um sócio/gerente: extraia os mesmos eventos quando a mensagem afirmar um fato (venda feita, pagamento, entrega, carro chegando); pergunta, combinação, opinião ou papo → nenhum.
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
        model=settings.openai_model_extracao,  # mini: visão é frequente (grupo de fotos) e o caro não compensa
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


NOTA_LEAD_SYSTEM = """Você lê a observação que o SDR escreve sobre um lead de loja de carros.
Responda SOMENTE um JSON com:
- "troca": o carro que o cliente quer dar na troca (modelo/ano/km como escrito), ou null
- "oferta_entrada": oferta de valor, entrada ou condição de pagamento citada (ex "ofereceu 100 mil à vista", "8 mil de entrada", "sem entrada, score bom"), ou null"""


def extrair_nota_lead(observacao: str) -> dict:
    if _client is None:
        return {}
    import json
    resp = _client.chat.completions.create(
        model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": NOTA_LEAD_SYSTEM}, {"role": "user", "content": observacao}])
    d = json.loads(resp.choices[0].message.content or "{}")
    return {k: (str(d[k])[:300] if d.get(k) else None) for k in ("troca", "oferta_entrada")}


DOC_SYSTEM = """Você classifica um documento enviado no grupo de vendas de uma loja de carros.
Responda SOMENTE um JSON com:
- "tipo": "comprovante_pagamento", "cnh", "documento_identidade", "comprovante_residencia", "contrato", "foto_carro" ou "outro"
  · comprovante_pagamento = comprovante de uma transação JÁ REALIZADA (Pix enviado, TED/transferência efetuada, pagamento concluído, recibo). Tem status como "realizado", "efetuado", "concluído", "enviado".
  · conta, fatura ou boleto A PAGAR (luz, água, gás, telefone, internet, cartão — com vencimento, código de barras ou QR Code para pagar) é "comprovante_residencia", NUNCA comprovante_pagamento.
Se e SOMENTE se for comprovante_pagamento, inclua também:
- "valor": número em reais (ex 1500.00)
- "pago_em": data e hora do pagamento EXATAMENTE como aparecem no comprovante (horário de Brasília, sem converter fuso), em ISO "YYYY-MM-DDTHH:MM:SS" sem "Z" (só a data se não houver hora)
- "id_transacao": o identificador único da transação (ID/E2E do Pix começando com E, nº de autenticação ou de controle), exatamente como escrito, ou null
- "forma": "pix", "ted", "cartao", "boleto" ou "outro"
- "banco": banco/instituição do comprovante
- "pagador": nome de quem pagou
- "recebedor": nome de quem recebeu
Para os demais tipos NÃO transcreva nenhum dado pessoal: devolva só o "tipo"."""


def ler_documento(texto: str | None = None, image_b64: str | None = None, mimetype: str | None = None,
                  nome_arquivo: str | None = None) -> dict:
    if _client is None:
        return {}
    import json
    conteudo: list = [{"type": "text", "text": f"Arquivo: {nome_arquivo or '-'}\n\n{(texto or '')[:6000]}"}]
    if image_b64:
        conteudo.append({"type": "image_url", "image_url": {"url": f"data:{mimetype or 'image/jpeg'};base64,{image_b64}"}})
    resp = _client.chat.completions.create(
        model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": DOC_SYSTEM}, {"role": "user", "content": conteudo}])
    return json.loads(resp.choices[0].message.content or "{}")
