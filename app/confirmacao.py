import re
from datetime import timedelta

from . import datas, db, ingest
from .schemas import Extracao

_AFIRMA = {"sim", "s", "isso", "isso mesmo", "confirmo", "confirma", "confirmar", "ok", "okay",
           "certo", "correto", "pode", "pode registrar", "perfeito", "exato", "positivo", "👍", "✅"}
_NEGA = {"não", "nao", "n", "errado", "incorreto", "negativo", "descarta", "descartar",
         "ignora", "ignorar", "esquece", "esquecer", "❌"}
# gatilhos de correção SEGUROS: prefixos que não disparam em perguntas normais.
# (removidos "era "/"não," — davam falso positivo em frases comuns)
_CORRIGE = ("corrig", "corrige", "na verdade", "o certo é", "o correto é")

_RE_CODIGO = re.compile(r"\b([0-9a-fA-F]{4})\b")


def _pendentes() -> list[dict]:
    limite = (datas.agora() - timedelta(hours=24)).isoformat()
    return db.select("eventos_brutos", {
        "select": "id,dados_extraidos,mensagem_original,tipo_evento,created_at",
        "status": "eq.pendente_confirmacao", "created_at": f"gte.{limite}",
        "order": "created_at.desc", "limit": "20",
    })


def _resumo(dados: dict) -> str:
    return dados.get("resumo") or dados.get("veiculo_descricao") or dados.get("tipo_evento") or "registro"


def _classificar(t: str) -> str | None:
    if t in _AFIRMA:
        return "sim"
    if t in _NEGA:
        return "nao"
    if t.startswith(_CORRIGE):
        return "corrige"
    return None


def listar_pendencias() -> str:
    pend = _pendentes()
    if not pend:
        return "Não há pendências aguardando confirmação."
    linhas = [f"• [#{ingest.codigo_pendencia(p['id'])}] {p.get('tipo_evento')} — {_resumo(p.get('dados_extraidos') or {})}"
              for p in pend]
    return "Pendências aguardando confirmação:\n" + "\n".join(linhas) + \
           "\n\nResponda com o código, ex: *A3F2 sim* / *A3F2 não* / *A3F2 corrige ...*"


def _aplicar_sim(ev: dict) -> str:
    ext = Extracao(**ev["dados_extraidos"])
    # dono confirmou: se foi flag de venda duplicada, força o registro
    tabela, rid = ingest.aplicar(ext, forcar_venda=True)
    db.update("eventos_brutos",
              {"status": "confirmado" if rid else "descartado", "registro_tabela": tabela, "registro_id": rid},
              {"id": f"eq.{ev['id']}"})
    if rid:
        return f"✅ Registrado: {_resumo(ev['dados_extraidos'])}"
    return "Tentei registrar, mas não encontrei o registro correspondente pra atualizar."


def _aplicar_corrige(ev: dict, texto: str) -> str:
    novo = ingest.reextrair(ev.get("mensagem_original") or "", texto)
    # o dono está corrigindo de propósito → força a venda (não trata como duplicada)
    tabela, rid = ingest.aplicar(novo, forcar_venda=True)
    if tabela == "duplicada":  # defensivo: nunca persistir 'duplicada' como sucesso
        tabela, rid = None, None
    db.update("eventos_brutos",
              {"status": "confirmado" if rid else "pendente_confirmacao",
               "dados_extraidos": novo.model_dump(mode="json"),
               "registro_tabela": tabela, "registro_id": rid},
              {"id": f"eq.{ev['id']}"})
    if rid:
        return f"✅ Corrigi e registrei: {novo.resumo or _resumo(novo.model_dump(mode='json'))}"
    return "Anotei a correção, mas ainda não bati com um registro existente."


def confirmar_id(evento_id: str) -> dict:
    """Confirma uma pendência específica (usado pelos botões do dashboard)."""
    ev = db.select("eventos_brutos", {"select": "id,dados_extraidos,mensagem_original",
                                      "id": f"eq.{evento_id}", "status": "eq.pendente_confirmacao", "limit": "1"})
    if not ev:
        return {"erro": "pendência não encontrada"}
    return {"ok": True, "mensagem": _aplicar_sim(ev[0])}


def descartar_id(evento_id: str) -> dict:
    rows = db.update("eventos_brutos", {"status": "descartado"},
                     {"id": f"eq.{evento_id}", "status": "eq.pendente_confirmacao"})
    return {"ok": bool(rows)}


def pendentes_itens() -> list[dict]:
    from . import ingest
    return [{"id": p["id"], "codigo": ingest.codigo_pendencia(p["id"]),
             "tipo": p.get("tipo_evento"), "resumo": _resumo(p.get("dados_extraidos") or {}),
             "mensagem": (p.get("mensagem_original") or "")[:200]}
            for p in _pendentes()]


def tentar_resolver(texto: str) -> str | None:
    """Resolve confirmação de pendências. Retorna None se o texto não for um comando
    de confirmação (aí o agente normal responde)."""
    pend = _pendentes()
    if not pend:
        return None

    t = texto.strip().lower()
    por_codigo = {ingest.codigo_pendencia(p["id"]).lower(): p for p in pend}

    # código explícito na mensagem? (ex: "a3f2 sim")
    alvo = None
    m = _RE_CODIGO.search(t)
    if m and m.group(1) in por_codigo:
        alvo = por_codigo[m.group(1)]
        t = (t[:m.start()] + " " + t[m.end():]).strip()

    acao = _classificar(t)
    if acao is None:
        return None  # não é confirmação → deixa o agente responder

    if alvo is None:
        if len(pend) == 1:
            alvo = pend[0]
        else:
            return ("Tenho mais de uma pendência. Diga o código:\n\n" + listar_pendencias())

    if acao == "sim":
        return _aplicar_sim(alvo)
    if acao == "nao":
        db.update("eventos_brutos", {"status": "descartado"}, {"id": f"eq.{alvo['id']}"})
        return "❌ Ok, descartei."
    return _aplicar_corrige(alvo, texto)
