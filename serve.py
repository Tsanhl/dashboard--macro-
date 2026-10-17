"""Loopback-only dashboard server with a single-flight refresh endpoint. No AI calls."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
from refresh import ROOT, Busy, atomic_json, refresh_lock, run


class Updates:
    def __init__(self, root):
        self.root = root
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.state = {'state':'idle'}
    def status(self):
        with self.lock: return dict(self.state)
    def start(self):
        with self.lock:
            if self.state['state'] == 'running': return dict(self.state)
            try:
                with refresh_lock(self.root): pass
            except Busy:
                return {'state':'external_running','message':'已有更新進行中；完成後可重新整理。'}
            self.state = {'state':'running','message':'正在抓取及核對資料；AI摘要不會改寫。'}
            threading.Thread(target=self.worker, daemon=True).start()
            return dict(self.state)
    def worker(self):
        try: result = run(root=self.root)
        except Exception as exc:
            # Detailed logs stay on disk; never expose local paths or source response bodies in the UI.
            result = {'state':'failed','message':'更新未完成，保留上一個有效版本。詳情見本地更新紀錄。'}
            atomic_json(self.root/'.refresh/last_error.json', {'error':str(exc)})
        with self.lock: self.state = result


class Handler(SimpleHTTPRequestHandler):
    def json_response(self, code, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def local_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
    def do_GET(self):
        if not self.local_host(): return self.json_response(403, {'error':'Invalid host'})
        path = urlsplit(self.path).path
        if path == '/api/update':
            return self.json_response(200, {**self.server.updates.status(),'token':self.server.updates.token})
        if path.startswith('/api/'): return self.json_response(404, {'error':'Unknown endpoint'})
        return super().do_GET()
    def do_POST(self):
        if not self.local_host(): return self.json_response(403, {'error':'Invalid host'})
        origin = self.headers.get('Origin')
        valid_origin = f'http://{self.headers.get("Host")}'
        if origin != valid_origin or not secrets.compare_digest(self.headers.get('X-Refresh-Token',''), self.server.updates.token):
            return self.json_response(403, {'error':'Same-origin refresh token required'})
        if self.headers.get('Content-Length','0') != '0':
            return self.json_response(400, {'error':'No request body allowed'})
        if urlsplit(self.path).path != '/api/update': return self.json_response(404, {'error':'Unknown endpoint'})
        return self.json_response(202, self.server.updates.start())
    def list_directory(self, path):
        self.send_error(404); return None
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        super().end_headers()


def make_server(port=8766, root=ROOT):
    server = ThreadingHTTPServer(('127.0.0.1',port), partial(Handler, directory=str(root/'site_private')))
    server.updates = Updates(root)
    return server


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--port',type=int,default=8766)
    args = ap.parse_args()
    server = make_server(args.port)
    print(f'Local dashboard: http://localhost:{server.server_port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()
