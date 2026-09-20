import { useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, Route, Routes, useParams, useSearchParams } from 'react-router-dom';
import { Box, Stack, Typography, Chip, Button, TextField, MenuItem, Paper, Card, CardActionArea, Divider, InputAdornment } from '@mui/material';
import { LabTheme, LabShell, LabIcon, Panel, Eyebrow } from '@playground/ui';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Demo, DemoStatus, searchDemos, type DemoEntry } from '@playground/demo-schema';
import data from './generated/demos.json';
import './style.css';

const demos: DemoEntry[] = data.map(({ readme, ...entry }) => ({ ...Demo.parse(entry), readme }));
const tags = [...new Set(demos.flatMap(d => d.tags))].sort();
const stacks = [...new Set(demos.flatMap(d => d.stack))].sort();
const statusColor = (value: string) => value === 'working' || value === 'completed' ? 'success' : value === 'exploring' ? 'warning' : 'default';
function Status({ value }: { value: string }) { return <Chip label={`● ${value}`} color={statusColor(value)} variant="outlined" />; }
function Chips({ values }: { values: string[] }) { return <Stack direction="row"   sx={{ gap: .7, flexWrap: "wrap" }}>{values.map(v => <Chip key={v} label={v} />)}</Stack>; }
function DemoCard({ demo, index }: { demo: DemoEntry; index: number }) {
  const kind = demo.id.includes('openfga') ? 'graph' : demo.id.includes('magentic') ? 'manager' : 'code';
  return <Card variant="outlined" className="demo-card"><CardActionArea component={Link} to={`/demos/${demo.id}`} sx={{ height: '100%', display: 'flex', alignItems: 'stretch', flexDirection: 'column' }}>
    <Box className={`card-art art-${kind}`}><span className="art-grid" /><Box className="card-art-icon"><LabIcon kind={kind} size={40} /></Box><span className="art-label">EXPERIMENT / {String(index + 1).padStart(2, '0')}</span><Box className="art-orbit orbit-one" /><Box className="art-orbit orbit-two" /></Box>
    <Box sx={{ p: 3, flex: 1, width: '100%' }}><Stack direction="row"   sx={{ mb: 2 , justifyContent: "space-between", alignItems: "center" }}><Eyebrow>{demo.id === 'decision-workbench' ? 'DECISION MODELS' : kind === 'graph' ? 'RELATIONSHIPS' : kind === 'manager' ? 'ORCHESTRATION' : 'INTERACTIVE TOOLS'}</Eyebrow><Status value={demo.status} /></Stack>
      <Typography variant="h2" sx={{ mb: 1.5, fontSize: '1.2rem' }}>{demo.title}</Typography><Typography variant="body2" color="text.secondary" sx={{ minHeight: 72, mb: 2.5 }}>{demo.summary}</Typography><Chips values={demo.tags} />
    </Box><Divider sx={{ width: '100%' }} /><Stack direction="row"    sx={{ px: 3, py: 2, width: '100%' , alignItems: "center", justifyContent: "space-between", gap: 2 }}><Typography variant="caption" color="text.secondary">{demo.stack.slice(0, 3).join(' · ')}</Typography><LabIcon kind="arrow" size={18} /></Stack>
    <Typography variant="caption" color="text.secondary" sx={{ px: 3, pb: 1.5, width: '100%' }}>Updated {demo.updatedAt}</Typography>
  </CardActionArea></Card>;
}
function Home() {
  const [params, setParams] = useSearchParams();
  const query = params.get('q') || '';
  const status = params.get('status') || '', tag = params.get('tag') || '', stack = params.get('stack') || '';
  const visible = useMemo(() => searchDemos(demos, query, { status, tag, stack }), [query, status, tag, stack]);
  function update(key: string, value: string) { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next, { replace: true }); }
  return <LabShell title="Curiosity, made runnable." subtitle="気になる技術を、動くDemoに。試して、観察して、次の問いへ。" actions={<Chip icon={<LabIcon kind="lab" size={17} />} label={`${demos.length} experiments in your lab`} sx={{ bgcolor: 'white', p: 1, height: 36 }} />}>
    <Paper variant="outlined" className="portal-banner" sx={{ backgroundColor: "#eef1f7", color: "#273453" }}><Box><Chip label="YOUR PERSONAL PLAYGROUND" sx={{ bgcolor: '#e2e7f1', color: '#465575', mb: 1.5 }} /><Typography sx={{ fontSize: { xs: 20, md: 23 }, fontWeight: 650, letterSpacing: '-.03em', mb: 1 }}>小さく試す。つながりが見える。</Typography><Typography sx={{ color: '#5c6880', fontSize: 13 }}>一つの問いから、実験・観察・発見まで。すべてをここに。</Typography></Box><Box className="banner-stats"><div><strong>{String(demos.length).padStart(2, '0')}</strong><span>Experiments</span></div><div><strong>{String(demos.filter(d => d.status === 'exploring').length).padStart(2, '0')}</strong><span>Exploring</span></div><div><strong>{String(demos.filter(d => d.status === 'working').length).padStart(2, '0')}</strong><span>Working</span></div></Box></Paper>
    <Box className="recent-strip"><Eyebrow>RECENTLY UPDATED</Eyebrow>{demos.slice(0, 3).map(d => <Link key={d.id} to={`/demos/${d.id}`}><span>{d.title}</span><time>{d.updatedAt}</time><LabIcon kind="arrow" size={14} /></Link>)}</Box>
    <Stack direction="row"   sx={{ mt: 4, mb: 2 , justifyContent: "space-between", alignItems: "center" }}><Stack direction="row"   sx={{ alignItems: "center", gap: 1 }}><Typography variant="h2">All experiments</Typography><Chip label={demos.length} /></Stack><Typography variant="caption" color="text.secondary">問い、技術、発見から探す</Typography></Stack>
    <Paper variant="outlined" sx={{ p: 2, mb: 2.5 }}><Box className="portal-filters"><TextField label="Search demos" placeholder="Ideas, questions, findings…" value={query} onChange={e => update('q', e.target.value)} slotProps={{ input: { startAdornment: <InputAdornment position="start"><LabIcon kind="search" size={18} /></InputAdornment> } }} />
      {([['status', 'Status', DemoStatus.options, status], ['tag', 'Tag', tags, tag], ['stack', 'Stack', stacks, stack]] as const).map(([key, title, options, value]) => <TextField select key={key} label={title} value={value} onChange={e => update(key, e.target.value)}><MenuItem value="">All</MenuItem>{options.map(v => <MenuItem key={v} value={v}>{v}</MenuItem>)}</TextField>)}
    </Box></Paper>
    <Stack direction="row"   sx={{ minHeight: 32, mb: 1.5 , justifyContent: "space-between", alignItems: "center" }}><Typography variant="caption" color="text.secondary">{visible.length} experiments found</Typography>{params.size > 0 && <Button size="small" onClick={() => setParams({})}>Clear filters ×</Button>}</Stack>
    <Box className="portal-cards">{visible.map(d => <DemoCard key={d.id} demo={d} index={demos.indexOf(d)} />)}</Box>
    {!visible.length && <Panel><Typography variant="h2">No experiments found</Typography><Typography color="text.secondary" sx={{ my: 2 }}>検索語やfilterを変えてみてください。</Typography><Button variant="outlined" onClick={() => setParams({})}>Reset search</Button></Panel>}
    <Paper variant="outlined" className="new-demo"><Stack direction="row"   sx={{ alignItems: "center", gap: 2 }}><Box className="new-demo-icon"><LabIcon size={26} /></Box><Box><Typography variant="h3">次は、何を試してみる？</Typography><Typography variant="body2" color="text.secondary">Templateを選んで、最初の問いを書くだけ。</Typography></Box></Stack><Box component="code" className="new-demo-command">pnpm demo:new <span>↵</span></Box></Paper>
  </LabShell>;
}
function Detail() {
  const { id } = useParams(); const d = demos.find(d => d.id === id);
  if (!d) return <LabShell title="Demo not found" subtitle="このDemoは見つかりませんでした。"><Button component={Link} to="/">Back to experiments</Button></LabShell>;
  return <LabShell title={d.title} subtitle={d.summary} number={String(demos.indexOf(d) + 1).padStart(2, '0')} actions={d.app && <Button variant="outlined" size="small" sx={{ flexShrink: 0, whiteSpace: 'nowrap', alignSelf: 'center' }} href={d.app.url} target="_blank" rel="noreferrer" endIcon={<LabIcon kind="arrow" size={18} />}>Open Demo</Button>}>
    <Button component={Link} to="/" sx={{ mb: 2 }}>← All experiments</Button><Box component="article" className="detail-grid"><div><Panel title="What I want to understand" action={<Status value={d.status} />}><Typography sx={{ mb: 2 }}>{d.goal}</Typography><Typography variant="h3">Questions</Typography><ul>{d.questions.map(q => <li key={q}>{q}</li>)}</ul></Panel>
      <Panel title="Findings">{d.findings.length ? <ul>{d.findings.map(f => <li key={f}>{f}</li>)}</ul> : <Typography color="text.secondary">まだ観察を記録していません。動かして demo.yaml に追記してください。</Typography>}</Panel>
      <Panel className="markdown"><Eyebrow>README.md</Eyebrow><Markdown remarkPlugins={[remarkGfm]}>{d.readme}</Markdown></Panel></div>
      <aside><Panel title="Experiment notebook"><Typography variant="h3" sx={{ mb: 1.5 }}>Tags</Typography><Chips values={d.tags} /><Typography variant="h3" sx={{ mt: 3, mb: 1.5 }}>Stack</Typography><Chips values={d.stack} /><Divider sx={{ my: 3 }} /><dl><dt>Created</dt><dd>{d.createdAt}</dd><dt>Updated</dt><dd>{d.updatedAt}</dd></dl><Typography className="mono" sx={{ mt: 2, overflowWrap: 'anywhere', color: 'text.secondary' }}>demos/{d.id}</Typography></Panel><Panel title="References">{d.references.map((ref, i) => <a className="reference" key={i} href={typeof ref === 'string' ? ref : ref.url} target="_blank" rel="noreferrer">{typeof ref === 'string' ? ref : ref.title} ↗</a>)}</Panel></aside>
    </Box>
  </LabShell>;
}
function App() { return <LabTheme><BrowserRouter><Routes><Route path="/" element={<Home />} /><Route path="/demos/:id" element={<Detail />} /><Route path="*" element={<LabShell title="Page not found" subtitle="ページが見つかりませんでした。"><Button component={Link} to="/">Back to experiments</Button></LabShell>} /></Routes></BrowserRouter></LabTheme>; }
createRoot(document.getElementById('root')!).render(<App />);
