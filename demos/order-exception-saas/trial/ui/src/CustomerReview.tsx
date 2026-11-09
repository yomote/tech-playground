import { useRef, useState } from 'react';
import { Alert, Box, Button, Checkbox, FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography } from '@mui/material';

export type CustomerReview = {
  id: string;
  originalText: string;
  editedText: string;
  reason: string;
  actorRole: 'order_clerk';
  authorizationMode: 'declared_role_fixture';
  verified: false;
  sendable: false;
  recordedAt: string;
  reviewScope?: 'two_option_customer_preview';
  factCheckStatus?: 'protected_facts_unchanged' | 'legacy_unchecked';
  factCheckScope?: 'locked_factual_lines_and_approved_question' | 'none';
};

type Props = {
  runId: string;
  originalText: string;
  questionChoices: string[];
  saved?: CustomerReview | null;
  canEdit: boolean;
  localReadOnly?: boolean;
  onSave: (values: { reason: string; editedText: string; operationId: string }) => Promise<CustomerReview>;
  onReadback: () => Promise<CustomerReview | null>;
};

export function CustomerReviewPanel({ runId, originalText, questionChoices, saved, canEdit, localReadOnly, onSave, onReadback }: Props) {
  const originalLines = originalText.split('\n');
  const hasFixedFactLines = originalLines.length === 5 &&
    originalLines[0].startsWith('【確認用・未送信】') &&
    originalLines[1].startsWith('通常便案：') &&
    originalLines[2].startsWith('特急便案：') &&
    originalLines[4].startsWith('契約への適合');
  const [reason, setReason] = useState('');
  const [questionLine, setQuestionLine] = useState('');
  const [declared, setDeclared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [readback, setReadback] = useState<CustomerReview | null>(null);
  const pendingOperation = useRef<{ signature: string; id: string } | null>(null);
  const review = saved ?? readback;
  const questionHasNumberOrNewline = /[\p{N}一二三四五六七八九十百千万億兆〇零\r\n]/u.test(questionLine);
  const questionApproved = questionChoices.includes(questionLine);
  const editedText = hasFixedFactLines
    ? [originalLines[0], originalLines[1], originalLines[2], questionLine.trim(), originalLines[4]].join('\n')
    : originalText;
  const canSubmit = canEdit && !busy && !review && declared && reason.trim().length > 0 &&
    hasFixedFactLines && questionApproved && !questionHasNumberOrNewline && questionLine.trim().length > 0 &&
    questionLine.trim() !== originalLines[3].trim();

  const save = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setMessage('');
    const trimmedReason = reason.trim();
    const trimmedText = editedText.trim();
    const signature = JSON.stringify([trimmedReason, trimmedText]);
    if (pendingOperation.current?.signature !== signature) {
      pendingOperation.current = { signature, id: crypto.randomUUID() };
    }
    try {
      const expected = await onSave({ reason: trimmedReason, editedText: trimmedText,
        operationId: pendingOperation.current.id });
      const persisted = await onReadback();
      if (!persisted || persisted.id !== expected.id || persisted.editedText !== expected.editedText ||
          persisted.originalText !== originalText) {
        throw new Error('保存後の文案を同じrunから読み直せません。業務APIの保存状態を確認してください。');
      }
      setReadback(persisted);
      setMessage('確認文の修正を同じrunから読み直しました。固定行は保存時に照合しました。顧客への送信、承認、出荷はしていません。');
    } catch (cause) {
      try {
        const persisted = await onReadback();
        if (persisted) {
          setReadback(persisted);
          setMessage(persisted.editedText === trimmedText && persisted.reason === trimmedReason
            ? '応答を確認できませんでしたが、同じrunから修正理由と修正文を読み直しました。照合範囲は保存記録で確認してください。'
            : 'このrunには別の修正記録が保存されています。表示内容を確認してください。');
        } else {
          setMessage(cause instanceof Error ? cause.message : '修正文を保存できません。業務APIの状態を確認してください。');
        }
      } catch {
        setMessage('保存結果を読み直せません。通信状態を確認してから、同じ入力で再試行してください。');
      }
    } finally { setBusy(false); }
  };

  const reload = async () => {
    setBusy(true);
    setMessage('');
    try {
      const persisted = await onReadback();
      setReadback(persisted);
      setMessage(persisted
        ? '同じrunから保存済みの修正理由と修正文を読み直しました。'
        : 'このrunに保存済みの修正文はありません。');
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : '保存した修正文を読み直せません。');
    } finally { setBusy(false); }
  };

  return <Paper variant="outlined" className="customer-review" data-testid="customer-review">
    <Typography component="h3" className="activity-title">合成受注担当者役の文案確認</Typography>
    <Typography variant="body2">担当役は自己申告です。実担当の認証や送信権限判定はありません。原案と修正文はいずれも未送信です。</Typography>
    <Typography variant="body2" sx={{ mt: 0.5 }}>修正対象：顧客への確認文だけです。受注、通常便案、特急便案、未確認事項の行は固定します。配送案の承認ではありません。</Typography>
    <Typography variant="caption" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>対象の保存run：{runId}</Typography>
    {review ? <>
      <Alert severity="warning" sx={{ mt: 1.5 }} role="status" data-testid="customer-review-state">
        {review.factCheckStatus === 'protected_facts_unchanged'
          ? '確認文を保存済み。数量・到着予定・費用などの固定4行と選択した文例は保存時に照合しました。実資料・実担当者の権限は未照合です。顧客回答・送信・出荷承認は未実施です。'
          : '合成受注担当者役の旧修正案を保存済み。内容は資料と未照合です。顧客回答・送信・出荷承認は未実施です。'}
      </Alert>
      <Box className="customer-review-comparison" data-testid="customer-review-comparison">
        <Box><Typography variant="subtitle2">業務APIが作った元の文案</Typography>
          <Typography variant="body2" className="customer-review-copy">{review.originalText}</Typography></Box>
        <Box><Typography variant="subtitle2">合成担当者役の修正文（未送信）</Typography>
          <Typography variant="body2" className="customer-review-copy">{review.editedText}</Typography></Box>
      </Box>
      <Typography variant="body2" sx={{ mt: 1 }}>修正理由：{review.reason}</Typography>
      <Typography variant="caption" color="text.secondary">保存記録：{review.id}／{review.recordedAt}</Typography>
      <Box sx={{ mt: 1 }}><Button variant="outlined" disabled={busy} onClick={() => void reload()}
        data-testid="customer-review-readback">保存した修正文を再読込</Button></Box>
    </> : canEdit && hasFixedFactLines && questionChoices.length > 0 ? <Stack spacing={1.5} sx={{ mt: 1.5 }}>
      <Alert severity="info">顧客への確認文を承認済みの文例から選びます。数量・到着予定・費用の行は変更できません。保存時に固定行と文例を照合しますが、実資料や実担当者の権限は確認していません。顧客への送信や承認は行いません。</Alert>
      <Box className="customer-review-locked" data-testid="customer-review-locked-facts">
        <Typography variant="subtitle2">固定する案内と配送条件</Typography>
        <Typography variant="body2" className="customer-review-copy">{originalLines.slice(0, 3).join('\n')}</Typography>
      </Box>
      <Typography variant="body2">元の確認文：{originalLines[3]}</Typography>
      <TextField label="修正理由" value={reason} onChange={event => setReason(event.target.value)}
        required multiline minRows={2} slotProps={{ htmlInput: { maxLength: 500 } }} data-testid="customer-review-reason" />
      <FormControl fullWidth required>
        <InputLabel id="customer-review-question-label">顧客への確認文（未送信）</InputLabel>
        <Select labelId="customer-review-question-label" value={questionLine}
          label="顧客への確認文（未送信）" onChange={event => setQuestionLine(event.target.value)}
          sx={{ '& .MuiSelect-select': { whiteSpace: 'normal', lineHeight: 1.5 } }}
          data-testid="customer-review-question-choice">
          {questionChoices.map(choice => <MenuItem key={choice} value={choice} sx={{ whiteSpace: 'normal' }}>{choice}</MenuItem>)}
        </Select>
      </FormControl>
      {questionLine && <Typography variant="body2" className="customer-review-selected" data-testid="customer-review-selected-question">
        選択中の確認文：{questionLine}
      </Typography>}
      <Typography variant="caption" color="text.secondary">業務APIが示す文例だけを選べます。自由記述はできません。</Typography>
      <Box className="customer-review-locked" data-testid="customer-review-locked-limitations">
        <Typography variant="subtitle2">固定する未確認事項</Typography>
        <Typography variant="body2" className="customer-review-copy">{originalLines[4]}</Typography>
      </Box>
      <FormControlLabel control={<Checkbox checked={declared} onChange={event => setDeclared(event.target.checked)} />}
        label="合成受注担当者役として、確認文を未送信の記録に保存する" />
      <Box><Button variant="contained" disabled={!canSubmit} onClick={() => void save()}
        data-testid="customer-review-save">修正理由と確認文を保存</Button></Box>
      <Box><Button variant="outlined" disabled={busy} onClick={() => void reload()}
        data-testid="customer-review-readback">保存状態を再読込</Button></Box>
      <Typography variant="caption" color="text.secondary">確認文を選び、修正理由を入力すると保存できます。</Typography>
    </Stack> : <Alert severity="info" sx={{ mt: 1.5 }} data-testid="customer-review-unavailable">
      {canEdit && !hasFixedFactLines
        ? '保存文案の固定行を識別できません。元文の5行構成を確認してください。'
        : canEdit && questionChoices.length === 0
          ? '確認文の文例を取得できません。業務APIの保存状態を再読込してください。'
        : localReadOnly
          ? '局所閲覧モードでは保存できません。本人限定の操作画面で現行runを開いてください。'
          : 'このrunは文案を修正できません。現行の保存案と資料の照合状態を確認してください。'}
    </Alert>}
    {message && <Alert severity={message.includes('読み直しました') ? 'success' : 'warning'} sx={{ mt: 1.5 }} role="status"
      data-testid="customer-review-message">{message}</Alert>}
  </Paper>;
}
