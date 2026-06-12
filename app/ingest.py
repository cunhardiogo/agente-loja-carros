import hashlib
import re
from datetime import timedelta

from . import datas, db, evolution, llm
from .config import settings
from .schemas import Extracao, TipoEvento


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


_PIX_PENDENTE = ("deposit", "restante", "devolv", "a receber", "falta")


def parse_pix(texto: str | None) -> float:
    """Soma o que JÁ foi pago no campo Pix (ignora parcelas marcadas como pendentes)."""
    if not texto:
        return 0.0
    total = 0.0
    for seg in str(texto).split("+"):
        if any(p in seg.lower() for p in _PIX_PENDENTE):
            continue
        m = re.search(r"[\d.,]+", seg)
        if not m:
            continue
        try:
            total += float(m.group().replace(".", "").replace(",", "."))
        except ValueError:
            pass
    return total


def grupo_por_jid(jid: str) -> dict | None:
    rows = db.select("grupos", {"jid": f"eq.{jid}", "ativo": "eq.true", "limit": "1"})
    return rows[0] if rows else None


def resolver_pessoa(nome: str | None, funcao: str | None = None) -> dict | None:
    if not nome:
        return None
    n = _norm(nome)
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    if funcao:
        vendedores = [v for v in vendedores if v["funcao"] == funcao] or vendedores
    for v in vendedores:
        apel = [_norm(a) for a in (v.get("apelidos") or [])]
        if _norm(v["nome"]) == n or n in apel:
            return v
    for v in vendedores:
        if n and (n in _norm(v["nome"]) or _norm(v["nome"]) in n):
            return v
    return None


def ja_processada(message_id: str | None) -> bool:
    if not message_id:
        return False
    return bool(db.select("eventos_brutos", {"message_id": f"eq.{message_id}", "limit": "1"}))


def _veiculo_casa(ext: Extracao, r: dict) -> bool:
    """True se o veículo da extração bate com o da venda r (placa ou tokens de modelo/versão)."""
    if ext.placa and r.get("placa") and \
            _norm(ext.placa).replace("-", "") == _norm(r.get("placa")).replace("-", ""):
        return True
    termos = " ".join(t for t in (ext.veiculo_descricao, ext.modelo, ext.versao) if t)
    toks = [_norm(x).replace("-", "") for x in termos.split() if len(x) >= 3 or any(c.isdigit() for c in x)]
    alvo = _norm(f"{r.get('modelo', '')} {r.get('versao', '')}").replace("-", "")
    return any(tok and tok in alvo for tok in toks)


def _venda_existente(ext: Extracao) -> dict | None:
    """Venda já registrada do MESMO cliente E mesmo veículo nas últimas 48h.
    Diferente de antes (só cliente): exige bater o veículo, evitando descartar
    o cliente que compra dois carros. Sem info de veículo, cai pro cliente."""
    if not ext.cliente_nome:
        return None
    desde = (datas.agora() - timedelta(hours=48)).isoformat()
    rows = db.select("vendas", {
        "select": "id,cliente_nome,modelo,versao,placa", "cliente_nome": db.ilike(ext.cliente_nome),
        "created_at": f"gte.{desde}", "order": "created_at.desc",
    })
    for r in rows:
        if _veiculo_casa(ext, r):
            return r
    if rows and not (ext.placa or ext.modelo or ext.versao or ext.veiculo_descricao):
        return rows[0]  # cliente bate e não há veículo pra comparar → trata como possível dup
    return None


# palavras/tokens genéricos demais pra casar uma venda específica
_STOP_VEICULO = {
    "carro", "carros", "veiculo", "auto", "automatico", "manual", "flex", "completo",
    "novo", "usado", "seminovo", "zero", "cor", "preto", "branco", "prata", "cinza",
    "vermelho", "azul", "vinho", "dourado", "bege", "verde", "amarelo", "marrom", "laranja",
}
_RE_GENERICO = re.compile(r"^\d+([.,]\d+)?v?$")  # ano (2020), motorização (1.0, 2.0, 16v)


def _tokens_veiculo(ext: Extracao) -> list[str]:
    """Tokens úteis pra identificar o veículo (sem ano/motorização/cor/genéricos)."""
    termos = " ".join(t for t in (ext.veiculo_descricao, ext.modelo, ext.versao) if t)
    out = []
    for x in termos.split():
        tok = _norm(x).replace("-", "")
        if len(tok) < 3 or tok in _STOP_VEICULO or _RE_GENERICO.match(tok):
            continue
        out.append(tok)
    return out


def _venda_pendente(ext: Extracao, campo_status: str) -> dict | None:
    """Acha a venda pendente por CLIENTE ou VEÍCULO, exigindo UNICIDADE.
    Se o critério bate em mais de uma venda (ambíguo), devolve None — o chamador
    transforma em pendência e pergunta, em vez de chutar a venda errada."""
    rows = db.select_all("vendas", {
        "select": "id,cliente_nome,modelo,versao,placa", campo_status: "eq.pendente",
        "order": "created_at.desc",
    })
    if not rows:
        return None

    # 1) placa (sinal forte) — tem de ser única
    if ext.placa:
        p = _norm(ext.placa).replace("-", "")
        cand = [r for r in rows if p and p in _norm(r.get("placa")).replace("-", "")]
        if len(cand) == 1:
            return cand[0]
        if len(cand) > 1:
            return None

    # 2) cliente — único; se houver homônimos pendentes, desempata pelo veículo
    if ext.cliente_nome:
        n = _norm(ext.cliente_nome)
        cand = [r for r in rows if n and n in _norm(r.get("cliente_nome"))]
        if len(cand) == 1:
            return cand[0]
        if len(cand) > 1:
            por_veic = [r for r in cand if _veiculo_casa(ext, r)]
            return por_veic[0] if len(por_veic) == 1 else None

    # 3) tokens de veículo (sem proibidos) — única venda casada
    toks = _tokens_veiculo(ext)
    if toks:
        cand = [r for r in rows
                if any(tok in _norm(f"{r.get('modelo', '')} {r.get('versao', '')}").replace("-", "") for tok in toks)]
        if len(cand) == 1:
            return cand[0]
    return None


def _agendamento_recente(cliente: str | None) -> dict | None:
    if not cliente:
        return None
    rows = db.select("agendamentos", {
        "select": "id,cliente_nome", "cliente_nome": db.ilike(cliente),
        "order": "data_agendada.desc.nullslast", "limit": "1",
    })
    return rows[0] if rows else None


def _linkar_veiculo(ext: Extracao, venda_id: str | None) -> None:
    """Liga a venda ao veículo do estoque (p/ giro e margem) e marca o carro como vendido.
    Casa por placa; senão por tokens de modelo/versão. Silencioso se não achar."""
    if not venda_id:
        return
    cand = db.select_all("veiculos", {
        "select": "id,modelo,versao,placa,status",
        "status": "in.(a_anunciar,anunciado,reservado)"})
    if not cand:
        return
    alvo = None
    if ext.placa:
        p = _norm(ext.placa).replace("-", "")
        alvo = next((v for v in cand if p and p in _norm(v.get("placa")).replace("-", "")), None)
    if not alvo:
        toks = _tokens_veiculo(ext)
        casados = [v for v in cand
                   if any(tok in _norm(f"{v.get('modelo', '')} {v.get('versao', '')}").replace("-", "") for tok in toks)]
        if len(casados) == 1:
            alvo = casados[0]
    if not alvo:
        return
    db.update("vendas", {"veiculo_id": alvo["id"]}, {"id": f"eq.{venda_id}"})
    db.update("veiculos", {"status": "vendido"}, {"id": f"eq.{alvo['id']}"})


# ===== ações (INSERT/UPDATE). Retornam (tabela, registro_id). =====
def aplicar(ext: Extracao, forcar_venda: bool = False) -> tuple[str | None, str | None]:
    t = ext.tipo_evento

    if t == TipoEvento.venda:
        existente = None if forcar_venda else _venda_existente(ext)
        if existente:
            return "duplicada", existente["id"]
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        row = db.insert("vendas", {
            "vendedor_id": vend["id"] if vend else None,
            "cliente_nome": ext.cliente_nome,
            "marca": ext.marca, "modelo": ext.modelo, "versao": ext.versao,
            "ano": ext.ano, "cor": ext.cor, "km": ext.km, "placa": ext.placa,
            "em_estoque": ext.em_estoque,
            "valor_venda": ext.valor, "tabela_preco": ext.tabela_preco, "desconto": ext.desconto,
            "over_valor": ext.over_valor, "retorno": ext.retorno,
            "forma_pagamento": ext.forma_pagamento, "banco": ext.banco,
            "valor_entrada": (parse_pix(ext.valor_pix) or ext.valor_entrada),
            "valor_financiado": ext.valor_financiado, "valor_pix": ext.valor_pix,
            "valor_avista": ext.valor_avista, "debitos": ext.debitos, "valor_total": ext.valor_total,
            "ipva": ext.ipva, "beneficios": ext.beneficios, "portal_venda": ext.portal_venda,
            "troca_modelo": ext.troca_modelo, "troca_placa": ext.troca_placa, "troca_valor": ext.troca_valor,
            "cliente_cpf": ext.cliente_cpf, "cliente_email": ext.cliente_email,
            "cliente_telefone": ext.cliente_telefone, "cliente_endereco": ext.cliente_endereco,
            "cliente_cep": ext.cliente_cep,
            "data_venda": ext.data_evento or datas.hoje_iso(),
            "status_pagamento": ext.status_pagamento or "pendente",
            "status_entrega": ext.status_entrega or "pendente",
            "data_entrega_prevista": ext.data_entrega,
            "observacoes": ext.obs or ext.veiculo_descricao,
        })
        _linkar_veiculo(ext, row.get("id"))
        return "vendas", row.get("id")

    if t == TipoEvento.avaliacao:
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        row = db.insert("avaliacoes", {
            "loja": ext.loja, "modelo": ext.modelo or ext.carro_troca, "versao": ext.versao,
            "combustivel": ext.combustivel, "ano": ext.ano, "km": ext.km, "placa": ext.placa,
            "ar_condicionado": ext.ar_condicionado, "gelando": ext.gelando, "buzina": ext.buzina,
            "limpador": ext.limpador, "luz_painel": ext.luz_painel, "chave_reserva": ext.chave_reserva,
            "revisado": ext.revisado, "revisao": ext.revisao, "pecas_qtd": ext.pecas_qtd,
            "pecas_obs": ext.pecas_obs, "pneus": ext.pneus, "obs": ext.obs,
            "fipe": ext.fipe, "valor_avaliacao": ext.valor_avaliacao or ext.valor,
            "valor_pretendido": ext.valor_pretendido,
            "carro_troca": ext.carro_troca, "carro_interesse": ext.carro_interesse,
            "vendedor_id": vend["id"] if vend else None,
        })
        return "avaliacoes", row.get("id")

    if t == TipoEvento.entrega_agendada:
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        veic = ext.veiculo_texto or ext.veiculo_descricao or ext.modelo
        ref = ("ent_" + hashlib.md5(f"{veic}|{ext.data_entrega}".lower().encode()).hexdigest()[:16]) if veic else None
        registro = {
            "loja": ext.loja, "data_entrega": ext.data_entrega, "horario": ext.horario,
            "vendedor_id": vend["id"] if vend else None, "veiculo": veic, "placa": ext.placa,
            "observacao": ext.observacao or ext.obs, "ref_externa": ref,
        }
        existe = db.select("entregas", {"select": "id", "ref_externa": f"eq.{ref}", "limit": "1"}) if ref else []
        if existe:
            db.update("entregas", registro, {"id": f"eq.{existe[0]['id']}"})
            return "entregas", existe[0]["id"]
        row = db.insert("entregas", registro)
        return "entregas", row.get("id")

    if t == TipoEvento.agendamento:
        sdr = resolver_pessoa(ext.sdr_nome, "sdr")
        vend = resolver_pessoa(ext.vendedor_nome, "vendedor")
        row = db.insert("agendamentos", {
            "cliente_nome": ext.cliente_nome,
            "sdr_id": sdr["id"] if sdr else None,
            "vendedor_id": vend["id"] if vend else None,
            "data_agendada": ext.data_agendada,
            "observacoes": ext.resumo,
        })
        return "agendamentos", row.get("id")

    if t == TipoEvento.anuncio:
        row = db.insert("veiculos", {
            "marca": ext.marca, "modelo": ext.modelo, "versao": ext.versao, "ano": ext.ano,
            "cor": ext.cor, "km": ext.km, "placa": ext.placa,
            "preco_anuncio": ext.valor, "status": "a_anunciar",
            "data_anuncio": ext.data_evento or datas.hoje_iso(),
            "observacoes": ext.veiculo_descricao or ext.resumo,
        })
        return "veiculos", row.get("id")

    if t == TipoEvento.anuncio_publicado:
        alvo = ext.modelo or ext.veiculo_descricao or ext.versao
        if not alvo:
            return None, None
        rows = db.select("veiculos", {"select": "id", "status": "eq.a_anunciar",
                                      "modelo": db.ilike(alvo), "order": "created_at.desc", "limit": "1"})
        if not rows:
            return None, None
        db.update("veiculos", {"status": "anunciado"}, {"id": f"eq.{rows[0]['id']}"})
        return "veiculos", rows[0]["id"]

    if t == TipoEvento.pagamento:
        v = _venda_pendente(ext, "status_pagamento")
        if not v:
            return None, None
        db.update("vendas", {"status_pagamento": ext.status_pagamento or "pago"}, {"id": f"eq.{v['id']}"})
        return "vendas", v["id"]

    if t == TipoEvento.entrega:
        v = _venda_pendente(ext, "status_entrega")
        if not v:
            return None, None
        dados = {"status_entrega": "entregue", "data_entrega_real": datas.hoje_iso()}
        det = db.select("vendas", {"select": "valor_total,valor_venda,valor_entrada",
                                   "id": f"eq.{v['id']}", "limit": "1"})
        d0 = det[0] if det else {}
        saldo = (d0.get("valor_total") or d0.get("valor_venda") or 0) - (d0.get("valor_entrada") or 0)
        if saldo <= 0:  # só quita automático se não há saldo (financiamento pode não ter caído)
            dados["status_pagamento"] = "pago"
        db.update("vendas", dados, {"id": f"eq.{v['id']}"})
        return "vendas", v["id"]

    if t == TipoEvento.comparecimento:
        a = _agendamento_recente(ext.cliente_nome)
        if not a:
            return None, None
        compareceu = ext.compareceu if ext.compareceu is not None else True
        db.update("agendamentos", {"compareceu": compareceu}, {"id": f"eq.{a['id']}"})
        return "agendamentos", a["id"]

    if t == TipoEvento.recall:
        veic = ext.veiculo_texto or ext.veiculo_descricao or \
            " ".join(x for x in (ext.modelo, ext.versao) if x) or None
        row = db.insert("recalls", {
            "cliente_nome": ext.cliente_nome, "veiculo": veic, "placa": ext.placa,
            "motivo": ext.motivo or ext.obs or ext.resumo, "observacao": ext.observacao,
        })
        return "recalls", row.get("id")

    return None, None


def reextrair(mensagem_original: str, correcao: str) -> Extracao:
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    texto = f"Mensagem original: {mensagem_original}\nCorreção informada pelo usuário: {correcao}\n" \
            f"Gere o evento já com a correção aplicada."
    return llm.extrair(texto, "correção", None, vendedores)


def codigo_pendencia(evento_id: str) -> str:
    """Código curto da pendência (4 hex do id) usado na fila de confirmação."""
    return (evento_id or "").replace("-", "")[:4].upper()


def _pergunta_duplicada(ext: Extracao, codigo: str) -> str:
    veic = ext.veiculo_descricao or " ".join(x for x in (ext.modelo, ext.versao) if x) or "veículo"
    return (f"⚠️ Já existe venda de *{ext.cliente_nome}* ({veic}) nas últimas 48h. [#{codigo}]\n"
            f"É a MESMA venda (ignorar) ou uma venda NOVA?\n\n"
            f"Responda: *{codigo} sim* (é nova, registrar) / *{codigo} não* (ignorar)")


def _pergunta_confirmacao(ext: Extracao, codigo: str | None = None) -> str:
    tag = f" [#{codigo}]" if codigo else ""
    instr = (f"Responda: *{codigo} sim* / *{codigo} não* / *{codigo} corrige ...*"
             if codigo else "Responda: *sim* / *não* / *corrigir ...*")
    return (f"❓ Confirma este registro?{tag}\n"
            f"• Tipo: {ext.tipo_evento.value}\n"
            f"• {ext.resumo or ext.veiculo_descricao or '(sem resumo)'}\n\n"
            f"{instr}")


def _split_entregas(texto: str) -> list[str]:
    """Se a mensagem tem várias entregas (lista), separa em blocos."""
    if texto.upper().count("ENTREGA") < 2:
        return [texto]
    blocos = re.split(r"\n\s*_{3,}\s*\n|(?=🎁)", texto)
    blocos = [b.strip() for b in blocos if b.strip() and "entrega" in b.lower()]
    return blocos or [texto]


def processar(grupo: dict, message_id: str | None, remetente: str | None,
              remetente_nome: str | None, texto: str, timestamp_msg: str | None) -> dict:
    blocos = _split_entregas(texto)
    if len(blocos) > 1:
        res = []
        for i, b in enumerate(blocos):
            # message_id composto: cada item da lista vira sua própria trava de idempotência
            mid = f"{message_id}#{i}" if message_id else None
            res.append(_processar_um(grupo, mid, remetente, remetente_nome, b, timestamp_msg))
        return {"multiplos": len(res), "itens": res}
    return _processar_um(grupo, message_id, remetente, remetente_nome, texto, timestamp_msg)


def _processar_um(grupo: dict, message_id: str | None, remetente: str | None,
                  remetente_nome: str | None, texto: str, timestamp_msg: str | None) -> dict:
    # LOCK antecipado: grava eventos_brutos como 'processando' ANTES de extrair/aplicar.
    # Se o message_id já existe (redelivery do Evolution), o índice único barra aqui e
    # nenhuma escrita de negócio (venda/avaliação) é duplicada.
    evento = db.insert_lock("eventos_brutos", {
        "grupo_id": grupo["id"], "message_id": message_id,
        "remetente": remetente, "remetente_nome": remetente_nome,
        "mensagem_original": texto, "timestamp_msg": timestamp_msg,
        "status": "processando",
    })
    if evento is None:
        return {"ignored": "duplicada", "message_id": message_id}
    return _executar(evento["id"], grupo, texto)


def _executar(evento_id: str, grupo: dict, texto: str) -> dict:
    """Extrai + aplica + finaliza o evento já travado (status 'processando' → final)."""
    vendedores = db.select("vendedores", {"select": "id,nome,apelidos,funcao", "ativo": "eq.true"})
    ext = llm.extrair(texto, grupo.get("nome") or "", grupo.get("tipo"), vendedores)

    tabela = registro_id = None
    status = "auto"

    codigo = codigo_pendencia(evento_id)
    if ext.tipo_evento == TipoEvento.nenhum:
        pass
    elif ext.tipo_evento == TipoEvento.agendamento and not settings.agendamento_via_grupo:
        status = "ignorado_planilha"  # agendamento é controlado pela planilha
    elif ext.confianca < settings.confianca_minima:
        status = "pendente_confirmacao"
        evolution.notificar_dono(_pergunta_confirmacao(ext, codigo))
    else:
        tabela, registro_id = aplicar(ext)
        if tabela == "duplicada":
            # nunca descarta calado: pergunta se é a mesma venda ou uma nova
            tabela, registro_id, status = None, None, "pendente_confirmacao"
            evolution.notificar_dono(_pergunta_duplicada(ext, codigo))
        elif registro_id:
            status = "auto"
        else:
            status = "pendente_confirmacao"
            evolution.notificar_dono(_pergunta_confirmacao(ext, codigo))

    db.update("eventos_brutos", {
        "tipo_evento": ext.tipo_evento.value, "dados_extraidos": ext.model_dump(mode="json"),
        "confianca": ext.confianca, "status": status,
        "registro_tabela": tabela, "registro_id": registro_id,
    }, {"id": f"eq.{evento_id}"})
    return {"evento_id": evento_id, "tipo": ext.tipo_evento.value,
            "confianca": ext.confianca, "status": status, "registro_id": registro_id}


def reprocessar(ev: dict) -> dict:
    """Retoma um evento travado em 'processando' (chamado pelo reaper). Reusa a linha
    existente — não cria nova trava. _venda_duplicada evita venda em dobro no retry."""
    g = db.select("grupos", {"select": "id,nome,tipo", "id": f"eq.{ev['grupo_id']}", "limit": "1"})
    grupo = g[0] if g else {"id": ev.get("grupo_id"), "nome": "", "tipo": None}
    return _executar(ev["id"], grupo, ev.get("mensagem_original") or "")
