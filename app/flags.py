"""Liga/desliga das automações e textos do dono, guardados em config_flags (o dono muda por mensagem)."""
import time

from . import db

# nome → (padrão, descrição pro dono)
AUTOMACOES = {
    "cobranca_leads": (True, "cobrança de retorno dos leads (negociação telefone) pro vendedor, com sua aprovação"),
    "cobranca_vendas": (True, "cobrança de resumo/pendências/reserva das vendas pro vendedor, com sua aprovação"),
    "cobranca_entregas": (True, "cobrança de entrega sem data / venda fora do quadro / entrega atrasada"),
    "posvenda": (True, "mensagem de pós-venda 3 dias depois da entrega, com sua aprovação"),
    "aviso_avaliacao": (True, "aviso de avaliação sem valor há 5 min"),
    "aviso_recall": (True, "aviso de recall sem data há 2 dias"),
    "recados_preparacao": (False, "recado pro Felipe de tarefa de preparação parada há 2 dias"),
    "descricao_vendas": (True, "atualizar a descrição do grupo de vendas com o ranking"),
    "relatorio_agenda": (True, "agenda da manhã"),
    "relatorio_planejamento": (True, "planejamento"),
    "relatorio_vespera_entregas": (True, "lembrete de véspera das entregas (vendedores e você)"),
    "relatorio_preparacao_grupo": (True, "resumo das pendências de preparação no grupo, 8h"),
    "relatorio_recall_grupo": (True, "resumo dos recalls abertos no grupo de recall, 8h"),
    "relatorio_agendados_dia_seguinte": (True, "resumo dos agendados do dia seguinte pra você e o Felipe, 18h"),
}
_cache: dict = {"t": 0.0, "rows": {}}


def _rows() -> dict:
    if time.time() - _cache["t"] > 60:
        try:
            _cache["rows"] = {r["nome"]: r for r in db.select("config_flags", {"select": "nome,ativo,valor"})}
            _cache["t"] = time.time()
        except Exception:
            pass
    return _cache["rows"]


def ativo(nome: str) -> bool:
    r = _rows().get(nome)
    return r["ativo"] if r and r.get("ativo") is not None else AUTOMACOES.get(nome, (True,))[0]


def valor(nome: str) -> str | None:
    r = _rows().get(nome)
    return r.get("valor") if r else None


def definir(nome: str, ativo: bool | None = None, valor: str | None = None) -> None:
    row = {"nome": nome}
    if ativo is not None:
        row["ativo"] = ativo
    if valor is not None:
        row["valor"] = valor
    db.upsert("config_flags", [row], on_conflict="nome")
    _cache["t"] = 0.0


def operacionais() -> dict[str, str]:
    """Números com acesso operacional (gerente): config 'acesso_operacional' = "5521...:Felipe, 5521...:Igor"."""
    out = {}
    for item in (valor("acesso_operacional") or "").split(","):
        num, _, nome = item.strip().partition(":")
        if num.strip():
            out[num.strip()] = nome.strip() or "gerente"
    return out


def situacao() -> list[dict]:
    return [{"automacao": n, "ligada": ativo(n), "o_que_faz": d} for n, (_, d) in AUTOMACOES.items()]
