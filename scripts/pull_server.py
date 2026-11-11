#!/usr/bin/env python3
# Minimal hub pull endpoint for worker_pull.sh. Serves one task file at a time from pull-queue/, stores receipts in state/worker-receipts/.
import http.server, pathlib, datetime, json
BASE=pathlib.Path('/home/ubuntu/ai-team')
Q=BASE/'pull-queue'; Q.mkdir(exist_ok=True)
L=BASE/'pull-queue-leased'; L.mkdir(exist_ok=True)
R=BASE/'state/worker-receipts'; R.mkdir(exist_ok=True)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/pull_task':
            import time as _time
            for _f in list(L.glob('*.sh')):
                if _time.time() - _f.stat().st_mtime > 1800:
                    _f.rename(Q / _f.name)
            files=sorted(Q.glob('*.sh'))
            body=files[0].read_text() if files else 'none'
            if files: files[0].rename(L / files[0].name)
            self.send_response(200); self.end_headers(); self.wfile.write(body.encode())
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path in ('/receipt', '/ack'):
            n=int(self.headers.get('Content-Length',0)); data=self.rfile.read(n)
            fn=R/f"pull-receipt-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            fn.write_bytes(data)
            leased=sorted(L.glob('*.sh'))
            if leased: leased[0].unlink()
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(('0.0.0.0', 18099), H).serve_forever()
