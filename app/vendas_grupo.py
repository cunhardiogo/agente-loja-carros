"""Grupo VENDAS: o aviso curto ("Fastback TTD-9E46 vendido") cria a venda, o Resumo de Venda completa,
reserva vira etapa, comprovantes de pagamento são lidos (valor, data, id da transação) e os demais
documentos só são marcados como recebidos. Resumo atrasado, incompleto ou reserva parada geram
cobrança aprovada pelo dono."""
import base64
import io
import logging
import re
from datetime import datetime, timedelta, timezone

from . import datas, db, evolution, llm
from .config import settings
from .leads import _ascii, _data, _dt, _iso, _quando, _stanza, _telefone, _texto

log = logging.getLogger("agente")

CONTEXTO_MIN = 60          # documento sem citação vale para o último carro citado pelo mesmo autor
VINCULO_ANTES_MIN = 10     # anexo mandado até 10 min ANTES do texto da venda também é dela
RECEBEDORES_LOJA = ("soberano", "brutus", "grupo sb", "sb veiculos")  # comprovante só conta se a loja recebeu
RESERVA_DIAS = 2
CORTE_DIA = 19             # aviso depois disso (ou prazo passando disso) → cobra 9h do dia seguinte
PRAZO_RESUMO_H = 3
CARENCIA_PENDENCIA_MIN = 15

STATUS_LABEL = {"aguardando_resumo": "Aguardando resumo", "completa": "Completa",
                "reservado": "Reservado", "desistiu": "Caiu"}
OPCIONAIS = {"retorno", "over", "desconto", "troca_carro", "troca_placa", "troca_valor", "obs"}
CAMPO_LABEL = {
    "data_venda": "data da venda", "data_entrega": "data da entrega", "vendedor": "vendedor",
    "modelo": "modelo", "versao": "versão", "ano": "ano", "cor": "cor", "km": "km", "estoque": "estoque",
    "placa": "placa", "tabela": "tabela", "valor_vendido": "valor vendido", "pagamento": "banco/financiado/pix",
    "total": "total", "ipva": "IPVA", "nome": "nome do cliente", "cpf": "CPF", "email": "e-mail",
    "tel": "telefone", "endereco": "endereço", "cep": "CEP", "portal": "portal da venda",
}

sujo = {"v": True}

_PLACA = re.compile(r"\b([A-Za-z]{3})-?(\d[A-Za-z0-9]\d{2})\b")
_ROTULOS = [  # (rótulo ascii no início da linha, campo) — ordem importa: mais específicos primeiro
    ("data da venda", "data_venda"), ("data da entrega", "data_entrega"), ("vendedor", "vendedor"),
    ("modelo", "modelo"), ("versao", "versao"), ("ano", "ano"), ("cor", "cor"), ("km", "km"),
    ("estoque", "estoque"), ("placa", "placa"), ("tabela", "tabela"), ("valor vendido", "valor_vendido"),
    ("vendido", "valor_vendido"), ("desconto", "desconto"), ("over", "over"), ("retorno", "retorno"),
    ("banco", "banco"), ("financiado", "financiado"), ("pix", "pix"), ("carro", "troca_carro"),
    ("valor troca", "troca_valor"), ("total", "total"), ("ipva", "ipva"), ("obs", "obs"), ("nome", "nome"),
    ("cpf", "cpf"), ("email", "email"), ("tel", "tel"), ("endereco", "endereco"), ("cep", "cep"),
    ("portal da venda", "portal"),
]


# ===== parsing =====
def placa_norm(texto: str | None) -> str | None:
    m = _PLACA.search(texto or "")
    return (m.group(1) + m.group(2)).upper() if m else None


def eh_resumo(texto: str) -> bool:
    return _ascii(texto).lstrip(" *_\n").startswith("resumo de venda")


def _valor(txt: str | None) -> float | None:
    """'R$69.900,00', 'R$136.900', '50.000,00.' e também o formato americano 'R$65,159.30'.
    O último separador seguido de exatamente 2 dígitos é o decimal; os demais são milhar."""
    m = re.search(r"\d[\d.,]*", (txt or "").replace(" ", ""))
    if not m:
        return None
    s = m.group().rstrip(".,")
    inteiro, frac = (s[:-3], s[-2:]) if re.search(r"[.,]\d{2}$", s) else (s, "0")
    try:
        return float(re.sub(r"[.,]", "", inteiro) + "." + frac)
    except ValueError:
        return None


def _marcado(txt: str | None, opcao: str) -> bool:
    return bool(re.search(r"\(\s*x\s*\)\s*" + opcao, _ascii(txt)))


def parse_resumo(texto: str) -> dict:
    """Resumo de Venda → campos crus (strings). Campos colados na mesma linha (separados por muitos
    espaços) são separados antes."""
    corpo = re.sub(r" {3,}", "\n", texto)
    campos: dict[str, str] = {}
    atual = None
    viu_carro_troca = False
    for linha in corpo.splitlines():
        limpa = linha.strip().strip("*")
        a = re.sub(r"^[^a-z0-9]+", "", _ascii(limpa))
        rot = next(((r, c) for r, c in _ROTULOS if re.match(re.escape(r) + r"[^:;]{0,12}[:;]", a)), None)
        if rot:
            r, campo = rot
            if campo == "troca_carro":
                viu_carro_troca = True
            if campo == "placa" and viu_carro_troca:
                campo = "troca_placa"
            if campo == "valor_vendido" and r == "vendido" and "valor_vendido" in campos:
                continue
            valor = re.split(r"[:;]", limpa, maxsplit=1)[1].strip() if re.search(r"[:;]", limpa) else ""
            if campo not in campos or not campos[campo]:
                campos[campo] = valor
            atual = campo
        elif atual == "obs" and limpa and not a.startswith("anexar"):
            campos["obs"] = (campos.get("obs", "") + "\n" + limpa).strip()
    return campos


def _data_iso(valor: str | None, ref) -> str | None:
    if not valor or re.search(r"\d{1,2}//", valor):
        return None
    d = _data(valor, ref)
    return d.isoformat() if d else None


def pendencias_venda(v: dict) -> list[str]:
    """Campos obrigatórios do Resumo de Venda que estão vazios (só retorno, over, desconto e os dados
    da troca podem faltar)."""
    entrega = _ascii(v.get("data_entrega_texto") or "")
    ok = {
        "data_venda": bool(v.get("data_venda")),
        "data_entrega": bool(v.get("data_entrega_prevista")) or (bool(entrega.strip())
                                                                   and not re.search(r"xx|sem data|//", entrega)),
        "vendedor": bool(v.get("vendedor_id")), "modelo": bool(v.get("modelo")), "versao": bool(v.get("versao")),
        "ano": bool(v.get("ano")), "cor": bool(v.get("cor")), "km": bool(v.get("km")),
        "estoque": v.get("em_estoque") is not None, "placa": bool(v.get("placa")),
        "tabela": v.get("tabela_preco") is not None, "valor_vendido": v.get("valor_venda") is not None,
        "pagamento": any(v.get(k) for k in ("banco", "valor_financiado", "valor_pix")),
        "total": v.get("valor_total") is not None, "ipva": bool(v.get("ipva")),
        "nome": bool(v.get("cliente_nome")), "cpf": bool(v.get("cliente_cpf")), "email": bool(v.get("cliente_email")),
        "tel": bool(v.get("cliente_telefone")), "endereco": bool(v.get("cliente_endereco")),
        "cep": bool(v.get("cliente_cep")), "portal": bool(v.get("portal_venda")),
    }
    falta = [CAMPO_LABEL[k] for k in CAMPO_LABEL if not ok[k]]
    total = v.get("valor_total") or v.get("valor_venda")
    if v.get("troca_valor") and total and v["troca_valor"] > total:
        falta.append("valor da troca (maior que o total)")
    return falta


def campos_venda(c: dict, ref) -> dict:
    """Campos crus do resumo → colunas da tabela vendas."""
    modelo = (c.get("modelo") or "").strip() or None
    km = _valor(c.get("km"))
    ano = re.search(r"\d{4}", c.get("ano") or "")
    tel = _telefone(c.get("tel") or "")
    return {
        "modelo": modelo, "versao": (c.get("versao") or "").strip() or None,
        "ano": int(ano.group()) if ano else None, "cor": (c.get("cor") or "").strip() or None,
        "km": int(km) if km else None, "placa": placa_norm(c.get("placa")) or (c.get("placa") or "").strip() or None,
        "em_estoque": True if _marcado(c.get("estoque"), "sim") else (False if _marcado(c.get("estoque"), "nao") else None),
        "tabela_preco": _valor(c.get("tabela")), "valor_venda": _valor(c.get("valor_vendido")),
        "desconto": _valor(c.get("desconto")), "over_valor": (c.get("over") or "").strip() or None,
        "retorno": (c.get("retorno") or "").strip() or None, "banco": (c.get("banco") or "").strip() or None,
        "valor_financiado": _valor(c.get("financiado")), "valor_pix": (c.get("pix") or "").strip() or None,
        "troca_modelo": (c.get("troca_carro") or "").strip() or None,
        "troca_placa": placa_norm(c.get("troca_placa")) or (c.get("troca_placa") or "").strip() or None,
        "troca_valor": _valor(c.get("troca_valor")), "valor_total": _valor(c.get("total")),
        "ipva": "loja" if _marcado(c.get("ipva"), "loja") else ("cliente" if _marcado(c.get("ipva"), "cliente") else None),
        "observacoes": (c.get("obs") or "").strip() or None, "cliente_nome": (c.get("nome") or "").strip() or None,
        "cliente_cpf": (c.get("cpf") or "").strip() or None, "cliente_email": (c.get("email") or "").strip() or None,
        "cliente_telefone": tel or (c.get("tel") or "").strip() or None,
        "cliente_endereco": (c.get("endereco") or "").strip() or None,
        "cliente_cep": (c.get("cep") or "").strip() or None, "portal_venda": (c.get("portal") or "").strip() or None,
        "data_venda": _data_iso(c.get("data_venda"), ref),
        "data_entrega_prevista": _data_iso(c.get("data_entrega"), ref),
        "data_entrega_texto": (c.get("data_entrega") or "").strip() or None,
    }


def classificar_aviso(texto: str) -> str | None:
    """'Fastback TTD-9E46 vendido' → 'venda'; 'Pulse Hybrid reservado' → 'reserva'."""
    a = _ascii(texto).strip()
    if not a or len(a) > 120 or eh_resumo(texto) or "?" in a:
        return None
    if re.search(r"\b(caiu|cancelad[oa]|desisti[ru]|desistencia|nao vai mais levar)\b", a):
        return "queda"
    if re.search(r"\breservad[oa]s?\b", a):
        return "reserva"
    if re.search(r"\b(vendid[oa]|vendeu)\b", a) or re.match(r"^venda\b", a):
        return "venda"
    return None


def eh_revenda(texto: str | None) -> bool:
    return bool(re.search(r"\b(revenda|repasse)\b", _ascii(texto)))


def _modelo_do_aviso(texto: str) -> str | None:
    t = _PLACA.sub(" ", texto)
    t = re.sub(r"(?i)\b(pra|para|p/)\s+(revenda|repasse)\b|\b(revenda|repasse)\b", " ", t)
    t = re.sub(r"(?i)\b(vendid[oa]s?|vendeu|venda|reservad[oa]s?)\b|[✅🚗!.]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–")
    return t or None


# ===== pessoas =====
def _vendedores() -> list[dict]:
    return db.select("vendedores", {"select": "id,nome,apelidos,telefone,lid,funcao", "ativo": "eq.true"})


def vendedor_por_nome(nome: str | None, vendedores: list[dict] | None = None) -> dict | None:
    a = _ascii(nome)
    for v in vendedores or _vendedores():
        nomes = [_ascii(v["nome"])] + [_ascii(x) for x in (v.get("apelidos") or [])]
        if any(re.search(rf"\b{re.escape(n)}\b", a) for n in nomes if n):
            return v
    return None


def vendedor_por_lid(lid: str | None, vendedores: list[dict] | None = None) -> dict | None:
    return next((v for v in (vendedores or _vendedores()) if lid and v.get("lid") == lid), None)


# ===== processamento =====
def processar(data: dict, instancia: str, apikey: str, historico: bool = False) -> dict:
    key = data.get("key") or {}
    msg = data.get("message") or {}
    pm = msg.get("protocolMessage")
    if pm:
        return _protocolo(pm)
    mid = key.get("id")
    autor = key.get("participant") or ("eu" if key.get("fromMe") else None)
    em = _quando(data)
    texto = _texto(msg)
    stanza = _stanza(data)

    if msg.get("documentMessage") or msg.get("documentWithCaptionMessage") or msg.get("imageMessage"):
        return _documento(data, mid, autor, em, texto, stanza, instancia, apikey, historico)
    if not texto:
        return {"ignored": "sem_texto"}
    if eh_resumo(texto):
        return _resumo(texto, mid, autor, em, historico)
    tipo = classificar_aviso(texto)
    if tipo:
        return _aviso(tipo, texto, mid, autor, em, historico)
    placa = placa_norm(texto)
    alvo = _venda_por_msg(stanza) if stanza else (_venda_por_placa(placa) if placa else None)
    if alvo:  # comentário/observação citando a venda, ou texto com a placa
        _contexto(alvo, autor, em)
        _vincular_recentes(alvo["id"], autor, em)
        if stanza:
            obs = "\n".join(x for x in (alvo.get("observacoes"), f"[{_hhmm(em)}] {texto}") if x)
            db.update("vendas", {"observacoes": obs}, {"id": f"eq.{alvo['id']}"})
            sujo["v"] = True
        return {"contexto": alvo["id"]}
    return {"ignored": "conversa"}


def _hhmm(dt: datetime) -> str:
    return dt.astimezone(datas.TZ).strftime("%d/%m %H:%M")


def _contexto(venda: dict, autor: str | None, em: datetime) -> None:
    db.update("vendas", {"contexto_em": _iso(em), "contexto_autor": autor}, {"id": f"eq.{venda['id']}"})


def _venda_por_msg(message_id: str | None) -> dict | None:
    if not message_id:
        return None
    for col in ("aviso_message_id", "resumo_message_id"):
        rows = db.select("vendas", {"select": "*", col: f"eq.{message_id}", "limit": "1"})
        if rows:
            return rows[0]
    return None


def _venda_por_placa(placa: str | None) -> dict | None:
    if not placa:
        return None
    rows = db.select("vendas", {"select": "*", "placa": f"eq.{placa}", "removido": "eq.false",
                                "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None


def _venda_aberta_do_vendedor(vendedor_id: str | None, modelo: str | None) -> dict | None:
    """Aviso ainda sem resumo do mesmo vendedor (casando pelo modelo quando houver)."""
    if not vendedor_id:
        return None
    rows = db.select("vendas", {"select": "*", "vendedor_id": f"eq.{vendedor_id}", "removido": "eq.false",
                                "status_venda": "in.(aguardando_resumo,reservado)", "order": "created_at.desc"})
    toks = [t for t in _ascii(modelo).split() if len(t) >= 3]
    for v in rows:
        alvo = _ascii(f"{v.get('modelo') or ''} {v.get('versao') or ''}")
        if toks and any(t in alvo for t in toks):
            return v
    sem_carro = [v for v in rows if not v.get("modelo") and not v.get("placa")]
    return sem_carro[0] if sem_carro else None


def _queda(texto, autor, em) -> dict:
    """'A venda do Fastback caiu' → a venda sai da contagem (status desistiu)."""
    alvo = _venda_por_placa(placa_norm(texto))
    if not alvo:
        toks = [t for t in _ascii(_modelo_do_aviso(texto)).split() if len(t) >= 3
                and t not in ("caiu", "cancelada", "cancelado", "desistiu", "desistir", "cliente", "venda")]
        rows = db.select("vendas", {"select": "*", "removido": "eq.false",
                                    "status_venda": "in.(aguardando_resumo,completa,reservado)",
                                    "order": "created_at.desc", "limit": "60"})
        cands = [v for v in rows if toks and any(t in _ascii(f"{v.get('modelo')} {v.get('versao')}") for t in toks)]
        alvo = cands[0] if len(cands) == 1 else None
    if not alvo:
        return {"ignored": "queda_sem_venda"}
    obs = "\n".join(x for x in (alvo.get("observacoes"), f"[{_hhmm(em)}] {texto}") if x)
    db.update("vendas", {"status_venda": "desistiu", "observacoes": obs, "contexto_em": _iso(em),
                         "contexto_autor": autor}, {"id": f"eq.{alvo['id']}"})
    sujo["v"] = True
    return {"queda": alvo["id"]}


def _aviso(tipo, texto, mid, autor, em, historico) -> dict:
    if tipo == "queda":
        return _queda(texto, autor, em)
    vends = _vendedores()
    vend = vendedor_por_nome(texto, vends) or vendedor_por_lid(autor, vends)
    placa = placa_norm(texto)
    modelo = _modelo_do_aviso(texto)
    if vend and modelo and _ascii(vend["nome"]) in _ascii(modelo):
        modelo = None  # "Venda Vinicius": o nome é do vendedor, não do carro
    existente = _venda_por_placa(placa) or _venda_aberta_do_vendedor(vend["id"] if vend else None, modelo)
    novo_status = "reservado" if tipo == "reserva" else "aguardando_resumo"
    if existente:
        upd = {"contexto_em": _iso(em), "contexto_autor": autor}
        if existente["status_venda"] == "reservado" and tipo == "venda":
            upd.update({"status_venda": "aguardando_resumo", "aviso_em": _iso(em), "aviso_message_id": mid,
                        "data_venda": em.astimezone(datas.TZ).date().isoformat()})
        db.update("vendas", upd, {"id": f"eq.{existente['id']}"})
        _vincular_recentes(existente["id"], autor, em)
        sujo["v"] = True
        return {"aviso_existente": existente["id"]}
    row = {"status_venda": novo_status, "origem": "grupo", "autor": autor, "aviso_message_id": mid,
           "aviso_em": _iso(em), "placa": placa, "modelo": modelo, "vendedor_id": vend["id"] if vend else None,
           "historico": historico, "contexto_em": _iso(em), "contexto_autor": autor,
           "observacoes": texto, "revenda": eh_revenda(texto)}
    if tipo == "reserva":
        row["reservado_em"] = _iso(em)
    else:
        row["data_venda"] = em.astimezone(datas.TZ).date().isoformat()
    criado = db.insert_lock("vendas", row)
    if criado is None:
        return {"ignored": "aviso_duplicado"}
    _vincular_recentes(criado["id"], autor, em)
    sujo["v"] = True
    return {"venda": criado.get("id"), "status": novo_status}


def _resumo(texto, mid, autor, em, historico, venda_id: str | None = None) -> dict:
    ref = em.astimezone(datas.TZ).date()
    c = parse_resumo(texto)
    campos = campos_venda(c, ref)
    vends = _vendedores()
    vend = vendedor_por_nome(c.get("vendedor"), vends) or vendedor_por_lid(autor, vends)
    campos.update({"vendedor_id": vend["id"] if vend else None, "status_venda": "completa", "origem": "grupo",
                   "resumo_message_id": mid, "resumo_em": _iso(em), "texto_resumo": texto,
                   "contexto_em": _iso(em), "contexto_autor": autor})
    campos["pendencias"] = pendencias_venda(campos)
    if not campos["data_venda"]:
        campos["data_venda"] = ref.isoformat()
    existente = None
    if venda_id:
        existente = db.select("vendas", {"select": "*", "id": f"eq.{venda_id}", "limit": "1"})[0]
    else:
        existente = _venda_por_msg(mid) or _venda_por_placa(campos["placa"]) or \
            _venda_aberta_do_vendedor(campos["vendedor_id"], campos["modelo"])
    if existente:
        if existente.get("resumo_message_id") == mid:
            if not venda_id:
                return {"ignored": "resumo_duplicado"}  # mesma mensagem reentregue
            campos.pop("resumo_message_id")  # edição do próprio resumo
        if existente.get("cobranca_status") == "proposta" and existente.get("cobranca_motivo") == "resumo":
            campos["cobranca_status"] = "cancelada"
        db.update("vendas", campos, {"id": f"eq.{existente['id']}"})
        venda = {**existente, **campos}
    else:
        venda = db.insert_lock("vendas", {**campos, "historico": historico, "aviso_em": _iso(em)})
        if venda is None:
            return {"ignored": "resumo_duplicado"}
    _vincular_recentes(venda["id"], autor, em)
    recalcular(venda["id"])  # o total do resumo muda a situação do pagamento
    _ligar_lead(venda)
    sujo["v"] = True
    return {"resumo": venda["id"], "pendencias": len(campos["pendencias"])}


def _ligar_lead(venda: dict) -> None:
    """Venda com o mesmo telefone (ou mesmo cliente + carro) de um lead → lead vira 'vendido'."""
    from .leads import _ascii as asc
    desde = (datas.hoje() - timedelta(days=60)).isoformat()
    leads = db.select_all("leads", {"select": "id,cliente_nome,telefone,veiculo,status", "removido": "eq.false",
                                    "recebido_em": f"gte.{desde}"})
    tel = venda.get("cliente_telefone")
    alvo = next((l for l in leads if tel and l.get("telefone") == tel), None)
    if not alvo:
        nome = (asc(venda.get("cliente_nome")).split() or [""])[0]
        toks = [t for t in asc(f"{venda.get('modelo') or ''} {venda.get('versao') or ''}").split() if len(t) >= 3]
        cands = [l for l in leads if nome and (asc(l.get("cliente_nome")).split() or [""])[0] == nome
                 and any(t in asc(l.get("veiculo")) for t in toks)]
        alvo = cands[0] if len(cands) == 1 else None
    if not alvo:
        return
    db.update("vendas", {"lead_id": alvo["id"]}, {"id": f"eq.{venda['id']}"})
    if alvo["status"] != "vendido":
        db.update("leads", {"status": "vendido", "status_em": datas.agora().isoformat()}, {"id": f"eq.{alvo['id']}"})
        from . import leads as _leads
        _leads.sujo["v"] = True


def _protocolo(pm: dict) -> dict:
    alvo = (pm.get("key") or {}).get("id")
    venda = _venda_por_msg(alvo)
    if not venda:
        return {"ignored": "protocolo_sem_venda"}
    editada = pm.get("editedMessage")
    if editada:
        texto = _texto(editada)
        em = _dt(venda.get("resumo_em") or venda.get("aviso_em")) or datetime.now(timezone.utc)
        if texto and eh_resumo(texto) and venda.get("resumo_message_id") == alvo:
            return _resumo(texto, alvo, venda.get("autor"), em, venda.get("historico", False), venda_id=venda["id"])
        return {"ignored": "edicao"}
    if pm.get("type") in (0, "REVOKE"):
        if venda.get("aviso_message_id") == alvo and venda["status_venda"] in ("aguardando_resumo", "reservado"):
            db.update("vendas", {"removido": True}, {"id": f"eq.{venda['id']}"})
            sujo["v"] = True
            return {"removido": venda["id"]}
    return {"ignored": "protocolo"}


# ===== documentos =====
_DOC_NOME = [(r"cnh", "cnh"), (r"\brg\b|identidade", "documento_identidade"),
             (r"fatura|conta|resid|light|enel|naturgy|cedae|claro|vivo|\btim\b", "comprovante_residencia"),
             (r"contrat|social|alterac", "contrato")]


def _tipo_por_nome(nome: str | None) -> str | None:
    a = _ascii(nome)
    if not a or re.search(r"comprovante|pix|transf|recibo|pagamento|santander|itau|bradesco|nubank|picpay|inter", a):
        return None
    return next((t for padrao, t in _DOC_NOME if re.search(padrao, a)), None)


def _texto_pdf(b64: str) -> str:
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(base64.b64decode(b64)))
        return "\n".join((p.extract_text() or "") for p in r.pages[:3])
    except Exception:
        return ""


def _documento(data, mid, autor, em, legenda, stanza, instancia, apikey, historico) -> dict:
    msg = data.get("message") or {}
    doc = msg.get("documentMessage") or (msg.get("documentWithCaptionMessage") or {}).get("message", {}).get("documentMessage") or {}
    nome = doc.get("fileName") or doc.get("title")
    venda = _venda_por_msg(stanza) or _venda_por_placa(placa_norm(legenda)) or _venda_do_contexto(autor, em)
    if legenda and venda is None:
        tipo_aviso = classificar_aviso(legenda)
        if tipo_aviso:  # foto com legenda "Hrv 2019 vendida ✅"
            return _aviso(tipo_aviso, legenda, mid, autor, em, historico)
    tipo = _tipo_por_nome(nome)
    info: dict = {}
    if tipo is None:
        b64, mime = evolution.get_media_base64(instancia, apikey, data)
        if not b64:
            return {"ignored": "midia_indisponivel"}
        mime = mime or doc.get("mimetype") or "image/jpeg"
        if "pdf" in mime:
            texto = _texto_pdf(b64)
            info = llm.ler_documento(texto=texto, nome_arquivo=nome) if texto.strip() else {"tipo": "outro"}
        elif mime.startswith("image"):
            info = llm.ler_documento(image_b64=b64, mimetype=mime, nome_arquivo=nome)
        else:
            info = {"tipo": "outro"}
        tipo = info.get("tipo") or "outro"
    if tipo == "comprovante_pagamento":
        res = _pagamento(info, venda, mid, autor, em, nome, historico)
        if res.get("ignored") != "pagamento_futuro":
            return res
        tipo = "comprovante_residencia"  # "pagamento" com data depois do envio = conta a pagar
    if tipo in ("cnh", "documento_identidade", "comprovante_residencia", "contrato"):
        if db.insert_lock("documentos", {"message_id": mid, "venda_id": venda["id"] if venda else None, "tipo": tipo,
                                         "arquivo": nome, "autor": autor, "recebido_em": _iso(em)}) is None:
            return {"ignored": "documento_repetido"}
        if venda:
            recalcular(venda["id"])
        return {"documento": tipo, "venda": venda["id"] if venda else None}
    return {"ignored": f"documento_{tipo}"}


def _vincular_recentes(venda_id: str, autor: str | None, em: datetime) -> None:
    """Comprovantes e documentos que o mesmo autor mandou logo ANTES do texto da venda (anexo primeiro,
    legenda depois) passam a ser dessa venda."""
    if not autor:
        return
    z = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # "+00:00" quebra dentro de and()
    filtro = {"venda_id": "is.null", "autor": f"eq.{autor}",
              "and": f"(recebido_em.gte.{z(em - timedelta(minutes=VINCULO_ANTES_MIN))},recebido_em.lte.{z(em)})"}
    a = db.update("pagamentos", {"venda_id": venda_id}, filtro)
    b = db.update("documentos", {"venda_id": venda_id}, filtro)
    if a or b:
        recalcular(venda_id)


def _venda_do_contexto(autor: str | None, em: datetime) -> dict | None:
    if not autor:
        return None
    desde = _iso(em - timedelta(minutes=CONTEXTO_MIN))
    rows = db.select("vendas", {"select": "*", "contexto_autor": f"eq.{autor}", "contexto_em": f"gte.{desde}",
                                "removido": "eq.false", "order": "contexto_em.desc", "limit": "1"})
    return rows[0] if rows else None


def _pagamento(info, venda, mid, autor, em, nome, historico) -> dict:
    idt = (info.get("id_transacao") or "").strip() or None
    pago_em = info.get("pago_em")
    try:
        dt_pago = datetime.fromisoformat(pago_em.replace("Z", "+00:00")) if pago_em else None
        pago_em = (dt_pago if dt_pago.tzinfo else dt_pago.replace(tzinfo=datas.TZ)).isoformat() if dt_pago else None
    except ValueError:
        pago_em = None
    if pago_em and datetime.fromisoformat(pago_em) > em + timedelta(hours=1):
        return {"ignored": "pagamento_futuro"}
    recebedor = _ascii(info.get("recebedor"))
    if recebedor and not any(k in recebedor for k in RECEBEDORES_LOJA):
        return {"ignored": "pagamento_terceiro"}  # dinheiro que não foi para a loja (conta, boleto de terceiro)
    valor = info.get("valor")
    try:
        valor = float(valor) if valor is not None else None
    except (TypeError, ValueError):
        valor = _valor(str(valor))
    if not idt and valor and pago_em:  # sem id: mesmo valor no mesmo minuto = mesmo pagamento
        dup = db.select("pagamentos", {"select": "id", "valor": f"eq.{valor}", "pago_em": f"eq.{pago_em}", "limit": "1"})
        if dup:
            return {"ignored": "pagamento_repetido"}
    row = {"venda_id": venda["id"] if venda else None, "message_id": mid, "id_transacao": idt, "valor": valor,
           "pago_em": pago_em, "tipo": info.get("forma"), "banco": info.get("banco"),
           "pagador": info.get("pagador"), "recebedor": info.get("recebedor"), "arquivo": nome,
           "autor": autor, "recebido_em": _iso(em), "historico": historico}
    try:
        criado = db.insert_lock("pagamentos", row)
    except Exception:
        criado = None
    if criado is None:
        return {"ignored": "pagamento_repetido"}
    if venda:
        recalcular(venda["id"])
    sujo["v"] = True
    return {"pagamento": criado.get("id"), "venda": venda["id"] if venda else None, "valor": valor}


def recalcular(venda_id: str) -> None:
    """Valor pago (soma dos comprovantes), situação do pagamento e documentos recebidos da venda."""
    pags = db.select("pagamentos", {"select": "valor", "venda_id": f"eq.{venda_id}"})
    docs_rows = db.select("documentos", {"select": "tipo", "venda_id": f"eq.{venda_id}"})
    total_pago = round(sum(p["valor"] or 0 for p in pags), 2)
    v = db.select("vendas", {"select": "valor_total,valor_venda", "id": f"eq.{venda_id}", "limit": "1"})[0]
    alvo = v.get("valor_total") or v.get("valor_venda") or 0
    status = "pago" if alvo and total_pago >= alvo else ("parcial" if total_pago > 0 else "pendente")
    docs = {d["tipo"]: True for d in docs_rows}
    if pags:
        docs["comprovantes"] = len(pags)
    db.update("vendas", {"valor_pago": total_pago, "status_pagamento": status, "docs": docs},
              {"id": f"eq.{venda_id}"})
    sujo["v"] = True


# ===== prazos e cobrança =====
def codigo(venda: dict) -> str:
    return venda["id"][:4].upper()


def prazo_resumo(aviso_em: datetime) -> datetime:
    local = aviso_em.astimezone(datas.TZ)
    prazo = local + timedelta(hours=PRAZO_RESUMO_H)
    if local.hour >= CORTE_DIA or prazo.hour >= CORTE_DIA or prazo.date() > local.date():
        prazo = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif prazo.hour < 8:
        prazo = prazo.replace(hour=9, minute=0, second=0, microsecond=0)
    return prazo


def _carro(v: dict) -> str:
    return " ".join(x for x in (v.get("modelo"), v.get("versao"), v.get("placa")) if x) or "carro sem identificação"


def _nome_vendedor(v: dict) -> str:
    if not v.get("vendedor_id"):
        return "vendedor"
    r = db.select("vendedores", {"select": "nome", "id": f"eq.{v['vendedor_id']}", "limit": "1"})
    return r[0]["nome"] if r else "vendedor"


def texto_cobranca(v: dict, motivo: str) -> str:
    nome = _nome_vendedor(v)
    carro = _carro(v)
    if motivo == "resumo":
        return (f"Fala {nome}! A venda do {carro} foi avisada no grupo às "
                f"{_hhmm(_dt(v['aviso_em']))} e o Resumo de Venda ainda não chegou. Manda lá no grupo de vendas 🙏")
    if motivo == "pendencias":
        return (f"Fala {nome}! No Resumo de Venda do {carro} ficou faltando: {', '.join(v.get('pendencias') or [])}. "
                f"Consegue completar lá no grupo (pode editar a mensagem)? 🙏")
    return (f"Fala {nome}! O {carro} está reservado desde {_hhmm(_dt(v['reservado_em']))}. "
            f"Fechou a venda ou o cliente desistiu? Me dá um retorno 🙏")


def _propor(v: dict, motivo: str, agora: datetime) -> bool:
    texto = texto_cobranca(v, motivo)
    cod = codigo(v)
    titulo = {"resumo": "Resumo de Venda atrasado", "pendencias": "Resumo de Venda incompleto",
              "reserva": f"Reserva parada há {RESERVA_DIAS} dias"}[motivo]
    try:
        evolution.enviar_texto(settings.meu_numero,
                               f"📨 {titulo} — cobrança pro {_nome_vendedor(v)} [#{cod}]\n{_carro(v)}\n\n\"{texto}\"\n\n"
                               f"Responda *ok {cod}* pra enviar · *não {cod}* pra descartar · "
                               f"ou *{cod}: texto novo* pra mandar outro texto.")
    except Exception:
        log.exception("falha propondo cobrança de venda")
        return False
    cobrado = dict(v.get("cobrado") or {})
    cobrado[motivo] = "|".join(v.get("pendencias") or []) if motivo == "pendencias" else True
    db.update("vendas", {"cobranca_status": "proposta", "cobranca_motivo": motivo, "cobranca_texto": texto,
                         "cobranca_proposta_em": _iso(agora), "cobrado": cobrado}, {"id": f"eq.{v['id']}"})
    return True


def checar_prazos(agora: datetime | None = None) -> int:
    agora = agora or datetime.now(timezone.utc)
    rows = db.select_all("vendas", {"select": "*", "historico": "eq.false", "removido": "eq.false",
                                    "origem": "eq.grupo", "status_venda": "in.(aguardando_resumo,completa,reservado)"})
    n = 0
    for v in rows:
        if v.get("cobranca_status") == "proposta" or v.get("revenda"):
            continue
        cobrado = v.get("cobrado") or {}
        if v["status_venda"] == "aguardando_resumo" and v.get("aviso_em") and "resumo" not in cobrado \
                and agora >= prazo_resumo(_dt(v["aviso_em"])):
            n += _propor(v, "resumo", agora)
        elif v["status_venda"] == "completa" and v.get("pendencias") and v.get("resumo_em") \
                and cobrado.get("pendencias") != "|".join(v["pendencias"]) \
                and agora >= _dt(v["resumo_em"]) + timedelta(minutes=CARENCIA_PENDENCIA_MIN):
            n += _propor(v, "pendencias", agora)
        elif v["status_venda"] == "reservado" and v.get("reservado_em") and "reserva" not in cobrado \
                and agora >= _dt(v["reservado_em"]) + timedelta(days=RESERVA_DIAS):
            n += _propor(v, "reserva", agora)
    if n:
        sujo["v"] = True
    return n


def tentar_resolver(texto: str) -> str | None:
    """Aprovação de cobrança pelo dono (leads e vendas juntos) — ver cobrancas.py."""
    from . import cobrancas
    return cobrancas.tentar_resolver(texto)


def _resolvida(atual: dict) -> bool:
    motivo = atual.get("cobranca_motivo")
    return (motivo == "resumo" and atual["status_venda"] != "aguardando_resumo") or \
        (motivo == "pendencias" and not atual.get("pendencias")) or \
        (motivo == "reserva" and atual["status_venda"] != "reservado")

def _enviar(venda: dict, msg: str) -> str:
    atual = db.select("vendas", {"select": "*", "id": f"eq.{venda['id']}", "limit": "1"})[0]
    if _resolvida(atual) or atual.get("removido"):
        db.update("vendas", {"cobranca_status": "cancelada"}, {"id": f"eq.{venda['id']}"})
        return f"Isso já foi resolvido no {_carro(atual)} — não enviei."
    v = db.select("vendedores", {"select": "telefone,nome", "id": f"eq.{atual.get('vendedor_id')}", "limit": "1"}) \
        if atual.get("vendedor_id") else []
    tel = v[0].get("telefone") if v else None
    if not tel:
        return "Não sei quem é o vendedor dessa venda (ou não tenho o telefone dele) — não enviei."
    evolution.enviar_por_coletor(tel, msg)
    db.update("vendas", {"cobranca_status": "enviada", "cobranca_texto": msg,
                         "cobranca_enviada_em": _iso(datetime.now(timezone.utc))}, {"id": f"eq.{venda['id']}"})
    sujo["v"] = True
    return f"✅ Cobrança enviada pro {v[0]['nome']}."


# ===== ranking do mês na descrição do grupo =====
GRUPO_VENDAS = "120363394210533119@g.us"
_MARCAS = {"fiat", "renault", "peugeot", "chevrolet", "gm", "volkswagen", "vw", "ford", "honda", "toyota", "hyundai",
           "nissan", "jeep", "citroen", "mitsubishi", "byd", "kia", "bmw", "mini", "audi", "yamaha", "suzuki", "caoa"}
_MEDALHAS = ["🥇", "🥈", "🥉"]
_descricao = {"aplicada": None, "t": 0.0}
DESCRICAO_ATIVA = True


def nome_carro(v: dict) -> str:
    modelo = (v.get("modelo") or "").strip()
    versao = (v.get("versao") or "").strip()
    if _ascii(modelo) in _MARCAS and versao:  # "FIAT / Fastback" → "Fastback"
        modelo = versao
    return modelo or versao or "Carro"


def _data_ranking(r: dict) -> str:
    """Venda conta pela data da venda; reserva, pela data da reserva."""
    if r.get("data_venda"):
        return r["data_venda"]
    d = _dt(r.get("reservado_em"))
    return d.astimezone(datas.TZ).date().isoformat() if d else ""


def _linha_carro(v: dict) -> str:
    carro = " ".join(str(x) for x in (nome_carro(v), v.get("ano")) if x)
    return f"{carro} - {v['placa']}" if v.get("placa") else carro


def texto_ranking(mes: str | None = None) -> str:
    """Descrição do grupo VENDAS: total do mês e vendas por vendedor (reserva conta; empate divide a medalha).
    Vendas para revenda ficam fora do total, listadas abaixo do traçado."""
    mes = mes or datas.hoje_iso()[:7]
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    rows = db.select_all("vendas", {"select": "modelo,versao,ano,placa,vendedor_id,data_venda,reservado_em,revenda,"
                                    "created_at", "removido": "eq.false",
                                    "status_venda": "in.(completa,aguardando_resumo,reservado)"})
    rows = sorted([r for r in rows if _data_ranking(r)[:7] == mes], key=lambda r: (_data_ranking(r), r.get("created_at") or ""))
    revenda = [r for r in rows if r.get("revenda")]
    rows = [r for r in rows if not r.get("revenda")]
    # todo vendedor da equipe aparece sempre (mesmo com zero); só muda de posição
    equipe = db.select("vendedores", {"select": "nome", "ativo": "eq.true", "funcao": "eq.vendedor",
                                      "telefone": "not.is.null"})
    por: dict[str, list] = {v["nome"]: [] for v in equipe}
    for r in rows:
        por.setdefault(nomes.get(r.get("vendedor_id"), "Sem vendedor"), []).append(r)
    ordem = sorted(por.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    qtds = sorted({len(v) for v in por.values()}, reverse=True)
    ultimo = min(qtds) if len(qtds) > 1 else None  # lanterna só quando há diferença
    linhas = [f"💰*TOTAL DE VENDAS GRUPO SB: {len(rows)}*"]
    for nome, vs in ordem:
        pos = qtds.index(len(vs))
        medalha = _MEDALHAS[pos] if pos < 3 else "🏅"
        lanterna = " 🔦" if len(vs) == ultimo else ""
        linhas.append(f"\n*{medalha}{nome}: {len(vs)}*{lanterna}")
        linhas += [_linha_carro(v) for v in vs]
    linhas.append("\n➖➖➖➖➖➖➖➖")
    if revenda:
        linhas.append("Revenda\n")
        linhas += [_linha_carro(v) for v in revenda]
    return "\n".join(linhas)


def atualizar_descricao(forcar: bool = False) -> bool:
    """Troca a descrição do grupo VENDAS quando o ranking do mês mudou (venda nova, venda que caiu)."""
    import time
    if not DESCRICAO_ATIVA and not forcar:
        return False
    if not forcar and time.time() - _descricao["t"] < 60:
        return False
    _descricao["t"] = time.time()
    novo = texto_ranking()
    if _descricao["aplicada"] is None:
        _descricao["aplicada"] = evolution.descricao_grupo(GRUPO_VENDAS)
    if novo.strip() == (_descricao["aplicada"] or "").strip():
        return False
    evolution.alterar_descricao_grupo(GRUPO_VENDAS, novo)
    _descricao["aplicada"] = novo
    return True
