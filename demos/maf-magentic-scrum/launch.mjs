import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
const venv = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
const child = spawn(existsSync(venv) ? venv : 'python', ['server.py'], { stdio: 'inherit' });
child.on('error', error => { console.error('Python 3.11+ required:', error.message); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code ?? 1; });
