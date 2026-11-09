import { StrictMode, useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Container, CssBaseline,
  Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel,
  Paper, Stack, TextField, ThemeProvider, Typography,
  createTheme,
} from '@mui/material';
import { InventoryAuthPanel } from './InventoryAuthPanel';
import { initializeInventoryIdentity, inventoryAccessToken, inventoryIdentity } from './inventory-identity';
import './style.css';

const orderId = 'SO-24017';
const trialBase = '/trial';
const base = `${trialBase}/api/orders/${orderId}`;
const requestText = '合成受注40箱の不足15箱を調べ、在庫・便・契約に根拠がある別送案を示してください。顧客回答と出荷承認は待ってください。';

type Allocation = { warehouseId: string; quantity: number; deliveryAt: string };
type ShipmentPlan = { warehouseId: string; quantityBoxes: number; deliveryAt: string; carrierSourceId?: string };
type Proposal = {
  id: string;
  runId?: string;
  shipments: ShipmentPlan[];
  additionalCostJpy: number;
  acknowledgedTerms?: { allocations: Allocation[]; additionalCostJpy: number };
  approvalAvailability?: string;
  sourceRefs?: string[];
  customerDecisionFields?: string[];
};
type Event = { sequence: number; kind: string; actor: string; message: string; sourceId?: string; createdAt: string };
type ToolResponse = { toolName: string; sourceIds: string[]; capturedAt: string };
type InventoryCountResult = {
  orderId: string; runId: string; orderVersion: number; warehouseId: string;
  previousAvailableBoxes: number; availableBoxes: number; reason: string;
  actorRole: 'warehouse_clerk'; dataMode: 'synthetic_fixture';
  authenticationMode: 'keycloak_oidc_openfga_local_trial';
};
type SourceRefAttempt = {
  attemptNo: number; proposalIndex: number; submittedRefs: string[]; expectedRefs: string[];
  submittedShape: string; duplicateRefs: string[];
  outcome: string; errorCode?: string; fieldPath?: string; reason?: string; createdAt: string;
};
type Run = {
  id: string;
  trialId: string;
  status: string;
  executionMode: string;
  requestText?: string;
  modelId?: string;
  modelRoute?: string;
  agentVersion?: string;
  errorCode?: string;
  modelFinalReply?: string;
  sourceSnapshot?: { order?: { version?: number; dueAt?: string } };
  events: Event[];
  toolResponses?: ToolResponse[];
  sourceRefAttempts?: SourceRefAttempt[];
  draft?: {
    body?: string;
    plan?: { proposals?: Proposal[]; limitations?: string[]; customerQuestions?: string[] };
    businessReviewStatus?: string;
    contractReviewStatus?: string;
    projectionMode?: string;
    generationMode?: string;
    finalReplyMode?: string;
    modelFinalReplyReceived?: boolean;
  };
};
type TrialArchive = {
  archiveId: string;
  trialId: string;
  orderId?: string;
  runIds: string[];
  completedAt: string;
  archivedAt?: string;
  dataMode?: string;
  outcome: {
    order?: { status?: string; version?: number };
    customerResponse?: { selectedOptionId?: string; runId?: string } | null;
    approval?: { optionId?: string; runId?: string } | null;
    sourceReviewRequest?: { id?: string; status?: string; reason?: string; response?: { outcome?: string; reason?: string; respondedAt?: string } | null } | null;
    shipment?: { reservations?: { warehouseId: string; quantity: number }[]; instructions?: Allocation[] } | null;
    inventory?: { warehouseId: string; available: number }[];
  };
};
type OrderDetail = {
  order: { id: string; quantity: number; dueAt: string; status: string; version: number; customerName: string; currentTrialId: string };
  exception: { message: string; detectedAt?: string; contractConstraint?: string };
  customerResponse?: {
    runId?: string;
    selectedOptionId: string;
    acknowledgedTerms?: { allocations: Allocation[]; additionalCostJpy: number };
    acceptedSplit: boolean;
    acceptedExpressCost?: boolean;
    note?: string;
  } | null;
  approval?: { runId?: string; optionId: string; reason: string; actorRole: string } | null;
  sourceReviewRequest?: {
    id: string; trialId: string; runId: string; optionId: string;
    requestedByRole: string; assignedToRole: string; reason: string;
    sourceRefs: string[]; sourceFixtureVersion: string; status: string; requestedAt: string;
    toolResponseIntegrity?: 'missing' | 'invalid' | 'verified';
    frozenContract?: {
      sourceId: string; version: string; clause: string; scope: string;
      toolName: string; responseText: string; responseSha256: string;
      integrityVerified: boolean;
      noPayerClauseReviewed: boolean;
    } | null;
    response?: {
      reviewId: string; trialId: string; runId: string; optionId: string;
      actorRole: string; outcome: string; reason: string; respondedAt: string;
    } | null;
  } | null;
  shipment?: { reservations: { warehouseId: string; quantity: number }[]; instructions: Allocation[] } | null;
  inventory?: { warehouseId: string; available: number }[];
  latestTrialArchiveId?: string | null;
  previousTrials?: TrialArchive[];
};
type Trace = { at: string; label: string; status: number; detail: string };
type Health = {
  status: string;
  dataMode?: string;
  authenticationMode?: string;
  agentExecutionMode?: string;
  modelConfigured?: boolean;
  inventoryCountAuthenticationMode?: string;
  supportedRequestText?: string;
  newRunAvailable?: boolean;
  scenarioExpiresAt?: string;
  currentTrialId?: string;
};

const theme = createTheme({
  palette: {
    primary: { main: '#183552', dark: '#11283f', contrastText: '#fff' },
    background: { default: '#f2f5f7', paper: '#fff' },
    text: { primary: '#17314c', secondary: '#4e6072' },
    warning: { main: '#986018' },
    success: { main: '#276f57' },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: '"Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans", "Yu Gothic", Meiryo, sans-serif',
    body1: { fontSize: '1rem', lineHeight: 1.65 },
    body2: { fontSize: '.925rem', lineHeight: 1.6 },
    button: { textTransform: 'none', fontWeight: 700 },
  },
  components: { MuiButton: { styleOverrides: { root: { minHeight: 44, boxShadow: 'none' } } } },
});

function warehouse(id: string) { return id === 'warehouse-a' ? '倉庫A' : id === 'warehouse-b' ? '倉庫B' : id; }
function date(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('ja-JP', {
    year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Tokyo',
  }).format(parsed);
}
function plannedTimestamp(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (Number(match[4]) > 23 || Number(match[5]) > 59 || Number(match[6]) > 59 ||
    (match[7] !== 'Z' && (Number(match[8]) > 14 || Number(match[9]) > 59 ||
      (Number(match[8]) === 14 && Number(match[9]) !== 0)))) return null;
  const calendar = new Date(Date.UTC(year, month - 1, day));
  if (year < 100 || calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}
function plannedDate(value: unknown): string {
  return plannedTimestamp(value) === null ? '日時不明' : `${date(value as string)} JST`;
}
function arrivalVerdict(deliveryAt: unknown, dueAt: unknown, sameOrderVersion: boolean): string {
  if (!sameOrderVersion) return '受注版を照合できず比較不可';
  const delivery = plannedTimestamp(deliveryAt);
  const due = plannedTimestamp(dueAt);
  if (delivery === null || due === null) return '比較不可';
  return delivery > due ? '予定上は納期超過' : '予定上は納期内';
}
function proposalArrivalVerdict(proposal: Proposal, dueAt: unknown, sameOrderVersion: boolean): string {
  if (!sameOrderVersion) return '受注版を照合できず比較不可';
  const due = plannedTimestamp(dueAt);
  const arrivals = proposal.shipments.map(item => plannedTimestamp(item.deliveryAt));
  if (!arrivals.length || due === null || arrivals.some(value => value === null)) return '日時が不足し比較不可';
  const latest = Math.max(...arrivals as number[]);
  if (latest <= due) return '予定上は全便が納期内';
  const minutes = Math.ceil((latest - due) / 60000);
  const delay = minutes % 60 === 0 ? `${minutes / 60}時間` : `${minutes}分`;
  return `予定上は希望日時を${delay}超過`;
}
function yen(value: number) { return `${value.toLocaleString('ja-JP')}円`; }
function modelReplyPlainText(value: string) {
  return value.replace(/\r\n?/g, '\n')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*```[^\n]*$/gm, '')
    .replace(/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$/gm, '')
    .replace(/^\s*\|/gm, '')
    .replace(/\|\s*$/gm, '')
    .replace(/\s*\|\s*/g, ' ・ ')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*]\s+/gm, '・')
    .replace(/(\*\*|__)([^\n]*?)\1/g, '$2')
    .replace(/(^|[\s(])([*_])([^\n]+?)\2(?=$|[\s).,、。])/gm, '$1$3')
    .replace(/[`~]/g, '')
    .replace(/[ \t]+$/gm, '');
}
function terms(proposal: Proposal) {
  return {
    allocations: proposal.shipments.map(item => ({
      warehouseId: item.warehouseId, quantity: item.quantityBoxes, deliveryAt: item.deliveryAt,
    })),
    additionalCostJpy: proposal.additionalCostJpy,
  };
}
function sourceName(id: string) {
  if (id.startsWith('order-')) return '受注条件';
  if (id.startsWith('stock-warehouse-a')) return '倉庫Aの在庫';
  if (id.startsWith('stock-warehouse-b')) return '倉庫Bの在庫';
  if (id.startsWith('carrier-a-normal-')) return '倉庫Aの通常便';
  if (id.startsWith('carrier-b-normal-')) return '倉庫Bの通常便';
  if (id.startsWith('carrier-b-express-')) return '倉庫Bの特急便';
  if (id.startsWith('contract-split-')) return '分納の契約条項';
  return id;
}
function actorName(actor: string) {
  return ({ ShipmentManager: '管理Agent', StockInvestigator: '在庫調査Agent',
    DispatchPlanner: '配送調整Agent', OrderInvestigator: '調査Agent', system: '実行システム',
    verified_warehouse_clerk: '認証済みの合成倉庫担当',
    'scripted-test-double': '固定試験ランナー' } as Record<string, string>)[actor] || actor;
}
function stopReason(code?: string) {
  return ({
    draft_grounding_failed: '案の数量・便・納期などが読んだ資料と一致せず、保存を止めました。',
    source_not_read: '必要な資料をToolで読み終えておらず、案の保存を止めました。',
    consolidation_source_missing: 'A/Bの荷物を一つの便にまとめる根拠がなく、案の保存を止めました。',
    source_stale: '受注・在庫・便の読取時点の条件が変わるか、集荷期限が過ぎたため、この操作を止めました。',
    execution_timeout: '制限時間内に構造化案が完成せず、案の保存を止めました。',
    human_wait_expired: '在庫補正の待機期限が切れ、このrunは案を保存せず終了しました。新しいrunで調べ直せます。',
    manager_loop_after_draft: '案の提出後も担当選択が続き、完了扱いにせず止めました。',
  } as Record<string, string>)[code || ''] || 'Agent案を保存できませんでした。保存runの失敗コードと資料読取を確認してください。';
}

function hasServerDraft(run: Run | null): run is Run & { draft: NonNullable<Run['draft']> } {
  if (!run || run.executionMode !== 'live_model' || !['completed', 'draft_ready'].includes(run.status)) return false;
  const draft = run.draft;
  if (!draft || !draft.body?.trim() || !Array.isArray(draft.plan?.proposals) || draft.plan.proposals.length === 0) return false;
  if (run.status === 'draft_ready' &&
    (draft.finalReplyMode !== 'tool_submitted_model_final_timeout' || draft.modelFinalReplyReceived !== false)) return false;
  return draft.plan.proposals.every(proposal => !proposal.runId || proposal.runId === run.id);
}

function isSourceRefsMismatch(run: Run): boolean {
  return run.status === 'failed' && run.events.some(event =>
    event.kind === 'draft_rejected' &&
    (event.actor === 'OrderInvestigator' || event.actor === 'system') &&
    /^(?:資料照合で案を拒否しました|業務APIで案を拒否しました): draft_grounding_failed \(proposals\[[01]\]\.sourceRefs: source_refs_mismatch\)$/.test(event.message));
}

function savedRunStatus(run: Run): string {
  if (run.status === 'failed') return '案保存失敗';
  if (run.status === 'timed_out') return '時間切れ';
  if (hasServerDraft(run)) return run.status === 'draft_ready' ? '案保存・最終返答待ち' : '案保存完了';
  if (run.status === 'completed') return '処理完了・案未確認';
  return run.status === 'queued' ? '開始待ち' : run.status === 'running' ? '調査中' : '状態不明';
}

function isReviewedHold(detail: OrderDetail | null): boolean {
  if (!detail || detail.order.status !== 'awaiting_approval') return false;
  const review = detail.sourceReviewRequest;
  return Boolean(review &&
    ['source_clause_no_payer', 'original_source_unavailable'].includes(review.status) &&
    review.response?.outcome === review.status &&
    !detail.approval &&
    (detail.shipment?.reservations.length ?? 0) === 0 &&
    (detail.shipment?.instructions.length ?? 0) === 0);
}

async function jsonRequest<T>(path: string, body?: object, bearerToken?: string): Promise<{ status: number; data: T }> {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {}) }, body: JSON.stringify(body),
  });
  const data = await response.json() as T;
  return { status: response.status, data };
}

function clearRunQuery() {
  const url = new URL(window.location.href);
  url.searchParams.delete('run');
  window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

function App({ authInitError }: { authInitError: string }) {
  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [archiveReadback, setArchiveReadback] = useState<Record<string, TrialArchive>>({});
  const [expandedArchive, setExpandedArchive] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [confirmedTerms, setConfirmedTerms] = useState(false);
  const [acceptedSplit, setAcceptedSplit] = useState(false);
  const [acceptedExpressCost, setAcceptedExpressCost] = useState(false);
  const [note, setNote] = useState('');
  const [reason, setReason] = useState('合成出荷責任者が顧客回答と資料を確認したため');
  const [reviewReason, setReviewReason] = useState('');
  const [reviewReplyReason, setReviewReplyReason] = useState('');
  const [openedReviewId, setOpenedReviewId] = useState('');
  const [requestConfirmed, setRequestConfirmed] = useState(false);
  const [humanWait, setHumanWait] = useState(false);
  const [warehouseConfirmed, setWarehouseConfirmed] = useState(false);
  const [warehouseCount, setWarehouseCount] = useState('19');
  const [warehouseReason, setWarehouseReason] = useState('合成棚卸しで倉庫Bの実数を確認したため補正');
  const [inventoryResult, setInventoryResult] = useState<InventoryCountResult | null>(null);
  const [inventoryResultRunId, setInventoryResultRunId] = useState('');
  const [newTrialOpen, setNewTrialOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [trace, setTrace] = useState<Trace[]>([]);
  const poller = useRef<number | null>(null);
  const runGeneration = useRef(0);
  const operation = useRef<Record<string, { signature: string; id: string }>>({});

  const append = useCallback((label: string, status: number, detailText: string) => {
    setTrace(current => [...current, { at: new Date().toLocaleTimeString('ja-JP', { timeZone: 'Asia/Tokyo' }), label, status, detail: detailText }]);
  }, []);

  const loadDetail = useCallback(async (record = false) => {
    const result = await jsonRequest<OrderDetail>(base);
    if (result.status !== 200) throw new Error(`受注を取得できません（HTTP ${result.status}）。`);
    setDetail(result.data);
    setExpandedArchive(current => current ?? result.data.latestTrialArchiveId ?? null);
    if (record) append('受注とSQLite保存状態を読戻し', result.status,
      `受注第${result.data.order.version}版・${result.data.order.status}・引当${result.data.shipment?.reservations.length ?? 0}件`);
    return result.data;
  }, [append]);

  const loadHealth = useCallback(async (record = false) => {
    const result = await jsonRequest<Health>(`${trialBase}/api/health`);
    if (result.status !== 200) throw new Error(`業務APIの状態を取得できません（HTTP ${result.status}）。`);
    setHealth(result.data);
    if (record) append('業務APIと合成資料の受付状態を読戻し', result.status,
      `試行 ${result.data.currentTrialId || '不明'}・新規run ${result.data.newRunAvailable ? '受付可' : '受付停止'}`);
    return result.data;
  }, [append]);

  const loadRuns = useCallback(async (record = false) => {
    const result = await jsonRequest<{ agentRuns: Run[] }>(`${base}/agent-runs`);
    if (result.status !== 200) throw new Error(`Agent run一覧を取得できません（HTTP ${result.status}）。`);
    const next = result.data.agentRuns ?? [];
    setRuns(next);
    if (record) append('保存run一覧を読戻し', result.status, `${next.length}件`);
    return next;
  }, [append]);

  const loadArchive = useCallback(async (archiveId: string) => {
    const result = await jsonRequest<{ archive: TrialArchive }>(
      `${base}/trial-archives/${encodeURIComponent(archiveId)}`);
    if (result.status !== 200 || result.data.archive?.archiveId !== archiveId) {
      throw new Error(`過去の保存結果を取得できません（HTTP ${result.status}）。`);
    }
    setArchiveReadback(current => ({ ...current, [archiveId]: result.data.archive }));
    append('過去の試行の保存結果を読戻し', result.status,
      `保存記録 ${archiveId}・試行 ${result.data.archive.trialId}・${result.data.archive.outcome.order?.status || '状態不明'}`);
    return result.data.archive;
  }, [append]);

  const loadRun = useCallback(async (id: string, generation = runGeneration.current) => {
    const result = await jsonRequest<{ run: Run }>(`${trialBase}/api/agent-runs/${encodeURIComponent(id)}`);
    if (result.status !== 200) throw new Error(`Agent runを取得できません（HTTP ${result.status}）。`);
    if (generation !== runGeneration.current) return result.data.run;
    setRun(result.data.run);
    const proposals = result.data.run.draft?.plan?.proposals ?? [];
    setSelectedId(current => proposals.some(item => item.id === current) ? current : '');
    if (['completed', 'draft_ready', 'failed', 'timed_out'].includes(result.data.run.status) && poller.current !== null) {
      window.clearInterval(poller.current);
      poller.current = null;
      append('Agent runの保存状態を読戻し', result.status, `${result.data.run.status}・案${result.data.run.draft?.plan?.proposals?.length ?? 0}件`);
      await Promise.all([loadDetail(), loadRuns()]);
    }
    return result.data.run;
  }, [append, loadDetail, loadRuns]);

  const showSavedRun = async (id: string, availableRuns = runs, currentDetail = detail) => {
    if (!availableRuns.some(item => item.id === id)) {
      setError('この受注の保存run一覧に対象のrunがありません。');
      return;
    }
    setError('');
    try {
      runGeneration.current += 1;
      const generation = runGeneration.current;
      if (poller.current !== null) { window.clearInterval(poller.current); poller.current = null; }
      const existing = await loadRun(id, generation);
      if (generation !== runGeneration.current) return;
      const savedAnswer = currentDetail?.customerResponse;
      if (savedAnswer?.runId === id && existing.trialId === currentDetail?.order.currentTrialId &&
          existing.draft?.plan?.proposals?.some(item => item.id === savedAnswer.selectedOptionId)) {
        setSelectedId(savedAnswer.selectedOptionId);
      }
      const url = new URL(window.location.href);
      url.searchParams.set('run', id);
      window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
      if (existing.status === 'queued' || existing.status === 'running') {
        poller.current = window.setInterval(() => {
          void loadRun(id).catch(cause => setError((cause as Error).message));
        }, 1000);
      }
    } catch (cause) { setError((cause as Error).message); }
  };

  const returnToCurrentTrial = async () => {
    runGeneration.current += 1;
    if (poller.current !== null) { window.clearInterval(poller.current); poller.current = null; }
    setBusy(true);
    setError('');
    try {
      const [freshDetail, freshRuns] = await Promise.all([loadDetail(), loadRuns(), loadHealth()]);
      clearRunQuery();
      setRun(null);
      setSelectedId('');
      const currentId = freshRuns.find(item => item.trialId === freshDetail.order.currentTrialId)?.id;
      if (currentId) await showSavedRun(currentId, freshRuns, freshDetail);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    let active = true;
    const initial = async () => {
      const generation = runGeneration.current;
      try {
        await loadHealth();
        const current = await loadDetail();
        const availableRuns = await loadRuns();
        if (!active || generation !== runGeneration.current) return;
        const requestedRunId = new URLSearchParams(window.location.search).get('run');
        if (requestedRunId && !availableRuns.some(item => item.id === requestedRunId)) clearRunQuery();
        const runId = (requestedRunId && availableRuns.some(item => item.id === requestedRunId) ? requestedRunId : '') ||
          current.customerResponse?.runId ||
          availableRuns.find(item => item.trialId === current.order.currentTrialId)?.id;
        if (runId) {
          const existing = await loadRun(runId, generation);
          if (!active || generation !== runGeneration.current) return;
          if (existing.status === 'queued' || existing.status === 'running') {
            poller.current = window.setInterval(() => {
              void loadRun(runId).catch(cause => setError((cause as Error).message));
            }, 1000);
          }
        }
        const savedAnswer = current.customerResponse;
        if (runId === savedAnswer?.runId && savedAnswer?.selectedOptionId) {
          setSelectedId(savedAnswer.selectedOptionId);
        }
      } catch (cause) { if (active) setError((cause as Error).message); }
    };
    void initial();
    return () => { active = false; if (poller.current !== null) window.clearInterval(poller.current); };
  }, [loadDetail, loadHealth, loadRuns, loadRun]);

  const startRun = async () => {
    const currentTrialRuns = runs.filter(item => item.trialId === detail?.order.currentTrialId);
    const latestRun = currentTrialRuns[0];
    if (!detail || detail.order.status !== 'exception_detected' ||
        (run && run.trialId !== detail.order.currentTrialId) ||
        (run && latestRun && run.id !== latestRun.id) ||
        currentTrialRuns.some(item => item.status === 'queued' || item.status === 'running') ||
        health?.currentTrialId !== detail.order.currentTrialId ||
        !health.newRunAvailable || health.supportedRequestText !== requestText ||
        !['single_agent_live_model', 'test_double'].includes(health.agentExecutionMode || '')) {
      setError('現在の合成資料では新しい調査を受け付けていません。APIの実行モードと資料期限を確認してください。');
      return;
    }
    const input = { requestText, expectedVersion: detail.order.version,
      humanWaitSeconds: humanWait && health.agentExecutionMode !== 'test_double' ? 600 : 0 };
    const signature = JSON.stringify(input);
    const old = operation.current.run;
    const operationId = old?.signature === signature ? old.id : crypto.randomUUID();
    operation.current.run = { signature, id: operationId };
    setBusy(true); setError('');
    try {
      runGeneration.current += 1;
      const generation = runGeneration.current;
      if (poller.current !== null) { window.clearInterval(poller.current); poller.current = null; }
      const result = await jsonRequest<{ run?: Run; message?: string; error?: string }>(`${base}/agent-runs`,
        { ...input, operationId });
      append('合成受注の調査依頼を送信', result.status, result.data.run?.id || result.data.message || result.data.error || 'runなし');
      if (result.status !== 200 || !result.data.run) throw new Error(result.data.message || `起動できません（HTTP ${result.status}）。`);
      const expectedMode = health.agentExecutionMode === 'test_double' ? 'test_double' : 'live_model';
      if (result.data.run.executionMode !== expectedMode) throw new Error('APIの実行モードと受け付けたrunの実行種別が一致しません。');
      if (result.data.run.trialId !== detail.order.currentTrialId) throw new Error('runの試行IDが現在の受注と一致しません。保存状態を読み直してください。');
      delete operation.current.run;
      setRun(result.data.run);
      setSelectedId('');
      setRequestConfirmed(false);
      setHumanWait(false);
      setConfirmedTerms(false);
      setAcceptedSplit(false);
      setAcceptedExpressCost(false);
      const url = new URL(window.location.href);
      url.searchParams.set('run', result.data.run.id);
      window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
      const id = result.data.run.id;
      poller.current = window.setInterval(() => { void loadRun(id).catch(cause => setError((cause as Error).message)); }, 1000);
      await loadRun(id, generation);
      await loadRuns();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  const recordInventoryCount = async () => {
    if (!detail || !run || run.trialId !== detail.order.currentTrialId ||
        inventoryResult ||
        run.status !== 'running' || run.events.some(event => event.kind === 'draft_submitted') ||
        !['read_order_inventory', 'read_carriers_contract'].every(name =>
          run.toolResponses?.some(item => item.toolName === name)) ||
        !warehouseConfirmed || !warehouseReason.trim()) return;
    const currentBoxes = detail.inventory?.find(item => item.warehouseId === 'warehouse-b')?.available;
    const nextBoxes = Number(warehouseCount);
    if (currentBoxes === undefined || !/^\d+$/.test(warehouseCount) || nextBoxes > 1000 ||
        nextBoxes === currentBoxes) {
      setError('倉庫Bの現在庫と異なる0～1000の整数を入力してください。');
      return;
    }
    const input = {
      runId: run.id, expectedVersion: detail.order.version, warehouseId: 'warehouse-b',
      expectedAvailableBoxes: currentBoxes, availableBoxes: nextBoxes,
      reason: warehouseReason.trim(), actorRole: 'warehouse_clerk',
    };
    const signature = JSON.stringify(input);
    const old = operation.current['inventory-count'];
    const operationId = old?.signature === signature ? old.id : crypto.randomUUID();
    operation.current['inventory-count'] = { signature, id: operationId };
    setBusy(true); setError('');
    try {
      const latest = await jsonRequest<{ run?: Run; message?: string }>(
        `${trialBase}/api/agent-runs/${encodeURIComponent(run.id)}`);
      if (latest.status !== 200 || !latest.data.run) throw new Error('Agentの保存状態を確認できません。更新してから補正してください。');
      setRun(latest.data.run);
      if (latest.data.run.status !== 'running' ||
          latest.data.run.events.some(event => event.kind === 'draft_submitted') ||
          !['read_order_inventory', 'read_carriers_contract'].every(name =>
            latest.data.run?.toolResponses?.some(item => item.toolName === name))) {
        throw new Error('案提出前のTool応答2件を確認できません。現在のrunを読み直してください。');
      }
      const token = await inventoryAccessToken();
      const result = await jsonRequest<InventoryCountResult & { error?: string; message?: string }>(
        `${base}/inventory-count`, { ...input, operationId }, token);
      if (result.status !== 200 || result.data.authenticationMode !== 'keycloak_oidc_openfga_local_trial' ||
          result.data.dataMode !== 'synthetic_fixture' || result.data.runId !== run.id ||
          result.data.orderId !== detail.order.id || result.data.warehouseId !== 'warehouse-b') {
        append('合成倉庫担当役が在庫数を補正', result.status,
          `${result.data.error || ''} ${result.data.message || '応答の実行・受注・倉庫IDを確認できません。'}`);
        throw new Error(result.data.message || `在庫を補正できません（HTTP ${result.status}）。`);
      }
      append('合成倉庫担当役が在庫数を補正', result.status,
        `倉庫B ${result.data.previousAvailableBoxes}→${result.data.availableBoxes}箱／受注第${result.data.orderVersion}版／Keycloak・OpenFGA認可`);
      delete operation.current['inventory-count'];
      setInventoryResult(result.data);
      setInventoryResultRunId(result.data.runId);
      setWarehouseConfirmed(false);
      await loadDetail(true);
    } catch (cause) {
      setError((cause as Error).message);
      try { await loadDetail(true); } catch { /* The failed operation remains visible. */ }
    } finally { setBusy(false); }
  };

  const send = async (kind: 'response' | 'decision' | 'early') => {
    if (!detail || !run || run.trialId !== detail.order.currentTrialId || !hasServerDraft(run) ||
        runs.find(item => item.trialId === detail.order.currentTrialId)?.id !== run.id) return;
    const proposal = run.draft?.plan?.proposals?.find(item => item.id === selectedId);
    if (!proposal) return;
    const common = { runId: run.id, expectedVersion: detail.order.version };
    const input = kind === 'response'
      ? { ...common, selectedOptionId: proposal.id, acknowledgedTerms: proposal.acknowledgedTerms ?? terms(proposal),
          acceptedSplit, acceptedExpressCost, actorRole: 'order_clerk', note: note.trim() || '合成顧客役の回答' }
      : { ...common, optionId: proposal.id, reason: kind === 'early'
          ? '顧客回答前の停止境界を試す' : reason.trim(), actorRole: 'shipping_manager' };
    const signature = JSON.stringify(input);
    const old = operation.current[kind];
    const operationId = old?.signature === signature ? old.id : crypto.randomUUID();
    operation.current[kind] = { signature, id: operationId };
    setBusy(true); setError('');
    const path = kind === 'response' ? 'agent-customer-response' : 'agent-decision';
    try {
      const result = await jsonRequest<OrderDetail & { error?: string; message?: string }>(`${base}/${path}`, { ...input, operationId });
      append(kind === 'response' ? '合成顧客の回答を送信' : kind === 'early' ? '顧客回答前の承認を試行' : '合成出荷責任者が承認',
        result.status, result.status === 200
          ? `run ${run.id} / 案 ${proposal.id} / 受注第${result.data.order.version}版`
          : `${result.data.error || ''} ${result.data.message || ''}`);
      if (result.status !== 200) {
        if (kind === 'early') setError(`${result.data.message || '顧客回答前の承認は停止しました。'} 保存状態を読戻して確認してください。`);
        else setError(result.data.message || `操作できません（HTTP ${result.status}）。`);
      } else {
        delete operation.current[kind];
        setDetail(result.data);
        if (kind === 'response') setConfirmedTerms(true);
      }
      await loadDetail(true);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  const requestSourceReview = async () => {
    if (!detail || !run || !hasServerDraft(run) ||
        run.trialId !== detail.order.currentTrialId || !reviewReason.trim() ||
        runs.find(item => item.trialId === detail.order.currentTrialId)?.id !== run.id) return;
    const proposal = run.draft.plan?.proposals?.find(item => item.id === selectedId);
    if (!proposal || proposal.additionalCostJpy <= 0 ||
        detail.customerResponse?.runId !== run.id ||
        detail.customerResponse.selectedOptionId !== proposal.id ||
        !detail.customerResponse.acceptedExpressCost) return;
    const input = { runId: run.id, optionId: proposal.id,
      reason: reviewReason.trim(), expectedVersion: detail.order.version,
      actorRole: 'shipping_manager' };
    const signature = JSON.stringify(input);
    const old = operation.current['source-review'];
    const operationId = old?.signature === signature ? old.id : crypto.randomUUID();
    operation.current['source-review'] = { signature, id: operationId };
    setBusy(true); setError('');
    try {
      const result = await jsonRequest<OrderDetail & { error?: string; message?: string }>(
        `${base}/cost-source-review`, { ...input, operationId });
      append('費用負担先の原資料確認を別担当へ依頼', result.status,
        result.status === 200
          ? `案 ${proposal.id} → ${result.data.sourceReviewRequest?.assignedToRole}・${result.data.sourceReviewRequest?.status}`
          : `${result.data.error || ''} ${result.data.message || ''}`);
      if (result.status !== 200) setError(result.data.message || `確認依頼を保存できません（HTTP ${result.status}）。`);
      else { delete operation.current['source-review']; setDetail(result.data); }
      await loadDetail(true);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  const openSourceReview = async () => {
    const current = detail?.sourceReviewRequest;
    if (!current || current.status !== 'pending') return;
    setBusy(true); setError('');
    try {
      const readback = await loadDetail();
      const latest = readback.sourceReviewRequest;
      if (!latest || latest.id !== current.id || latest.status !== 'pending' ||
          latest.trialId !== readback.order.currentTrialId) {
        throw new Error('確認依頼の状態が変わりました。保存状態を読み直してください。');
      }
      setOpenedReviewId(latest.id);
      append('契約確認担当役が確認依頼を画面で開く', 200,
        `確認依頼 ${latest.id}・試行 ${latest.trialId}。${latest.frozenContract ? '保存runの契約Tool応答を照合。' : latest.toolResponseIntegrity === 'missing' ? '旧runの原文は未保存。' : '保存原文を照合できません。'}担当者への実送達・受領記録ではありません。`);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  const respondSourceReview = async () => {
    const review = detail?.sourceReviewRequest;
    if (!detail || !review || review.status !== 'pending' ||
        openedReviewId !== review.id || !reviewReplyReason.trim() ||
        (review.frozenContract && review.frozenContract.noPayerClauseReviewed !== true) ||
        (!review.frozenContract && review.toolResponseIntegrity !== 'missing') ||
        review.trialId !== detail.order.currentTrialId) return;
    const input = {
      reviewId: review.id, trialId: review.trialId, runId: review.runId,
      optionId: review.optionId, outcome: review.frozenContract?.noPayerClauseReviewed === true
        ? 'source_clause_no_payer' : 'original_source_unavailable',
      reason: reviewReplyReason.trim(), expectedVersion: detail.order.version,
      actorRole: 'contract_reviewer',
    };
    const signature = JSON.stringify(input);
    const old = operation.current['source-review-response'];
    const operationId = old?.signature === signature ? old.id : crypto.randomUUID();
    operation.current['source-review-response'] = { signature, id: operationId };
    setBusy(true); setError('');
    try {
      const result = await jsonRequest<OrderDetail & { error?: string; message?: string }>(
        `${base}/cost-source-review-response`, { ...input, operationId });
      append(review.frozenContract?.noPayerClauseReviewed === true ? '契約確認担当役が負担条項なしと回答を保存' : '契約確認担当役が原文を照合できない回答を保存', result.status,
        result.status === 200
          ? `確認依頼 ${review.id}・${result.data.sourceReviewRequest?.status}・受注第${result.data.order.version}版・出荷指示${result.data.shipment?.instructions.length ?? 0}件`
          : `${result.data.error || ''} ${result.data.message || ''}`);
      if (result.status !== 200) setError(result.data.message || `回答を保存できません（HTTP ${result.status}）。`);
      else { delete operation.current['source-review-response']; setDetail(result.data); }
      await loadDetail(true);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  const startNewTrial = async () => {
    if (!detail || (detail.order.status !== 'shipment_instructed' && !isReviewedHold(detail)) ||
        (run && run.trialId !== detail.order.currentTrialId) ||
        (run && runs.find(item => item.trialId === detail.order.currentTrialId)?.id !== run.id) ||
        runs.some(item => item.trialId === detail.order.currentTrialId &&
          (item.status === 'queued' || item.status === 'running')) ||
        health?.currentTrialId !== detail.order.currentTrialId || !health.newRunAvailable) {
      setError('現行試行の保存結果と合成資料の受付状態を読み直してください。');
      setNewTrialOpen(false);
      return;
    }
    const input = { expectedVersion: detail.order.version, confirmNewTrial: true };
    const signature = JSON.stringify(input);
    const old = operation.current['new-trial'];
    const operationId = old?.signature === signature ? old.id : crypto.randomUUID();
    operation.current['new-trial'] = { signature, id: operationId };
    setBusy(true); setError('');
    try {
      const result = await jsonRequest<OrderDetail & { previousArchiveId?: string; error?: string; message?: string }>(
        `${base}/new-trial`, { ...input, operationId });
      append(detail.order.status === 'shipment_instructed'
        ? '出荷指示済みの結果を保存して新しい合成試行を開始'
        : '契約確認済み・出荷保留の結果を保存して新しい合成試行を開始', result.status,
        result.status === 200
          ? `旧試行 ${detail.order.currentTrialId} → 保存記録 ${result.data.previousArchiveId || result.data.latestTrialArchiveId || 'IDなし'} → 新試行 ${result.data.order?.currentTrialId || 'IDなし'}`
          : `${result.data.error || ''} ${result.data.message || ''}`);
      if (result.status !== 200 || !result.data.order?.currentTrialId ||
          result.data.order.currentTrialId === detail.order.currentTrialId) {
        throw new Error(result.data.message || `新しい試行を開始できません（HTTP ${result.status}）。`);
      }
      operation.current = {};
      runGeneration.current += 1;
      if (poller.current !== null) { window.clearInterval(poller.current); poller.current = null; }
      clearRunQuery();
      setDetail(result.data);
      setExpandedArchive(result.data.latestTrialArchiveId ?? null);
      setRun(null);
      setSelectedId('');
      setRequestConfirmed(false);
      setWarehouseConfirmed(false);
      setWarehouseCount('19');
      setInventoryResult(null);
      setInventoryResultRunId('');
      setConfirmedTerms(false);
      setAcceptedSplit(false);
      setAcceptedExpressCost(false);
      setNote('');
      setReason('合成出荷責任者が顧客回答と資料を確認したため');
      setReviewReason('');
      setReviewReplyReason('');
      setOpenedReviewId('');
      setNewTrialOpen(false);
      await Promise.all([loadDetail(true), loadRuns(true), loadHealth(true)]);
      const archiveId = result.data.previousArchiveId || result.data.latestTrialArchiveId;
      if (archiveId) await loadArchive(archiveId);
    } catch (cause) {
      setError((cause as Error).message);
      setNewTrialOpen(false);
      try { await Promise.all([loadDetail(true), loadRuns(true), loadHealth(true)]); }
      catch { /* The original operation error remains visible; the next manual readback can retry. */ }
    } finally { setBusy(false); }
  };

  const currentRun = Boolean(detail && run && run.trialId === detail.order.currentTrialId);
  const staleRun = Boolean(run && detail && !currentRun);
  const currentTrialRuns = runs.filter(item => item.trialId === detail?.order.currentTrialId);
  const latestCurrentRun = currentTrialRuns[0];
  const olderRun = Boolean(currentRun && latestCurrentRun && run?.id !== latestCurrentRun.id);
  const readOnlyRun = staleRun || olderRun;
  const activeTrialRun = currentTrialRuns.some(item => item.status === 'queued' || item.status === 'running');
  const toolNames = new Set(run?.toolResponses?.map(item => item.toolName) ?? []);
  const toolsReady = toolNames.has('read_order_inventory') && toolNames.has('read_carriers_contract');
  const readOrderSourceId = run?.toolResponses?.find(item => item.toolName === 'read_order_inventory')
    ?.sourceIds.find(id => /^order-SO-24017-v\d+$/.test(id));
  const readOrderVersion = readOrderSourceId ? Number(readOrderSourceId.match(/-v(\d+)$/)?.[1]) : undefined;
  const savedOrderVersion = run?.sourceSnapshot?.order?.version;
  const savedDueAt = run?.sourceSnapshot?.order?.dueAt;
  const sameOrderVersion = Boolean(detail && savedOrderVersion !== undefined &&
    savedOrderVersion === detail.order.version && readOrderVersion === savedOrderVersion);
  const sourceChangedSinceRead = Boolean(detail && readOrderVersion !== undefined &&
    readOrderVersion !== detail.order.version);
  const draftSubmitted = Boolean(run?.events.some(event => event.kind === 'draft_submitted'));
  const currentWarehouseB = detail?.inventory?.find(item => item.warehouseId === 'warehouse-b')?.available;
  const nextWarehouseB = Number(warehouseCount);
  const validWarehouseCount = /^\d+$/.test(warehouseCount) && nextWarehouseB <= 1000 &&
    currentWarehouseB !== undefined && nextWarehouseB !== currentWarehouseB;
  const inventoryCountAvailable = Boolean(inventoryIdentity.authenticated && currentRun && !readOnlyRun && run?.executionMode === 'live_model' &&
    !inventoryResult && !sourceChangedSinceRead &&
    run?.status === 'running' && toolsReady && !draftSubmitted &&
    detail?.order.status === 'exception_detected' && !detail.customerResponse);
  const readableDraft = hasServerDraft(run);
  const runReady = currentRun && !olderRun && readableDraft;
  const draftReady = readableDraft && run.status === 'draft_ready';
  const proposals = readableDraft ? run.draft.plan?.proposals ?? [] : [];
  const inventoryCorrectionStoppedRun = Boolean(currentRun && sourceChangedSinceRead &&
    run?.events.some(event => event.kind === 'human_inventory_count'));
  const selected = proposals.find(item => item.id === selectedId);
  const answered = Boolean(detail?.customerResponse);
  const instructed = detail?.order.status === 'shipment_instructed';
  const reviewedHold = isReviewedHold(detail);
  const runStopped = run?.status === 'failed' || run?.status === 'timed_out' ||
    (run && ['completed', 'draft_ready'].includes(run.status) && !readableDraft);
  const singleAgent = health?.agentExecutionMode === 'single_agent_live_model' ||
    Boolean(run?.events.some(event => event.actor === 'OrderInvestigator'));
  const ownerEntry = health?.authenticationMode === 'tailnet_owner_identity';
  const inventoryAuthReady = health?.inventoryCountAuthenticationMode === 'keycloak_oidc_openfga_local_trial';
  const liveModelReady = health?.modelConfigured === true &&
    ['single_agent_live_model', 'live_model'].includes(health?.agentExecutionMode || '');
  const testDoubleRun = run?.executionMode === 'test_double';
  const testDoubleServer = health?.agentExecutionMode === 'test_double';
  const testDoubleVisible = testDoubleRun || (!run && testDoubleServer);
  const localNoAuth = import.meta.env.DEV && import.meta.env.VITE_OE02_LOCAL_NO_AUTH === 'true';
  const modelLabel = testDoubleVisible ? '固定試験・モデル呼出しなし'
    : liveModelReady ? '局所実モデルの接続設定あり'
    : run?.executionMode === 'live_model' ? '局所実モデルの保存runを閲覧中'
    : 'モデル未接続';
  const executionLabel = localNoAuth ? 'ローカル複製DB・保存run読戻し／認証未接続'
    : `${ownerEntry ? '本人限定入口' : '本人認証なし'}・${modelLabel}`;
  const requestAvailable = !localNoAuth && health?.newRunAvailable === true &&
    health.supportedRequestText === requestText &&
    ['single_agent_live_model', 'test_double'].includes(health.agentExecutionMode || '') &&
    health.modelConfigured === true &&
    detail?.order.status === 'exception_detected' &&
    health.currentTrialId === detail.order.currentTrialId && !answered && !readOnlyRun && !activeTrialRun;
  const requestStatus = instructed ? 'この合成受注は出荷指示済みです。保存結果を確認してください。'
    : readOnlyRun ? '旧runは閲覧専用です。「最新のrunへ戻る」を選んでください。'
    : activeTrialRun ? 'Agentの実行中です。保存状態を読み直して完了を待ってください。'
      : answered ? '合成顧客の回答を保存済みです。新しい調査は送信できません。'
      : requestAvailable && latestCurrentRun ? '旧runを残して新しいrunを送信できます。案の選択と出荷は新runの結果を確認してから進めます。'
      : requestAvailable ? testDoubleServer ? 'この固定依頼の固定試験を受付中。モデルは呼びません。' : 'この固定依頼の局所実モデル実行を受付中。'
        : health?.modelConfigured === false ? 'モデル接続設定がないため、新しい調査は受付停止中です。保存runは閲覧できます。'
        : 'この固定依頼の新しい実行は受付停止中です。実行モード・受注版・合成資料の期限を確認してください。';
  const selectedMatches = detail?.customerResponse?.selectedOptionId === selectedId &&
    currentRun && !olderRun && (!detail.customerResponse?.runId || detail.customerResponse.runId === run?.id);
  const sourceReviewBlocksApproval = Boolean(detail?.sourceReviewRequest && selected && run &&
    detail.sourceReviewRequest.trialId === detail.order.currentTrialId &&
    detail.sourceReviewRequest.runId === run.id &&
    detail.sourceReviewRequest.optionId === selected.id &&
    ['pending', 'original_source_unavailable', 'source_clause_no_payer'].includes(detail.sourceReviewRequest.status));
  const nextActor = readOnlyRun ? '旧runを閲覧中。人役の操作は最新runへ戻ってから行います'
    : instructed ? '完了：保存結果を読み直せます。新しい合成試行を開始できます'
    : reviewedHold ? '契約確認の回答済み・出荷0。結果を保存して新しい合成試行を開始できます'
    : detail?.sourceReviewRequest?.status === 'pending' ? '契約確認担当役へ確認依頼済み。原資料の確認待ち・出荷指示なし'
    : detail?.sourceReviewRequest?.status === 'original_source_unavailable' ? '契約確認担当役の回答：保存原文を照合できない。出荷承認は保留'
    : detail?.sourceReviewRequest?.status === 'source_clause_no_payer' ? '契約確認担当役の回答：原文を確認したが負担条項なし。出荷承認は保留'
    : answered ? '次に判断する人：合成出荷責任者役'
      : runReady ? '次に判断する人：合成顧客役'
        : runStopped ? testDoubleRun ? '固定試験案を拒否。人の回答と出荷は停止中' : 'Agent案を保存できませんでした。依頼を再実行できます'
          : inventoryResult && inventoryResultRunId === run?.id && run?.status === 'running'
            ? '在庫補正を保存済み。Agent案の保存結果を待っています'
          : sourceChangedSinceRead && run?.status === 'running'
            ? 'Tool読取後に受注版が更新済み。旧runの案保存結果を待っています'
          : inventoryCountAvailable ? '次に動く人：合成倉庫担当役。Toolで読んだ在庫を補正できます'
          : run ? testDoubleRun ? '固定試験が資料読取イベントを記録中。人の回答はまだ不要です' : 'Agentが資料を確認中。人の回答はまだ不要です'
            : '依頼する人：合成受注の調査を送信してください';

  return <ThemeProvider theme={theme}><CssBaseline />
    <Box className="app-header"><Container maxWidth="lg" className="header-inner">
      <Typography className="brand">Tech Playground｜出荷例外</Typography>
      <Chip className="mode-chip" variant="outlined" label={executionLabel} />
    </Container></Box>
    <Container maxWidth="lg" className="main-content">
      {run?.status === 'failed' && run.errorCode === 'source_stale' && <Alert severity="warning"
        className="firstfold-result" data-testid="stopped-run-firstfold" role="status">
        <Typography component="h2" className="firstfold-result-title">旧調査は停止・保存案0件</Typography>
        <Typography variant="body2">{inventoryCorrectionStoppedRun
          ? `このrunには合成倉庫担当者の在庫補正が記録されています。Agentが読んだ受注第${readOrderVersion}版と現行第${detail?.order.version}版は異なります。業務APIは旧案を保存せず停止しました（source_stale）。`
          : `${stopReason(run.errorCode)} 旧runの案は保存されていません（${run.errorCode}）。`}
          このrunから顧客回答・出荷承認はできません。
        </Typography>
        {latestCurrentRun && latestCurrentRun.id !== run.id
          ? <Button size="small" variant="outlined" sx={{ mt: 1.5 }} disabled={busy}
              onClick={() => void returnToCurrentTrial()}>
              現行の調査・保存案を確認
            </Button>
          : readOnlyRun
            ? <Button size="small" variant="outlined" sx={{ mt: 1.5 }} disabled={busy}
                onClick={() => void returnToCurrentTrial()}>現行の試行へ戻る</Button>
            : requestAvailable
            ? <Button size="small" variant="outlined" sx={{ mt: 1.5 }}
                onClick={() => document.querySelector('[data-testid="request-step"]')?.scrollIntoView({ behavior: 'smooth' })}>
                現行版の再依頼へ進む
              </Button>
            : <Typography variant="body2" sx={{ mt: 1 }}>現行版の新しい調査は受付停止中です。</Typography>}
      </Alert>}
      {runReady && proposals.length > 0 && <Alert severity="success" className="firstfold-result"
        data-testid="saved-options-firstfold" role="status">
        <Typography component="h2" className="firstfold-result-title">現行版の保存案{proposals.length}件</Typography>
        <Typography variant="body2">保存runの読戻しです。{testDoubleRun
          ? '固定試験の提出値を業務APIが資料と照合しました。モデルは呼んでいません。'
          : '局所実モデルのTool提出値を業務APIが資料と照合しました。'}顧客回答・出荷承認は別の操作です。</Typography>
        {run?.executionMode === 'live_model' && run.status === 'completed' && detail && <Alert severity="warning" sx={{ mt: 1 }} data-testid="structured-delivery-firstfold">
          <Typography variant="body2" sx={{ fontWeight: 700 }}>業務API保存値の納期照合：保存runの受注希望納期 {plannedDate(savedDueAt)}（第{savedOrderVersion ?? '不明'}版）</Typography>
          {!sameOrderVersion && <Typography variant="body2">現在の受注は第{detail.order.version}版、Tool参照は第{readOrderVersion ?? '不明'}版です。版を照合できないため、現行受注との納期比較はできません。</Typography>}
          {proposals.map((proposal, index) => <Typography key={proposal.id} variant="body2">
            案{index + 1}：{proposalArrivalVerdict(proposal, savedDueAt, sameOrderVersion)}（{proposal.shipments.map(item => `${warehouse(item.warehouseId)} ${plannedDate(item.deliveryAt)}`).join('／') || '到着予定不明'}）
          </Typography>)}
          <Typography variant="caption" color="text.secondary">モデル原文の到着日と照合してください。契約適合は未判定です。</Typography>
        </Alert>}
        <Button size="small" variant="outlined" sx={{ mt: 1.5 }}
          onClick={() => document.querySelector('[data-testid="saved-options"]')?.scrollIntoView({ behavior: 'smooth' })}>
          保存案の条件を見る
        </Button>
        {run?.modelFinalReply && <Button size="small" variant="text" sx={{ mt: 1.5, ml: { sm: 1 } }}
          onClick={() => document.querySelector('[data-testid="model-final-reply"]')?.scrollIntoView({ behavior: 'smooth' })}>
          モデル原文と照合する
        </Button>}
      </Alert>}
      <Alert severity="info" className="scope-strip">
        {localNoAuth
          ? '複製DBで保存runと業務状態を確認しています。ここではKeycloakとOpenFGAに接続せず、新しい実モデルrunも開始しません。'
          : `${ownerEntry ? '本人限定入口で閲覧中です。' : 'この入口は本人認証に接続していません。'}${liveModelReady ? '局所実モデルの接続設定があります。実際の接続成否はrun結果で確認します。' : testDoubleServer ? '固定試験を実行できます。モデルは呼びません。' : '新しい実モデルrunは受付停止中です。'}${inventoryAuthReady ? '在庫補正には合成担当者のKeycloakログインとOpenFGA判定が必要です。' : '在庫補正のKeycloakとOpenFGAは未接続です。'}`}
        受注・資料・担当者は合成です。在庫補正以外の人役の業務権限は自己申告のままです。外部ERP・WMSやAzure配備には接続していません。
      </Alert>
      <Box className="lead"><Typography component="h1" className="lead-title">不足15箱を、誰の判断で出荷するか</Typography>
        <Typography className="lead-detail">受注 {orderId}・40箱。Agentが受注・在庫と便・契約を読みます。倉庫担当者が在庫を補正した場合は、旧案を止めて現行条件で調べ直します。</Typography>
        <Typography className="next-actor" role="status">{nextActor}</Typography>
      </Box>
      <Box className="progress" aria-label="操作の順序">
        {['依頼', testDoubleVisible ? '固定試験の資料読取・案' : 'Tool読取・在庫補正・案', '合成顧客の回答', '合成出荷承認', '保存結果'].map((label, index) =>
          <Box key={label} className={`progress-step ${index === (instructed ? 4 : answered ? 3 : runReady ? 2 : run ? 1 : 0) ? 'is-current' : ''}`}>
            <span className="progress-number">{index + 1}</span><span>{label}</span></Box>)}
      </Box>
      {readOnlyRun && <Alert severity="warning" className="stale-run-notice" role="status"
        action={<Button color="inherit" onClick={() => void returnToCurrentTrial()}>最新のrunへ戻る</Button>}>
        表示中のrun {run?.id} は{staleRun ? `過去の試行 ${run?.trialId}` : '現在の試行の旧run'}に属する保存記録です。案とイベントを閲覧できますが、このrunから顧客回答と出荷承認はできません。
      </Alert>}
      {error && <Alert severity="warning" sx={{ mb: 2 }} role="alert">{error}</Alert>}
      {!detail ? <Paper className="content-panel"><CircularProgress size={22} /> 受注を読み込み中です</Paper> : <>
        {(instructed || reviewedHold) && <Paper className="content-panel new-trial-panel" data-testid="new-trial-step">
          <Typography component="h2" className="section-title">{instructed ? '出荷指示済みの合成案件をもう一度試す' : '出荷を保留した合成案件をもう一度試す'}</Typography>
          <Typography>現在の試行 {detail.order.currentTrialId} は{instructed ? '出荷指示済み' : '契約確認担当役が回答済みで、承認・引当・出荷指示は0件の保留状態'}です。現在の結果を履歴に保存し、合成受注の回答・承認・引当・出荷指示を初期化して、別の試行で調査を始められます。</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Agent runと契約確認の回答を含む現在の結果は保存履歴から読み直せます。合成在庫は初期値に戻します。実際の配送、在庫、顧客には変更を加えません。</Typography>
          <Button variant="outlined" onClick={() => setNewTrialOpen(true)}
            disabled={localNoAuth || busy || readOnlyRun || activeTrialRun || health?.newRunAvailable !== true || health.currentTrialId !== detail.order.currentTrialId}
            sx={{ mt: 2 }}>結果を保存して新しい合成試行を開始する</Button>
          {health?.newRunAvailable !== true && <Typography variant="body2" color="warning.main" sx={{ mt: 1 }}>合成資料の期限を過ぎたため、新しい試行は開始できません。</Typography>}
        </Paper>}
        <Paper className="content-panel" data-testid="request-step">
          <Typography component="h2" className="section-title">依頼：不足15箱の別送案を調べる</Typography>
          <TextField fullWidth multiline minRows={3} label="送信する合成依頼（この1件に固定）" value={requestText}
            slotProps={{ input: { readOnly: true } }} helperText="この試行ではSO-24017の条件だけを受け付けます。文面の自由入力はまだ実装していません。" />
          <FormControlLabel sx={{ mt: 1 }} control={<Checkbox checked={requestConfirmed} disabled={!requestAvailable || busy}
            onChange={event => setRequestConfirmed(event.target.checked)} />}
            label={latestCurrentRun
              ? testDoubleServer ? '旧runを残し、合成資料で固定試験を再実行する（モデル呼出しなし）' : '旧runを残し、合成資料で局所実モデルを再実行する'
              : testDoubleServer ? '合成資料と固定試験を使う依頼だと確認した（モデル呼出しなし）' : '合成資料と局所実モデルを使う依頼だと確認した'} />
          <FormControlLabel sx={{ display: 'block', mt: .5 }} control={<Checkbox
            data-testid="human-wait-checkbox"
            checked={!testDoubleServer && humanWait}
            disabled={testDoubleServer || !requestAvailable || busy}
            onChange={event => setHumanWait(event.target.checked)} />}
            label="このrunで在庫補正を最大10分待つ" />
          {!testDoubleServer && <Typography variant="body2" color="text.secondary">
            調査だけを試す場合は待機をオフにできます。待機をオンにすると、Agentが資料を読んだ後に担当者が倉庫Bを補正できます。補正がなければこのrunだけを期限切れで終了し、現行資料から新しいrunを送信できます。
          </Typography>}
          <Button variant="contained" onClick={() => void startRun()} disabled={busy || !requestConfirmed || !requestAvailable}
            sx={{ mt: 1 }}>{latestCurrentRun ? '新しいrunで合成受注を調べ直す' : 'この合成受注の調査を送信する'}</Button>
          <Typography variant="body2" color={requestAvailable || activeTrialRun ? 'text.secondary' : 'warning.main'} sx={{ mt: 1 }}>
            {requestStatus}
            {health?.scenarioExpiresAt ? `／合成資料の期限：${date(health.scenarioExpiresAt)} JST` : ''}
          </Typography>
          {run?.requestText && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>保存runが受け付けた依頼：「{run.requestText}」</Typography>}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {instructed ? `受注第${detail.order.version}版の保存結果を表示中です。`
              : run ? `送信時に指定した受注版と固定依頼を同じrunで確認できます。`
                : `受注第${detail.order.version}版を指定して送信します。`}
            資料と受注は合成で、便の実予約や顧客への連絡は行いません。
          </Typography>
        </Paper>
        <InventoryAuthPanel detail={detail} authInitError={authInitError} onReadback={() => loadDetail(true)} />

        <Paper className="content-panel secondary-panel" data-testid="order-summary">
          <Box className="order-summary-top"><Typography color="text.secondary">{detail.order.customerName}</Typography>
            <Chip label={detail.order.status === 'shipment_instructed' ? '出荷指示済み' : detail.order.status === 'awaiting_approval' ? '出荷承認待ち' : '出荷例外'} color={instructed ? 'success' : 'warning'} variant="outlined" /></Box>
          <Typography component="h2" className="order-heading">受注 {orderId}：40箱、倉庫Aは25箱</Typography>
          <Typography>希望納期：{date(detail.order.dueAt)}。倉庫Aで15箱不足し、倉庫Bの現在庫は{currentWarehouseB ?? '読込中'}箱です。</Typography>
          {detail.exception.detectedAt && <Typography variant="body2" color="text.secondary">合成例外の検知時点：{date(detail.exception.detectedAt)}</Typography>}
          <Typography color="text.secondary" variant="body2">{detail.exception.message}</Typography>
          <Typography color="text.secondary" variant="body2">受注第{detail.order.version}版／現在の在庫：{detail.inventory?.map(item => `${warehouse(item.warehouseId)} ${item.available}箱`).join('、')}</Typography>
          <Typography color="text.secondary" variant="body2">現在の合成試行：{detail.order.currentTrialId}／この試行の保存run：{runs.filter(item => item.trialId === detail.order.currentTrialId).length}件</Typography>
        </Paper>

        {currentTrialRuns.length > 0 && <Paper className="content-panel secondary-panel" data-testid="current-trial-run-history">
          <Typography component="h2" className="section-title">この試行の保存run</Typography>
          <Typography variant="body2" color="text.secondary">新しい調査を送信しても旧runは消えません。旧runの案とTool操作は閲覧専用で、顧客回答と出荷承認には使えません。</Typography>
          <Stack spacing={1} sx={{ mt: 2 }}>
            {currentTrialRuns.map((item, index) => <Box key={item.id} sx={{ overflowWrap: 'anywhere' }}>
              <Typography variant="body2">{index === 0 ? '最新' : '旧run・閲覧専用'}／{item.executionMode === 'test_double' ? '固定試験・モデル呼出しなし' : '局所実モデル'}／{savedRunStatus(item)}／{item.id}</Typography>
              {isSourceRefsMismatch(item) && <Typography variant="body2" color="warning.main" data-testid="source-ref-mismatch-summary">
                案保存前に資料参照ID不一致で停止。{item.sourceRefAttempts?.length
                  ? 'このrunの提出IDと期待IDを下の診断記録で確認できます。'
                  : 'このrunで提出した参照IDの具体値は保存されておらず不明です。'}
              </Typography>}
              {hasServerDraft(item) && <Typography variant="body2" color="text.secondary" data-testid="saved-draft-summary">
                Agentが資料照合済み案{item.draft.plan?.proposals?.length ?? 0}件を保存。合成顧客の回答・責任者の承認は、案保存後の別の業務操作です。
                {detail.approval?.runId === item.id && detail.shipment?.instructions.length
                  ? ` その回答・承認後に出荷指示${detail.shipment.instructions.length}件を保存しました。` : ''}
              </Typography>}
              <Button size="small" variant="text" onClick={() => void showSavedRun(item.id)} disabled={busy || run?.id === item.id}>
                {index === 0 ? '最新のrunを表示' : '旧runを参照'}
              </Button>
            </Box>)}
          </Stack>
        </Paper>}

        <Paper className="content-panel secondary-panel" data-testid="agent-step">
          <Typography component="h2" className="section-title">{testDoubleVisible ? '固定試験の資料読取イベントと資料照合' : liveModelReady || run?.executionMode === 'live_model' ? '局所実モデルのTool読取と資料照合' : 'Agent未接続・保存資料の確認'}</Typography>
          <Typography>{testDoubleVisible
            ? '固定試験ランナーが合成資料の読取イベントを記録し、到着日時が資料と異なる案を提出します。これはTool呼出しやモデル判断の証拠ではありません。'
            : singleAgent
            ? '調査Agentが受注・在庫の束と便・契約の束をToolで読み、構造化案を提出します。下の資料行はTool呼出し回数ではなく資料IDです。'
            : run ? '管理Agentが担当を選び、担当Agentが在庫・配送・契約のToolを読み、構造化案を提出します。' : 'Agentが受注・在庫・便・契約をToolで読み、構造化案を提出します。'} 業務APIは案の数量・便・納期・費用を資料と照合します。</Typography>
          {!run && <Typography color="text.secondary" sx={{ mt: 2 }}>上の依頼欄から調査を送信すると、この欄に{testDoubleVisible ? '固定試験' : 'Agent'}と業務APIの結果が表示されます。</Typography>}
          {run && <>
            {readOnlyRun && <Alert severity="warning" sx={{ mt: 2 }}>この保存runは閲覧専用です。案とイベントを参照できますが、人役の操作対象にはできません。</Alert>}
            <Alert severity={readOnlyRun ? 'info' : draftReady ? 'info' : runReady ? 'success' : runStopped ? 'warning' : 'info'} sx={{ mt: 2 }} role="status">
              run {run.id}（{run.status}）：{readOnlyRun
                ? runStopped
                  ? run.errorCode === 'source_stale' && sourceChangedSinceRead
                    ? `旧runの最終停止理由：Tool読取時の受注第${readOrderVersion}版から現行の第${detail.order.version}版に変わったため、業務APIが旧案を保存せず停止しました（source_stale）。現在の人役の操作には使えません。`
                    : `旧runの最終停止理由：${stopReason(run.errorCode)}（${run.errorCode || run.executionMode}）。現在の人役の操作には使えません。`
                  : '旧runとして保存された記録です。現在の人役の操作には使えません。'
                : draftReady
                ? 'AgentがToolへ案を提出／モデル最終返答は未取得／業務APIが資料照合し案文投影'
                : runReady ? '実モデル提出案の資料値を業務APIが照合し、下書きを保存'
                  : runStopped && (run.status === 'draft_ready' || run.status === 'completed')
                    ? '保存runに操作可能な資料照合済み下書きがありません。合成人役の操作を止めています。'
                    : runStopped ? `${stopReason(run.errorCode)}（${run.errorCode || run.executionMode}）`
                      : testDoubleRun ? '固定試験を実行中。保存runを読み直しています。' : 'Agentが調査中。保存runを読み直しています。'}
            </Alert>
            <Typography variant="body2" sx={{ mt: 1 }}>モード：{run.executionMode}／{testDoubleRun ? '固定試験ランナー' : 'モデル'}：{run.modelId || '記録なし'}／経路：{run.modelRoute || '記録なし'}／業務レビュー：{run.draft?.businessReviewStatus || '未実施'}／契約レビュー：{run.draft?.contractReviewStatus || '未実施'}</Typography>
            {run.executionMode === 'live_model' && run.status === 'completed' && <Box className="live-activity" sx={{ mt: 2 }} data-testid="model-final-reply">
              <Typography component="h3" className="activity-title">SDKが返したテキスト（集約）</Typography>
              {run.modelFinalReply
                ? <><Alert severity="warning" sx={{ mb: 1 }}>モデル原文の事実・契約判断は未検証です。受注の例外状態、顧客回答、承認、出荷と案の根拠は下の業務API保存結果で確認してください。</Alert>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{modelReplyPlainText(run.modelFinalReply)}</Typography>
                    <Typography variant="caption" color="text.secondary">AgentResponse.text はrun中のテキスト内容を集約します。最後の発話だけかは未確認です。保存したテキストを画面では記号のない本文に投影しています。</Typography></>
                : <Typography variant="body2" color="text.secondary">このrunのSDKテキストは保存されていません。下の案文は業務APIが構造化値から作成したものです。</Typography>}
            </Box>}
            {run.executionMode === 'live_model' && run.status === 'completed' && readableDraft && detail &&
              <Box className="live-activity" sx={{ mt: 2 }} data-testid="structured-delivery-check">
                <Typography component="h3" className="activity-title">業務API保存値：希望納期と案の到着予定</Typography>
                <Typography variant="body2">保存runの受注希望納期：{plannedDate(savedDueAt)}（第{savedOrderVersion ?? '不明'}版）</Typography>
                {!sameOrderVersion && <Typography variant="body2">現在の受注は第{detail.order.version}版、Tool参照は第{readOrderVersion ?? '不明'}版です。版を照合できないため、現行受注との納期比較はできません。</Typography>}
                {proposals.map((proposal, index) => <Box key={proposal.id} sx={{ mt: 1 }} data-testid={`structured-delivery-option-${index + 1}`}>
                  <Typography variant="body2" sx={{ fontWeight: 700 }}>案{index + 1}：{proposalArrivalVerdict(proposal, savedDueAt, sameOrderVersion)}</Typography>
                  {proposal.shipments.map(item => <Typography key={item.warehouseId} variant="body2">
                    {warehouse(item.warehouseId)} {item.quantityBoxes}箱：到着予定 {plannedDate(item.deliveryAt)}／{arrivalVerdict(item.deliveryAt, savedDueAt, sameOrderVersion)}
                  </Typography>)}
                </Box>)}
                <Typography variant="caption" color="text.secondary">保存runの到着予定と保存runの受注希望納期を日時で比較しました。実際の到着、契約適合、費用負担先は未判定です。</Typography>
              </Box>}
            {testDoubleRun && <Alert severity="info" sx={{ mt: 1 }}>このrunは固定試験です。モデルは呼んでいません。資料読取イベントも固定試験ランナーが記録しました。</Alert>}
            {(run.status === 'running' || run.status === 'queued') && <Stack direction="row" spacing={1} sx={{ mt: 2 }}><CircularProgress size={18}/><Typography>{testDoubleRun ? '固定試験' : 'Agent'}の保存記録を待機中</Typography></Stack>}
            <Box className="live-activity" aria-live="polite">
              <Typography component="h3" className="activity-title">保存runの直近の操作</Typography>
              {run.events.length ? run.events.slice(-4).map(event => <Typography key={event.sequence} variant="body2">
                {event.kind === 'source_read' ? `${run.executionMode === 'test_double' ? '固定試験の資料読取イベント' : 'Tool資料'}：${event.sourceId ? sourceName(event.sourceId) : event.message}`
                  : `${actorName(event.actor)}：${event.message}`}
              </Typography>) : <Typography variant="body2" color="text.secondary">実行受付後の保存イベントを待っています。</Typography>}
            </Box>
            {run.executionMode === 'live_model' && run.status === 'running' && currentRun && !readOnlyRun &&
              <Box className="inventory-correction" data-testid="inventory-correction-step">
                <Typography component="h3" className="activity-title">実行中runの在庫補正</Typography>
                <Typography variant="body2">Agentが受注・在庫と便・契約のTool応答を保存した後、案を提出する前に、ログインした担当者が合成棚卸しの数を登録できます。業務APIが送信したrun IDと保存済みTool応答を再照合し、補正と実行イベントを一緒に記録します。</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  保存Tool応答：受注・在庫 {toolNames.has('read_order_inventory') ? '確認済み' : '待機中'}／便・契約 {toolNames.has('read_carriers_contract') ? '確認済み' : '待機中'}。在庫補正はKeycloakログインとOpenFGAの倉庫権限を送信時に確認します。
                </Typography>
                {inventoryResult && inventoryResultRunId === run.id && <Alert severity="success" role="status" sx={{ mt: 2 }} data-testid="inventory-count-result">
                  このrunに記録した補正：倉庫B {inventoryResult.previousAvailableBoxes}→{inventoryResult.availableBoxes}箱を受注第{inventoryResult.orderVersion}版に保存しました。顧客回答・出荷承認・出荷指示はこの補正で行いません。
                </Alert>}
                <Stack className="inventory-fields" spacing={1.5} sx={{ mt: 2 }}>
                  <Typography variant="body2">倉庫Bの現在庫：{currentWarehouseB ?? '読込中'}箱／受注第{detail.order.version}版</Typography>
                  <TextField label="補正後の在庫（箱）" type="number" value={warehouseCount}
                    onChange={event => setWarehouseCount(event.target.value)}
                    slotProps={{ htmlInput: { min: 0, max: 1000, step: 1 } }}
                    helperText="0～1000の整数。現在庫と異なる数を入力してください。" />
                  <TextField label="合成棚卸しの補正理由" multiline minRows={2} value={warehouseReason}
                    onChange={event => setWarehouseReason(event.target.value)}
                    slotProps={{ htmlInput: { maxLength: 500 } }} />
                  <FormControlLabel control={<Checkbox checked={warehouseConfirmed}
                    onChange={event => setWarehouseConfirmed(event.target.checked)} />}
                    label="合成棚卸しの数と補正理由を確認した" />
                  <Button variant="outlined" onClick={() => void recordInventoryCount()}
                    disabled={busy || !inventoryCountAvailable || !warehouseConfirmed ||
                      !validWarehouseCount || !warehouseReason.trim()}
                    data-testid="submit-inventory-count">
                    {busy ? '在庫補正を送信中' : 'ログイン済み担当者として在庫を補正する'}
                  </Button>
                  {!inventoryCountAvailable && <Typography variant="body2" color="text.secondary" role="status">
                    {inventoryResult && inventoryResultRunId === run.id
                      ? run.status === 'running' ? '在庫補正は保存済みです。Agent案の保存結果を待っています。' : '在庫補正は保存済みです。このrunの案保存結果を確認してください。'
                      : inventoryResult ? 'この試行では倉庫Bの在庫を補正済みです。現行版の案と業務状態を確認してください。'
                      : sourceChangedSinceRead ? 'Tool読取後に受注版が更新されています。このrunの案保存結果を確認してください。'
                      : !toolsReady ? 'Tool応答2件の保存を待っています。'
                      : draftSubmitted ? 'Agentが案を提出済みです。このrunでは案提出前の補正操作を受け付けません。'
                        : 'このrunは実行中ではありません。補正後の旧案停止と再依頼結果を確認してください。'}
                  </Typography>}
                </Stack>
              </Box>}
            {runStopped && run.events.some(event => event.kind === 'draft_rejected') && <Alert severity="warning" sx={{ mt: 2 }} data-testid="draft-rejection">業務APIの拒否記録：{run.events.filter(event => event.kind === 'draft_rejected').at(-1)?.message}</Alert>}
            {run.events.some(event => event.kind === 'human_inventory_count') && <Alert severity={run.errorCode === 'source_stale' ? 'warning' : 'info'} sx={{ mt: 2 }} data-testid="linked-inventory-count">
              このrunに結び付いた認証済み合成担当の補正：{run.events.find(event => event.kind === 'human_inventory_count')?.message}
              {run.errorCode === 'source_stale' ? ' Agentが読んだ受注版が変わり、旧案は保存されませんでした。' : ' Agentの最終結果を確認してください。'}
            </Alert>}
            {Boolean(run.sourceRefAttempts?.length) && <Box sx={{ mt: 2 }} data-testid="source-ref-attempts">
              <Typography component="h3" className="activity-title">案を提出したときの資料参照ID</Typography>
              <Typography variant="body2" color="text.secondary">
                {testDoubleRun ? '固定試験の提出値です。モデルは呼んでいません。' : 'AgentがToolで資料を読んだ後、案の保存前に提出した値です。'}
                合成資料ID以外の入力は値を伏せています。過去に記録していないrunの提出値は復元できません。
              </Typography>
              {run.sourceRefAttempts?.map(attempt => <Box key={`${attempt.attemptNo}-${attempt.proposalIndex}`}
                sx={{ mt: 1, p: 1.5, border: '1px solid #d8e1e8', borderRadius: 1, overflowWrap: 'anywhere' }}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>提出{attempt.attemptNo}回目・{attempt.proposalIndex < 0 ? '案の形式' : `案${attempt.proposalIndex + 1}`}：{attempt.outcome === 'validated' ? '資料ID一致' : attempt.reason === 'source_refs_mismatch' ? '資料ID不一致' : 'この案の資料IDは未判定'}</Typography>
                {attempt.reason && <Typography variant="body2" color="text.secondary">検査結果：{attempt.fieldPath || '項目不明'}／{attempt.reason}</Typography>}
                <Typography variant="body2">提出ID：{attempt.submittedShape === 'missing' ? '項目なし' : attempt.submittedShape === 'invalid_shape' ? '配列ではない値' : attempt.submittedRefs.length ? attempt.submittedRefs.join('、') : '安全な合成IDは記録なし'}</Typography>
                {attempt.expectedRefs.length > 0 && <Typography variant="body2">{attempt.outcome === 'not_checked' ? '指定便から計算した参照候補（案全体は未検査）' : 'この案で必要なID'}：{attempt.expectedRefs.join('、')}</Typography>}
                {attempt.duplicateRefs?.length > 0 && <Typography variant="body2" color="warning.main">重複：{attempt.duplicateRefs.join('、')}</Typography>}
                {attempt.reason === 'source_refs_mismatch' && attempt.expectedRefs.length > 0 && <Typography variant="body2" color="warning.main">
                  不足：{attempt.expectedRefs.filter(id => !attempt.submittedRefs.includes(id)).join('、') || 'なし'}／余分：{attempt.submittedRefs.filter(id => !attempt.expectedRefs.includes(id)).join('、') || 'なし'}。重複・件数も含め、受注版と使用する便のIDを確認して再実行してください。
                </Typography>}
              </Box>)}
            </Box>}
            {runStopped && <Alert severity="warning" sx={{ mt: 2 }}>このrunに承認可能な案はありません。合成顧客の回答、出荷承認、引当、出荷指示は進めません。下の保存結果で受注と在庫を確認できます。</Alert>}
            {runStopped && !answered && <Box sx={{ mt: 2 }}>
              <Typography variant="body2">再実行する場合は現行の受注版と在庫を確認し、依頼欄から新しいrunを送信してください。</Typography>
              {run.errorCode === 'human_wait_expired' && <Typography variant="body2" color="warning.main" sx={{ mt: 1 }}>
                このrunの在庫補正待機は期限切れです。現行資料の期限内なら、依頼欄で待機を選んで新しいrunを送信できます。
              </Typography>}
              {run.errorCode === 'source_stale' && <Button variant="outlined" sx={{ mt: 1 }}
                onClick={() => document.querySelector('[data-testid="request-step"]')?.scrollIntoView({ behavior: 'smooth' })}>
                現行版の再依頼へ戻る
              </Button>}
            </Box>}
            <details className="source-details"><summary>{testDoubleRun ? '固定試験イベント' : 'Agent実行イベント'}（{run.events.length}件）</summary>
              <Box component="ol" className="run-events">{run.events.map(event => <Box component="li" key={event.sequence} className="run-event">
                <Typography variant="body2" color="text.secondary">{actorName(event.actor)}／{event.kind === 'human_inventory_count' ? '在庫補正の保存' : event.kind === 'manager_selection' ? '担当選択' : event.kind === 'source_read' ? run.executionMode === 'live_model' ? 'Toolで得た資料' : '資料読取イベント' : event.kind}</Typography>
                <Typography>{event.sourceId ? `${sourceName(event.sourceId)}（${event.sourceId}）` : event.message}</Typography>
              </Box>)}</Box>
            </details>
          </>}
          {readableDraft && <>
            <Divider sx={{ my: 3 }}/>
            <Typography component="h3" className="section-title">業務APIが資料照合した別送案</Typography>
            <Alert severity="warning" sx={{ mb: 2 }}>{draftReady ? 'AgentのTool提出値を業務APIが資料と照合し、案文を作成しました。モデル最終返答は未取得です。' : '表示文は業務APIが照合済みの構造化値から生成しました。'}先頭表示はAPIの固定順で、モデルの推薦順位ではありません。業務妥当性・契約適合・特急便の費用負担先は未判定です。</Alert>
            {draftReady && <details className="source-details"><summary>業務APIが投影した案文を読む</summary>
              <Typography className="agent-draft" variant="body2">{run.draft.body}</Typography>
            </details>}
            <Stack spacing={2} data-testid="saved-options">{proposals.map(proposal => <Paper key={proposal.id} className={`option-card ${selectedId === proposal.id ? 'is-selected' : ''}`}>
              <Box className="option-card-head"><Typography component="h4" className="option-title">{proposal.additionalCostJpy === 0 ? '通常便で別送' : '特急便で別送'}</Typography>
                <Chip size="small" label={selectedId === proposal.id ? '選択中' : '未選択'} color={selectedId === proposal.id ? 'primary' : 'default'} variant="outlined" /></Box>
              <Typography variant="body2">案ID：{proposal.id}／run ID：{proposal.runId || run.id}</Typography>
              {proposal.shipments.map(item => <Typography key={item.warehouseId}>{warehouse(item.warehouseId)}から{item.quantityBoxes}箱、{date(item.deliveryAt)}に到着</Typography>)}
              <Typography sx={{ fontWeight: 700, mt: 1 }}>追加費用：{yen(proposal.additionalCostJpy)}</Typography>
              {proposal.additionalCostJpy > 0 && <Typography variant="body2" color="warning.main">追加費用の負担先は資料で確定していません。出荷承認では停止します。</Typography>}
              <details className="source-details"><summary>この案の根拠資料を見る</summary><Typography variant="body2">{proposal.sourceRefs?.map(sourceName).join('、') || 'run履歴の資料参照を確認してください'}</Typography></details>
              <Button variant={selectedId === proposal.id ? 'contained' : 'outlined'} onClick={() => { setSelectedId(proposal.id); setConfirmedTerms(false); setAcceptedSplit(false); setAcceptedExpressCost(false); }} sx={{ mt: 2 }} disabled={answered || readOnlyRun}>この案を選ぶ</Button>
            </Paper>)}</Stack>
          </>}
        </Paper>

        <Paper className="content-panel secondary-panel" data-testid="customer-step">
          <Typography component="h2" className="section-title">合成顧客役：提示条件を確認して回答</Typography>
          {readOnlyRun ? <Alert severity="info">表示中の旧runは閲覧専用です。現在の顧客回答には使えません。</Alert>
            : !selected ? <Typography color="text.secondary">{testDoubleVisible ? '固定試験案' : '実モデル案'}が資料照合に通って保存された場合だけ、案を選べます。失敗runでは回答を保存しません。</Typography> : <>
            <Typography>選んだ案：{selected.id}（run {run?.id}）</Typography>
            {selected.shipments.map(item => <Typography key={item.warehouseId}>{warehouse(item.warehouseId)} {item.quantityBoxes}箱、到着 {date(item.deliveryAt)}</Typography>)}
            <Typography sx={{ fontWeight: 700 }}>追加費用 {yen(selected.additionalCostJpy)}</Typography>
            {detail.customerResponse ? <Alert severity="success" sx={{ mt: 2 }}>合成顧客の回答を保存済み。案 {detail.customerResponse.selectedOptionId}／分納承諾 {detail.customerResponse.acceptedSplit ? 'あり' : 'なし'}。出荷はこの回答だけでは変更されません。</Alert> : <>
              <FormControlLabel control={<Checkbox checked={confirmedTerms} onChange={event => { setConfirmedTerms(event.target.checked); setError(''); }} />}
                label="上記の案ID・A/B数量・各納期・追加費用を確認した" />
              <FormControlLabel control={<Checkbox checked={acceptedSplit} onChange={event => { setAcceptedSplit(event.target.checked); setError(''); }} />}
                label="分納を承諾する" />
              {selected.additionalCostJpy > 0 && <FormControlLabel control={<Checkbox checked={acceptedExpressCost} onChange={event => { setAcceptedExpressCost(event.target.checked); setError(''); }} />}
                label={`特急便の追加費用 ${yen(selected.additionalCostJpy)} の発生を承諾する（費用負担先の確定ではありません）`} />}
              <TextField fullWidth label="合成顧客の回答メモ（任意）" value={note} onChange={event => setNote(event.target.value)} sx={{ mt: 2 }}/>
              <Button variant="contained" disabled={localNoAuth || busy || !confirmedTerms || !acceptedSplit || (selected.additionalCostJpy > 0 && !acceptedExpressCost)}
                onClick={() => void send('response')} sx={{ mt: 2 }}>この案の顧客回答を保存する</Button>
            </>}
          </>}
        </Paper>

        <Paper className="content-panel secondary-panel" data-testid="approval-step">
          <Typography component="h2" className="section-title">合成出荷責任者役：同じ案を承認</Typography>
          <Typography variant="body2" color="text.secondary">役割名は自己申告の試験入力です。ログイン認証や権限判定の証拠ではありません。</Typography>
          {readOnlyRun && <Alert severity="info" sx={{ mt: 2 }} data-testid="read-only-approval-boundary">
            表示中の旧runから顧客回答や出荷承認はできません。後続runの回答・承認と出荷結果は、下の現在の試行の業務状態で確認してください。
          </Alert>}
          {selected && !answered && !readOnlyRun && <Button variant="outlined" disabled={localNoAuth || busy || !runReady} onClick={() => void send('early')} sx={{ mt: 2 }}>顧客回答前の承認を試す</Button>}
          {!readOnlyRun && detail.customerResponse && <Typography sx={{ mt: 2 }}>顧客回答の案 {detail.customerResponse.selectedOptionId}／run {detail.customerResponse.runId || '記録内で確認'}／A/Bの数量・納期・費用を保存済み</Typography>}
          {readOnlyRun ? null
            : instructed ? <Alert severity="success" sx={{ mt: 2 }}>出荷指示を保存済み。承認案 {detail.approval?.optionId}。</Alert>
            : <>
              <TextField fullWidth multiline minRows={2} label="承認理由" value={reason} onChange={event => setReason(event.target.value)} sx={{ mt: 2 }}/>
              <Button variant="contained" disabled={localNoAuth || busy || !selected || !selectedMatches || !reason.trim() || sourceReviewBlocksApproval} onClick={() => void send('decision')} sx={{ mt: 2 }}>同じ案を承認し、出荷指示を保存する</Button>
              {sourceReviewBlocksApproval && <Typography variant="body2" color="warning.main" sx={{ mt: 1 }} data-testid="source-review-approval-blocked">
                {detail.sourceReviewRequest?.status === 'pending'
                  ? '費用負担先の原資料確認を依頼中です。この特急案の出荷承認は保留します。'
                  : detail.sourceReviewRequest?.status === 'source_clause_no_payer'
                    ? '保存runの契約原文に費用負担の条項がありません。この特急案の出荷承認はできません。'
                    : '便・契約の保存原文を照合できず、費用負担先は未確定です。この特急案の出荷承認はできません。'}
              </Typography>}
              {!selectedMatches && <Typography variant="body2" color="warning.main" sx={{ mt: 1 }}>顧客回答と同じ案ID・run IDを選ぶと承認できます。</Typography>}
            </>}
        </Paper>

        <Paper className="content-panel secondary-panel" data-testid="source-review-step">
          <Typography component="h2" className="section-title">追加費用の負担先を別担当へ確認依頼</Typography>
          <Typography variant="body2">合成顧客の追加費用への同意は、費用負担先を確定しません。特急案は負担先の根拠資料がないため、出荷承認を止めて契約確認担当役へ引き継ぎます。</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>担当名は自己申告の合成役です。保存runのTool応答がある場合だけ、その本文とハッシュを表示します。実人の認可、担当者への実送達、契約専門家の判断は示しません。</Typography>
          {detail.sourceReviewRequest ? <Alert severity={detail.sourceReviewRequest.response ? 'warning' : 'info'} sx={{ mt: 2 }} data-testid="source-review-pending">
            <Typography variant="body2">確認依頼を保存済み：{detail.sourceReviewRequest.status === 'pending' ? '原資料の確認待ち' : detail.sourceReviewRequest.status === 'source_clause_no_payer' ? '原文に負担条項なしと回答済み' : '保存原文を照合できないと回答済み'}／担当：契約確認担当役（合成）。出荷指示はありません。</Typography>
            <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>試行ID：{detail.sourceReviewRequest.trialId}／案ID：{detail.sourceReviewRequest.optionId}／run ID：{detail.sourceReviewRequest.runId}</Typography>
            <Typography variant="body2">理由：{detail.sourceReviewRequest.reason}</Typography>
            <details className="source-details"><summary>確認対象の資料IDと版を読む</summary>
              <Typography variant="body2">{detail.sourceReviewRequest.frozenContract ? '契約原文はこのrunのTool応答を保存したものです。表示したSHA-256との一致を業務APIが検証しました。' : detail.sourceReviewRequest.toolResponseIntegrity === 'missing' ? `便・契約は現行v${detail.sourceReviewRequest.sourceFixtureVersion}の固定資料IDです。旧runのTool応答原文は保存されていません。` : '保存した契約Tool応答の整合性を確認できません。費用負担先の判断を止めます。'}</Typography>
              {detail.sourceReviewRequest.sourceRefs.map(id => <Typography key={id} variant="body2" sx={{ overflowWrap: 'anywhere' }}>{sourceName(id)}：{id}</Typography>)}
            </details>
            {detail.sourceReviewRequest.status === 'pending' && !readOnlyRun && <>
              <Button variant="outlined" disabled={localNoAuth || busy} onClick={() => void openSourceReview()} sx={{ mt: 2 }}>
                契約確認担当役として確認依頼を開く
              </Button>
              {openedReviewId === detail.sourceReviewRequest.id && <Box data-testid="source-review-opened" sx={{ mt: 2 }}>
                <Typography variant="body2">この画面で確認依頼を読みました。原文の実受領を記録する操作ではありません。</Typography>
                {detail.sourceReviewRequest.frozenContract ? <Box data-testid="frozen-contract-source" sx={{ mt: 1 }}>
                  <Typography variant="body2">このrunで読んだ合成契約の条項：{detail.sourceReviewRequest.frozenContract.clause}</Typography>
                  <Typography variant="body2">資料版：{detail.sourceReviewRequest.frozenContract.version}／照合済みSHA-256：{detail.sourceReviewRequest.frozenContract.responseSha256.slice(0, 16)}…</Typography>
                  <details className="source-details"><summary>保存したTool応答原文とハッシュを読む</summary>
                    <Typography variant="body2" sx={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{detail.sourceReviewRequest.frozenContract.responseText}</Typography>
                    <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>SHA-256：{detail.sourceReviewRequest.frozenContract.responseSha256}</Typography>
                  </details>
                  {detail.sourceReviewRequest.frozenContract.noPayerClauseReviewed === true
                    ? <Typography variant="body2" color="warning.main">この条項は分納承諾を定めています。特急費用の負担先は記載されていません。</Typography>
                    : <Typography variant="body2" color="warning.main">保存原文は照合できましたが、この条項の負担先判断は画面の対象外です。回答を保存せず、契約の担当者へ引き継いでください。</Typography>}
                </Box> : <Typography variant="body2">{detail.sourceReviewRequest.toolResponseIntegrity === 'missing' ? `旧runには資料IDが残っていますが、便・契約の原文は保存されていません。現行fixture v${detail.sourceReviewRequest.sourceFixtureVersion}の本文を旧runの原文として扱えません。` : '契約Tool応答の保存本文または照合情報が一致せず、原文を確認できません。負担先の判断を止めます。'}</Typography>}
                {(detail.sourceReviewRequest.toolResponseIntegrity === 'missing' || detail.sourceReviewRequest.frozenContract?.noPayerClauseReviewed === true) && <>
                  <TextField fullWidth multiline minRows={2} sx={{ mt: 2 }} label={detail.sourceReviewRequest.frozenContract ? '負担先を確定できない理由' : '原文を照合できない理由'}
                    value={reviewReplyReason} onChange={event => setReviewReplyReason(event.target.value)}
                    helperText={detail.sourceReviewRequest.frozenContract ? '保存原文のどの条項を読み、何が不足するか記入してください。' : '不足する原文と、費用負担先を確定できない理由を記入してください。'} />
                  <Button variant="contained" disabled={localNoAuth || busy || !reviewReplyReason.trim()}
                    onClick={() => void respondSourceReview()} sx={{ mt: 2 }}>
                    {detail.sourceReviewRequest.frozenContract ? '負担条項なしと回答を保存する' : '原文を照合できないと回答を保存する'}
                  </Button>
                </>}
              </Box>}
            </>}
            {detail.sourceReviewRequest.response && <Box data-testid="source-review-response" sx={{ mt: 2 }}>
              <Typography variant="body2">契約確認担当役（合成）の回答：{detail.sourceReviewRequest.response.outcome === 'source_clause_no_payer' ? '保存runの契約原文は確認しましたが、特急費用の負担条項がありません。' : '保存runの原文を照合できません。'}費用負担先は未確定です。</Typography>
              <Typography variant="body2">回答理由：{detail.sourceReviewRequest.response.reason}</Typography>
              <Typography variant="body2">保存日時：{date(detail.sourceReviewRequest.response.respondedAt)}／確認依頼ID：{detail.sourceReviewRequest.response.reviewId}</Typography>
              <Typography variant="body2">次の操作：{detail.sourceReviewRequest.response.outcome === 'source_clause_no_payer' ? '費用負担の条項または責任者の判断を取得する。' : '便・契約の正しい原文を取得し、負担先を再確認する。'}出荷承認は保留します。</Typography>
            </Box>}
          </Alert> : <>
            <TextField fullWidth multiline minRows={2} label="確認依頼の理由" value={reviewReason}
              onChange={event => setReviewReason(event.target.value)} sx={{ mt: 2 }}
              helperText="契約確認担当役に調べてほしい資料と未確定の判断を書いてください。" />
            <Button variant="outlined" sx={{ mt: 2 }} onClick={() => void requestSourceReview()}
              disabled={localNoAuth || busy || readOnlyRun || !selectedMatches || !detail.customerResponse?.acceptedExpressCost ||
                (selected?.additionalCostJpy ?? 0) <= 0 || !reviewReason.trim() || instructed}>
              契約確認担当役へ原資料確認を依頼する
            </Button>
            {!selectedMatches && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>合成顧客の回答済み特急案を選ぶと依頼できます。</Typography>}
          </>}
        </Paper>

        <Paper className="content-panel secondary-panel" data-testid="result-step">
          <Typography component="h2" className="section-title">現在の試行の業務状態（局所SQLite）</Typography>
          {readOnlyRun && run?.status === 'failed' && !run.draft && instructed &&
            detail.approval?.runId !== run.id && <Alert severity="info" sx={{ mt: 2 }} data-testid="historical-run-result-boundary">
              表示中の失敗runは案・合成人役の操作・出荷指示を保存していません。下の出荷指示{detail.shipment?.instructions.length ?? 0}件は、後続runの案に合成人役が回答・承認した後の現在の業務状態です。
            </Alert>}
          <Button variant="outlined" onClick={() => void Promise.all([loadDetail(true), loadRuns(true), loadHealth(true)]).catch(cause => setError((cause as Error).message))}>保存状態を読み直す</Button>
          <Typography sx={{ mt: 2 }}>受注：{detail.order.status}（第{detail.order.version}版）／顧客回答：{answered ? 'あり' : 'なし'}／出荷承認：{detail.approval ? 'あり' : 'なし'}</Typography>
          <Typography>引当：{detail.shipment?.reservations.length ?? 0}件／出荷指示：{detail.shipment?.instructions.length ?? 0}件</Typography>
          <Typography>費用負担先の確認依頼：{detail.sourceReviewRequest?.status === 'pending' ? '契約確認担当役の確認待ち' : detail.sourceReviewRequest?.status === 'source_clause_no_payer' ? '保存原文に負担条項なし・費用負担先は未確定' : detail.sourceReviewRequest?.status === 'original_source_unavailable' ? '保存原文を照合できないと回答済み・費用負担先は未確定' : 'なし'}</Typography>
          {detail.shipment?.instructions.map(item => <Typography key={item.warehouseId}>{warehouse(item.warehouseId)} {item.quantity}箱、到着 {date(item.deliveryAt)}</Typography>)}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>在庫残：{detail.inventory?.map(item => `${warehouse(item.warehouseId)} ${item.available}箱`).join('、')}</Typography>
          <details className="source-details"><summary>この画面で送ったHTTP操作を見る</summary>
            <Box component="ol" className="run-events">{trace.map((item, index) => <Box component="li" className="run-event" key={index}>
              <Typography variant="body2">{item.at}／HTTP {item.status}／{item.label}</Typography><Typography variant="body2" color="text.secondary">{item.detail}</Typography>
            </Box>)}</Box>
          </details>
        </Paper>

        {Boolean(detail.previousTrials?.length) && <Paper className="content-panel secondary-panel" data-testid="trial-history">
          <Typography component="h2" className="section-title">過去の合成試行と保存結果</Typography>
          <Typography variant="body2" color="text.secondary">以前の出荷指示または出荷保留の結果とAgent runは保存済みです。保存runを表示しても、現在の試行の回答や承認には使えません。</Typography>
          {detail.previousTrials?.map(archive => {
            const saved = archiveReadback[archive.archiveId] ?? archive;
            return <details className="trial-history-entry" key={archive.archiveId}
              open={expandedArchive === archive.archiveId}
              onToggle={event => {
                if (event.currentTarget.open) setExpandedArchive(archive.archiveId);
                else setExpandedArchive(current => current === archive.archiveId ? null : current);
              }}>
              <summary>試行 {archive.trialId}／{saved.outcome.order?.status === 'shipment_instructed'
                ? '出荷指示済み'
                : saved.outcome.order?.status === 'awaiting_approval' &&
                  ['source_clause_no_payer', 'original_source_unavailable'].includes(saved.outcome.sourceReviewRequest?.status || '')
                  ? '契約確認回答済み・出荷保留'
                  : saved.outcome.order?.status || '状態不明'}／試行結果の保存 {date(archive.completedAt)}</summary>
              <Box className="trial-history-body">
                <Typography variant="body2">保存記録：{archive.archiveId}／受注第{saved.outcome.order?.version ?? '不明'}版</Typography>
                {saved.archivedAt && <Typography variant="body2">履歴へ保存：{date(saved.archivedAt)}</Typography>}
                <Typography variant="body2">顧客回答：{saved.outcome.customerResponse?.selectedOptionId || 'なし'}／出荷承認：{saved.outcome.approval?.optionId || 'なし'}</Typography>
                {saved.outcome.sourceReviewRequest && <>
                  <Typography variant="body2">契約確認依頼：{saved.outcome.sourceReviewRequest.id || 'ID記録なし'}／{saved.outcome.sourceReviewRequest.reason || '理由記録なし'}</Typography>
                  <Typography variant="body2">担当役の回答：{saved.outcome.sourceReviewRequest.response?.outcome === 'source_clause_no_payer' ? '原文に費用負担条項なし' : saved.outcome.sourceReviewRequest.response?.outcome === 'original_source_unavailable' ? '原文を照合できない' : '回答待ち'}／{saved.outcome.sourceReviewRequest.response?.reason || '理由記録なし'}</Typography>
                </>}
                <Typography variant="body2">引当：{saved.outcome.shipment?.reservations?.length ?? 0}件／出荷指示：{saved.outcome.shipment?.instructions?.length ?? 0}件</Typography>
                {saved.outcome.shipment?.instructions?.map(item => <Typography variant="body2" key={item.warehouseId}>{warehouse(item.warehouseId)} {item.quantity}箱、到着 {date(item.deliveryAt)}</Typography>)}
                <Typography variant="body2">保存時の在庫：{saved.outcome.inventory?.map(item => `${warehouse(item.warehouseId)} ${item.available}箱`).join('、') || '記録なし'}</Typography>
                {archiveReadback[archive.archiveId] && <Typography variant="body2" className="readback-label">保存記録をAPIから読戻し済み</Typography>}
                <Stack direction="row" useFlexGap sx={{ mt: 1, flexWrap: 'wrap', gap: 1 }}>
                  <Button size="small" variant="outlined" onClick={() => void loadArchive(archive.archiveId).catch(cause => setError((cause as Error).message))}>この保存結果を読み直す</Button>
                  {saved.runIds.map(id => <Button size="small" variant="text" key={id} onClick={() => void showSavedRun(id)}>保存run {id} を表示</Button>)}
                </Stack>
              </Box>
            </details>;
          })}
        </Paper>}

      </>}
    </Container>
    <Dialog className="new-trial-dialog" open={newTrialOpen && Boolean(detail && (instructed || reviewedHold))} onClose={busy ? undefined : () => setNewTrialOpen(false)}
      aria-labelledby="new-trial-title" maxWidth="sm" fullWidth>
      <DialogTitle id="new-trial-title">新しい合成試行を開始しますか</DialogTitle>
      <DialogContent dividers>
        <Typography>受注 {orderId} の{reviewedHold ? '契約確認の回答済み・出荷0の保留結果' : '出荷指示済みの結果'}を履歴に保存します。現在の顧客回答・出荷承認・引当・出荷指示を初期化し、合成在庫を初期値に戻します。</Typography>
        <Typography sx={{ mt: 1 }}>現在の試行 {detail?.order.currentTrialId} の{reviewedHold ? '契約確認回答とAgent run' : '顧客回答・出荷指示とAgent run'}は保存履歴から読み直せます。新しい試行では調査依頼から操作をやり直します。</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>実際の出荷指示、在庫、顧客連絡は行いません。</Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => setNewTrialOpen(false)} disabled={busy}>戻る</Button>
        <Button variant="contained" onClick={() => void startNewTrial()} disabled={localNoAuth || busy}
          data-testid="confirm-new-trial">保存して新しい合成試行を開始</Button>
      </DialogActions>
    </Dialog>
  </ThemeProvider>;
}

// Keycloak processes its authorization callback before the app reads or changes the URL.
void (async () => {
  // Keycloak may redirect during check-sso. Retain a validated run deep link
  // across that round trip without widening the Keycloak redirect allowlist.
  const runKey = 'oe02-trial-return-run';
  const requestedRun = new URLSearchParams(window.location.search).get('run');
  if (requestedRun && /^order-run-[0-9a-f]{32}$/.test(requestedRun)) {
    window.sessionStorage.setItem(runKey, requestedRun);
  }
  const localNoAuth = import.meta.env.DEV && import.meta.env.VITE_OE02_LOCAL_NO_AUTH === 'true';
  let authInitError = localNoAuth
    ? 'ローカル確認モード：Keycloak認証を接続していません。在庫補正は操作できません。'
    : '';
  if (!localNoAuth) {
    try {
      await initializeInventoryIdentity();
    } catch {
      authInitError = 'Keycloakのログイン状態を確認できません。認証サービスの起動後に画面を再読込してください。';
    }
  }
  const returnRun = window.sessionStorage.getItem(runKey);
  window.sessionStorage.removeItem(runKey);
  if (returnRun && /^order-run-[0-9a-f]{32}$/.test(returnRun) &&
      !new URLSearchParams(window.location.search).has('run')) {
    const url = new URL(window.location.href);
    url.searchParams.set('run', returnRun);
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
  }
  createRoot(document.getElementById('root')!).render(<StrictMode><App authInitError={authInitError} /></StrictMode>);
})();
