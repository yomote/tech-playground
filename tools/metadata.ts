import { readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';
import { Demo, type DemoEntry } from '@playground/demo-schema';

export const root = fileURLToPath(new URL('../', import.meta.url));
export async function loadDemos(base = root): Promise<DemoEntry[]> {
  const entries = await readdir(path.join(base, 'demos'), { withFileTypes: true });
  const demos = await Promise.all(entries.filter(e => e.isDirectory() && !e.name.startsWith('.')).map(async entry => {
    const folder = path.join(base, 'demos', entry.name);
    const parsed = Demo.safeParse(parse(await readFile(path.join(folder, 'demo.yaml'), 'utf8')));
    if (!parsed.success) throw new Error(`${entry.name}/demo.yaml: ${parsed.error.message}`);
    if (parsed.data.id !== entry.name) throw new Error(`${entry.name}: id must equal directory name`);
    const readme = await readFile(path.join(folder, 'README.md'), 'utf8');
    return { ...parsed.data, readme };
  }));
  return demos.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt) || a.id.localeCompare(b.id));
}
export async function generateIndex() {
  const demos = await loadDemos();
  const output = path.join(root, 'apps/portal/src/generated');
  await mkdir(output, { recursive: true });
  await writeFile(path.join(output, 'demos.json'), JSON.stringify(demos, null, 2) + '\n');
  return demos.length;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const count = process.argv.includes('--validate') ? (await loadDemos()).length : await generateIndex();
  console.log(`Validated ${count} demos${process.argv.includes('--validate') ? '' : '; generated Portal index'}.`);
}
