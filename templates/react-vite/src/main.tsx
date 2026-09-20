import { useState } from 'react';
import { createRoot } from 'react-dom/client';
function App() { const [runs, setRuns] = useState(0); return <main><h1>Your experiment</h1><button onClick={() => setRuns(runs + 1)}>Run {runs}</button></main>; }
createRoot(document.getElementById('root')!).render(<App />);
