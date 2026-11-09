import { Alert, Box, Button, Typography } from '@mui/material';

export type ReplyAssessment = {
  status: 'contradiction_found' | 'no_contradiction_found' | 'not_assessed';
  checkedAgainst: string;
  claims: {
    id: string;
    label: string;
    verdict: 'contradicted' | 'consistent' | 'not_assessed';
    modelQuote?: string;
    savedValue?: string;
    reason: string;
  }[];
  limitations: string[];
};

function resultText(assessment?: ReplyAssessment): string {
  if (!assessment) return '返答の照合結果を取得できません。モデル原文を確認済みとは扱えません。';
  if (assessment.status === 'contradiction_found') {
    if (assessment.claims.some(item => item.id === 'save_state' && item.verdict === 'contradicted')) {
      return 'モデル返答は案を未保存・保存予定と説明していますが、この保存runには案が保存済みです。文章生成時点の状態は未判定です。';
    }
    return `モデル返答に業務APIの保存値と食い違う記述があります（${assessment.claims.filter(item => item.verdict === 'contradicted').length}項目）。`;
  }
  if (assessment.status === 'no_contradiction_found') {
    return '確認対象の記述では保存値との食い違いを検出していません。返答全体の正確性は未判定です。';
  }
  return 'モデル返答を照合できませんでした。保存案の値を確認してください。';
}

export function ReplyAssessmentFirstfold({ assessment }: { assessment?: ReplyAssessment }) {
  return <Box className="reply-assessment-firstfold" data-testid="reply-assessment-firstfold">
    <Typography variant="body2" sx={{ fontWeight: 700 }}>モデル返答の業務値照合</Typography>
    <Typography variant="body2">{resultText(assessment)}</Typography>
    <Button size="small" variant="text" sx={{ mt: 0.5 }}
      onClick={() => {
        const target = document.querySelector<HTMLElement>('[data-testid="reply-assessment-details"]');
        target?.scrollIntoView({ behavior: 'auto' });
        target?.focus({ preventScroll: true });
      }}>
      照合した記述と保存値を見る
    </Button>
  </Box>;
}

const verdictText: Record<ReplyAssessment['claims'][number]['verdict'], string> = {
  contradicted: '保存値と食い違い',
  consistent: '保存値と一致（この項目のみ）',
  not_assessed: '照合できず',
};

export function ReplyAssessmentDetails({ assessment }: { assessment?: ReplyAssessment }) {
  return <Box className="reply-assessment-details" data-testid="reply-assessment-details" tabIndex={-1}>
    <Typography component="h4" className="activity-title">モデル返答と業務API保存値の照合</Typography>
    <Alert severity={assessment?.status === 'contradiction_found' || !assessment || assessment.status === 'not_assessed' ? 'warning' : 'info'}>
      {resultText(assessment)}
    </Alert>
    {assessment && <>
      <Typography variant="body2" sx={{ mt: 1 }}>照合先：{assessment.checkedAgainst}</Typography>
      {assessment.claims.length > 0 && <Box component="ul" className="reply-assessment-claims">
        {assessment.claims.map(claim => <Box component="li" key={claim.id} className="reply-assessment-claim">
          <Typography variant="body2" sx={{ fontWeight: 700 }}>{claim.label}：{verdictText[claim.verdict]}</Typography>
          {claim.modelQuote && <Typography variant="body2">モデルの記述：「{claim.modelQuote}」</Typography>}
          {claim.savedValue && <Typography variant="body2">業務APIの保存値：{claim.savedValue}</Typography>}
          <Typography variant="body2" color="text.secondary">{claim.reason}</Typography>
        </Box>)}
      </Box>}
      {assessment.limitations.map((limit, index) => <Typography key={index} variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
        未判定：{limit}
      </Typography>)}
    </>}
  </Box>;
}
