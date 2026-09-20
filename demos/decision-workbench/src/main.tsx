import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Checkbox, Chip, CircularProgress, Divider, FormControlLabel, LinearProgress, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { Eyebrow, LabIcon, LabShell, LabTheme, Panel } from '@playground/ui';
import { elapsed, isPristine, scoreLabel, taskFrom, type Config, type Language, type ModelId, type Mode, type Result, type Run, type Task } from './types';
import './style.css';

const modeLabels: Record<Mode, string> = { classification: 'Classification / 分類', criteria: 'Criteria / 基準への適合', sufficiency: 'Sufficiency / 情報の十分性' };
const blank: Task = { mode: 'criteria', language: 'ja', text: '', question: '', criteria: '', options: [{ id: 'a', label: '', description: '' }, { id: 'b', label: '', description: '' }] };
async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, body === undefined ? { signal } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : `Request failed (${response.status})`);
  return data;
}
function optionLabel(task: Task, id: string | null | undefined): string { return task.options.find(option => option.id === id)?.label || id || '選択なし'; }
function raw(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
function exportRun(run: Run) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], { type: 'application/json' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `decision-${run.id}.json`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ModelResult({ result, task, name, waiting }: { result?: Result; task: Task; name: string; waiting: boolean }) {
  return <Panel title={name} action={<Chip variant="outlined" label={result?.status || (waiting ? 'Waiting' : 'Not run')} color={result?.status === 'ok' ? 'success' : result ? 'warning' : 'default'} />}>
    {!result ? <Box className="result-empty">{waiting && <CircularProgress size={19} />}<Typography variant="body2" color="text.secondary">{waiting ? 'モデルの読み込み・推論を待っています。初回loadは時間がかかることがあります。' : '入力と候補を用意して実行すると、このモデルの判断が表示されます。'}</Typography></Box> : <>
      {result.status === 'error' ? <Alert severity="error">{result.error || 'Inference failed'}</Alert> : <><Eyebrow>SELECTED OPTION</Eyebrow><Typography variant="h2" sx={{ mt: .6, mb: 1.5 }}>{optionLabel(task, result.selectedOptionId)}</Typography><Chip variant="outlined" label={result.status === 'invalid' ? '無効な回答 / 候補を選べませんでした' : result.matchesExpected === null ? '参照解なし / 自分で観察' : result.matchesExpected ? '参照解と一致' : '参照解と不一致'} color={result.status === 'invalid' || result.matchesExpected === false ? 'warning' : result.matchesExpected ? 'success' : 'default'} /></>}
      {!!result.scores.length && <Box sx={{ mt: 2.5 }}><Typography variant="body2" sx={{ fontWeight: 650 }}>Candidate scores</Typography><Typography variant="caption" color="text.secondary">{result.scoreKind} · 正解率ではありません</Typography><Stack spacing={1.5} sx={{ mt: 1.5 }}>{result.scores.map(score => <Box key={score.optionId}><Stack direction="row" sx={{ justifyContent: 'space-between', gap: 1, mb: .5 }}><Typography variant="body2" sx={{ fontWeight: score.optionId === result.selectedOptionId ? 650 : 400 }}>{optionLabel(task, score.optionId)}</Typography><Typography variant="body2" className="mono">{scoreLabel(score.score)}</Typography></Stack><LinearProgress variant="determinate" value={Math.max(0, Math.min(100, score.score * 100))} color={score.optionId === result.selectedOptionId ? 'primary' : 'secondary'} sx={{ height: 7, borderRadius: 4, bgcolor: '#f0f2f7' }} /></Box>)}</Stack></Box>}
      <Box className="result-timing"><div><Eyebrow>MODEL LOAD</Eyebrow><strong>{elapsed(result.loadMs)}</strong></div><div><Eyebrow>INFERENCE</Eyebrow><strong>{elapsed(result.inferenceMs)}</strong></div></Box>
      <Accordion disableGutters elevation={0} sx={{ mt: 1 }}><AccordionSummary expandIcon={<span>⌄</span>}><Typography variant="caption">Prompt / raw output / raw scores</Typography></AccordionSummary><AccordionDetails>{result.warning && <Box sx={{ mb: 2 }}><Typography variant="caption" sx={{ fontWeight: 650 }}>Model notes</Typography><Typography variant="body2" color="text.secondary">{result.warning}</Typography></Box>}<Typography variant="caption">Input prompt</Typography><pre className="code-block">{result.prompt || '(not available)'}</pre><Typography variant="caption">Raw answer</Typography><pre className="code-block">{raw(result.rawAnswer ?? '(not provided)')}</pre><Typography variant="caption">Raw scores</Typography><pre className="code-block">{JSON.stringify(result.scores, null, 2)}</pre></AccordionDetails></Accordion>
    </>}
  </Panel>;
}

function InputComparison({ current, previous, run, previousRun, inputIndex }: { current?: Task; previous?: Task; run?: Run; previousRun?: Run; inputIndex: number }) {
  const fields = ['mode', 'language', 'text', 'question', 'criteria', 'options'] as const;
  const changed = current && previous ? fields.filter(field => JSON.stringify(current[field]) !== JSON.stringify(previous[field])) : [];
  return <Panel title="What changed?" subtitle="今回の実行と、その前の実行を比べる。">
    {!current || !previous ? <Typography variant="body2" color="text.secondary">同じ入力を少し変えて2回実行すると、入力差分と判断の変化が見えます。</Typography> : <><Stack direction="row" sx={{ gap: .6, flexWrap: 'wrap', mb: 1.5 }}>{changed.length ? changed.map(field => <Chip key={field} label={`${field} changed`} variant="outlined" />) : <Chip label="入力は同じ" variant="outlined" />}</Stack>
      {run?.models.map(model => { const now = run.results.find(result => result.model === model && result.inputIndex === inputIndex); const before = previousRun?.results.find(result => result.model === model && result.inputIndex === inputIndex); return <Box className="result-change" key={model}><Typography variant="body2" sx={{ fontWeight: 650 }}>{now?.modelName || model}</Typography><Typography variant="caption" color="text.secondary">{before ? optionLabel(previous, before.selectedOptionId) : '未実行'} → {now ? optionLabel(current, now.selectedOptionId) : '待機中'}</Typography>{before && now && <Chip sx={{ mt: .6 }} label={before.status !== 'ok' || now.status !== 'ok' ? '有効な選択を比較できません' : before.selectedOptionId === now.selectedOptionId ? '選択は同じ' : '選択が変化'} variant="outlined" color={before.selectedOptionId === now.selectedOptionId ? 'default' : 'warning'} />}</Box>; })}
      {!!changed.length && <Accordion disableGutters elevation={0}><AccordionSummary expandIcon={<span>⌄</span>}><Typography variant="caption">入力差分を読む</Typography></AccordionSummary><AccordionDetails>{changed.map(field => <Box key={field} sx={{ mb: 2 }}><Eyebrow>{field}</Eyebrow><Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>前回</Typography><div className="diff-text diff-before">{raw(previous[field]) || '(empty)'}</div><Typography variant="caption" color="text.secondary">今回</Typography><div className="diff-text diff-after">{raw(current[field]) || '(empty)'}</div></Box>)}</AccordionDetails></Accordion>}
    </>}
  </Panel>;
}

function Workbench() {
  const [config, setConfig] = useState<Config>({ models: [], fixtures: [] });
  const [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [draft, setDraft] = useState<Task>(blank), [fixtureId, setFixtureId] = useState('');
  const [models, setModels] = useState<ModelId[]>(['modernbert', 'gliclass']);
  const [job, setJob] = useState<Run | null>(null), [starting, setStarting] = useState(false);
  const [history, setHistory] = useState<Run[]>([]), [viewId, setViewId] = useState<string | null>(null), [inputIndex, setInputIndex] = useState(0);
  const initialized = useRef(false);
  async function refreshConfig() {
    setLoading(true); setError('');
    try { const next = await api<Config>('/api/config'); setConfig(next); if (!initialized.current) { const first = next.fixtures.find(f => f.id === 'chicken-vegan-ja') || next.fixtures.find(f => f.mode === 'criteria' && f.language === 'ja' && f.basis === 'knowledge') || next.fixtures[0]; if (first) { setDraft(taskFrom(first)); setFixtureId(first.id); } initialized.current = true; } } catch (reason) { setError(String(reason)); } finally { setLoading(false); }
  }
  useEffect(() => { void refreshConfig(); }, []);
  useEffect(() => {
    if (!job || job.status !== 'running') return;
    const controller = new AbortController(); const id = job.id; let timer: ReturnType<typeof setTimeout>;
    async function poll() { try { const next = await api<Run>(`/api/runs/${encodeURIComponent(id)}`, undefined, controller.signal); if (!controller.signal.aborted) { setJob(next); if (next.status === 'running') timer = setTimeout(() => void poll(), 400); } } catch (reason) { if (!controller.signal.aborted) { setError(String(reason)); timer = setTimeout(() => void poll(), 1500); } } }
    timer = setTimeout(() => void poll(), 400); return () => { controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status]);
  useEffect(() => { if (job && job.status !== 'running') setHistory(previous => [job, ...previous.filter(run => run.id !== job.id)].slice(0, 10)); }, [job]);
  const busy = starting || job?.status === 'running';
  const fixture = config.fixtures.find(item => item.id === fixtureId);
  const pristine = isPristine(draft, fixture);
  const languageFixtures = config.fixtures.filter(item => item.language === draft.language).slice(0, 12);
  const view = (viewId ? history.find(run => run.id === viewId) : job) || undefined;
  const historyIndex = view ? history.findIndex(run => run.id === view.id) : -1;
  const previousRun = historyIndex >= 0 ? history[historyIndex + 1] : history.find(run => run.id !== view?.id);
  const currentInput = view?.inputs[inputIndex];
  const input = currentInput || draft;
  const referenceFixture = view ? config.fixtures.find(item => item.id === currentInput?.fixtureId) : pristine ? fixture : undefined;
  const missingModels = models.filter(id => !config.models.find(model => model.id === id)?.available);
  const ready = !loading && !!models.length && !missingModels.length;
  const validInput = !!draft.text.trim() && !!draft.question.trim() && (draft.mode !== 'criteria' || !!draft.criteria.trim()) && draft.options.length >= 2 && draft.options.length <= 4 && draft.options.every(option => !!option.label.trim());
  const expectedResults = useMemo(() => view?.results.filter(result => result.status === 'ok' && result.matchesExpected !== null) || [], [view]);
  function update(patch: Partial<Task>) { setDraft(current => ({ ...current, ...patch })); }
  function selectFixture(id: string) { if (!id) { setFixtureId(''); return; } const chosen = config.fixtures.find(item => item.id === id); if (chosen) { setFixtureId(id); setDraft(taskFrom(chosen)); } }
  function changeLanguage(language: Language) { const counterpart = pristine && fixture ? config.fixtures.find(item => item.id === fixture.id.replace(/-(ja|en)$/, `-${language}`)) : undefined; if (counterpart) selectFixture(counterpart.id); else update({ language }); }
  function snapshot(): Task { const task = taskFrom(draft); return pristine && fixture ? { ...task, fixtureId: fixture.id, expectedOptionId: fixture.expectedOptionId } : task; }
  async function start(batch: boolean) {
    if (busy || !ready) return;
    setStarting(true); setError('');
    try { const inputs = batch ? languageFixtures.map(item => ({ ...taskFrom(item), fixtureId: item.id, expectedOptionId: item.expectedOptionId })) : [snapshot()]; const next = await api<Run>('/api/runs', { inputs, models: [...models] }); setJob(next); setViewId(null); setInputIndex(0); } catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  }
  return <LabShell number="04" title="Decision Workbench" subtitle="分類・基準判定・情報の十分性。同じ問いを小さなモデルに渡し、入力の変化が判断にどう現れるか観察する。" actions={<Chip label="LOCAL INFERENCE / NO MOCK" variant="outlined" />}>
    {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 2 }}>{error}</Alert>}
    <Box className="workbench-toolbar"><Box><Eyebrow>COMPARE TWO APPROACHES</Eyebrow><Typography variant="body2" color="text.secondary">候補のscoreと参照解への一致は別の指標です。モデル間のscoreをそのまま比較しないでください。</Typography></Box><Button variant="outlined" disabled={loading || !!busy} onClick={() => void refreshConfig()}>{loading ? 'Checking setup…' : 'Refresh model setup'}</Button></Box>
    <Box className="workbench-layout">
      <Box component="aside" className="workbench-input"><Panel title="Compose the decision" subtitle="一度に変える条件を絞ると、挙動を追いやすくなります。">
        <Stack spacing={2} component="form" onSubmit={event => { event.preventDefault(); if (validInput) void start(false); }}>
          <TextField select label="Fixture preset" value={languageFixtures.some(item => item.id === fixtureId) ? fixtureId : ''} disabled={!!busy} onChange={event => selectFixture(event.target.value)}><MenuItem value="">Custom input</MenuItem>{languageFixtures.map(item => <MenuItem value={item.id} key={item.id}>{item.title}</MenuItem>)}</TextField>
          <Stack direction="row" sx={{ gap: 1 }}><TextField select label="Mode" value={draft.mode} fullWidth disabled={!!busy} onChange={event => update({ mode: event.target.value as Mode })}>{Object.entries(modeLabels).map(([value, label]) => <MenuItem value={value} key={value}>{label}</MenuItem>)}</TextField><TextField select label="Language" value={draft.language} sx={{ minWidth: 93 }} disabled={!!busy} onChange={event => changeLanguage(event.target.value as Language)}><MenuItem value="ja">日本語</MenuItem><MenuItem value="en">English</MenuItem></TextField></Stack>
          <TextField label="Text / 判断する内容" multiline minRows={4} maxRows={12} value={draft.text} required disabled={!!busy} onChange={event => update({ text: event.target.value })} />
          <TextField label="Question / 問い" multiline minRows={2} maxRows={5} value={draft.question} required disabled={!!busy} onChange={event => update({ question: event.target.value })} />
          <TextField label="Criteria / 判断基準" multiline minRows={2} maxRows={7} value={draft.criteria} required={draft.mode === 'criteria'} disabled={!!busy} onChange={event => update({ criteria: event.target.value })} helperText={draft.mode === 'sufficiency' ? '不足していると判断する条件も書けます。' : '意味や前提を明示すると、判断が変わるか観察できます。'} />
          <Divider /><Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between' }}><Typography variant="h3">Options <Typography component="span" variant="caption" color="text.secondary">2–4 candidates</Typography></Typography><Button variant="outlined" disabled={!!busy || draft.options.length >= 4} onClick={() => { const id = `option-${crypto.randomUUID().slice(0, 8)}`; update({ options: [...draft.options, { id, label: '', description: '' }] }); }}>＋ Add</Button></Stack>
          {draft.options.map((option, index) => <Box className="option-editor" key={option.id}><Stack direction="row" sx={{ gap: 1, alignItems: 'center', mb: 1 }}><Chip label={String.fromCharCode(65 + index)} variant="outlined" /><TextField label={`Option ${index + 1} label`} fullWidth required value={option.label} disabled={!!busy} onChange={event => update({ options: draft.options.map(item => item.id === option.id ? { ...item, label: event.target.value } : item) })} /><Button aria-label={`Remove option ${index + 1}`} disabled={!!busy || draft.options.length <= 2} onClick={() => update({ options: draft.options.filter(item => item.id !== option.id) })}>×</Button></Stack><TextField label={`Option ${index + 1} description`} multiline minRows={1} maxRows={4} fullWidth value={option.description} disabled={!!busy} onChange={event => update({ options: draft.options.map(item => item.id === option.id ? { ...item, description: event.target.value } : item) })} /></Box>)}
          {!pristine && fixture && <Alert severity="info" action={<Button disabled={!!busy} onClick={() => selectFixture(fixture.id)}>Reset</Button>}>編集した入力にはfixtureの参照解を付けません。</Alert>}
          <Divider /><Typography variant="h3">Models</Typography><Box>{config.models.map(model => <Box className="model-setup" key={model.id}><FormControlLabel control={<Checkbox size="small" checked={models.includes(model.id)} disabled={!!busy} onChange={event => setModels(current => event.target.checked ? [...current, model.id] : current.filter(id => id !== model.id))} />} label={<Typography variant="body2" sx={{ fontWeight: 650 }}>{model.name}</Typography>} /><Chip variant="outlined" label={model.available ? 'Ready' : 'Setup required'} color={model.available ? 'success' : 'warning'} /><Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{model.detail}</Typography></Box>)}</Box>
          {!!missingModels.length && !loading && <Alert severity="info">選択したモデルにsetupが必要です。Demo READMEの手順を実行し、Refresh model setupで再確認してください。未導入モデルの予測は生成しません。</Alert>}
          <Box><Button type="submit" variant="outlined" startIcon={busy ? <CircularProgress size={13} /> : <LabIcon kind="play" size={14} />} disabled={!!busy || !ready || !validInput}>{busy ? 'Running…' : 'Run comparison'}</Button></Box>
        </Stack>
      </Panel></Box>
      <Box className="workbench-results"><Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', gap: 1, mb: 2 }}><Box><Eyebrow>MODEL OUTPUTS</Eyebrow><Typography variant="h2">Observe the decision</Typography></Box>{view && <Chip variant="outlined" label={view.status} color={view.status === 'completed' ? 'success' : view.status === 'failed' ? 'error' : 'default'} />}</Stack>
        {view?.error && <Alert severity="error" sx={{ mb: 2 }}>{view.error}</Alert>}
        {view && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>実行時点の入力を表示 · {new Date(view.createdAt).toLocaleString()} · {view.results.length}/{view.inputs.length * view.models.length} results</Typography>}
        {view && view.inputs.length > 1 && <TextField select fullWidth label="Batch input" value={inputIndex} onChange={event => setInputIndex(Number(event.target.value))} sx={{ mb: 2 }}>{view.inputs.map((task, index) => <MenuItem key={index} value={index}>{index + 1}. {config.fixtures.find(item => item.id === task.fixtureId)?.title || task.question}</MenuItem>)}</TextField>}
        {(view?.models || models).map(id => <ModelResult key={id} name={config.models.find(model => model.id === id)?.name || id} task={input} result={view?.results.find(result => result.model === id && result.inputIndex === inputIndex)} waiting={view?.status === 'running'} />)}
        {!models.length && !view && <Alert severity="info">比較するモデルを1つ以上選択してください。</Alert>}
        <Panel title="Reading the result"><Typography variant="body2" color="text.secondary">① 候補のどれを選んだか<br />② 参照解と一致するか<br />③ 説明や基準を足すと変わるか<br />④ model loadとinferenceに何秒かかったか</Typography><Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>Scoreが高くても、判断が正しいとは限りません。モデルの知識が必要な問題と、本文だけで答えられる問題を分けて観察します。</Typography></Panel>
      </Box>
      <Box component="aside" className="workbench-history"><InputComparison current={currentInput} previous={previousRun?.inputs[inputIndex]} run={view} previousRun={previousRun} inputIndex={inputIndex} />
        <Panel title="Run history" subtitle="このタブ内のみ・最新10件。再読み込みで消えます。">{!history.length && <Typography variant="body2" color="text.secondary">完了した実行がここに残ります。保存するにはJSONを書き出してください。</Typography>}<Stack spacing={1}>{history.map((run, index) => <Button key={run.id} className={`history-button ${view?.id === run.id ? 'selected' : ''}`} variant="outlined" onClick={() => { setViewId(run.id); setInputIndex(0); }}><span><strong>{run.inputs.length > 1 ? `${run.inputs.length} fixtures` : modeLabels[run.inputs[0].mode].split(' / ')[0]}</strong><small>{new Date(run.createdAt).toLocaleTimeString()} · {run.inputs[0].language.toUpperCase()} · {run.status}</small></span><span>#{history.length - index}</span></Button>)}</Stack>{job?.status === 'running' && <Button sx={{ mt: 1 }} onClick={() => { setViewId(null); setInputIndex(0); }}>Show running job</Button>}{view && <Button variant="outlined" sx={{ mt: 2 }} onClick={() => exportRun(view)}>Export run JSON ↓</Button>}</Panel>
        {referenceFixture && <Panel title="Human reference"><Chip label={referenceFixture.basis === 'knowledge' ? 'Background knowledge' : 'Evidence in text'} variant="outlined" sx={{ mb: 1.5 }} /><Typography variant="body2" sx={{ fontWeight: 650, mb: 1 }}>{optionLabel(referenceFixture, referenceFixture.expectedOptionId)}</Typography><Typography variant="body2" color="text.secondary">{referenceFixture.rationale}</Typography></Panel>}
      </Box>
    </Box>
    <Panel title="Fixture bench" subtitle={`${draft.language === 'ja' ? '日本語' : 'English'}の固定例 / 最大12件。参照解と人間の理由を先に読み、失敗の種類を比べます。`} action={<Button variant="outlined" disabled={!!busy || !ready || !languageFixtures.length} onClick={() => void start(true)}>Run {languageFixtures.length} fixtures</Button>}>
      {view && <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', mb: 2 }}><Chip variant="outlined" label={`参照解一致 ${expectedResults.filter(result => result.matchesExpected).length}/${expectedResults.length}（推論成功のみ）`} /><Chip variant="outlined" label={`無効回答 ${view.results.filter(result => result.status === 'invalid').length}`} /><Chip variant="outlined" label={`実行エラー ${view.results.filter(result => result.status === 'error').length}`} /></Stack>}
      <TableContainer><Table size="small" aria-label="Fixture expected answers and results"><TableHead><TableRow><TableCell>Fixture / basis</TableCell><TableCell>Expected</TableCell><TableCell>Human rationale</TableCell><TableCell>ModernBERT</TableCell><TableCell>GLiClass</TableCell></TableRow></TableHead><TableBody>{languageFixtures.map(item => { const index = view?.inputs.findIndex(task => task.fixtureId === item.id) ?? -1; return <TableRow key={item.id}><TableCell><Button sx={{ justifyContent: 'flex-start', whiteSpace: 'normal', textAlign: 'left', px: 0 }} disabled={!!busy} onClick={() => selectFixture(item.id)}>{item.title}</Button><Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{item.mode} · {item.basis === 'text' ? '本文の根拠' : '背景知識'}</Typography></TableCell><TableCell>{optionLabel(item, item.expectedOptionId)}</TableCell><TableCell sx={{ minWidth: 250, maxWidth: 460 }}><Typography variant="body2">{item.rationale}</Typography></TableCell>{(['modernbert', 'gliclass'] as ModelId[]).map(model => { const result = index < 0 ? undefined : view?.results.find(value => value.inputIndex === index && value.model === model); return <TableCell key={model}>{result ? <><Typography variant="body2">{result.status === 'error' ? 'Error' : result.status === 'invalid' ? 'Invalid' : optionLabel(item, result.selectedOptionId)}</Typography><Typography variant="caption" color={result.matchesExpected ? 'success.main' : 'text.secondary'}>{result.status !== 'ok' ? '判定対象外' : result.matchesExpected === null ? '—' : result.matchesExpected ? '一致' : '不一致'}</Typography></> : <Typography variant="body2" color="text.secondary">—</Typography>}</TableCell>; })}</TableRow>; })}</TableBody></Table></TableContainer>
    </Panel>
  </LabShell>;
}

createRoot(document.getElementById('root')!).render(<LabTheme><Workbench /></LabTheme>);
