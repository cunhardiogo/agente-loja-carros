"""Edição de mensagem que chega criptografada (secretEncryptedMessage, tipo MESSAGE_EDIT): decifra com o
segredo da mensagem original (messageContextInfo.messageSecret) e devolve no formato de edição que os grupos
já tratam (protocolMessage + editedMessage)."""
import base64
import hashlib
import hmac
import logging
import re

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

log = logging.getLogger("agente")

MESSAGE_EDIT = 2
_JID = re.compile(r"[\w.:-]+@(s\.whatsapp\.net|lid|g\.us|c\.us|broadcast)")


def _campos(buf: bytes):
    """Leitor mínimo de protobuf: (número do campo, tipo, valor)."""
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        campo, tipo = tag >> 3, tag & 7
        if tipo == 0:
            v, i = _varint(buf, i)
        elif tipo == 2:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif tipo == 1:
            v, i = buf[i:i + 8], i + 8
        elif tipo == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            return
        yield campo, tipo, v


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        b = buf[i]
        i += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, i
        s += 7


def _textos(buf: bytes, prof: int = 0) -> list[str]:
    out = []
    try:
        for _, tipo, v in _campos(buf):
            if tipo != 2:
                continue
            try:
                t = v.decode("utf-8")
                if t.isprintable() or "\n" in t:
                    out.append(t)
            except UnicodeDecodeError:
                pass
            if prof < 6:
                out += _textos(v, prof + 1)
    except Exception:
        pass
    return out


def _texto_editado(plano: bytes, alvo_id: str) -> str | None:
    """O texto novo é a maior string legível que não é jid, id nem lixo binário."""
    # jid sozinho fica de fora; texto com e-mail (ex.: resumo de venda) não
    cands = [t for t in _textos(plano) if t != alvo_id and not _JID.fullmatch(t.strip()) and len(t.strip()) >= 2]
    # o campo de fora também decodifica como texto, com 1-3 bytes de cabeçalho na frente: fica o de dentro
    cands = [t for t in cands if not any(o != t and t.endswith(o) and len(t) - len(o) <= 3 for o in cands)]
    return max(cands, key=len).strip() if cands else None


def _bytes(v) -> bytes:
    """O webhook pode mandar bytes como base64, como {"type": "Buffer", "data": [...]} ou como lista/dict de ints."""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return base64.b64decode(v)
    if isinstance(v, dict):
        if "data" in v:
            return bytes(v["data"])
        return bytes(v[k] for k in sorted(v, key=lambda x: int(x)))
    return bytes(v)


def decifrar(edicao: dict, original: dict) -> str | None:
    s = (edicao.get("message") or {}).get("secretEncryptedMessage") or {}
    segredo = ((original.get("message") or {}).get("messageContextInfo") or {}).get("messageSecret")
    if s.get("secretEncType") != MESSAGE_EDIT or not segredo:
        return None
    alvo = s["targetMessageKey"]["id"]
    chave_s = _bytes(segredo)
    iv, carga = _bytes(s["encIv"]), _bytes(s["encPayload"])
    k0 = hmac.new(b"\x00" * 32, chave_s, hashlib.sha256).digest()
    jids = [j for j in dict.fromkeys([original.get("key", {}).get("participant"), original.get("key", {}).get("participantAlt"),
                                      edicao.get("key", {}).get("participant"), edicao.get("key", {}).get("participantAlt"),
                                      (original.get("key") or {}).get("remoteJid")]) if j]
    for criador in jids:
        for editor in jids:
            dk = hmac.new(k0, f"{alvo}{criador}{editor}Message Edit".encode() + b"\x01", hashlib.sha256).digest()
            for aad in (None, f"{alvo}\x00{editor}".encode()):
                try:
                    return _texto_editado(AESGCM(dk).decrypt(iv, carga, aad), alvo)
                except Exception:
                    continue
    return None


def original_de(edicao: dict) -> dict | None:
    alvo = (((edicao.get("message") or {}).get("secretEncryptedMessage") or {}).get("targetMessageKey") or {})
    if not alvo.get("id"):
        return None
    with httpx.Client(base_url=settings.evolution_url.rstrip("/"), headers={"apikey": settings.evolution_apikey},
                      verify=settings.verify_ssl, timeout=30) as c:
        r = c.post(f"/chat/findMessages/{settings.evolution_instance}",
                   json={"where": {"key": {"id": alvo["id"]}}, "limit": 1, "page": 1})
        r.raise_for_status()
        recs = r.json()["messages"]["records"]
    return recs[0] if recs else None


def como_edicao(edicao: dict, original: dict | None = None) -> dict | None:
    """Dados no formato que os grupos tratam como edição, ou None se não deu pra decifrar."""
    try:
        original = original or original_de(edicao)
        texto = decifrar(edicao, original) if original else None
    except Exception:
        log.exception("falha decifrando edição")
        return None
    if not texto:
        return None
    alvo = edicao["message"]["secretEncryptedMessage"]["targetMessageKey"]
    return {**edicao, "message": {"protocolMessage": {"key": {**alvo, "id": alvo["id"]}, "type": 14,
                                                      "editedMessage": {"conversation": texto}}}}
