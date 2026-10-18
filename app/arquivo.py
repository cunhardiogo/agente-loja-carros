"""Arquivo de TODAS as mensagens que o coletor vê (grupos e conversas monitoradas), pra o dono buscar
qualquer coisa por mensagem ("o que o Felipe falou do Civic?")."""
import logging
from datetime import timedelta

from . import datas, db
from .leads import _ascii, _dt, _iso, _quando, _stanza, _texto

log = logging.getLogger("agente")

_TIPOS = {"imageMessage": "foto", "videoMessage": "video", "audioMessage": "audio", "documentMessage": "documento",
          "documentWithCaptionMessage": "documento", "stickerMessage": "sticker"}
_nomes: dict = {}


def _autor_nome(data: dict) -> str | None:
    key = data.get("key") or {}
    if key.get("fromMe"):
        return "Diogo"
    autor = key.get("participant") or key.get("remoteJid")
    if not _nomes:
        for v in db.select("vendedores", {"select": "nome,lid,telefone"}):
            for k in (v.get("lid"), v.get("telefone") and f"{v['telefone']}@s.whatsapp.net"):
                if k:
                    _nomes[k] = v["nome"]
        from .preparacao_grupo import PESSOAS
        for k, n in PESSOAS.items():
            _nomes.setdefault(k, n)
    return _nomes.get(autor or "") or data.get("pushName")


def linha(data: dict, conversa: str | None) -> dict | None:
    key = data.get("key") or {}
    msg = data.get("message") or {}
    pm = msg.get("protocolMessage") or {}
    if pm.get("editedMessage") and key.get("id"):  # edição: guarda o texto novo (e marca como vista p/ repescagem)
        return {"message_id": key["id"], "jid": key.get("remoteJid"), "conversa": conversa,
                "autor": key.get("participant"), "autor_nome": _autor_nome(data), "from_me": bool(key.get("fromMe")),
                "tipo": "edicao", "texto": _texto(pm["editedMessage"]) or None,
                "citada": (pm.get("key") or {}).get("id"), "em": _iso(_quando(data))}
    if not key.get("id") or not msg or "protocolMessage" in msg or "reactionMessage" in msg:
        return None
    tipo = next((t for k, t in _TIPOS.items() if msg.get(k)), "texto" if _texto(msg) else "outro")
    doc = (msg.get("documentMessage") or (msg.get("documentWithCaptionMessage") or {}).get("message", {})
           .get("documentMessage") or {})
    texto = _texto(msg) or doc.get("fileName") or None
    return {"message_id": key["id"], "jid": key.get("remoteJid"), "conversa": conversa,
            "autor": key.get("participant") or (None if key.get("fromMe") else key.get("remoteJid")),
            "autor_nome": (_autor_nome(data) if key.get("fromMe") or "@g.us" in (key.get("remoteJid") or "")
                           else (conversa or "").replace("Conversa privada - ", "").split()[0] or _autor_nome(data)),
            "from_me": bool(key.get("fromMe")), "tipo": tipo, "texto": texto,
            "citada": _stanza(data), "em": _iso(_quando(data))}


def arquivar(data: dict, conversa: str | None) -> dict | None:
    try:
        row = linha(data, conversa)
        if row:
            db.upsert("mensagens", [row], on_conflict="message_id")
        return row
    except Exception:
        log.exception("falha arquivando mensagem")


def buscar(termo: str | None = None, conversa: str | None = None, pessoa: str | None = None, dias: int = 30,
           limite: int = 40) -> dict:
    """Busca por palavra (todas as palavras precisam aparecer), conversa/grupo e quem mandou."""
    desde = _iso(datas.agora() - timedelta(days=max(1, min(dias or 30, 365))))
    params = {"select": "message_id,conversa,autor_nome,tipo,texto,citada,em", "em": f"gte.{desde}",
              "order": "em.desc", "limit": "500"}
    palavras = [p for p in (termo or "").split() if len(p) >= 2][:4]
    if palavras:
        # vogal/ç viram curinga: "cambio" acha "câmbio" (o filtro exato sem acento vem depois)
        params["texto"] = db.ilike_simples("".join("_" if c in "aeioucáéíóúâêôãõç" else c
                                                   for c in max(palavras, key=len).lower()))
    a_conv, a_pessoa = _ascii(conversa or ""), _ascii(pessoa or "")
    if a_conv:  # "gerencia" acha "GERÊNCIA SB": casa sem acento pela lista de grupos/conversas
        jids = [g["jid"] for g in db.select("grupos", {"select": "jid,nome"}) if a_conv in _ascii(g["nome"])]
        if not jids:
            return {"erro": f"não conheço a conversa '{conversa}'",
                    "conversas": [g["nome"] for g in db.select("grupos", {"select": "nome"})]}
        params["jid"] = f"in.({','.join(jids)})"
    rows = db.select("mensagens", params)
    rows = [r for r in rows
            if all(_ascii(p) in _ascii(r.get("texto") or "") for p in palavras)
            and (not a_pessoa or a_pessoa in _ascii(r.get("autor_nome") or ""))]
    citadas = {r["citada"] for r in rows[:limite] if r.get("citada")}
    orig = {c["message_id"]: c for c in db.select("mensagens", {
        "select": "message_id,autor_nome,texto", "message_id": f"in.({','.join(citadas)})"})} if citadas else {}
    out = []
    for r in rows[:limite]:
        q = orig.get(r.get("citada") or "")
        out.append({"quando": f"{_dt(r['em']).astimezone(datas.TZ):%d/%m %H:%M}", "onde": r.get("conversa"),
                    "quem": r.get("autor_nome"), "tipo": r["tipo"], "texto": (r.get("texto") or "")[:600],
                    **({"respondendo_a": f"{q.get('autor_nome')}: {(q.get('texto') or '')[:150]}"} if q else {})})
    return {"encontradas": len(rows), "mostrando": len(out), "mensagens": out}
