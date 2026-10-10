"""Grupo AVALIAÇÕES: o formulário do vendedor vira uma avaliação; o valor que o gestor posta (citando o
formulário ou logo depois) é guardado literalmente. Sem valor em 5 min → aviso pro dono. Quando uma venda
fecha com esse carro na troca, a avaliação vira 'fechou' e o carro ganha cartão em "Carro em trânsito"."""
import logging
import re
from datetime import datetime, timedelta, timezone

from . import db, evolution, trello
from .config import settings
from .leads import _ascii, _dt, _iso, _quando, _stanza, _texto

log = logging.getLogger("agente")

AVISO_MIN = 5
JANELA_VALOR_H = 3          # resposta sem citação vale para a última avaliação sem valor nas últimas 3 h
GESTORES = {"50852479930380@lid": "Igor", "50427244589224@lid": "Felipe"}  # + o dono (fromMe)
_CHECK = {"ar cond": "ar_condicionado", "gelando": "gelando", "buzina": "buzina", "limpador": "limpador",
          "luz no painel": "luz_painel", "chave reserva": "chave_reserva", "revisado": "revisado"}

sujo = {"v": True}


def eh_formulario(texto: str) -> bool:
    a = _ascii(texto)
    return "fipe" in a and "modelo" in a and ("loja" in a or "combustivel" in a)


def _num(v: str | None) -> float | None:
    from .vendas_grupo import _valor
    return _valor(v)


def _marca(valor: str) -> bool | None:
    a = _ascii(valor).replace(" ", "")
    if re.search(r"\(x\)sim", a):
        return True
    if re.search(r"\(x\)nao", a):
        return False
    return None


def parse_formulario(texto: str) -> dict:
    campos: dict = {}
    for linha in texto.splitlines():
        if ":" not in linha and ";" not in linha and "-" not in linha:
            continue
        rot, _, valor = re.split(r"([:;]|-\s)", linha, maxsplit=1) if re.search(r"[:;]|-\s", linha) else (linha, "", "")
        r = re.sub(r"^[^a-z]+", "", _ascii(rot)).strip(" .")
        valor = valor.strip(" *")
        if "👨" in rot or "🎨" in rot:
            campos["pecas_pintadas"] = valor
        elif "🚫" in rot:
            campos["pecas_trocar"] = valor
        elif r.startswith("loja"):
            campos["loja"] = valor
        elif r.startswith("modelo"):
            campos["modelo"] = valor
        elif r.startswith("combust"):
            campos["combustivel_txt"] = valor
        elif r.startswith("ano"):
            m = re.search(r"\d{4}", valor)
            campos["ano"] = int(m.group()) if m else None
        elif r.startswith("km"):
            km = _num(valor)
            campos["km"] = int(km) if km else None
        elif r.startswith("placa"):
            from .vendas_grupo import placa_norm
            campos["placa"] = placa_norm(valor)
        elif r.startswith("revisao"):
            campos["revisao"] = valor or None
        elif r.startswith("pneus"):
            campos["pneus"] = valor or None
        elif r.startswith("obs"):
            campos["obs"] = valor or None
        elif r.startswith("fipe"):
            campos["fipe"] = _num(valor)
        elif r.startswith("avaliacao"):
            campos["valor_pretendido"] = _num(valor)
        elif r.startswith("troca"):
            campos["carro_interesse"] = valor or None
        else:
            for chave, col in _CHECK.items():
                if r.startswith(chave):
                    campos[col] = _marca(valor)
    return {k: (v if v != "" else None) for k, v in campos.items()}


def valor_numerico(texto: str) -> float | None:
    """'38' → 38.000; '100' → 100.000; '6k' → 6.000; 'R$ 45.000' → 45.000. Primeiro número da mensagem."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(k|mil)?", _ascii(texto))
    if not m:
        return None
    n = float(m.group(1).replace(".", "").replace(",", ".")) if "." in m.group(1) and len(m.group(1).split(".")[-1]) == 3 \
        else float(m.group(1).replace(",", "."))
    return n * 1000 if n < 1000 else n


# ===== processamento =====
def processar(data: dict, historico: bool = False) -> dict:
    from .vendas_grupo import vendedor_por_lid
    key = data.get("key") or {}
    msg = data.get("message") or {}
    if msg.get("protocolMessage"):
        return {"ignored": "protocolo"}
    texto = _texto(msg)
    if not texto:
        return {"ignored": "sem_texto"}
    em = _quando(data)
    mid = key.get("id")
    autor = key.get("participant")
    if eh_formulario(texto):
        vend = vendedor_por_lid(autor)
        row = {**parse_formulario(texto), "message_id": mid, "origem": "grupo", "autor": autor,
               "vendedor_id": vend["id"] if vend else None, "avaliado_em": _iso(em), "historico": historico,
               "texto_original": texto}
        criado = db.insert_lock("avaliacoes", row)
        if criado is None:
            return {"ignored": "duplicada"}
        sujo["v"] = True
        return {"avaliacao": criado["id"], "modelo": row.get("modelo")}
    gestor = "Diogo" if key.get("fromMe") else GESTORES.get(autor or "")
    if not gestor or not re.search(r"\d", texto):
        return {"ignored": "conversa"}
    stanza = _stanza(data)
    alvo = None
    if stanza:
        r = db.select("avaliacoes", {"select": "*", "message_id": f"eq.{stanza}", "limit": "1"})
        alvo = r[0] if r else None
    if alvo is None:  # continuação do mesmo gestor ("pago 35" + "manda para ele 48")
        r = db.select("avaliacoes", {"select": "*", "origem": "eq.grupo", "valor_por": f"eq.{gestor}",
                                     "valor_em": f"gte.{_iso(em - timedelta(minutes=10))}",
                                     "order": "valor_em.desc", "limit": "1"})
        alvo = r[0] if r and _dt(r[0]["valor_em"]) <= em else None
    if alvo is None:
        desde = _iso(em - timedelta(hours=JANELA_VALOR_H))
        r = db.select("avaliacoes", {"select": "*", "origem": "eq.grupo", "valor_texto": "is.null",
                                     "avaliado_em": f"gte.{desde}", "order": "avaliado_em.desc", "limit": "1"})
        alvo = r[0] if r and _dt(r[0]["avaliado_em"]) <= em else None
    if not alvo:
        return {"ignored": "valor_sem_avaliacao"}
    literal = texto.strip() if not alvo.get("valor_texto") else f"{alvo['valor_texto']}\n{texto.strip()}"
    upd = {"valor_texto": literal, "valor_por": gestor, "valor_em": _iso(em)}
    if not alvo.get("valor_avaliacao"):
        upd["valor_avaliacao"] = valor_numerico(texto)
    db.update("avaliacoes", upd, {"id": f"eq.{alvo['id']}"})
    sujo["v"] = True
    return {"valor": alvo["id"], "texto": texto.strip()}


# ===== aviso de avaliação sem valor e desfecho com a venda =====
def checar(agora: datetime | None = None) -> int:
    agora = agora or datetime.now(timezone.utc)
    n = 0
    limite = _iso(agora - timedelta(minutes=AVISO_MIN))
    nomes = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    for a in db.select_all("avaliacoes", {"select": "*", "origem": "eq.grupo", "historico": "eq.false",
                                          "valor_texto": "is.null", "aviso_em": "is.null",
                                          "avaliado_em": f"lte.{limite}"}):
        try:
            evolution.enviar_texto(settings.meu_numero,
                                   f"⏱️ Avaliação sem valor há {AVISO_MIN} min — {nomes.get(a.get('vendedor_id'), '?')}\n"
                                   f"{a.get('modelo') or '?'} {a.get('ano') or ''} · {a.get('km') or '?'} km · "
                                   f"FIPE {a.get('fipe') or '?'}"
                                   + (f"\nCliente quer: {a['carro_interesse']}" if a.get("carro_interesse") else ""))
        except Exception:
            log.exception("falha avisando avaliação sem valor")
            continue
        db.update("avaliacoes", {"aviso_em": _iso(agora)}, {"id": f"eq.{a['id']}"})
        n += 1
    n += ligar_vendas()
    return n


def _toks(t: str | None) -> set[str]:
    """Palavras que identificam o carro — sem marca ("Volkswagen" casava Virtus com Nivus) e sem ano."""
    from .vendas_grupo import _MARCAS
    return {x for x in re.split(r"[^a-z0-9]+", _ascii(t))
            if len(x) >= 3 and x not in _MARCAS and not re.fullmatch(r"(19|20)\d{2}", x)}


def ligar_vendas() -> int:
    """Venda com troca (modelo/placa) que bate com uma avaliação aberta do mesmo vendedor → 'fechou' + cartão
    do carro da troca em "Carro em trânsito"."""
    abertas = db.select_all("avaliacoes", {"select": "*", "origem": "eq.grupo", "status": "eq.aberta"})
    if not abertas:
        return 0
    vendas = db.select_all("vendas", {"select": "id,vendedor_id,troca_modelo,troca_placa,data_venda,status_venda",
                                      "removido": "eq.false", "troca_modelo": "not.is.null", "origem": "eq.grupo",
                                      "status_venda": "in.(completa,aguardando_resumo)"})
    usadas = {a["venda_id"] for a in db.select_all("avaliacoes", {"select": "venda_id", "venda_id": "not.is.null"})}
    n = 0
    for v in vendas:
        if v["id"] in usadas:
            continue
        # a venda tem que ser do mesmo período: no dia da avaliação ou depois
        recentes = [a for a in abertas if (v.get("data_venda") or "") >= (a.get("avaliado_em") or "")[:10]]
        cands = [a for a in recentes if (v.get("troca_placa") and a.get("placa") == v["troca_placa"])
                 or (a.get("vendedor_id") == v.get("vendedor_id") and _toks(a.get("modelo")) & _toks(v.get("troca_modelo")))]
        if len(cands) != 1:
            continue
        a = cands[0]
        db.update("avaliacoes", {"status": "fechou", "venda_id": v["id"],
                                 "placa": a.get("placa") or v.get("troca_placa")}, {"id": f"eq.{a['id']}"})
        abertas.remove(a)
        if not a.get("historico"):
            _cartao_da_troca({**a, "placa": a.get("placa") or v.get("troca_placa")})
        sujo["v"] = True
        n += 1
    return n


def _cartao_da_troca(a: dict) -> None:
    if not trello.configurado():
        return
    try:
        with trello._cliente() as c:
            cards, listas = trello.cartoes(c)
            if a.get("placa") and any(trello._placa(cd["name"]) == a["placa"] for cd in cards):
                return
            transito = next(k for k, v in listas.items() if v == "carro em transito")
            card = c.post("/cards", params={"idList": transito, "pos": "top",
                                             "name": trello.nome_cartao({"modelo": a.get("modelo"), "ano": a.get("ano"),
                                                                         "placa": a.get("placa")}, None)}).json()
        db.update("avaliacoes", {"trello_card_id": card["id"]}, {"id": f"eq.{a['id']}"})
    except Exception:
        log.exception("falha criando cartão do carro da troca")
