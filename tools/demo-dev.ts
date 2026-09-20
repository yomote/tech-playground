import { spawn } from 'node:child_process';
import { loadDemos, root } from './metadata.js';
const id = process.argv[2];
const demos = await loadDemos();
if (!demos.some(d => d.id === id)) throw new Error(`Choose a demo: ${demos.map(d => d.id).join(', ')}`);
// pnpm's JS entry avoids shell interpolation and works on Windows.
const child = spawn(process.execPath, [process.env.npm_execpath!, '--dir', `${root}/demos/${id}`, 'dev'], { stdio: 'inherit' });
child.on('exit', code => { process.exitCode = code ?? 1; });
