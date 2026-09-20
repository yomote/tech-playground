"""Loopback-only Decision Workbench API; optional model dependencies load lazily."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import uuid

ROOT = Path(__file__).resolve().parent
MODEL_IDS = ('modernbert', 'gliclass', 'llm-adapter')
MODES = ('classification', 'criteria', 'sufficiency')
TASK_FIELDS = ('mode', 'language', 'text', 'question', 'criteria', 'options')
runs: dict[str, dict] = {}
run_lock = threading.Lock()


def fixtures() -> list[dict]:
    return json.loads((ROOT / 'fixtures.json').read_text(encoding='utf-8'))


def string_field(value, name: str, maximum: int, empty=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'{name}: provide a {"possibly empty " if empty else "nonempty "}string up to {maximum} characters')
    return value


def validate_task(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Each input must be an object')
    if data.get('mode') not in MODES or data.get('language') not in ('ja', 'en'):
        raise ValueError('Unknown mode or language')
    task = dict(mode=data['mode'], language=data['language'])
    for name, maximum, empty in [('text', 12000, False), ('question', 1500, False), ('criteria', 4000, True)]:
        task[name] = string_field(data.get(name, ''), name, maximum, empty)
    options = data.get('options')
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        raise ValueError('Provide 2 to 4 options')
    clean_options = []
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', option['id']):
            raise ValueError('Option IDs must contain 1–40 ASCII letters, digits, underscores or hyphens')
        clean_options.append(dict(id=option['id'], label=string_field(option.get('label'), 'option label', 160),
                                  description=string_field(option.get('description', ''), 'option description', 1000, True)))
    ids = [option['id'] for option in clean_options]
    if len(set(ids)) != len(ids):
        raise ValueError('Option IDs must be unique')
    task['options'] = clean_options
    expected = data.get('expectedOptionId')
    task['expectedOptionId'] = expected
    fixture_id = data.get('fixtureId')
    if fixture_id is not None:
        if not isinstance(fixture_id, str):
            raise ValueError('fixtureId must be a string')
        fixture = next((item for item in fixtures() if item['id'] == fixture_id), None)
        if fixture is None:
            raise ValueError('Unknown fixtureId')
        task['fixtureId'] = fixture_id
        # A reference answer applies only to the unchanged question and choices.
        # Editing the input cannot silently retain a fixture's expected answer.
        task['expectedOptionId'] = fixture['expectedOptionId'] if all(task[key] == fixture[key] for key in TASK_FIELDS) else None
    elif expected is not None and (not isinstance(expected, str) or expected not in ids):
        raise ValueError('Expected option must be one of the current options')
    return task


def validate_request(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Request must be an object')
    inputs = data.get('inputs')
    models = data.get('models')
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 12:
        raise ValueError('Provide 1 to 12 inputs')
    if not isinstance(models, list) or not 1 <= len(models) <= len(MODEL_IDS) or any(not isinstance(model, str) or model not in MODEL_IDS for model in models):
        raise ValueError('Select modernbert, gliclass and/or llm-adapter')
    if len(set(models)) != len(models):
        raise ValueError('Do not repeat a model')
    return {'inputs': [validate_task(task) for task in inputs], 'models': list(models)}


def execute_run(run_id: str):
    with run_lock:
        request = deepcopy(runs[run_id])
    try:
        from inference import infer
        # One worker and sequential model calls bound CPU/memory pressure.
        for model in request['models']:
            for index, task in enumerate(request['inputs']):
                try:
                    result = infer(model, task)
                except Exception as error:  # A model failure must not hide another model's result.
                    result = dict(model=model, modelName=model, status='error', selectedOptionId=None,
                                  scores=[], scoreKind='unavailable', loadMs=0, inferenceMs=0, prompt='', error=str(error))
                result['inputIndex'] = index
                expected = task['expectedOptionId']
                result['matchesExpected'] = (result.get('status') == 'ok' and result.get('selectedOptionId') == expected) if expected else None
                with run_lock:
                    runs[run_id]['results'].append(result)
        with run_lock:
            run = runs[run_id]
            run['status'] = 'completed' if any(item['status'] != 'error' for item in run['results']) else 'failed'
            if run['status'] == 'failed':
                run['error'] = 'Model execution failed. Check the per-model error and setup instructions.'
            run['finishedAt'] = datetime.now(timezone.utc).isoformat()
            completed = deepcopy(run)
        (ROOT / 'runs').mkdir(exist_ok=True)
        (ROOT / 'runs' / f'{run_id}.json').write_text(json.dumps(completed, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as error:
        with run_lock:
            runs[run_id].update(status='failed', error=str(error))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dist'), **kwargs)

    def respond_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/config':
            from inference import model_status
            return self.respond_json({'models': model_status(), 'fixtures': fixtures()})
        if self.path.startswith('/api/runs/'):
            with run_lock:
                run = deepcopy(runs.get(self.path.removeprefix('/api/runs/')))
            return self.respond_json(run if run else {'error': 'Run not found'}, 200 if run else 404)
        if self.path.startswith('/api/'):
            return self.respond_json({'error': 'Not found'}, 404)
        return super().do_GET()

    def do_POST(self):
        if self.headers.get('Origin') not in (None, 'http://127.0.0.1:5177', 'http://localhost:5177'):
            return self.respond_json({'error': 'Local origin required'}, 403)
        if self.path != '/api/runs':
            return self.respond_json({'error': 'Not found'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 * 1024:
                raise ValueError('Request size must be between 1 byte and 1 MiB')
            request = validate_request(json.loads(self.rfile.read(length)))
            with run_lock:
                if any(run['status'] == 'running' for run in runs.values()):
                    return self.respond_json({'error': 'Wait for the active experiment to finish'}, 409)
                while len(runs) >= 30:
                    del runs[next(iter(runs))]
                run_id = str(uuid.uuid4())
                run = dict(id=run_id, status='running', createdAt=datetime.now(timezone.utc).isoformat(), results=[], **request)
                runs[run_id] = run
                snapshot = deepcopy(run)
            threading.Thread(target=execute_run, args=(run_id,), daemon=True).start()
            self.respond_json(snapshot, 202)
        except (ValueError, TypeError) as error:
            self.respond_json({'error': str(error)}, 400)


if __name__ == '__main__':
    if not (ROOT / 'dist/index.html').exists():
        raise SystemExit('Build the UI first: pnpm --filter @playground/decision-workbench build')
    print('Decision Workbench: http://127.0.0.1:5177 (real local models / configured LLM API)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 5177), Handler).serve_forever()
