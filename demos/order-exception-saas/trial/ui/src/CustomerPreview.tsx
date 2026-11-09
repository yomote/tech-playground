import { Alert, Box, Paper, Typography } from '@mui/material';
import { CustomerReviewPanel, type CustomerReview } from './CustomerReview';

export type CustomerPreview = {
  status: 'available' | 'not_available';
  text?: string;
  basis: 'saved_structured_draft';
  limitations: string[];
  historical: boolean;
  historicalReason?: string;
  sendable: false;
  reason?: string;
  reviewBasisSha256?: string;
  reviewScope?: 'two_option_customer_preview';
  review?: CustomerReview | null;
  historicalReview?: (CustomerReview & { historical: true }) | null;
  reviewQuestionChoices?: string[];
};

export function CustomerPreviewPanel({ preview, readOnlyRun, localReadOnly, allowText, customerResponseSaved, approvalSaved,
  runId, canReview, onReviewSave, onReviewReadback }: {
  preview?: CustomerPreview;
  readOnlyRun: boolean;
  localReadOnly?: boolean;
  allowText: boolean;
  customerResponseSaved: boolean;
  approvalSaved: boolean;
  runId?: string;
  canReview?: boolean;
  onReviewSave?: (values: { reason: string; editedText: string; operationId: string }) => Promise<CustomerReview>;
  onReviewReadback?: () => Promise<CustomerReview | null>;
}) {
  const historical = readOnlyRun || preview?.historical === true;
  const canShowText = allowText && !historical && preview?.status === 'available' &&
    preview.basis === 'saved_structured_draft' && preview.sendable === false &&
    typeof preview.text === 'string' && preview.text.trim().length > 0;
  const reviewed = canShowText && Boolean(preview?.review);
  const historicalReview = preview?.historicalReview ?? null;

  return <Paper className="content-panel secondary-panel" data-testid="customer-preview">
    <Typography component="h2" className="section-title">顧客向け案内文（確認用・未送信）</Typography>
    <Alert severity="warning" sx={{ mb: 1 }} role="status">
      {historical
        ? '旧runの保存案は閲覧専用です。現行の顧客案内には使えず、送信もしていません。'
        : historicalReview
          ? '保存時の文案は閲覧専用です。現在の資料では使えず、顧客へ送信もしていません。'
        : reviewed
          ? preview?.review?.factCheckStatus === 'protected_facts_unchanged'
            ? '合成受注担当者役の確認文を保存済み（自己申告）。固定した数量・到着予定・費用は元文と一致し、確認文は文例から選択しました。実資料は未照合で、顧客へ未送信です。'
            : '合成受注担当者役の旧修正案を保存済み（自己申告）。内容は資料と未照合で、顧客へ未送信です。下で元文と修正文を確認できます。'
        : '合成顧客向けの確認用文案です。未送信で、担当役の確認が必要です。'}
    </Alert>
    {historical && preview?.historicalReason && <Typography variant="body2" sx={{ mb: 1 }}>
      旧runの理由：{preview.historicalReason}
    </Typography>}
    {!historical && preview?.status === 'not_available' && preview.reason &&
      <Typography variant="body2" sx={{ mb: 1 }}>現在使えない理由：{preview.reason}</Typography>}
    <Typography variant="body2" sx={{ fontWeight: 700 }}>
      出所：業務APIの保存案。新しいモデル推論はしていません。到着予定は保存資料を読んだ時点の値です。
    </Typography>
    {!historical && <Typography variant="body2" sx={{ mt: 0.5 }}>
      合成顧客の回答：{customerResponseSaved ? '保存済み' : '担当役の確認待ち'}／出荷承認：{approvalSaved ? '保存済み' : customerResponseSaved ? '担当役の判断待ち' : '顧客回答後の判断待ち'}。
    </Typography>}
    <Typography variant="body2" sx={{ mt: 0.5 }}>
      契約適合・特急費用の負担先・実際の集荷と出荷日時は未確認です。表示だけでは顧客回答・出荷承認・出荷指示は保存されません。
    </Typography>
    {canShowText && !reviewed
      ? <Box className="customer-preview-copy" data-testid="customer-preview-text">
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{preview.text}</Typography>
        </Box>
      : reviewed
        ? <Typography variant="body2" sx={{ mt: 1 }} data-testid="customer-preview-reviewed">
            元文と修正文は下の保存記録に並べています。どちらも送信文として確定していません。
          </Typography>
      : <Alert severity="info" sx={{ mt: 2 }} data-testid="customer-preview-unavailable">
          {historicalReview
            ? '現行の顧客案内には使えません。下は保存時点の閲覧専用記録です。'
            : `文案を表示できません。${preview?.reason || '保存案と資料の照合状態を確認してください。'}`}
        </Alert>}
    {canShowText && runId && onReviewSave && onReviewReadback &&
      <CustomerReviewPanel key={runId} runId={runId} originalText={preview.text!}
        questionChoices={preview.reviewQuestionChoices || []}
        saved={preview.review} canEdit={Boolean(canReview && preview.reviewBasisSha256 &&
          preview.reviewScope === 'two_option_customer_preview')}
        localReadOnly={localReadOnly}
        onSave={onReviewSave} onReadback={onReviewReadback} />}
    {historicalReview && <Paper variant="outlined" className="customer-review" data-testid="customer-review-historical">
      <Typography component="h3" className="activity-title">
        {historical ? '旧runに保存した未送信文案' : '保存時の未送信文案（現在は使用不可）'}
      </Typography>
      <Alert severity="warning" sx={{ mt: 1 }} role="status">
        この記録は閲覧専用です。現在の受注・資料に対する文案として編集・送信できません。
        {historicalReview.factCheckStatus === 'protected_facts_unchanged'
          ? '保存時に固定4行と元文の一致、確認文の文例一致を確認しました。実資料は未照合です。'
          : '保存時の内容は資料と未照合です。'}
      </Alert>
      <Box className="customer-review-comparison" data-testid="customer-review-historical-comparison">
        <Box><Typography variant="subtitle2">当時の元文</Typography>
          <Typography variant="body2" className="customer-review-copy">{historicalReview.originalText}</Typography></Box>
        <Box><Typography variant="subtitle2">当時の修正文（未送信）</Typography>
          <Typography variant="body2" className="customer-review-copy">{historicalReview.editedText}</Typography></Box>
      </Box>
      <Typography variant="body2" sx={{ mt: 1 }}>当時の修正理由：{historicalReview.reason}</Typography>
      <Typography variant="caption" color="text.secondary">保存記録：{historicalReview.id}／{historicalReview.recordedAt}</Typography>
    </Paper>}
    {preview?.limitations?.length ? <details className="source-details">
      <summary>文案で確認が必要な範囲</summary>
      <Box component="ul" sx={{ m: '8px 0 0', pl: 2.5 }}>
        {preview.limitations.map((item, index) => <Box component="li" key={index}>
          <Typography variant="body2">{item}</Typography>
        </Box>)}
      </Box>
    </details> : null}
  </Paper>;
}
