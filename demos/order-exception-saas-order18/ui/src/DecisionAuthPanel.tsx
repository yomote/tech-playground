import { useState } from 'react';
import { Alert, Button, Chip, Paper, Stack, Typography } from '@mui/material';
import { inventoryIdentity } from './inventory-identity';

type Identity = {
  subject: string;
  displayName: string;
  roles: string[];
  canRespondSourceReview: boolean;
  canApproveShipment: boolean;
};
type Props = {
  identity: Identity | null;
  status: string;
  authReady: boolean;
  onReadback: () => Promise<unknown>;
};

const returnUri = import.meta.env.VITE_OE02_KEYCLOAK_REDIRECT_URI || `${window.location.origin}/trial-order18/`;

export function DecisionAuthPanel({ identity, status, authReady, onReadback }: Props) {
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const login = async () => {
    try { await inventoryIdentity.login({ redirectUri: returnUri }); }
    catch { setMessage('Keycloakのログイン画面を開けません。認証サービスの接続を確認してください。'); }
  };
  const readSavedState = async () => {
    setBusy(true);
    try { await onReadback(); setMessage('受注・引当・出荷指示を業務APIから再読込しました。'); }
    catch { setMessage('業務APIの保存状態を再読込できません。'); }
    finally { setBusy(false); }
  };

  return <Paper className="content-panel" data-testid="decision-auth-card">
    <Typography component="h2" className="section-title">契約確認と出荷承認の担当者認証</Typography>
    <Typography variant="body2">合成担当者のKeycloakログイン後、業務APIがOpenFGAでこの受注への権限を確認します。ログインだけでは回答・承認できません。保存済みのAgent案はログインなしでも閲覧できます。</Typography>
    <Stack direction="row" useFlexGap sx={{ mt: 1.5, flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
      <Chip variant="outlined" color={identity ? 'success' : 'default'}
        label={identity ? `認証・権限確認済み：${identity.displayName}` : inventoryIdentity.authenticated ? 'Keycloakログイン済み・業務権限を確認中' : '未ログイン'}
        data-testid="decision-auth-identity" />
      {identity?.canRespondSourceReview && <Chip color="success" variant="outlined" label="契約確認の回答可" data-testid="review-authorized" />}
      {identity?.canApproveShipment && <Chip color="success" variant="outlined" label="出荷承認可" data-testid="approval-authorized" />}
      {identity && !identity.canRespondSourceReview && !identity.canApproveShipment && <Chip variant="outlined" label="この受注の回答・承認権限なし" data-testid="decision-unauthorized" />}
      <Chip variant="outlined" label="合成受注・局所認証試行" />
    </Stack>
    {!authReady && <Alert severity="warning" sx={{ mt: 1.5 }} role="status">業務APIの認証機能を確認できません。回答と承認は停止しています。</Alert>}
    {status && <Alert severity="warning" sx={{ mt: 1.5 }} role="status" data-testid="decision-auth-status">{status}</Alert>}
    {!inventoryIdentity.authenticated && <Button variant="contained" sx={{ mt: 2 }} data-testid="decision-auth-login" onClick={() => void login()}>合成担当者でKeycloakログイン</Button>}
    {inventoryIdentity.authenticated && <Typography variant="body2" sx={{ mt: 2 }} data-testid="decision-auth-switch-note">別の担当者は別のブラウザープロファイルでこの画面を開き、ログインしてください。</Typography>}
    <Button variant="outlined" sx={{ mt: 2, ml: 1 }} data-testid="decision-auth-readback" disabled={busy} onClick={() => void readSavedState()}>保存状態を再読込</Button>
    {message && <Alert severity="info" sx={{ mt: 1.5 }} role="status">{message}</Alert>}
  </Paper>;
}
