"""Autoriza o agente a escrever no Google Sheets com um cliente OAuth (uma vez).
Uso: GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python -m scripts.google_oauth
Abre http://localhost:3001/callback, troca o código pelo refresh token e grava no .env."""
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

REDIRECT = "http://localhost:3001/callback"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
CID, SECRET = os.environ["GOOGLE_CLIENT_ID"], os.environ["GOOGLE_CLIENT_SECRET"]


def _gravar_env(chave: str, valor: str) -> None:
    linhas = [l for l in open(".env", encoding="utf-8").read().splitlines() if not l.startswith(chave + "=")]
    linhas.append(f"{chave}={valor}")
    open(".env", "w", encoding="utf-8").write("\n".join(linhas) + "\n")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in q:
            self.send_response(400); self.end_headers()
            self.wfile.write(f"Erro: {q.get('error')}".encode())
            return
        r = httpx.post("https://oauth2.googleapis.com/token", verify=False, data={
            "code": q["code"][0], "client_id": CID, "client_secret": SECRET,
            "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
        tok = r.json()
        ok = "refresh_token" in tok
        if ok:
            _gravar_env("GOOGLE_CLIENT_ID", CID)
            _gravar_env("GOOGLE_CLIENT_SECRET", SECRET)
            _gravar_env("GOOGLE_REFRESH_TOKEN", tok["refresh_token"])
        print("RESULTADO:", "OK refresh token gravado no .env" if ok else tok)
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(("<h2>Pronto! Pode fechar esta aba.</h2>" if ok else f"<h2>Falhou: {tok}</h2>").encode())
        self.server.feito = True

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": CID, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent"})
    print("LINK:", url, flush=True)
    s = HTTPServer(("localhost", 3001), H)
    s.feito = False
    while not s.feito:
        s.handle_request()
