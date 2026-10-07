"""Leva para os leads de um mês o desfecho registrado na planilha antiga de agendamentos e
cria como lead quem só está na planilha. Uso: python -m scripts.importar_planilha_antiga 2026-10 [--aplicar]"""
import sys
from difflib import SequenceMatcher

from app import db
from app.leads import _ascii

STATUS_PLANILHA = {"realizado": "compareceu", "vendido": "vendido", "cancelado": "cancelado",
                   "reservado": "reservado", "nao veio": "nao_veio", "faltou": "nao_veio"}
# desfecho explícito vindo do grupo não é sobrescrito
STATUS_GRUPO_FINAL = {"vendido", "nao_veio", "cancelado", "compareceu", "reservado"}


def _primeiro(n: str | None) -> str:
    p = _ascii(n).split()
    return p[0] if p else ""


def _parecido(a: str, b: str) -> bool:
    return a == b or (len(a) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8)


def _dia(iso: str | None) -> int:
    return int(iso[8:10]) if iso else 0


def casar(leads: list[dict], plan: list[dict]) -> tuple[list, list]:
    pares, usados = [], set()
    # 1º mesma data e nome igual/parecido; 2º nome igual com até 3 dias de diferença
    for tolerancia, exige_igual in ((0, False), (3, True)):
        for a in plan:
            if a["id"] in usados:
                continue
            na = _primeiro(a["cliente_nome"])
            cands = [l for l in leads if l["id"] not in {p[0]["id"] for p in pares}
                     and abs(_dia(l["data_agendada"]) - _dia(a["data_agendada"])) <= tolerancia
                     and (na == _primeiro(l["cliente_nome"]) if exige_igual else _parecido(na, _primeiro(l["cliente_nome"])))]
            if cands:
                pares.append((min(cands, key=lambda l: abs(_dia(l["data_agendada"]) - _dia(a["data_agendada"]))), a))
                usados.add(a["id"])
    return pares, [a for a in plan if a["id"] not in usados]


def main(mes: str, aplicar: bool) -> None:
    ini = f"{mes}-01"
    leads = [l for l in db.select_all("leads", {"select": "*", "removido": "eq.false"})
             if (l["data_agendada"] or "") >= ini and (l["data_agendada"] or "")[:7] == mes]
    plan = [a for a in db.select_all("agendamentos", {"select": "*", "origem": "eq.planilha"})
            if (a["data_agendada"] or "")[:7] == mes]
    vend = {v["id"]: v["nome"] for v in db.select("vendedores", {"select": "id,nome"})}
    pares, so_planilha = casar(leads, plan)

    print(f"leads {len(leads)} | planilha {len(plan)} | casados {len(pares)} | só planilha {len(so_planilha)}")
    for l, a in pares:
        novo = STATUS_PLANILHA.get(_ascii(a.get("resultado")).strip())
        muda = novo and l["status"] not in STATUS_GRUPO_FINAL
        print(f"  {'*' if muda else ' '} {l['cliente_nome'].strip():<18} grupo {l['data_agendada'][8:]}/{l['status']:<18} "
              f"planilha {a['cliente_nome'].strip()} {a['data_agendada'][8:10]}/{a.get('resultado')}")
        if muda and aplicar:
            db.update("leads", {"status": novo, "status_em": a["updated_at"]}, {"id": f"eq.{l['id']}"})
            db.insert_lock("lead_eventos", {"lead_id": l["id"], "message_id": f"planilha:{a['id']}",
                                            "em": a["updated_at"], "autor": "planilha antiga",
                                            "texto": a.get("resultado"), "status": novo})
    for a in so_planilha:
        partes = [p.strip() for p in (a.get("observacoes") or "").split("·")]
        veiculo = partes[2] if len(partes) > 2 else None
        canal = partes[3] if len(partes) > 3 else None
        status = STATUS_PLANILHA.get(_ascii(a.get("resultado")).strip(), "aguardando_retorno")
        print(f"  + {a['cliente_nome']} {a['data_agendada'][8:10]}/{a.get('resultado')} {veiculo} {canal}")
        if aplicar:
            db.insert_lock("leads", {
                "message_id": f"planilha:{a['id']}", "recebido_em": a["created_at"], "tipo": "visita",
                "fila": "planilha antiga", "cliente_nome": a["cliente_nome"], "telefone": a.get("telefone"),
                "data_agendada": a["data_agendada"][:10], "veiculo": veiculo, "canal": canal,
                "vendedor_id": a.get("vendedor_id"), "vendedor_nome": vend.get(a.get("vendedor_id")),
                "status": status, "status_em": a["updated_at"], "historico": True,
                "observacao": "Importado da planilha antiga (não passou pelo grupo)"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-10", "--aplicar" in sys.argv)
