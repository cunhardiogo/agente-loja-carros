"""Grupo FOTOS: "novidades" com placa criam o cartão em "Carro em trânsito"; "pronto para foto" move o cartão do carro para "Preparação / Foto"; quando o Caio posta as
fotos (álbum + nome do carro), o cartão vai para "Estoque SB!". Só mexe em cartão que está no caminho da
preparação — Vendidos, Finalizado e Recall ficam onde estão."""
import json
import logging
import re

from . import db, evolution, llm, trello
from .config import settings
from .leads import _ascii, _quando, _texto

log = logging.getLogger("agente")

CAIO = "217982575837352@lid"
PREP_FOTO, ESTOQUE = "preparacao / foto", "estoque sb!"
_ultimo_pronto: dict[str, float] = {}  # autor → quando avisou "pronto" (fotos sem legenda logo depois também contam)
PODE_MEXER = {"estoque sb!", "carro em transito", "pintura", "mecanico", "preparacao / foto"}

PROMPT = """Você lê mensagens do grupo de FOTOS de uma loja de carros. O Caio é o fotógrafo: depois de postar as fotos
de um carro ele manda o nome do carro (ex "Dolphin 2024", "Jimny 2019", "Polo Comfortline 1.0 TSI 2018").
Os outros avisam quando um carro está pronto para ser fotografado (ex "Jimny pronto para fotos", "King e IX35 prontos para fotos").
Responda SOMENTE um JSON {"acao": "pronto_para_foto" | "fotos_feitas" | "outro", "carros": ["..."]}:
- pronto_para_foto: avisa que carro(s) está(ão) pronto(s) pra foto. Se o texto não diz o carro, identifique pela FOTO
  (marca, modelo, ano aproximado, cor e placa se aparecer) — ex "Fiat Pulse branco", "Honda Civic RJR-0E26".
- fotos_feitas: o Caio mandando só o nome do carro que acabou de fotografar.
- outro: conversa, pergunta, preço, combinação.
"carros": um texto por carro, como escrito (ou como identificado na foto)."""


PROMPT_NOVIDADES = """Mensagem de "novidades" (carros novos chegando na loja). Liste cada carro citado.
Responda SOMENTE JSON {"carros": [{"carro": "marca e modelo/versão", "ano": 2020 ou null, "placa": "ABC1D23" ou null}]}.
Se a mensagem não cita carro nenhum (ex. só "NOVIDADES CHEGANDO"), devolva {"carros": []}."""
TRANSITO = "carro em transito"
NOVIDADE_MIN = 30
_ultima_novidade: dict[str, float] = {}  # autor → quando postou o cabeçalho "NOVIDADE CHEGANDO"


def _placa_autocerto(carro: str | None, ano) -> str | None:
    """Carro sem placa na novidade: procura no estoque do Autocerto pelo modelo e ano (só se achar um)."""
    from . import autocerto
    from .leads import _ascii
    if not carro or not autocerto.configurado():
        return None
    toks = [t for t in re.split(r"[^a-z0-9]+", _ascii(carro)) if len(t) >= 2 and not re.fullmatch(r"(19|20)\d{2}", t)]
    achados = [i for i in autocerto.estoque()
               if toks and all(t in _ascii(i["carro"]) for t in toks[:2]) and (not ano or str(i.get("ano")) == str(ano))]
    return achados[0]["placa"] if len(achados) == 1 and achados[0].get("placa") else None


def novidades(texto: str, mid: str) -> dict:
    """'Novidades' com carro e placa: cria o cartão em "Carro em trânsito" (se a placa ainda não está no quadro).
    Carro sem placa: avisa o dono."""
    from .vendas_grupo import placa_norm
    if db.insert_lock("prep_msgs", {"message_id": f"novidade:{mid}"}) is None:
        return {"ignored": "duplicada"}
    try:
        resp = llm._client.chat.completions.create(
            model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": PROMPT_NOVIDADES}, {"role": "user", "content": texto}])
        carros = json.loads(resp.choices[0].message.content or "{}").get("carros") or []
    except Exception:
        log.exception("falha lendo novidades")
        db.delete("prep_msgs", {"message_id": f"eq.novidade:{mid}"})
        return {"erro": "novidades"}
    if not carros:
        return {"ignored": "novidade_sem_carro"}
    criados, sem_placa = [], []
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c)
        no_quadro = {trello._placa(cd["name"]) for cd in cards}
        transito = next(k for k, v in listas.items() if v == TRANSITO)
        for car in carros:
            placa = placa_norm(car.get("placa")) or placa_norm(_placa_autocerto(car.get("carro"), car.get("ano")))
            if not placa:  # cria com o modelo; a placa entra depois (procura no Autocerto a cada 2 dias)
                nome = trello.nome_cartao({"modelo": car.get("carro"), "ano": car.get("ano")}, None)
                if any(cd["name"] == nome and listas.get(cd["idList"]) == TRANSITO for cd in cards):
                    continue
                c.post("/cards", params={"idList": transito, "pos": "top", "name": nome}).raise_for_status()
                cards.append({"name": nome, "idList": transito})
                sem_placa.append(nome)
                continue
            if placa in no_quadro:
                continue
            nome = trello.nome_cartao({"modelo": car.get("carro"), "ano": car.get("ano"), "placa": placa}, None)
            c.post("/cards", params={"idList": transito, "pos": "top", "name": nome}).raise_for_status()
            no_quadro.add(placa)
            criados.append(nome)
    if criados or sem_placa:
        try:
            evolution.enviar_texto(settings.meu_numero, "🚗 *Novidades no grupo de Fotos*"
                                   + ("\nCriei em Carro em trânsito: " + ", ".join(criados) if criados else "")
                                   + ("\nCriei sem placa (procuro no Autocerto a cada 2 dias): " + ", ".join(sem_placa)
                                      if sem_placa else ""))
        except Exception:
            log.exception("falha avisando novidades")
    return {"criados": criados, "sem_placa": sem_placa}


def completar_placas() -> list[str]:
    """Cartões sem placa (fora do FINALIZADO): acha a placa no Autocerto pelo modelo e ano e põe no nome do cartão.
    Só quando o Autocerto tem UM carro que bate e a placa ainda não está em outro cartão."""
    from . import autocerto
    from .leads import _ascii
    if not (trello.configurado() and autocerto.configurado()):
        return []
    estoque = autocerto.estoque(forcar=True)
    feitos = []
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c)
        usadas = {trello._placa(cd["name"]) for cd in cards if trello._placa(cd["name"])}
        for cd in cards:
            if listas.get(cd["idList"]) == trello.FINALIZADO or trello._placa(cd["name"]):
                continue
            base = re.sub(r"\s+-\s+[A-Za-zÀ-ú]+$", "", cd["name"])  # tira " - Vendedor"
            ano = re.search(r"\b(19|20)\d{2}\b", base)
            toks = [t for t in re.split(r"[^a-z0-9]+", _ascii(base)) if len(t) >= 2 and not re.fullmatch(r"(19|20)\d{2}", t)]
            marca = {"fiat", "vw", "volkswagen", "gwm", "renault", "chevrolet", "ford", "honda", "toyota", "hyundai",
                     "nissan", "jeep", "mitsubishi", "mit", "byd", "peugeot", "citroen", "kia", "chery", "bmw", "yamaha"}
            modelo = [t for t in toks if t not in marca]
            if not modelo:
                continue
            achados = [i for i in estoque if all(t in _ascii(i["carro"]) for t in modelo[:2])
                       and (not ano or str(i.get("ano")) == ano.group())
                       and (i.get("placa") or "").replace("-", "").upper() not in usadas]
            if len(achados) != 1 or not achados[0].get("placa"):
                continue
            p = achados[0]["placa"].replace("-", "").upper()
            novo = cd["name"].replace(base, f"{base} ({p[:3]}-{p[3:]})", 1)
            c.put(f"/cards/{cd['id']}", params={"name": novo}).raise_for_status()
            usadas.add(p)
            feitos.append(novo)
    if feitos:
        try:
            evolution.enviar_texto(settings.meu_numero, "🔎 Achei no Autocerto a placa de cartões que estavam sem:\n"
                                   + "\n".join(f"• {n}" for n in feitos))
        except Exception:
            log.exception("falha avisando placas completadas")
    return feitos


def classificar(texto: str, autor_caio: bool, imagem: tuple | None) -> dict:
    conteudo: list = [{"type": "text", "text": f"Autor: {'Caio (fotógrafo)' if autor_caio else 'equipe'}\n"
                                               f"Mensagem: {texto or '(só foto)'}"}]
    if imagem and imagem[0]:
        conteudo.append({"type": "image_url", "image_url": {"url": f"data:{imagem[1] or 'image/jpeg'};base64,{imagem[0]}"}})
    resp = llm._client.chat.completions.create(
        model=settings.openai_model_extracao, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": conteudo}])
    return json.loads(resp.choices[0].message.content or "{}")


def processar(data: dict, instancia: str, apikey: str, aplicar: bool = True) -> dict:
    key = data.get("key") or {}
    msg = data.get("message") or {}
    texto = _texto(msg)
    autor_caio = key.get("participant") == CAIO
    a = _ascii(texto)
    tem_imagem = bool(msg.get("imageMessage"))
    autor = key.get("participant") or ("eu" if key.get("fromMe") else "")
    quando = _quando(data).timestamp()
    if "novidade" in a:  # carros novos chegando: cartão em "Carro em trânsito"
        _ultima_novidade[autor] = quando
        return novidades(texto, key.get("id")) if aplicar else {"novidades": texto[:60]}
    if texto and not autor_caio and len(texto) > 15 and quando - _ultima_novidade.get(autor, 0) <= NOVIDADE_MIN * 60:
        return novidades(texto, key.get("id")) if aplicar else {"novidades": texto[:60]}  # carros logo depois do cabeçalho
    if "pront" in a and not autor_caio:
        _ultimo_pronto[autor] = quando
    foto_apos_pronto = tem_imagem and not texto and not autor_caio and quando - _ultimo_pronto.get(autor, 0) <= 600
    if foto_apos_pronto:
        texto, a = "Pronto para foto", "pronto para foto"
    # filtro barato antes da IA: só texto do Caio, ou aviso de "pronto"
    if not texto or (not autor_caio and "pront" not in a):
        return {"ignored": "nao_interessa"}
    if autor_caio and len(texto) > 60:
        return {"ignored": "conversa"}
    if db.insert_lock("prep_msgs", {"message_id": f"fotos:{key.get('id')}"}) is None:
        return {"ignored": "duplicada"}
    imagem = None
    if tem_imagem and "pront" in a:
        try:
            imagem = evolution.get_media_base64(instancia, apikey, data)
        except Exception:
            imagem = None
    try:
        r = classificar(texto, autor_caio, imagem)
    except Exception:
        log.exception("falha classificando mensagem de fotos")
        db.delete("prep_msgs", {"message_id": f"eq.fotos:{key.get('id')}"})  # repescagem tenta de novo
        return {"erro": "classificacao"}
    acao, carros = r.get("acao"), [c for c in (r.get("carros") or []) if c]
    if acao not in ("pronto_para_foto", "fotos_feitas") or not carros:
        return {"ignored": acao or "outro"}
    destino = PREP_FOTO if acao == "pronto_para_foto" else ESTOQUE
    resultado = {"acao": acao, "movidos": [], "nao_achados": [], "fora_do_caminho": []}
    with trello._cliente() as c:
        cards, listas = trello.cartoes(c)
        por_nome = {v: k for k, v in listas.items()}
        # na dúvida (dois Dolphin 2024), vale o cartão que está na lista de onde ele deveria sair
        origem = [cd for cd in cards if (listas.get(cd["idList"]) == PREP_FOTO) == (destino == ESTOQUE)
                  and listas.get(cd["idList"]) in PODE_MEXER]
        for carro in carros:
            card = trello.achar_cartao(carro, cards, listas) or trello.achar_cartao(carro, origem, listas)
            if not card:
                resultado["nao_achados"].append(carro)
                continue
            atual = listas.get(card["idList"])
            if atual == destino:
                continue
            if atual not in PODE_MEXER:
                resultado["fora_do_caminho"].append(f"{card['name']} ({atual})")
                continue
            if aplicar:
                c.put(f"/cards/{card['id']}", params={"idList": por_nome[destino], "pos": "top"}).raise_for_status()
            resultado["movidos"].append(f"{card['name']}: {atual} → {destino}")
    if aplicar and resultado["nao_achados"]:
        try:
            evolution.enviar_texto(settings.meu_numero,
                                   f"📸 Grupo de fotos — não achei no Trello o cartão de: "
                                   f"{', '.join(resultado['nao_achados'])} "
                                   f"({'pronto para foto' if acao == 'pronto_para_foto' else 'fotos feitas'}). "
                                   f"Move à mão ou me diz qual é.")
        except Exception:
            log.exception("falha avisando cartão não achado")
    return resultado
