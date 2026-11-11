import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
DATA = "/home/ubuntu/ai-team/advisor-data"
WT = os.environ.get("ADVISOR_WRITE_TOKEN", "")
RT = os.environ.get("ADVISOR_READ_TOKEN", "")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health": return self._send(200, {"ok": True})
        if u.path == "/read":
            if self.headers.get("X-Token") not in (RT, WT): return self._send(403, {"error": "forbidden"})
            key = parse_qs(u.query).get("key", [""])[0]
            p = os.path.join(DATA, key + ".json")
            if not os.path.exists(p): return self._send(404, {"error": "not_found"})
            return self._send(200, json.load(open(p)))
        return self._send(404, {"error": "not_found"})
    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/write":
            if self.headers.get("X-Token") != WT: return self._send(403, {"error": "write_forbidden"})
            n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
            key = body.get("key", ""); 
            if not key or "/" in key or ".." in key: return self._send(400, {"error": "bad_key"})
            json.dump(body.get("value", {}), open(os.path.join(DATA, key + ".json"), "w"))
            return self._send(200, {"ok": True, "key": key})
        return self._send(404, {"error": "not_found"})
HTTPServer(("127.0.0.1", 18901), H).serve_forever()
