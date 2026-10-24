"""Server side of the dashboard tab: forwards /api/isis/<path> to isis-api with the bearer token.

Excerpt from a small self-built dashboard (a BaseHTTPRequestHandler). It relies on: self.send(code, body),
self.same_origin() (Origin header == own page), env(name) (os.environ, empty → None) and these globals:
"""
import json
import os
import re
import urllib.error
import urllib.request

ISIS_ON = os.environ.get("HL_ISIS") == "1"
ISIS_API = os.environ.get("HL_ISIS_API", "http://127.0.0.1:8091")
# only these paths reach isis-api (all read-only except chat, which only answers questions)
ISIS_GET = re.compile(r"status|courses|events|files|vault|node|search|file")


class Handler:  # in do_GET / do_POST: if path.startswith("/api/isis/"): return self.isis(method, path[10:], query)
    def isis(self, method, path, query):
        """Weiterleitung an die isis-API (Bearer-Token ISIS_API_TOKEN aus der .env, verlässt das Backend nie).
        /chat (NDJSON) und /file (PDFs) werden stückweise durchgereicht."""
        if not ISIS_ON:
            return self.send(404, '{"error":"ISIS in dieser Version abgeschaltet (HL_ISIS=1)"}'.encode())
        token = env("ISIS_API_TOKEN")
        if not token:
            return self.send(503, b'{"error":"ISIS_API_TOKEN fehlt in der .env"}')
        data = None
        if method == "POST":
            if path != "chat":
                return self.send(404, b"{}")
            if not self.same_origin() or not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(403, '{"error":"Herkunft nicht erlaubt"}'.encode())
            n = int(self.headers.get("Content-Length") or 0)
            if n > 40_000:
                return self.send(413, '{"error":"Anfrage zu groß"}'.encode())
            data = self.rfile.read(n) or b"{}"
        elif not ISIS_GET.fullmatch(path):
            return self.send(404, b"{}")
        url = f"{ISIS_API}/{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=600 if path == "chat" else 30)
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                detail = json.loads(body).get("error")
            except (ValueError, AttributeError):
                detail = None
            return self.send(e.code, json.dumps({"error": str(detail or f"isis-API: HTTP {e.code}")}, ensure_ascii=False).encode())
        except Exception as e:  # noqa: BLE001
            return self.send(502, json.dumps({"error": f"isis-API nicht erreichbar: {e}"}, ensure_ascii=False).encode())
        with resp:
            if path not in ("chat", "file"):
                return self.send(resp.status, resp.read())
            self.send_response(200)
            for h in ("Content-Type", "Content-Length", "Content-Disposition"):
                if resp.headers.get(h):
                    self.send_header(h, resp.headers[h])
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if path == "file":  # Dateien aus ISIS dürfen hier keine Skripte ausführen (HTML-Seiten, SVG)
                self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                while True:
                    b = resp.readline() if path == "chat" else resp.read(1 << 16)
                    if not b:
                        break
                    self.wfile.write(b)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
