import type { ReactNode } from 'react';
import { ThemeProvider, createTheme, CssBaseline, Box, Stack, Typography, Chip, Paper, Button, SvgIcon } from '@mui/material';
import './style.css';

export const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: '#303873', dark: '#1c234a', light: '#6570b3', contrastText: '#ffffff' },
    secondary: { main: '#e9ebf2', dark: '#c8cedc', contrastText: '#424b65' },
    background: { default: '#f6f7fa', paper: '#ffffff' },
    text: { primary: '#202641', secondary: '#606b80' },
    divider: '#e5e8f0', success: { main: '#207669' }, warning: { main: '#875711' }, error: { main: '#a53e52' },
  },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily: 'Inter, "Segoe UI", "Noto Sans JP", sans-serif',
    h1: { fontSize: '2rem', fontWeight: 700, letterSpacing: '-.035em', lineHeight: 1.3 },
    h2: { fontSize: '1.08rem', fontWeight: 650, letterSpacing: '-.02em' },
    h3: { fontSize: '1rem', fontWeight: 650 },
    body1: { fontSize: '.9rem', lineHeight: 1.8 },
    body2: { fontSize: '.8rem', lineHeight: 1.75 },
    button: { textTransform: 'none', fontWeight: 600 },
    overline: { fontSize: '.64rem', fontWeight: 700, letterSpacing: '.15em' },
  },
  components: {
    MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { backgroundImage: 'none' } } },
    MuiButton: {
      defaultProps: { disableElevation: true, size: 'small' },
      styleOverrides: { root: {
        borderRadius: 6, minHeight: 30, padding: '4px 10px', fontSize: '.75rem', lineHeight: 1.5, whiteSpace: 'nowrap', flexShrink: 0,
        '&.MuiButton-outlined': { borderColor: '#cbd1de', backgroundColor: '#ffffff', '&:hover': { borderColor: '#8c97b2', backgroundColor: '#f0f2f8' } },
        '&.Mui-disabled': { color: '#717b8e', backgroundColor: '#edf0f5', borderColor: '#d8dde7' },
        '&.Mui-focusVisible': { outline: '2px solid #6570b3', outlineOffset: 3 },
      } },
    },
    MuiTextField: { defaultProps: { size: 'small', variant: 'outlined' } },
    MuiOutlinedInput: { styleOverrides: { root: { background: '#fff', fontSize: '.83rem', borderRadius: 8 } } },
    MuiChip: { defaultProps: { size: 'small' }, styleOverrides: { root: { borderRadius: 6, fontSize: '.68rem', fontWeight: 600 }, colorDefault: { background: '#eef0f6', color: '#566279' } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 10, fontSize: '.8rem' } } },
    MuiTooltip: { defaultProps: { arrow: true } },
  },
});

const paths: Record<string, string> = {
  lab: 'M9 3h6M10 3v6l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M7 15h10',
  graph: 'M8 6h8M6 8v8m2 2h8M18 8v8M8 8l8 8M8 16l8-8M4 4h4v4H4zm12 0h4v4h-4zM4 16h4v4H4zm12 0h4v4h-4z',
  user: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2',
  team: 'M14 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM3 20v-2a7 7 0 0 1 14 0v2M17 4a3 3 0 0 1 0 6m2 4a6 6 0 0 1 3 5',
  folder: 'M3 6V4h6l2 3h10v13H3V6Z',
  document: 'M5 3h9l5 5v13H5V3Zm9 0v6h5M9 13h6m-6 4h6',
  code: 'm8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18',
  manager: 'M12 2v4M8 2h8M4 8h16v12H4V8Zm4 5h1m6 0h1m-7 4h6M1 12h3m16 0h3',
  check: 'm4 12 5 5L20 6',
  search: 'M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Zm-2 5 6 6',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  play: 'm8 4 12 8-12 8V4Z',
};
export function LabIcon({ kind = 'lab', size = 22 }: { kind?: string; size?: number }) {
  return <SvgIcon sx={{ fontSize: size }}><path d={paths[kind] || paths.lab} fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" /></SvgIcon>;
}
export function LabTheme({ children }: { children: ReactNode }) { return <ThemeProvider theme={theme}><CssBaseline />{children}</ThemeProvider>; }
export function Eyebrow({ children }: { children: ReactNode }) { return <Typography variant="overline" color="text.secondary">{children}</Typography>; }
export function Panel({ children, title, subtitle, action, className = '' }: { children: ReactNode; title?: string; subtitle?: string; action?: ReactNode; className?: string }) {
  return <Paper variant="outlined" className={`lab-panel ${className}`}>
    {title && <Stack direction="row"    sx={{ mb: 2 , alignItems: "center", justifyContent: "space-between", gap: 2 }}><Box><Typography variant="h2">{title}</Typography>{subtitle && <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>{subtitle}</Typography>}</Box>{action}</Stack>}{children}
  </Paper>;
}
export function Metric({ label, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return <Paper variant="outlined" sx={{ p: 2.25, minWidth: 0 }}><Typography variant="body2" color="text.secondary">{label}</Typography><Typography sx={{ fontSize: '1.55rem', fontWeight: 700, mt: .5, letterSpacing: '-.04em', overflowWrap: 'anywhere' }}>{value}</Typography>{detail && <Typography variant="caption" color="text.secondary">{detail}</Typography>}</Paper>;
}
export function LabShell({ children, title, subtitle, number, actions }: { children: ReactNode; title: string; subtitle: string; number?: string; actions?: ReactNode }) {
  return <><Box component="header" className="lab-header"><a className="lab-brand" href="http://127.0.0.1:5173"><span className="lab-logo"><LabIcon size={22} /></span>Tech Playground</a><Stack direction="row"   sx={{ alignItems: "center", gap: 2 }}><Typography variant="caption" color="text.secondary" className="desktop-only">PERSONAL EXPERIMENT LAB</Typography><Chip label="v0.1" variant="outlined" />{number && <Button href="http://127.0.0.1:5173" size="small">← All experiments</Button>}</Stack></Box>
    <Box component="main" className="lab-main"><Stack className="lab-heading" direction={{ xs: 'column', md: 'row' }}    sx={{ justifyContent: "space-between", alignItems: { xs: 'flex-start', md: 'center' }, gap: 2 }}><Box><Eyebrow>{number ? `EXPERIMENT ${number} / LEARN BY RUNNING` : 'YOUR SPACE TO EXPLORE'}</Eyebrow><Typography variant="h1" sx={{ mt: 1, mb: 1.5 }}>{title}</Typography><Typography color="text.secondary">{subtitle}</Typography></Box>{actions}</Stack>{children}</Box>
    <Box component="footer" className="lab-footer"><span>Tech Playground / Personal lab</span><span>Small experiments. Clearer understanding.</span></Box></>;
}
