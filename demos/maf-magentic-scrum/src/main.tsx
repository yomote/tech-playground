import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Box, Stack, Typography, Button, Chip, Alert, TextField, MenuItem, FormControlLabel, Checkbox, Slider, Divider, Accordion, AccordionSummary, AccordionDetails } from '@mui/material';
import { ReactFlow, Background, Controls, Handle, Position, MarkerType, type Node, type NodeProps, type Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { LabTheme, LabShell, LabIcon, Panel, Metric } from '@playground/ui';
import { trajectory, specialists, agentName, type Run } from './trajectory';
import './style.css';

type AgentNode = Node<{ name: string; role: string; kind: string; active: boolean; running: boolean; count: number | null }, 'agent'>;
function AgentCard({ data }: NodeProps<AgentNode>) {
  return <div className={`flow-node ${data.active ? 'active' : ''} ${data.active && data.running ? 'running' : ''}`}><Handle type="target" position={Position.Top} isConnectable={false} /><div className="node-icon"><LabIcon kind={data.kind} size={20} /></div><div className="node-label">{data.name}</div><div className="node-subtitle">{data.role}</div>{data.count !== null && <div className="agent-turns">{data.count ? `${data.count} selections` : 'Waiting for manager'}</div>}<Handle type="source" position={Position.Bottom} isConnectable={false} /></div>;
}
const nodeTypes = { agent: AgentCard };
async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, body === undefined ? { signal } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  const result = await response.json(); if (!response.ok) throw new Error(result.error); return result;
}
function ScrumApp() {
  const [run, setRun] = useState<Run | null>(null);
  const [mode, setMode] = useState('mock'), [scenario, setScenario] = useState('test-failure'), [approval, setApproval] = useState(false);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null), [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!run || run.status !== 'running') return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>; const id = run.id;
    const poll = async () => { try { const next = await api<Run>(`/api/runs/${id}`, undefined, controller.signal); if (!controller.signal.aborted) { setRun(next); if (next.status === 'running') timer = setTimeout(() => void poll(), 350); } } catch (e) { if (!controller.signal.aborted) { setError(String(e)); timer = setTimeout(() => void poll(), 1500); } } };
    timer = setTimeout(() => void poll(), 350);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [run?.id, run?.status]);
  const events = run?.events ?? [];
  useEffect(() => {
    if (!playing || !events.length) return;
    const timer = setInterval(() => setCursor(current => { const next = (current ?? -1) + 1; if (next >= events.length - 1) { setPlaying(false); return events.length - 1; } return next; }), 850);
    return () => clearInterval(timer);
  }, [playing, events.length]);
  const index = cursor === null ? events.length - 1 : Math.min(cursor, events.length - 1);
  const visibleEvents = useMemo(() => events.slice(0, index + 1), [events, index]);
  const state = useMemo(() => trajectory(visibleEvents), [visibleEvents]);
  const selectedEvent = events[index];
  const moving = playing || (cursor === null && run?.status === 'running' && !run?.pending);
  const toolEvent = selectedEvent?.kind === 'tool';
  const nodeDefinitions = [
    { name: 'Manager', role: 'Planning · selection · replan', kind: 'manager', x: 345, y: 15 },
    ...specialists.map((name, i) => ({ name, role: ['Acceptance criteria', 'Implementation & fixes', 'Review & feedback', 'Tests & evidence'][i], kind: ['document', 'code', 'search', 'check'][i], x: 15 + i * 220, y: 245 })),
    { name: 'Fixture', role: 'solution.py / unittest', kind: 'folder', x: 345, y: 460 },
    ...(state.tools.has('Verifier') ? [{ name: 'Verifier', role: 'Independent final tests', kind: 'check', x: 675, y: 460 }] : []),
  ];
  const nodes: AgentNode[] = nodeDefinitions.map(n => ({ id: n.name, type: 'agent', position: { x: n.x, y: n.y }, data: { name: n.name, role: n.role, kind: n.kind, active: state.active === n.name || (n.name === 'Fixture' && toolEvent), running: !!moving, count: n.name in state.visits ? state.visits[n.name] : null } }));
  const edges: Edge[] = specialists.map(name => {
    const active = state.active === name && !toolEvent;
    return { id: `manager-${name}`, source: 'Manager', target: name, type: 'smoothstep', label: state.visits[name] ? `${state.visits[name]} turns` : '', animated: active && !!moving, markerEnd: { type: MarkerType.ArrowClosed, color: active ? '#5b60ae' : '#b9c0d3' }, style: { stroke: active ? '#5b60ae' : '#cbd1e1', strokeWidth: active ? 3 : 1.6 } };
  });
  for (const name of state.tools) {
    const active = toolEvent && agentName(selectedEvent.agent) === name;
    edges.push({ id: `tool-${name}`, source: name, target: 'Fixture', type: 'smoothstep', label: active ? selectedEvent.message : 'tools', animated: active && !!moving, markerEnd: { type: MarkerType.ArrowClosed, color: active ? '#278779' : '#c3ccda' }, style: { stroke: active ? '#278779' : '#d1d8e4', strokeWidth: active ? 3 : 1.3, strokeDasharray: active ? undefined : '4 4' } });
  }
  async function start() {
    setBusy(true); setError(''); setPlaying(false); setCursor(null);
    try { setRun(await api<Run>('/api/runs', { mode, scenario, approval })); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function respond(decision: string) { if (!run) return; setBusy(true); try { await api(`/api/approval/${run.id}`, { decision }); setRun(await api<Run>(`/api/runs/${run.id}`)); } catch (e) { setError(String(e)); } finally { setBusy(false); } }
  function exportRun() { const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = `magentic-${run?.id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  const decision = state.decision;
  const instruction = decision?.ledger?.instruction_or_question?.answer || decision?.message;
  const checkpointEvent = [...(run?.events ?? [])].reverse().find(e => e.kind === 'approval');
  return <LabShell number="02" title="Watch the team think together." subtitle="Managerの選択、agentの仕事、失敗からの修正。結果までの道のりをグラフで追う。" actions={<Chip label={(run?.mode || mode) === 'mock' ? '● MOCK / Scripted agents' : '● LIVE / Magentic'} variant="outlined" color={(run?.mode || mode) === 'live' ? 'success' : 'default'} />}>
    <Panel><Box component="form" className="run-controls" onSubmit={event => { event.preventDefault(); void start(); }}><TextField select label="Execution" value={mode} disabled={run?.status === 'running'} onChange={e => setMode(e.target.value)}><MenuItem value="mock">Mock · real fixture tests</MenuItem><MenuItem value="live">Live · Microsoft Agent Framework</MenuItem></TextField><TextField select label="Mock scenario" value={scenario} disabled={mode === 'live' || run?.status === 'running'} onChange={e => setScenario(e.target.value)}><MenuItem value="test-failure">Test failure → stall → replan</MenuItem><MenuItem value="review-failure">Review catches a defect</MenuItem><MenuItem value="happy-path">Happy path</MenuItem></TextField><FormControlLabel control={<Checkbox size="small" checked={approval} disabled={run?.status === 'running'} onChange={e => setApproval(e.target.checked)} />} label={<Typography variant="body2">Plan & final approval</Typography>} /><Button type="submit" variant="outlined" size="small" sx={{ alignSelf: 'center', justifySelf: 'start', whiteSpace: 'nowrap' }} disabled={busy || run?.status === 'running'} startIcon={<LabIcon kind="play" size={16} />}>Start experiment</Button></Box><Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>{mode === 'mock' ? 'Mockでは選択順序はscripted、fixtureの編集とテストは実行します。終了後にReplayでゆっくり観察できます。' : 'LiveではMagentic managerが次のagentを選びます。API credentialが必要です。'}</Typography></Panel>
    {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 2 }}>{error}</Alert>}
    <Box className="metric-grid"><Metric label={cursor === null ? 'Current agent' : 'Agent at this step'} value={state.active} detail={cursor === null ? run?.status || 'Ready to run' : 'Replay / 実行はしません'} /><Metric label="Rounds" value={selectedEvent?.round ?? 0} detail="Manager coordination rounds" /><Metric label="Replans" value={selectedEvent?.replans ?? 0} detail="Plan resets, not every correction" /><Metric label="Tool calls" value={state.toolCount} detail="Read · write · test" /></Box>
    {run?.pending && run.status === 'running' && <Alert severity="warning" sx={{ mb: 2 }} action={<Stack direction="row"  sx={{ gap: 1 }}><Button disabled={busy} onClick={() => void respond('reject')} color="inherit">Reject</Button><Button disabled={busy} variant="outlined" onClick={() => void respond('approve')}>Approve</Button></Stack>}><strong>{run.pending === 'plan' ? 'Plan approval' : 'Final approval'}待ち</strong><Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{checkpointEvent?.message}</Typography></Alert>}
    <Box className="scrum-layout"><Box><Panel title="Agent orchestration" subtitle="濃いnodeが現在の担当。線はManagerの選択と、実際に記録されたtool callsです。" action={<Chip label={cursor === null ? '● Follow execution' : playing ? '▶ Replay' : 'Ⅱ Paused'} color="primary" variant="outlined" />}>
      <Box className="graph-canvas" sx={{ height: 510 }}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} nodesDraggable={false} nodesConnectable={false} deleteKeyCode={null} fitView fitViewOptions={{ padding: .1 }} minZoom={.3} maxZoom={1.3}><Background color="#dfe4f1" gap={20} /><Controls showInteractive={false} /></ReactFlow></Box>
      <Stack direction="row"     sx={{ mt: 2 , alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 1 }}><Stack direction="row"  sx={{ gap: .7 }}><Button variant="outlined" disabled={!events.length} onClick={() => { if (playing) setPlaying(false); else { if (cursor === null || index >= events.length - 1) setCursor(0); setPlaying(true); } }}>{playing ? 'Ⅱ Pause' : '▶ Replay'}</Button><Button disabled={!events.length || index <= 0} onClick={() => { setPlaying(false); setCursor(Math.max(0, index - 1)); }}>← Step</Button><Button disabled={!events.length || index >= events.length - 1} onClick={() => { setPlaying(false); setCursor(index + 1); }}>Step →</Button></Stack><Button size="small" disabled={!events.length || cursor === null} onClick={() => { setPlaying(false); setCursor(null); }}>Follow latest</Button></Stack>
      <Box sx={{ px: 1, pt: 1 }}><Slider aria-label="Trajectory step" min={0} max={Math.max(1, events.length - 1)} value={Math.max(0, index)} disabled={!events.length} onChange={(_event, value) => { setPlaying(false); setCursor(Number(value)); }} valueLabelDisplay="auto" valueLabelFormat={value => `Event ${value + 1}`} /></Box><Typography variant="caption" color="text.secondary">Event {events.length ? index + 1 : 0} / {events.length} · {selectedEvent?.kind || 'Waiting'} {cursor !== null && '· 保存されたeventの再生です。新しいagent実行は発生しません。'}</Typography>
    </Panel><Panel title="Current task"><Typography variant="body2" color="text.secondary">{run?.task || 'clamp(value, low, high) を実装 → review → unittest → 必要なら修正。'}</Typography></Panel></Box>
    <Box component="aside"><Panel title="Manager decision" subtitle={decision ? `Round ${decision.round}` : 'Waiting for first decision'}><Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{instruction || 'Start experimentで、Managerが仕事を振り分け始めます。'}</Typography>{decision?.ledger?.next_speaker?.reason && <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: 'block' }}>{decision.ledger.next_speaker.reason}</Typography>}{selectedEvent?.kind === 'replan' && <Alert severity="warning" sx={{ mt: 2 }}>{selectedEvent.message}</Alert>}</Panel>
    <Panel title="Agent history" subtitle="eventを選択すると、その瞬間のグラフに移動します。" action={<Button size="small" disabled={!run} onClick={exportRun}>Export JSON</Button>}><Box className="trajectory-list">{!events.length && <Typography variant="body2" color="text.secondary">まだeventはありません。</Typography>}{events.map((event, i) => <button key={event.seq} className={`trajectory-event ${i === index ? 'current' : ''} ${event.kind}`} onClick={() => { setPlaying(false); setCursor(i); }}><span className="event-round">{event.round ? `R${event.round}` : '•'}</span><span><strong>{event.agent} <small>{event.kind}</small></strong><span className="event-message">{event.message}</span></span></button>)}</Box></Panel></Box></Box>
    <Box className="event-bottom"><Panel title="Selected event" subtitle="Tool引数、実行結果、ledgerの詳細"><pre className="code-block">{selectedEvent ? JSON.stringify(selectedEvent, null, 2) : 'Select an event in the history.'}</pre></Panel><Panel title="Final result" action={run && <Chip label={run.status} color={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : 'default'} />}><Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{[...(run?.events ?? [])].reverse().find(e => ['final', 'error'].includes(e.kind))?.message || '実行が完了すると、検証結果がここに表示されます。'}</Typography><Divider sx={{ my: 2 }} /><Accordion disableGutters elevation={0}><AccordionSummary expandIcon={<span>⌄</span>}><Typography variant="body2">このグラフの読み方</Typography></AccordionSummary><AccordionDetails><Typography variant="body2" color="text.secondary">Managerからの線は選択したagentへの委譲、Fixtureへの線は記録されたtool呼び出しです。Reviewの指摘後にDeveloperを選び直すことと、planそのものを作り直すreplanは別に数えます。Mockはscripted、Liveはframeworkのeventに基づいて描画します。</Typography></AccordionDetails></Accordion></Panel></Box>
  </LabShell>;
}
createRoot(document.getElementById('root')!).render(<LabTheme><ScrumApp /></LabTheme>);
