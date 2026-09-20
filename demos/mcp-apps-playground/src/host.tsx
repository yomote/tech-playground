import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, Button, Chip, Stack, Typography, ToggleButton, ToggleButtonGroup, Accordion, AccordionSummary, AccordionDetails } from '@mui/material';
import { Client, StreamableHTTPClientTransport, type CallToolResult } from '@modelcontextprotocol/client';
import { AppBridge, PostMessageTransport, getToolUiResourceUri } from '@modelcontextprotocol/ext-apps/app-bridge';
import { LabTheme, LabShell, Panel, LabIcon, Eyebrow } from '@playground/ui';
import type { Settings } from '../contract';
import '@playground/ui/style.css';
import './style.css';

const client = new Client({ name: 'playground-local-host', version: '0.2.0' });
const connected = client.connect(new StreamableHTTPClientTransport(new URL('/mcp', location.href)));
void connected.catch(() => {});
type Entry = { label: string; data: unknown };
function Host() {
  const mount = useRef<HTMLDivElement>(null), bridge = useRef<AppBridge | null>(null);
  const [kind, setKind] = useState<Settings['kind']>('prompt-comparison');
  const [busy, setBusy] = useState(false), [opened, setOpened] = useState(false), [error, setError] = useState('');
  const [entries, setEntries] = useState<Entry[]>([]), [data, setData] = useState<unknown>(null);
  const [view, setView] = useState('ui');
  useEffect(() => { void connected.catch(e => setError(String(e))); }, []);
  const log = (label: string, value: unknown) => setEntries(previous => [...previous.slice(-23), { label, data: value }]);
  async function open() {
    setBusy(true); setError(''); setOpened(false); setEntries([]); setData(null); setView('ui');
    try {
      await connected; await bridge.current?.close(); mount.current!.replaceChildren();
      const tools = await client.listTools();
      const tool = tools.tools.find(t => t.name === 'create_experiment');
      if (!tool) throw new Error('create_experiment is unavailable');
      const uri = getToolUiResourceUri(tool); if (!uri) throw new Error('Tool has no UI resource');
      log('01 · tools/list — UIへの参照を発見', tool._meta);
      const args = { model: 'GPT', dataset: 'Sample A', runs: 10, kind };
      const result = await client.callTool({ name: 'create_experiment', arguments: args }) as CallToolResult;
      if (result.isError) throw new Error(JSON.stringify(result.content));
      log('02 · tools/call — フォーム定義と実験データ', { arguments: args, structuredContent: result.structuredContent }); setData(result.structuredContent);
      const resource = await client.readResource({ uri });
      log('03 · resources/read — HTML / JavaScriptを取得', { uri, mimeType: resource.contents[0].mimeType });
      const content = resource.contents[0]; if (!('text' in content)) throw new Error('Expected text UI resource');
      const iframe = document.createElement('iframe'); iframe.title = 'Experiment Dashboard MCP App'; iframe.setAttribute('sandbox', 'allow-scripts allow-forms');
      mount.current!.replaceChildren(iframe);
      const activeBridge = new AppBridge(client, { name: 'Local experiment host', version: '0.2.0' }, { serverTools: {} });
      bridge.current = activeBridge;
      activeBridge.oncalltool = async request => {
        log(`App → ${request.name}`, request);
        const response = await client.callTool(request) as CallToolResult;
        log(`${request.name} → App`, response);
        if (!response.isError) setData(response.structuredContent);
        return response;
      };
      activeBridge.oninitialized = () => {
        log('04 · App ready — 初期データをUIへ通知', { kind });
        void activeBridge.sendToolInput({ arguments: args }).catch(e => setError(String(e)));
        void activeBridge.sendToolResult(result).catch(e => setError(String(e)));
      };
      await activeBridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
      iframe.srcdoc = content.text; setOpened(true);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  return <LabShell title="Tools become experiences." subtitle="MCPの結果を、読むデータから操作できるアプリへ。入力を変え、フォームが変わるところから試してみましょう。" number="01" actions={<Chip label="Real MCP · Mock experiments" color="primary" variant="outlined" />}>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <div className="mcp-layout"><aside className="mcp-sidebar">
      <Panel title="01 / Choose an intent" subtitle="これはホスト側の操作です。選択内容をtoolの引数に渡します。">
        <Stack spacing={1.5}>
          {(['prompt-comparison', 'structured-extraction'] as const).map((value, i) => <button key={value} className={`intent-card ${kind === value ? 'chosen' : ''}`} onClick={() => setKind(value)} aria-pressed={kind === value} disabled={busy}>
            <LabIcon kind={i ? 'document' : 'code'} /><strong>{i ? '構造化データ抽出' : 'プロンプト比較'}</strong><span>{i ? '文書 → フィールド定義 → JSON' : 'Prompt A / B → 評価設定 → 比較'}</span>
          </button>)}
          <div className="code-block small-code">create_experiment({'{'}<br/>  kind: "{kind}"<br/>{'}'})</div>
          <Button variant="outlined" size="small" sx={{ alignSelf: 'flex-start', whiteSpace: 'nowrap' }} onClick={() => void open()} disabled={busy} startIcon={<LabIcon kind="play" size={15} />}>{busy ? 'Opening app…' : 'Call tool & open app'}</Button>
        </Stack>
      </Panel>
      <Panel title="何が返ってくる？">
        <ol className="mcp-explainer"><li><strong>UIへの参照</strong><p>tool定義にui://…のresource URI。</p></li><li><strong>結果データ</strong><p>tool resultにexperimentとform。選んだkindによって入力欄が変わります。</p></li><li><strong>操作できるUI</strong><p>ホストがHTML resourceを取得して表示。中のボタンがさらにMCP toolを呼びます。</p></li></ol>
        <Alert severity="info">フォームの定義は、このDemo独自のJSONです。MCP Apps自体がフォーム専用の仕様という意味ではありません。</Alert>
      </Panel>
      <Panel title="Observe the loop" subtitle="操作に応じて、実際のprotocol通信が増えます。">
        <div className="protocol-feed">{entries.length ? entries.map((entry, i) => <details key={i}><summary><span>{String(i + 1).padStart(2, '0')}</span>{entry.label}</summary><pre className="code-block">{JSON.stringify(entry.data, null, 2)}</pre></details>) : <Typography color="text.secondary" variant="body2">toolを呼ぶと通信の履歴がここに表示されます。</Typography>}</div>
      </Panel>
    </aside><section className="app-stage">
      <div className="app-stage-bar"><Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}><span className={`connection-dot ${opened ? 'connected' : ''}`} /><Typography variant="subtitle2">MCP App sandbox</Typography></Stack><ToggleButtonGroup size="small" exclusive value={view} onChange={(_, value: string | null) => value && setView(value)}><ToggleButton value="ui">Interactive UI</ToggleButton><ToggleButton value="json">Tool result JSON</ToggleButton></ToggleButtonGroup></div>
      <div hidden={view !== 'ui'}>
        {!opened && <div className="app-empty"><LabIcon kind="lab" size={56} /><Eyebrow>AN INTERFACE, DELIVERED BY A TOOL</Eyebrow><Typography variant="h5">この場所に、操作できるフォームが開きます。</Typography><Typography color="text.secondary">左の入力を選び、Call tool & open appを押してください。</Typography></div>}
        <div ref={mount} className="app-mount" />
      </div>
      {view === 'json' && <div className="json-view"><Alert severity="info" sx={{ mb: 2 }}>同じtool resultをJSONで表示しています。form.fieldsがUIの入力欄、experimentが現在の値です。HTML本体は別のUI resourceです。</Alert><pre className="code-block">{JSON.stringify(data, null, 2) ?? 'Call a tool to inspect its result.'}</pre></div>}
      <Accordion><AccordionSummary>このホストと、MCP Apps対応チャットの違い</AccordionSummary><AccordionDetails><Typography variant="body2">ここでは学習用ホストがチャットの代わりにtoolを呼び、公式AppBridgeでUIを埋め込んでいます。対応するMCP Appsホストに接続しても、同じtoolとUI resourceを利用できます。実験データはmockですが、MCPの通信とUIからのtool呼び出しは実際に動いています。</Typography></AccordionDetails></Accordion>
    </section></div>
  </LabShell>;
}
createRoot(document.getElementById('root')!).render(<LabTheme><Host /></LabTheme>);
