"""Local stdlib HTTP API. No framework or credentials needed for mock mode."""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading
from pathlib import Path
from runner import Run, ROOT

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass

runs = {}
lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dist'), **kwargs)

    def respond_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/api/runs/'):
            run = runs.get(self.path.split('/')[-1])
            self.respond_json(run.snapshot() if run else {'error': 'Run not found'}, 200 if run else 404)
        else:
            super().do_GET()

    def do_POST(self):
        try:
            if self.headers.get('Origin') not in (None, 'http://localhost:5175', 'http://127.0.0.1:5175'):
                return self.respond_json({'error': 'Local origin required'}, 403)
            length = int(self.headers.get('Content-Length', '0'))
            if length > 10000:
                raise ValueError('Request too large')
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/runs':
                if data.get('mode') not in ('mock', 'live') or data.get('scenario') not in ('happy-path', 'review-failure', 'test-failure'):
                    raise ValueError('Invalid mode or scenario')
                with lock:
                    if any(run.status == 'running' for run in runs.values()):
                        return self.respond_json({'error': 'Finish or reject the active run first'}, 409)
                    run = Run(data['mode'], data['scenario'], bool(data.get('approval')))
                    runs[run.id] = run
                threading.Thread(target=run.execute, daemon=True).start()
                self.respond_json(run.snapshot(), 201)
            elif self.path.startswith('/api/approval/'):
                run = runs.get(self.path.split('/')[-1])
                if not run:
                    return self.respond_json({'error': 'Run not found'}, 404)
                run.respond(data.get('decision'))
                self.respond_json({'ok': True})
            else:
                self.respond_json({'error': 'Not found'}, 404)
        except (ValueError, KeyError) as error:
            self.respond_json({'error': str(error)}, 400)


if __name__ == '__main__':
    if not (Path(ROOT) / 'dist/index.html').exists():
        raise SystemExit('Build the UI first: pnpm --filter @playground/maf-magentic-scrum build')
    print('Magentic Scrum: http://localhost:5175 (mock by default)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 5175), Handler).serve_forever()
