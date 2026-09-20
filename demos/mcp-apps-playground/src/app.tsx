import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from '@modelcontextprotocol/ext-apps';
import { Alert, Button, Chip, Stack, Typography, TextField, MenuItem, LinearProgress, Table, TableBody, TableCell, TableHead, TableRow } from '@mui/material';
import { LabTheme, Panel, Metric, Eyebrow, LabIcon } from '@playground/ui';
import type { Experiment, ToolData } from '../contract';
import '@playground/ui/style.css';
import './style.css';
const app = new App({ name: 'Experiment Dashboard', version: '0.2.0' });
function ExperimentApp() {
  const [data, setData] = useState<ToolData | null>(null), [draft, setDraft] = useState<Experiment | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [dirty, setDirty] = useState(false);
  function receive(result: { isError?: boolean; content?: unknown; structuredContent?: unknown }) {
    if (result.isError) throw new Error(JSON.stringify(result.content));
    const content = result.structuredContent as ToolData | undefined;
    if (!content?.experiment || !content.form) return;
    setData(content); setDraft(content.experiment); setDirty(false);
  }
  useEffect(() => {
    app.ontoolresult = result => { try { receive(result); } catch (e) { setError(String(e)); } };
    void app.connect().catch(e => setError(`MCP Apps hostから開いてください: ${String(e)}`));
    return () => { void app.close(); };
  }, []);
  async function invoke(name: string, args: Record<string, unknown>) { const result = await app.callServerTool({ name, arguments: args }); receive(result); }
  async function run() {
    if (!draft) return; setBusy(true); setError('');
    try {
      await invoke('update_experiment', { experimentId: draft.experimentId, kind: draft.kind, configuration: draft.configuration, model: draft.model, dataset: draft.dataset, runs: draft.runs });
      await invoke('run_experiment', { experimentId: draft.experimentId });
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function refresh() {
    if (!draft) return; setBusy(true); setError('');
    try { await invoke('get_result', { experimentId: draft.experimentId }); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const edit = (value: Partial<Experiment>) => { setDraft(previous => previous && { ...previous, ...value }); setDirty(true); };
  if (!data || !draft) return <div className="embedded-app"><Alert severity={error ? 'error' : 'info'}>{error || 'Waiting for tool result…'}</Alert></div>;
  const result = data.experiment.result;
  return <div className="embedded-app">
    <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 2 }}><Eyebrow>TOOL-PROVIDED INTERACTIVE UI</Eyebrow><Chip size="small" label="MOCK · No LLM calls" color="warning" variant="outlined" /></Stack>
    <Typography variant="h4" sx={{ mb: 1 }}>{data.form.title}</Typography><Typography color="text.secondary" sx={{ mb: 3 }}>{data.form.description}</Typography>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <form onSubmit={event => { event.preventDefault(); void run(); }}>
      <fieldset disabled={busy} className="experiment-fields">
        <Panel title="01 / Configure the experiment" subtitle="tool resultのform.fieldsを読み取り、このフォームを描画しています。">
          <div className="dynamic-fields">{data.form.fields.map(field => <TextField key={field.key} label={field.label} helperText={field.help} select={field.type === 'select'} multiline={field.type === 'textarea'} minRows={field.type === 'textarea' ? 4 : undefined} type={field.type === 'number' ? 'number' : 'text'} value={draft.configuration[field.key]} disabled={busy} fullWidth className={field.type === 'textarea' ? 'wide-field' : ''} slotProps={{ htmlInput: { min: field.min, max: field.max, step: field.step, maxLength: field.key === 'document' ? 8000 : field.type === 'textarea' ? 4000 : 500 } }} onChange={event => edit({ configuration: { ...draft.configuration, [field.key]: field.type === 'number' ? Number(event.target.value) : event.target.value } })}>{field.options?.map(option => <MenuItem key={option} value={option}>{option}</MenuItem>)}</TextField>)}</div>
        </Panel>
        <Panel title="02 / Run settings"><div className="run-settings"><TextField select label="Model" value={draft.model} onChange={event => edit({ model: event.target.value as Experiment['model'] })} disabled={busy}>{['GPT', 'Claude', 'Local'].map(model => <MenuItem key={model} value={model}>{model}</MenuItem>)}</TextField><TextField select label="Dataset" value={draft.dataset} onChange={event => edit({ dataset: event.target.value as Experiment['dataset'] })} disabled={busy}>{['Sample A', 'Sample B'].map(dataset => <MenuItem key={dataset} value={dataset}>{dataset}</MenuItem>)}</TextField><TextField label="Runs" type="number" value={draft.runs} onChange={event => edit({ runs: Number(event.target.value) })} slotProps={{ htmlInput: { min: 1, max: 100 } }} disabled={busy} /></div>
          <Stack direction="row" spacing={1.5} sx={{ mt: 2 }}><Button type="submit" variant="outlined" disabled={busy} startIcon={<LabIcon kind="play" size={18} />}>{busy ? 'Calling MCP tools…' : 'Run experiment'}</Button><Button onClick={() => void refresh()} disabled={busy}>Reload saved state</Button></Stack>
        </Panel>
      </fieldset>
    </form>
    <Panel title="03 / Inspect the result" action={<Chip size="small" label={dirty ? 'Unsaved settings' : result ? 'Mock result' : 'Ready to run'} variant="outlined" />}>
      {dirty && <Alert severity="info" sx={{ mb: 2 }}>設定を変更しました。下の結果は前回保存時のものです。Run experimentで更新します。</Alert>}
      {!result ? <Typography color="text.secondary">フォームを編集して実行すると、ここに比較表や構造化JSONが表示されます。</Typography> : <>
        <div className="metrics-row"><Metric label="Success" value={`${result.success} / ${data.experiment.runs}`} /><Metric label="Latency · simulated" value={`${result.latencySeconds}s`} /><Metric label={draft.kind === 'structured-extraction' ? 'Field coverage' : 'Base score'} value={result.score.toFixed(2)} /></div>
        {result.comparisons && <Table size="small" aria-label="Prompt comparison results"><TableHead><TableRow><TableCell>Variant / prompt</TableCell><TableCell align="right">Mock score</TableCell></TableRow></TableHead><TableBody>{result.comparisons.map(row => <TableRow key={row.variant}><TableCell><Typography variant="subtitle2">{row.variant}</Typography><Typography variant="body2" color="text.secondary" sx={{ maxWidth: 420, overflowWrap: 'anywhere' }}>{row.preview}</Typography></TableCell><TableCell sx={{ minWidth: 130 }}><Typography align="right">{row.score.toFixed(2)}</Typography><LinearProgress variant="determinate" value={row.score * 100} sx={{ mt: 1 }} /></TableCell></TableRow>)}</TableBody></Table>}
        {result.extracted && <><pre className="code-block">{JSON.stringify(result.extracted, null, 2)}</pre>{!!result.reviewFields?.length && <Alert severity="warning">要確認: {result.reviewFields.join(', ')}</Alert>}</>}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>{draft.kind === 'structured-extraction' ? 'Mock: key:valueの行を抽出。モデル・datasetは抽出内容を変えません。成功数はfield coverage × runs、latencyはモデルごとの固定値です。' : 'Mock: モデルとdatasetで基準点、プロンプトのハッシュ・rubric・temperatureで比較スコアを計算。LLM性能の測定値ではありません。'}</Typography>
      </>}
    </Panel>
    <div className="handle-note"><strong>Explicit handle</strong><code>{draft.experimentId}</code><span>update → run → get_resultは、このIDを毎回送ります。サーバー再起動でデータは消えます。</span></div>
  </div>;
}
createRoot(document.getElementById('root')!).render(<LabTheme><ExperimentApp /></LabTheme>);
