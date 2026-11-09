import { useState } from 'react';
import { Alert, Button, Chip, Paper, Stack, Typography } from '@mui/material';
import { inventoryIdentity } from './inventory-identity';

type OrderReadback = {
  order: { version: number; status: string };
  inventory?: { warehouseId: string; available: number }[];
  shipment?: { instructions: unknown[] } | null;
};
type Props = {
  detail: OrderReadback | null;
  authInitError: string;
  onReadback: () => Promise<OrderReadback>;
};

function warehouseB(detail: OrderReadback | null): number | undefined {
  return detail?.inventory?.find(item => item.warehouseId === 'warehouse-b')?.available;
}

export function InventoryAuthPanel({ detail, authInitError, onReadback }: Props) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const authenticated = Boolean(inventoryIdentity.authenticated);
  const localNoAuth = import.meta.env.DEV && import.meta.env.VITE_OE02_LOCAL_NO_AUTH === 'true';
  const username = String(inventoryIdentity.tokenParsed?.preferred_username || 'ログイン済み担当者');
  const login = async () => {
    try { await inventoryIdentity.login({ redirectUri: `${window.location.origin}${import.meta.env.BASE_URL}` }); }
    catch { setMessage('Keycloakのログイン画面を開けません。認証サービスを確認してください。'); }
  };
  const logout = async () => {
    try { await inventoryIdentity.logout({ redirectUri: `${window.location.origin}${import.meta.env.BASE_URL}` }); }
    catch { setMessage('ログアウトを完了できません。画面を再読込してログイン状態を確認してください。'); }
  };
  const readSavedState = async () => {
    setBusy(true);
    try {
      const saved = await onReadback();
      setMessage(`業務APIから再読込：受注第${saved.order.version}版／倉庫B ${warehouseB(saved) ?? '不明'}箱／出荷指示${saved.shipment?.instructions.length ?? 0}件。`);
    } catch { setMessage('業務APIの保存状態を再読込できません。'); }
    finally { setBusy(false); }
  };

  return <Paper className="content-panel" data-testid="inventory-auth-card">
    <Typography component="h2" className="section-title">合成倉庫担当者のログイン（在庫補正のみ）</Typography>
    <Typography variant="body2">本人限定入口に入った後、調査依頼は倉庫担当者のKeycloakログインなしで開始できます。倉庫Bを補正するときだけ、権限のある合成倉庫担当者がここでログインします。Agentが資料を読んだ後、「実行中runの在庫補正」から送信します。業務APIが送信時に権限を確認します。</Typography>
    <Stack direction="row" useFlexGap sx={{ mt: 1.5, flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
      <Chip color={authenticated ? 'success' : 'default'} variant="outlined" label={authenticated ? `Keycloakログイン済み：${username}` : '未ログイン'} data-testid="inventory-auth-identity" />
      {localNoAuth ? <Chip variant="outlined" label="ローカル複製DB・認証未接続" /> : <>
        <Chip variant="outlined" label="隔離HTTPS試行" />
        <Chip variant="outlined" label="実Keycloak・実OpenFGA" />
      </>}
      <Chip variant="outlined" label="合成受注・公開業務版へ未反映" />
    </Stack>
    {authInitError && <Alert severity="warning" sx={{ mt: 1.5 }} role="alert" data-testid="inventory-auth-error">{authInitError}</Alert>}
    {!authenticated && !authInitError && <Button variant="contained" sx={{ mt: 2 }} data-testid="inventory-auth-login" onClick={() => void login()}>Keycloakでログイン</Button>}
    {authenticated && <Button variant="outlined" sx={{ mt: 2 }} data-testid="inventory-auth-logout" onClick={() => void logout()}>ログアウト</Button>}
    <Typography variant="body2" sx={{ mt: 1.5 }}>現在の保存状態：受注第{detail?.order.version ?? '読込中'}版／倉庫B {warehouseB(detail) ?? '読込中'}箱／出荷指示{detail ? (detail.shipment?.instructions.length ?? 0) : '読込中'}件</Typography>
    <Button variant="outlined" sx={{ mt: 1 }} onClick={() => void readSavedState()} disabled={busy || !detail} data-testid="inventory-auth-readback">保存状態を再読込</Button>
    {message && <Alert severity="info" role="status" sx={{ mt: 1.5 }} data-testid="inventory-auth-readback-notice">{message}</Alert>}
  </Paper>;
}
