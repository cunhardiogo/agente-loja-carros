from datetime import timedelta

from . import datas, db, ingest
from .schemas import Extracao

_AFIRMA = {"sim", "s", "isso", "isso mesmo", "confirmo", "confirma", "confirmar", "ok", "okay",
           "certo", "correto", "pode", "pode registrar", "perfeito", "exato", "positivo", "👍", "✅"}
_NEGA = {"não", "nao", "n", "errado", "incorreto", "negativo", "descarta", "descartar",
         "ignora", "ignorar", "esquece", "esquecer", "❌"}
_CORRIGE = ("corrig", "corrige", "na verdade", "era ", "não,", "nao,", "o certo")


def _pendente_recente() -> dict | None:
    limite = (datas.agora() - timedelta(hours=3)).isoformat()
    rows = db.select("eventos_brutos", {
        "select": "*", "status": "eq.pendente_confirmacao",
        "created_at": f"gte.{limite}", "order": "created_at.desc", "limit": "1",
    })
    return rows[0] if rows else None


def _resumo(dados: dict) -> str:
    return dados.get("resumo") or dados.get("veiculo_descricao") or dados.get("tipo_evento") or "registro"


def tentar_resolver(texto: str) -> str | None:
    """Se houver um evento pendente e o texto for sim/não/correção, resolve. Senão retorna None."""
    ev = _pendente_recente()
    if not ev:
        return None
    t = texto.strip().lower()

    if t in _AFIRMA:
        ext = Extracao(**ev["dados_extraidos"])
        tabela, rid = ingest.aplicar(ext)
        novo_status = "confirmado" if rid else "descartado"
        db.update("eventos_brutos",
                  {"status": novo_status, "registro_tabela": tabela, "registro_id": rid},
                  {"id": f"eq.{ev['id']}"})
        if rid:
            return f"✅ Registrado: {_resumo(ev['dados_extraidos'])}"
        return "Tentei registrar, mas não encontrei o registro correspondente pra atualizar."

    if t in _NEGA:
        db.update("eventos_brutos", {"status": "descartado"}, {"id": f"eq.{ev['id']}"})
        return "❌ Ok, descartei."

    if t.startswith(_CORRIGE):
        novo = ingest.reextrair(ev.get("mensagem_original") or "", texto)
        tabela, rid = ingest.aplicar(novo)
        novo_status = "confirmado" if rid else "pendente_confirmacao"
        db.update("eventos_brutos",
                  {"status": novo_status, "dados_extraidos": novo.model_dump(mode="json"),
                   "registro_tabela": tabela, "registro_id": rid},
                  {"id": f"eq.{ev['id']}"})
        if rid:
            return f"✅ Corrigi e registrei: {novo.resumo or _resumo(novo.model_dump(mode='json'))}"
        return "Anotei a correção, mas ainda não bati com um registro existente."

    return None
