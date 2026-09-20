import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const cwd = fileURLToPath(new URL('.', import.meta.url));
const venv = fileURLToPath(new URL(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python', import.meta.url));
const child = spawn(existsSync(venv) ? venv : 'python', ['server.py'], { cwd, stdio: 'inherit' });
child.on('error', error => { console.error('Python 3.11+ required:', error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
