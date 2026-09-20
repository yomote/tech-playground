import { input, select } from '@inquirer/prompts';
import { cp, mkdir, writeFile, rename, rm, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { stringify } from 'yaml';
import { Demo } from '@playground/demo-schema';
import { root } from '../metadata.js';

export const templates = ['blank', 'react-vite', 'node', 'python'] as const;
export function slugify(name: string) { return name.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
export async function createDemo(options: { name: string; summary: string; template: string; tags: string[]; id?: string }, base = root) {
  if (!templates.includes(options.template as typeof templates[number])) throw new Error('Unknown template');
  const id = options.id || slugify(options.name);
  const today = new Date().toISOString().slice(0, 10);
  const demo = Demo.parse({ id, title: options.name, status: 'idea', summary: options.summary, goal: options.summary,
    questions: [], tags: options.tags, stack: options.template === 'blank' ? [] : options.template === 'react-vite' ? ['react', 'vite', 'typescript'] : [options.template],
    findings: [], references: [], createdAt: today, updatedAt: today });
  const target = path.join(base, 'demos', id);
  await mkdir(path.dirname(target), { recursive: true });
  // Exclusive directory reservation: never merge into an existing demo.
  await mkdir(target);
  try {
    const source = path.join(root, 'templates', options.template);
    for (const entry of await readdir(source)) {
      await cp(path.join(source, entry), path.join(target, entry), { recursive: true, force: false, errorOnExist: true });
    }
    const templatePackage = path.join(target, 'package.template.json');
    if (['node', 'react-vite'].includes(options.template)) {
      const { readFile } = await import('node:fs/promises');
      const pkg = JSON.parse(await readFile(templatePackage, 'utf8'));
      pkg.name = `@playground/${id}`;
      await writeFile(templatePackage, JSON.stringify(pkg, null, 2) + '\n');
      await rename(templatePackage, path.join(target, 'package.json'));
    }
    await writeFile(path.join(target, 'demo.yaml'), stringify(demo), { flag: 'wx' });
    const run = options.template === 'python' ? `cd demos/${id}\npython main.py` : options.template === 'blank' ? '# Add the runtime and commands for this experiment.' : `pnpm install\npnpm --filter @playground/${id} dev`;
    await writeFile(path.join(target, 'README.md'), `# ${demo.title}\n\n## What I want to understand\n${demo.goal}\n\n## Architecture\nTemplate: ${options.template}. Keep this demo self-contained.\n\n## Run\n\n\`\`\`sh\n${run}\n\`\`\`\n\n## Things to try\n- Record a concrete question in demo.yaml.\n\n## Findings\nNo findings yet. Add observations to demo.yaml so Portal search can find them.\n\n## References\nAdd primary sources to demo.yaml.\n`, { flag: 'wx' });
  } catch (error) {
    // Only the directory exclusively created by this invocation is removed.
    if (path.dirname(path.resolve(target)) !== path.resolve(base, 'demos')) throw error;
    await rm(target, { recursive: true, force: true });
    throw error;
  }
  return target;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const name = await input({ message: 'Demo name?', required: true });
    const id = await input({ message: 'Demo id? (lowercase kebab-case)', default: slugify(name), required: true });
    const summary = await input({ message: 'What do you want to try?', required: true });
    const template = await select({ message: 'Initial template?', choices: templates.map(value => ({ value, name: value })) });
    const tags = (await input({ message: 'Tags? (comma separated)' })).split(',').map(s => s.trim()).filter(Boolean);
    console.log(`Created ${await createDemo({ name, id, summary, template, tags })}\nRun pnpm install, then pnpm dev.`);
  } catch (error) { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; }
}
