"""Painel local com dados reais: http://127.0.0.1:8766  (python -m scripts.painel_local [porta])
Serve mockups/painel-real.html e /api?periodo=mes com as métricas calculadas na hora (cache 5 min)."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import metricas

PAGINA = Path(__file__).resolve().parent.parent / "mockups" / "painel-real.html"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                corpo = json.dumps(metricas.em_cache(q.get("periodo", "mes"), q.get("inicio"), q.get("fim")),
                                   ensure_ascii=False, default=str).encode()
                tipo = "application/json"
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
                return
        else:
            corpo, tipo = PAGINA.read_bytes(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import sys
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    srv = ThreadingHTTPServer(("127.0.0.1", porta), H)
    print(f"painel em http://127.0.0.1:{porta}", flush=True)
    srv.serve_forever()
