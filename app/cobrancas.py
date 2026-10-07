"""Aprovação das cobranças (leads e vendas) pelo dono: 'ok CÓD', 'não CÓD', 'CÓD: texto' ou,
com uma única cobrança esperando, 'ok' / 'pode enviar' / 'não' / 'texto novo: ...' sem código."""
import re

from . import db, ingest, leads, vendas_grupo
from .leads import _ascii

_AFIRMA = {"ok", "sim", "s", "envia", "enviar", "manda", "mandar", "pode", "pode enviar", "pode mandar",
           "aprovado", "aprovo", "aprova", "beleza", "blz", "ok pode enviar", "pode sim", "envia ai", "manda ai", ""}
_NEGA = {"nao", "n", "descarta", "descartar", "cancela", "cancelar", "nao envia", "nao manda", "deixa", "ignora"}
_PREFIXO_TEXTO = re.compile(r"^\s*(texto novo|novo texto|manda|mande|envia|envie)\b\s*[:.\-–]?\s*", re.I)
_CODIGO = re.compile(r"#?\b([0-9a-fA-F]{4})\b")


def _pendentes() -> list[dict]:
    out = []
    for l in db.select("leads", {"select": "*", "cobranca_status": "eq.proposta"}):
        out.append({"origem": "lead", "row": l, "codigo": leads.codigo(l),
                    "rotulo": f"{l.get('vendedor_nome') or 'vendedor'} — lead {l.get('cliente_nome') or '?'} ({l.get('veiculo') or '?'})"})
    for v in db.select("vendas", {"select": "*", "cobranca_status": "eq.proposta"}):
        out.append({"origem": "venda", "row": v, "codigo": vendas_grupo.codigo(v),
                    "rotulo": f"{vendas_grupo._nome_vendedor(v)} — {vendas_grupo._carro(v)}"})
    return out


def _descartar(p: dict) -> str:
    tabela = "leads" if p["origem"] == "lead" else "vendas"
    db.update(tabela, {"cobranca_status": "descartada"}, {"id": f"eq.{p['row']['id']}"})
    (leads if p["origem"] == "lead" else vendas_grupo).sujo["v"] = True
    return f"Ok, cobrança #{p['codigo']} descartada."


def _enviar(p: dict, msg: str) -> str:
    if p["origem"] == "lead":
        return leads._enviar_cobranca(p["row"], msg)
    return vendas_grupo._enviar(p["row"], msg)


def tentar_resolver(texto: str) -> str | None:
    """None = a mensagem não é sobre cobrança (segue para o assistente)."""
    pend = _pendentes()
    if not pend:
        return None
    bruto = texto.strip()
    m = _CODIGO.search(bruto)
    alvo = None
    if m:
        alvo = next((p for p in pend if p["codigo"] == m.group(1).upper()), None)
        if not alvo:
            return None  # código de outra coisa
        resto = (bruto[:m.start()] + bruto[m.end():]).strip(" :#-–\n")
    else:
        resto = bruto
    prefixo = _PREFIXO_TEXTO.match(resto)
    r = _ascii(resto).strip(" .!")
    eh_comando = r in _AFIRMA or r in _NEGA or bool(prefixo)
    if not alvo:
        if not eh_comando:
            return None
        from . import confirmacao
        if len(pend) > 1 or (ingest.PERGUNTAR_DONO and confirmacao.pendentes_itens()):
            lista = "\n".join(f"• #{p['codigo']} — {p['rotulo']}" for p in pend)
            return f"Tem {len(pend)} cobranças esperando. Responda com o código, ex: *ok {pend[0]['codigo']}*:\n{lista}"
        alvo = pend[0]
    if r in _NEGA:
        return _descartar(alvo)
    if prefixo and resto[prefixo.end():].strip():
        return _enviar(alvo, resto[prefixo.end():].strip())
    if r in _AFIRMA:
        return _enviar(alvo, alvo["row"]["cobranca_texto"])
    if m and len(resto) >= 15:
        return _enviar(alvo, resto)
    return None
