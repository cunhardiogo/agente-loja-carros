"""Padroniza os nomes dos cartões do quadro ESTOQUE no formato 'Marca Modelo Ano (XXX-XXXX)' (+ ' - Vendedor'
quando o cartão já tinha), só com o que está escrito no próprio cartão — nada é inventado.
Uso: python -m scripts.padronizar_trello [--aplicar]"""
import json
import sys

from app import llm, trello
from app.config import settings

PROMPT = """Você recebe nomes de cartões de carros de uma loja. Reescreva cada um no formato exato:
"Marca Modelo Ano (XXX-XXXX)" e, se o nome terminar com o nome de um vendedor (ex: "- Vinicius", "- YAN"), mantenha " - Vendedor" no fim com só a primeira letra maiúscula.
Regras:
- Marca e Modelo com iniciais maiúsculas (siglas como BYD, GTI, XEI, DS3, IX35, CB 300R em maiúsculas). Pode deduzir a MARCA pelo modelo (Kicks→Nissan, Onix→Chevrolet, Fit→Honda, Jimny→Suzuki, Xmax→Yamaha...).
- Modelo sem versão, motor, câmbio, km, cor ou preço (ex: "CITROEN C3 2017 1.6 TENDANCE 16V" → "Citroen C3 2017"). Mantenha nomes de modelo compostos (Pajero Sport, Song Pro, Eclipse Cross, Arrizo 6 Pro, Dolphin Mini, Corolla Altis).
- Ano: o ano que estiver no nome. Se não houver, sem ano.
- Placa: SÓ se estiver escrita no nome, formatada XXX-XXXX em maiúsculas. NUNCA invente placa.
- Se não for um carro (ex: tarefa, anotação), devolva o nome original sem mudar.
Responda SOMENTE um JSON {"nomes": [...]} na mesma ordem e quantidade da entrada."""


def plano() -> list[tuple]:
    with trello._cliente() as c:
        cards = c.get(f"/boards/{settings.trello_board}/cards", params={"fields": "name,idList"}).json()
        listas = {l["id"]: l["name"] for l in c.get(f"/boards/{settings.trello_board}/lists").json()}
    out = []
    for i in range(0, len(cards), 40):
        lote = cards[i:i + 40]
        resp = llm._client.chat.completions.create(
            model=settings.openai_model_consulta, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": PROMPT},
                      {"role": "user", "content": json.dumps([cd["name"] for cd in lote], ensure_ascii=False)}])
        nomes = json.loads(resp.choices[0].message.content)["nomes"]
        if len(nomes) != len(lote):
            raise RuntimeError("quantidade de nomes diferente da entrada")
        out += [(listas.get(cd["idList"]), cd["name"], novo.strip(), cd["id"]) for cd, novo in zip(lote, nomes)]
    return out


if __name__ == "__main__":
    linhas = plano()
    for lista, atual, novo, _ in linhas:
        if novo != atual.strip():
            print(f"[{lista}] {atual}  →  {novo}")
    if "--aplicar" in sys.argv:
        with trello._cliente() as c:
            for _, atual, novo, cid in linhas:
                if novo != atual.strip():
                    c.put(f"/cards/{cid}", params={"name": novo}).raise_for_status()
        print("aplicado")
