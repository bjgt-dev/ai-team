#!/usr/bin/env python3
# TEST ONLY - hub HTTP pull pilot, test DB only, echo-only, kill after test.
import hashlib, json, subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
Q = chr(39)
def psql(sql):
    r = subprocess.run(["docker","exec","ops-pg","psql","-U","postgres","-d","central_queue_test","-t","-A","-c",sql], capture_output=True, text=True, timeout=30)
    return r.stdout.strip(), r.stderr.strip(), r.returncode
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.endswith("/health"): self._send(200, {"ok": True, "db": "central_queue_test", "mode": "pilot-only"})
        else: self._send(404, {"error": "not found"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except Exception: return self._send(400, {"error": "bad json"})
        if self.path.endswith("/pull"):
            sql1 = "UPDATE central_tasks SET status=" + Q + "DISPATCHING" + Q + ", updated_at=now() WHERE task_id=(SELECT task_id FROM central_tasks WHERE status=" + Q + "READY" + Q + " AND payload->>" + Q + "type" + Q + "=" + Q + "script" + Q + " ORDER BY created_at LIMIT 1) RETURNING task_id;"
            out1, err1, rc1 = psql(sql1)
            if rc1 != 0 or not out1: return self._send(404, {"error": "no READY test task", "detail": err1[:200]})
            tid0 = out1.strip().splitlines()[0]
            sql = "UPDATE central_tasks SET status=" + Q + "CLAIMED" + Q + ", current_fencing_token=current_fencing_token+1, updated_at=now() WHERE task_id=" + Q + tid0 + Q + " RETURNING task_id, payload, current_fencing_token;"
            out, err, rc = psql(sql)
            if rc != 0 or not out: return self._send(404, {"error": "no READY test task", "detail": err[:200]})
            tid, payload_s, fence = out.split("|", 2)
            fence = fence.strip().splitlines()[0]
            psql("INSERT INTO central_leases (task_id, worker_id, fencing_token, expires_at) VALUES (" + Q + tid + Q + ", " + Q + "instabox-worker-1-http-pilot" + Q + ", " + fence + ", now() + interval " + Q + "600 seconds" + Q + ");")
            try: payload = json.loads(payload_s)
            except Exception: payload = {}
            return self._send(200, {"task_id": tid, "command": payload.get("command",""), "fencing_token": int(fence)})
        # Cutover51 W2.4: /complete route REMOVED (2026-11-10). The old handler
        # set DONE with verified_by='http-pull-pilot', which the production
        # DONE trigger now refuses for code/script tasks. Original code is
        # archived in pull-pilot-server-20261104.py.bak-cutover51-20261110
        # (kept, read-only). DONE is verifier-only via c2-verifier.
        if self.path.endswith("/complete"):
            return self._send(410, {"error": "route removed (cutover51 W2.4); DONE is verifier-only"})
        return self._send(404, {"error": "not found"})
HTTPServer(("127.0.0.1", 18911), H).serve_forever()
