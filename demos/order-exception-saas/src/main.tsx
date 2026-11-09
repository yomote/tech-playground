import { StrictMode, useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Alert, Box, Button, Chip, CircularProgress, Container, CssBaseline, Divider,
  FormControl, FormControlLabel, FormLabel, Paper, Radio, RadioGroup, Stack,
  TextField, ThemeProvider, Typography, createTheme,
} from '@mui/material';
import './style.css';

type Status = 'exception_detected' | 'awaiting_approval' | 'shipment_instructed';
type Order = {
  id: string;
  customerName: string;
  itemName: string;
  sku?: string;
  quantity: number;
  dueAt: string;
  status: Status;
  version: number;
  nextActor: string;
  nextAction: string;
  dataMode: 'synthetic_fixture';
};
type Exception = { type: string; message: string; detectedAt: string; contractConstraint?: string };
type Allocation = { warehouseId: string; quantity: number; deliveryAt: string };
type Option = {
  id: string;
  title: string;
  summary: string;
  allocations: Allocation[];
  additionalCostJpy: number;
  requiresCustomerConsent: boolean;
  customerConsentField: 'acceptedSplit' | null;
  sourceRefs: string[];
  generationMode: 'fixed_fixture';
  dataMode: 'synthetic_fixture';
};
type CustomerResponse = { acceptedSplit: boolean; note: string; recordedAt: string };
type Approval = { optionId: string; reason: string; approvedAt: string; actorRole: string };
type Shipment = {
  reservations: { warehouseId: string; quantity: number }[];
  instructions: Allocation[];
  instructedAt: string;
};
type OrderDetail = {
  order: Order;
  exception: Exception;
  options: Option[];
  customerResponse: CustomerResponse | null;
  approval: Approval | null;
  shipment: Shipment | null;
  inventory?: { warehouseId: string; available: number }[];
};
type RunEvent = {
  sequence: number;
  kind: 'status' | 'manager_selection' | 'source_read' | 'source_refused' | 'draft_submitted';
  actor: string;
  message: string;
  sourceId?: string;
  createdAt: string;
};
type AgentRun = {
  id: string;
  orderId: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'timed_out';
  agentVersion: string;
  executionMode: 'live_model' | 'test_double';
  modelRoute: string;
  modelId: string;
  dataMode: 'synthetic_fixture';
  startedAt: string;
  endedAt?: string | null;
  events: RunEvent[];
  draft?: {
    body: string;
    plan: { recommendation: string; alternatives: string; customerQuestion: string };
    contractReviewStatus: string;
    generationMode: 'live_model' | 'test_double';
    requiresHumanApproval: boolean;
    shipmentChanged: boolean;
    sourceReads: unknown[];
    managerEvents: unknown[];
  };
  errorCode?: string;
};
type Route =
  | { screen: 'list' }
  | { screen: 'order' | 'response' | 'approval' | 'result'; orderId: string }
  | { screen: 'agent'; orderId: string; runId?: string };

const theme = createTheme({
  palette: {
    primary: { main: '#183552', dark: '#11283f', contrastText: '#ffffff' },
    background: { default: '#f2f5f7', paper: '#ffffff' },
    text: { primary: '#17314c', secondary: '#4e6072' },
    warning: { main: '#986018' },
    success: { main: '#276f57' },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: '"Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", Meiryo, sans-serif',
    body1: { fontSize: '1rem', lineHeight: 1.65 },
    body2: { fontSize: '0.925rem', lineHeight: 1.6 },
    button: { fontSize: '1rem', fontWeight: 700, textTransform: 'none' },
  },
  components: {
    MuiButton: { styleOverrides: { root: { minHeight: 44, borderRadius: 8, boxShadow: 'none' } } },
    MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
  },
});

const stages = ['受注と例外', '顧客回答', '出荷承認', '出荷指示'];
const statusLabel: Record<Status, string> = {
  exception_detected: '出荷例外',
  awaiting_approval: '出荷承認待ち',
  shipment_instructed: '出荷指示済み',
};

function parseRoute(): Route {
  const parts = window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'orders' && parts[1]) {
    const orderId = decodeURIComponent(parts[1]);
    if (parts[2] === 'agent') return { screen: 'agent', orderId, runId: parts[3] ? decodeURIComponent(parts[3]) : undefined };
    if (parts[2] === 'response') return { screen: 'response', orderId };
    if (parts[2] === 'approval') return { screen: 'approval', orderId };
    if (parts[2] === 'result') return { screen: 'result', orderId };
    return { screen: 'order', orderId };
  }
  return { screen: 'list' };
}

function go(route: Route) {
  window.location.hash = route.screen === 'list'
    ? '#/'
    : `#/orders/${encodeURIComponent(route.orderId)}${route.screen === 'order' ? '' : route.screen === 'agent'
      ? `/agent${route.runId ? `/${encodeURIComponent(route.runId)}` : ''}` : `/${route.screen}`}`;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    });
  } catch {
    throw new Error('受注データを取得できません。業務APIの起動を確認してください。');
  }
  if (!response.ok) {
    let message = '';
    try {
      const body = await response.json() as { message?: string };
      message = body.message || '';
    } catch { /* The HTTP code still identifies the failure. */ }
    throw new Error(message || `操作を完了できませんでした（HTTP ${response.status}）。`);
  }
  return response.json() as Promise<T>;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
}

function yen(value: number) {
  return `¥${value.toLocaleString('ja-JP')}`;
}

function warehouseName(id: string) {
  return ({ 'warehouse-a': '倉庫A', 'warehouse-b': '倉庫B' } as Record<string, string>)[id] || id;
}

function sourceName(id: string) {
  if (/^order-so-24017-v\d+$/i.test(id)) return '受注条件（保存版）';
  const stockSnapshot = /^stock-warehouse-([ab])-snapshot-order-v(\d+)$/.exec(id);
  if (stockSnapshot) return `倉庫${stockSnapshot[1].toUpperCase()}の在庫（受注第${stockSnapshot[2]}版時点）`;
  return ({
    'stock-a-v1': '倉庫Aの在庫（第1版）',
    'stock-b-v1': '倉庫Bの在庫（第1版）',
    'carrier-normal-v1': '通常便の集荷条件（第1版）',
    'carrier-express-v1': '専用便の集荷条件（第1版）',
    'contract-split-v1': '分納に関する契約条項（第1版）',
    'order-SO-24017': '受注条件',
    'stock-warehouse-a': '倉庫Aの在庫',
    'stock-warehouse-b': '倉庫Bの在庫',
    'carrier-a-normal-v1': '倉庫Aの通常便（第1版）',
    'carrier-b-normal-v1': '倉庫Bの通常便（第1版）',
    'carrier-b-express-v1': '倉庫Bの専用便（第1版）',
  } as Record<string, string>)[id] || id;
}

function plainText(value: string) {
  return value.replace(/^\s*```[^\n]*$/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*]\s+/gm, '・')
    .trim();
}

function Loading() {
  return <Box className="loading" role="status"><CircularProgress size={26} /><Typography>受注を読み込み中です</Typography></Box>;
}

function Retry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <Alert severity="error" action={<Button color="inherit" onClick={onRetry}>再読み込み</Button>}>{message}</Alert>;
}

function Progress({ current }: { current: number }) {
  return <Box className="progress" aria-label="出荷例外の進行">
    {stages.map((stage, index) => <Box
      key={stage}
      className={`progress-step ${index === current ? 'is-current' : ''} ${index < current ? 'is-done' : ''}`}
      aria-current={index === current ? 'step' : undefined}
    >
      <span className="progress-number">{index + 1}</span><span>{stage}</span>
    </Box>)}
  </Box>;
}

function Lead({ title, actor, detail }: { title: string; actor: string; detail: string }) {
  return <Box className="lead">
    <Typography component="h1" className="lead-title">{title}</Typography>
    <Typography className="lead-detail">{detail}</Typography>
    <Typography className="next-actor">次に動く人：{actor}</Typography>
  </Box>;
}

function OrderSummary({ order }: { order: Order }) {
  return <Box className="order-summary">
    <Box className="order-summary-top">
      <Typography color="text.secondary" variant="body2">受注 {order.id} / {order.customerName}</Typography>
      <Chip size="small" color={order.status === 'shipment_instructed' ? 'success' : 'warning'}
        variant="outlined" label={statusLabel[order.status]} />
    </Box>
    <Typography component="h2" className="order-heading">{order.itemName} {order.quantity}箱</Typography>
    <Typography variant="body2" color="text.secondary">約束した納品時刻：{formatDate(order.dueAt)}</Typography>
  </Box>;
}

function SourceDetails({ detail }: { detail: OrderDetail }) {
  return <details className="source-details">
    <summary>在庫・配送・契約の根拠を確認する</summary>
    <Typography variant="body2" sx={{ mt: 1 }}>例外：{detail.exception.message}</Typography>
    <Typography variant="body2" color="text.secondary">検知：{formatDate(detail.exception.detectedAt)}</Typography>
    {detail.exception.contractConstraint && <Typography variant="body2" sx={{ mt: 1 }}>
      契約条件：{detail.exception.contractConstraint}
    </Typography>}
    {(detail.inventory?.length ?? 0) > 0 && <Typography variant="body2" sx={{ mt: 1 }}>
      現在の在庫：{detail.inventory!.map(item => `${warehouseName(item.warehouseId)} ${item.available}箱`).join('、')}
    </Typography>}
    <Divider sx={{ my: 2 }} />
    {detail.options.map(option => <Box key={option.id} sx={{ mb: 2 }}>
      <Typography sx={{ fontWeight: 700 }}>{option.title}</Typography>
      <Typography variant="body2">参照データ：{option.sourceRefs.map(sourceName).join('、') || '記録なし'}</Typography>
      {option.allocations.map((allocation, index) => <Typography variant="body2" key={index}>
        {warehouseName(allocation.warehouseId)}から{allocation.quantity}箱、到着予定 {formatDate(allocation.deliveryAt)}
      </Typography>)}
    </Box>)}
  </details>;
}

function OptionCard({ option, selected, onSelect }: { option: Option; selected?: boolean; onSelect?: () => void }) {
  const selectable = Boolean(onSelect);
  return <Paper
    className={`option-card ${selected ? 'is-selected' : ''}`}
    component={selectable ? 'button' : 'div'}
    onClick={onSelect}
    type={selectable ? 'button' : undefined}
    elevation={0}
    aria-pressed={selectable ? Boolean(selected) : undefined}
  >
    <Box className="option-card-head">
      <Typography component="h3" className="option-title">{option.title}</Typography>
      {selectable && <span className="choice-mark" aria-hidden="true">{selected ? '選択中' : '選択'}</span>}
    </Box>
    <Typography>{option.summary}</Typography>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ mt: 1.5 }}>
      <Typography variant="body2">追加費用：{yen(option.additionalCostJpy)}</Typography>
      <Typography variant="body2">{option.requiresCustomerConsent ? '顧客の分納承諾が必要' : '分納承諾は不要'}</Typography>
    </Stack>
    <Typography className="fixture-label">合成データで固定した対処案</Typography>
  </Paper>;
}

function OrderList({ onOpen }: { onOpen: (id: string) => void }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { setOrders((await api<{ orders: Order[] }>('/api/orders')).orders); }
    catch (cause) { setError((cause as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return <>
    <Lead title="対応する受注を選ぶ" detail="納品時刻に影響する出荷例外を確認します。" actor="受注担当者" />
    {loading ? <Loading /> : error ? <Retry message={error} onRetry={() => void load()} /> : orders.length === 0
      ? <Paper className="content-panel"><Typography>対応する受注はありません。</Typography></Paper>
      : <Stack spacing={2}>{orders.map(order => <Paper className="order-card" key={order.id}>
        <Box className="order-card-content">
          <Typography variant="body2" color="text.secondary">{order.customerName} / 受注 {order.id}</Typography>
          <Typography component="h2" className="order-card-title">{order.itemName} {order.quantity}箱</Typography>
          <Typography variant="body2">納品 {formatDate(order.dueAt)} / {statusLabel[order.status]}</Typography>
        </Box>
        <Button variant="contained" onClick={() => onOpen(order.id)}>受注を開く</Button>
      </Paper>)}</Stack>}
  </>;
}

function OrderPage({ detail, onNext, onAgent }: { detail: OrderDetail; onNext: () => void; onAgent: () => void }) {
  const { order } = detail;
  const initial = order.status === 'exception_detected';
  const fixedOptions = <>
    <Typography component="h2" className="section-title">固定の対処案</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
      案は合成データで固定しています。AIが作った案ではありません。
    </Typography>
    <Stack spacing={1.5}>{detail.options.map(option => <OptionCard option={option} key={option.id} />)}</Stack>
    <SourceDetails detail={detail} />
  </>;
  return <>
    <Progress current={0} />
    <Lead title={initial ? '出荷例外を確認する' : order.status === 'awaiting_approval'
      ? '出荷責任者の承認を待つ' : '出荷指示の結果を確認する'}
      detail={initial ? detail.exception.message : order.nextAction} actor={order.nextActor} />
    <Paper className="content-panel">
      <OrderSummary order={order} />
      <Divider sx={{ my: 3 }} />
      {initial && <Box className="agent-entry">
        <Typography component="h2" className="section-title">AIに調査を依頼する</Typography>
        <Typography>受注、在庫、配送便、契約の合成資料を調べ、対処案と顧客への質問を作ります。</Typography>
        <Button variant="contained" size="large" onClick={onAgent} sx={{ mt: 2 }}>AIで出荷方法を調べる</Button>
      </Box>}
      {initial ? <details className="manual-options"><summary>固定の対処案を使って手作業で進める</summary>
        {fixedOptions}
        <Button variant="outlined" size="large" onClick={onNext} sx={{ mt: 3 }}>顧客回答を登録する</Button>
      </details> : <>
        {fixedOptions}
        <Button variant="contained" size="large" onClick={onNext} sx={{ mt: 3 }}>
          {order.status === 'shipment_instructed' ? '出荷結果を見る' : '出荷承認へ進む'}
        </Button>
      </>}
      {order.status === 'awaiting_approval' &&
        <Button variant="text" onClick={onAgent} sx={{ mt: 2 }}>保存済みのAI調査を確認する</Button>}
    </Paper>
  </>;
}

function actorName(actor: string) {
  return ({
    ShipmentManager: '管理Agent',
    StockInvestigator: '在庫調査Agent',
    DispatchPlanner: '配送調整Agent',
    tool: '資料Tool',
    system: '実行システム',
  } as Record<string, string>)[actor] || actor;
}

function eventName(kind: RunEvent['kind']) {
  return ({
    status: '実行状態',
    manager_selection: '担当Agentを選択',
    source_read: '資料を読取り',
    source_refused: '資料の読取りを拒否',
    draft_submitted: 'Agent内で案を提出',
  } as Record<RunEvent['kind'], string>)[kind];
}

function eventMessage(message: string) {
  return plainText(message)
    .replaceAll('StockInvestigator', '在庫調査Agent')
    .replaceAll('DispatchPlanner', '配送調整Agent')
    .replaceAll('ShipmentManager', '管理Agent');
}

function AgentPage({ detail, runId, onStarted, onManual }: {
  detail: OrderDetail;
  runId?: string;
  onStarted: (runId: string) => void;
  onManual: () => void;
}) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [history, setHistory] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(Boolean(runId));
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const operationId = useRef<string | null>(null);
  const orderId = detail.order.id;

  useEffect(() => {
    if (runId) return;
    let active = true;
    void api<{ orderId: string; agentRuns: AgentRun[] }>(`/api/orders/${encodeURIComponent(orderId)}/agent-runs`)
      .then(response => { if (active) setHistory(response.agentRuns); })
      .catch(() => { if (active) setError('過去のAI調査を取得できません。受注から開き直してください。'); });
    return () => { active = false; };
  }, [orderId, runId, refreshKey]);

  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: number | undefined;
    setLoading(true);
    const load = async () => {
      try {
        const response = await api<{ run: AgentRun }>(`/api/agent-runs/${encodeURIComponent(runId)}`);
        if (!active) return;
        if (response.run.orderId !== orderId) {
          setError('この受注のAI調査ではありません。受注から開き直してください。');
          setLoading(false);
          return;
        }
        setRun(response.run);
        setError('');
        setLoading(false);
        if (response.run.status === 'queued' || response.run.status === 'running') {
          timer = window.setTimeout(() => void load(), 1500);
        }
      } catch {
        if (active) {
          setError('実行状況を取得できません。通信を確認して状態を更新してください。');
          setLoading(false);
        }
      }
    };
    void load();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [runId, orderId, refreshKey]);

  const start = async () => {
    operationId.current ??= crypto.randomUUID();
    setStarting(true); setError('');
    try {
      const response = await api<{ run: AgentRun }>(`/api/orders/${encodeURIComponent(orderId)}/agent-runs`, {
        method: 'POST', body: JSON.stringify({ operationId: operationId.current }),
      });
      operationId.current = null;
      setRun(response.run);
      onStarted(response.run.id);
    } catch (cause) {
      const message = (cause as Error).message;
      setError(message.includes('HTTP 503') || message.includes('モデル')
        ? 'AI調査を開始できません。モデル接続の設定を確認してください。顧客回答の登録は手作業で続けられます。'
        : message);
    } finally { setStarting(false); }
  };

  const active = run?.status === 'queued' || run?.status === 'running';
  const failed = run?.status === 'failed' || run?.status === 'timed_out';
  const completed = run?.status === 'completed';
  const liveRun = run?.executionMode === 'live_model';
  const selectedAgents = [...new Set((run?.events ?? [])
    .filter(event => event.kind === 'manager_selection')
    .map(event => event.message.includes('StockInvestigator') ? '在庫調査Agent'
      : event.message.includes('DispatchPlanner') ? '配送調整Agent' : eventMessage(event.message)))];
  const readSources = [...new Set((run?.events ?? [])
    .filter(event => event.kind === 'source_read' && event.sourceId)
    .map(event => sourceName(event.sourceId!)))];
  const title = !runId ? 'AIで出荷方法を調べる'
    : loading && !run ? 'AI調査の記録を確認しています'
      : run?.status === 'running' ? 'AIが資料を調べています'
        : run?.status === 'queued' ? 'AI調査の開始を待っています'
          : completed ? liveRun ? '実モデルの未検証下書きを確認する' : '試験ランナーの案を確認する'
            : run?.status === 'timed_out' ? 'AI調査は時間切れです'
              : failed ? 'AI調査を完了できませんでした' : '実行状況を確認できません';
  const intro = !runId ? '受注、在庫、配送便、契約の合成資料を調べ、出荷方法と顧客への質問を作ります。'
    : completed ? 'runの処理は完了しました。下書きの数量配分、納期、費用、契約への適合を業務担当者が確認してください。'
      : failed ? 'このrunから出荷案は採用していません。手作業で調整を続けられます。'
        : '管理Agentが専門Agentを選び、許可された合成資料を調べます。';

  return <>
    <Progress current={0} />
    <Lead title={title} detail={intro} actor={active ? 'AIエージェント' : '受注担当者'} />
    {loading && !run ? <Loading /> : <>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Paper className="content-panel">
        <OrderSummary order={detail.order} />
        {!runId ? <>
          <Typography sx={{ mt: 3 }}>設定されたモデルで調査します。入力は架空の受注と合成資料です。</Typography>
          <Button variant="contained" size="large" disabled={starting || detail.order.status === 'shipment_instructed'}
            onClick={() => void start()} sx={{ mt: 3 }}>
            {starting ? 'AI調査を開始中' : 'AI調査を開始する'}
          </Button>
        </> : <>
          {active && <Stack direction="row" spacing={1.5} role="status" sx={{ mt: 3, alignItems: 'center' }}>
            <CircularProgress size={22} />
            <Typography>{run?.status === 'queued' ? '実行開始を待っています' : '保存済みrunの経過を更新中です'}</Typography>
          </Stack>}
          {failed && <Alert severity="warning" sx={{ mt: 3 }}>
            {run?.status === 'timed_out' ? '実行期限に達しました。' : 'Agentが案を完成できませんでした。'}
            {run?.events.some(event => event.kind === 'draft_submitted')
              ? '途中でAgent内の案提出は記録されましたが、業務アプリに完成案は保存されていません。'
              : '業務アプリに完成案は保存されていません。'}
            出荷案は承認へ送られていません。
          </Alert>}
          {completed && !run?.draft && <Alert severity="warning" sx={{ mt: 3 }}>実行は終了しましたが、保存された案がありません。</Alert>}
          {completed && run?.draft && <Box sx={{ mt: 3 }}>
            <Alert severity="warning" sx={{ mb: 3 }}>
              {liveRun ? '実モデル' : '試験ランナー'}の処理は完了しました。これは未検証の下書きです。数量配分、契約への適合、費用、納期、記述内容を確認するまで顧客への提示や出荷承認に使わないでください。この案から出荷指示は作成していません。
            </Alert>
            <Typography component="h2" className="section-title">{liveRun ? '実モデルが作った未検証下書き' : '試験ランナーが返した案'}</Typography>
            <Typography className="agent-draft">{plainText(run.draft.plan.recommendation)}</Typography>
            <Typography component="h2" className="section-title" sx={{ mt: 3 }}>ほかの候補</Typography>
            <Typography className="agent-draft">{plainText(run.draft.plan.alternatives)}</Typography>
            <Typography component="h2" className="section-title" sx={{ mt: 3 }}>顧客に確認すること</Typography>
            <Typography className="agent-draft">{plainText(run.draft.plan.customerQuestion)}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
              契約確認：{run.draft.contractReviewStatus === 'not_evaluated' ? '未評価' : run.draft.contractReviewStatus}
            </Typography>
          </Box>}
          {error && <Button variant="outlined" onClick={() => setRefreshKey(value => value + 1)} sx={{ mt: 2 }}>状態を更新する</Button>}
          {failed && detail.order.status !== 'shipment_instructed' && <Button variant="outlined" disabled={starting}
            onClick={() => void start()} sx={{ mt: 3 }}>
            {starting ? '再試行を開始中' : 'AI調査を再試行する'}
          </Button>}
        </>}
        {runId && <Typography variant="body2" color="text.secondary" sx={{ mt: 3 }}>
          次の手作業画面は合成の固定選択肢を使います。AIの下書きは自動反映されません。
        </Typography>}
        <Button variant={completed ? 'contained' : 'text'} onClick={onManual} sx={{ mt: 2 }}>
          {runId ? '固定選択肢で顧客回答を登録する' : '顧客回答を手作業で登録する'}
        </Button>
      </Paper>
      {run && <Paper className="content-panel secondary-panel">
        <Typography component="h2" className="section-title">保存済みの調査経過</Typography>
        {run.events.length === 0 ? <Typography color="text.secondary">まだ記録はありません。実行状態を更新中です。</Typography> : <>
          {selectedAgents.length > 0 && <Typography>選ばれた担当：{selectedAgents.join('、')}</Typography>}
          {readSources.length > 0 && <Typography sx={{ mt: 1 }}>読んだ資料：{readSources.join('、')}</Typography>}
          {run.events.some(event => event.kind === 'draft_submitted') &&
            <Typography sx={{ mt: 1 }}>Agent内で案を提出しました。業務アプリに完成案が保存されたかは上の実行状態で確認してください。</Typography>}
          <details className="source-details"><summary>すべての操作履歴（{run.events.length}件）</summary>
            <Box component="ol" className="run-events">{run.events.map(event => <Box component="li"
              className="run-event" key={event.sequence}>
              <Typography variant="body2" color="text.secondary">{formatDate(event.createdAt)} / {actorName(event.actor)}</Typography>
              <Typography sx={{ fontWeight: 700 }}>{eventName(event.kind)}</Typography>
              <Typography className="agent-event-message">{eventMessage(event.message)}</Typography>
              {event.sourceId && <Typography variant="body2" color="text.secondary">資料：{sourceName(event.sourceId)}</Typography>}
            </Box>)}</Box>
          </details>
        </>}
        <details className="source-details"><summary>実行IDと接続経路を確認する</summary>
          <Typography variant="body2">run ID：{run.id}</Typography>
          <Typography variant="body2">実行モード：{liveRun ? '実モデル' : '試験ランナー'}</Typography>
          <Typography variant="body2">Agent版：{run.agentVersion} / モデル：{run.modelId} / 経路：{run.modelRoute}</Typography>
          {run.errorCode && <Typography variant="body2">終了コード：{run.errorCode}</Typography>}
          <Typography variant="body2">経過は業務APIに保存されたrunを読み直した結果です。ライブtoken配信ではありません。OpenFGAとAgentgatewayはこの受注の実行に未接続です。</Typography>
        </details>
      </Paper>}
      {!runId && history.length > 0 && <Paper className="content-panel secondary-panel">
        <Typography component="h2" className="section-title">以前のAI調査</Typography>
        <Stack spacing={1}>{history.slice(0, 5).map(item => <Box key={item.id} className="history-row">
          <Typography variant="body2">{formatDate(item.startedAt)} / {item.executionMode === 'live_model' ? '実モデル' : '試験ランナー'} /
            {item.status === 'completed' ? '処理完了・業務未評価'
            : item.status === 'timed_out' ? '時間切れ' : item.status === 'failed' ? '失敗' : '実行中'}</Typography>
          <Button size="small" onClick={() => onStarted(item.id)}>記録を開く</Button>
        </Box>)}</Stack>
      </Paper>}
    </>}
  </>;
}

function ResponsePage({ detail, onSaved }: { detail: OrderDetail; onSaved: (next: OrderDetail) => void }) {
  const [acceptedSplit, setAcceptedSplit] = useState<boolean | null>(detail.customerResponse?.acceptedSplit ?? null);
  const [note, setNote] = useState(detail.customerResponse?.note ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const save = async () => {
    if (acceptedSplit === null || !note.trim()) return;
    setBusy(true); setError('');
    try {
      const input = {
        acceptedSplit,
        note: note.trim(),
        expectedVersion: detail.order.version,
        actorRole: 'order_clerk',
      };
      const signature = JSON.stringify(input);
      const id = operation.current?.signature === signature ? operation.current.id : crypto.randomUUID();
      operation.current = { signature, id };
      const next = await api<OrderDetail>(`/api/orders/${encodeURIComponent(detail.order.id)}/customer-response`, {
        method: 'POST',
        body: JSON.stringify({ ...input, operationId: id }),
      });
      onSaved(next);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  return <>
    <Progress current={1} />
    <Lead title="顧客の回答を記録する" detail="分納できるか、顧客購買担当者に確認した結果を登録します。" actor="受注担当者" />
    <Paper className="content-panel">
      <OrderSummary order={detail.order} />
      <Divider sx={{ my: 3 }} />
      <FormControl>
        <FormLabel sx={{ color: 'text.primary', fontWeight: 700 }}>顧客は分納を承諾しましたか</FormLabel>
        <RadioGroup value={acceptedSplit === null ? '' : acceptedSplit ? 'yes' : 'no'}
          onChange={event => setAcceptedSplit(event.target.value === 'yes')}>
          <FormControlLabel value="yes" control={<Radio />} label="承諾した" />
          <FormControlLabel value="no" control={<Radio />} label="承諾しなかった" />
        </RadioGroup>
      </FormControl>
      <TextField fullWidth multiline minRows={3} label="顧客からの回答"
        value={note} onChange={event => setNote(event.target.value)}
        placeholder="例：25箱を先行、残り15箱を翌々日とする案を承諾"
        sx={{ mt: 2 }} />
      {error && <Alert severity="error" sx={{ mt: 2 }} action={<Button color="inherit" onClick={() => window.location.reload()}>最新状態を読む</Button>}>{error}</Alert>}
      <Button variant="contained" size="large" onClick={() => void save()}
        disabled={busy || acceptedSplit === null || !note.trim()} sx={{ mt: 3 }}>
        {busy ? '回答を保存中' : '顧客回答を保存する'}
      </Button>
    </Paper>
  </>;
}

function ApprovalPage({ detail, onSaved }: { detail: OrderDetail; onSaved: (next: OrderDetail) => void }) {
  const [optionId, setOptionId] = useState(detail.options[0]?.id ?? '');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const option = detail.options.find(item => item.id === optionId);
  const consentMissing = Boolean(option?.requiresCustomerConsent && !detail.customerResponse?.acceptedSplit);
  const save = async () => {
    if (!option || !reason.trim() || consentMissing) return;
    setBusy(true); setError('');
    try {
      const input = {
        optionId,
        reason: reason.trim(),
        expectedVersion: detail.order.version,
        actorRole: 'shipping_manager',
      };
      const signature = JSON.stringify(input);
      const id = operation.current?.signature === signature ? operation.current.id : crypto.randomUUID();
      operation.current = { signature, id };
      const next = await api<OrderDetail>(`/api/orders/${encodeURIComponent(detail.order.id)}/decision`, {
        method: 'POST',
        body: JSON.stringify({ ...input, operationId: id }),
      });
      onSaved(next);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  return <>
    <Progress current={2} />
    <Lead title="出荷方法を承認する" detail="顧客回答と納品時刻を確認し、出荷する案を一つ選びます。" actor="出荷責任者" />
    <Paper className="content-panel">
      <OrderSummary order={detail.order} />
      <Alert severity="info" sx={{ mt: 2 }}>このデモの役割切替は模擬です。ログインと認可判定は未接続です。</Alert>
      <Divider sx={{ my: 3 }} />
      <Typography component="h2" className="section-title">顧客回答</Typography>
      {detail.customerResponse
        ? <Typography>{detail.customerResponse.acceptedSplit ? '分納を承諾' : '分納は不可'}：{detail.customerResponse.note}</Typography>
        : <Alert severity="warning">顧客回答は未登録です。分納案を承認できません。</Alert>}
      <Typography component="h2" className="section-title" sx={{ mt: 3 }}>出荷案を選ぶ</Typography>
      <Stack spacing={1.5}>{detail.options.map(item =>
        <OptionCard key={item.id} option={item} selected={optionId === item.id} onSelect={() => setOptionId(item.id)} />)}
      </Stack>
      {consentMissing && <Alert severity="warning" sx={{ mt: 2 }}>分納案には顧客の承諾が必要です。別の案を選ぶか、回答を登録してください。</Alert>}
      <TextField fullWidth multiline minRows={2} label="承認理由" value={reason}
        onChange={event => setReason(event.target.value)} sx={{ mt: 3 }} />
      {error && <Alert severity="error" sx={{ mt: 2 }} action={<Button color="inherit" onClick={() => window.location.reload()}>最新状態を読む</Button>}>{error}</Alert>}
      <Button variant="contained" size="large" onClick={() => void save()}
        disabled={busy || !optionId || !reason.trim() || consentMissing} sx={{ mt: 3 }}>
        {busy ? '出荷指示を保存中' : 'この案を承認し、出荷指示を作る'}
      </Button>
      <SourceDetails detail={detail} />
    </Paper>
  </>;
}

function ResultPage({ detail }: { detail: OrderDetail }) {
  const { shipment, approval } = detail;
  return <>
    <Progress current={3} />
    <Lead title={shipment && approval ? '出荷指示を保存しました' : '出荷指示は未作成です'}
      detail={shipment && approval ? '承認した案の引当と出荷指示を、受注に記録しました。' : '出荷責任者の承認後に結果を表示します。'}
      actor={shipment && approval ? '倉庫担当者' : detail.order.nextActor} />
    <Paper className="content-panel">
      <OrderSummary order={detail.order} />
      {!shipment || !approval ? <Alert severity="warning" sx={{ mt: 2 }}>出荷結果を確認できません。受注を再読み込みしてください。</Alert> : <>
        <Divider sx={{ my: 3 }} />
        <Typography component="h2" className="section-title">引当した在庫</Typography>
        <Stack spacing={1}>{shipment.reservations.map((item, index) =>
          <Typography key={index}>{warehouseName(item.warehouseId)}：{item.quantity}箱</Typography>)}</Stack>
        <Typography component="h2" className="section-title" sx={{ mt: 3 }}>出荷指示</Typography>
        <Stack spacing={1}>{shipment.instructions.map((item, index) =>
          <Typography key={index}>{warehouseName(item.warehouseId)}から{item.quantity}箱、到着予定 {formatDate(item.deliveryAt)}</Typography>)}</Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>承認理由：{approval.reason}</Typography>
        <Typography variant="body2" color="text.secondary">出荷指示の作成：{formatDate(shipment.instructedAt)}</Typography>
      </>}
    </Paper>
  </>;
}

function App() {
  const [route, setRoute] = useState<Route>(parseRoute);
  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const update = () => setRoute(parseRoute());
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  const load = useCallback(async () => {
    if (route.screen === 'list') { setDetail(null); return; }
    setLoading(true); setError('');
    try {
      setDetail(await api<OrderDetail>(`/api/orders/${encodeURIComponent(route.orderId)}`));
    } catch (cause) { setError((cause as Error).message); }
    finally { setLoading(false); }
  }, [route]);
  useEffect(() => { void load(); }, [load]);
  const afterResponse = (next: OrderDetail) => {
    setDetail(next);
    go({ screen: 'approval', orderId: next.order.id });
  };
  const afterDecision = (next: OrderDetail) => {
    setDetail(next);
    go({ screen: 'result', orderId: next.order.id });
  };
  const orderId = route.screen === 'list' ? '' : route.orderId;
  return <ThemeProvider theme={theme}><CssBaseline />
    <Box className="app-header"><Container maxWidth="md" className="header-inner">
      <Button className="brand" color="primary" onClick={() => go({ screen: 'list' })}>卸売受注</Button>
      <Chip className="mode-chip" size="small" variant="outlined" label="合成データ・認可未接続" />
    </Container></Box>
    <Container maxWidth="md" component="main" className="main-content">
      {route.screen !== 'list' && <Button variant="text" onClick={() => go({ screen: 'list' })} sx={{ mb: 2 }}>受注一覧に戻る</Button>}
      {route.screen === 'list' ? <OrderList onOpen={id => go({ screen: 'order', orderId: id })} />
        : loading ? <Loading />
          : error ? <Retry message={error} onRetry={() => void load()} />
            : detail && detail.order.id === orderId
              ? route.screen === 'order' ? <OrderPage detail={detail}
                onAgent={() => go({ screen: 'agent', orderId })}
                onNext={() => go({ screen: detail.order.status === 'shipment_instructed' ? 'result'
                  : detail.order.status === 'awaiting_approval' ? 'approval' : 'response', orderId })} />
                : route.screen === 'agent' ? <AgentPage key={`${orderId}-${route.runId || 'start'}`}
                  detail={detail} runId={route.runId}
                  onStarted={runId => go({ screen: 'agent', orderId, runId })}
                  onManual={() => go({ screen: 'response', orderId })} />
                  : route.screen === 'response' ? <ResponsePage key={`${orderId}-${detail.order.version}`}
                  detail={detail} onSaved={afterResponse} />
                  : route.screen === 'approval' ? <ApprovalPage key={`${orderId}-${detail.order.version}`}
                    detail={detail} onSaved={afterDecision} />
                    : <ResultPage detail={detail} />
              : null}
    </Container>
  </ThemeProvider>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
