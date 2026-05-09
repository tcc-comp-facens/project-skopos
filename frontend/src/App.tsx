import { useState, useCallback, useMemo } from 'react';
import { Header } from './components/Header';
import { TabNav } from './components/TabNav';
import { UserTab } from './components/UserTab';
import { TechTab } from './components/TechTab';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useWebSocket } from './hooks/useWebSocket';
import { parseWinner } from './utils/parseWinner';
import { API_URL } from './config';
import type { AnalysisRequest, ActiveTab } from './types';

export function App() {
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [useLlm, setUseLlm] = useState(false);
  const [useLlmJudge, setUseLlmJudge] = useState(false);
  const [activeTab, setActiveTab] = useState<ActiveTab>('user');

  const ws = useWebSocket(analysisId);

  // Derived state — winner identified from comparative report
  const winner = useMemo(
    () => parseWinner(ws.comparativeReport),
    [ws.comparativeReport],
  );

  const handleSubmit = useCallback(async (request: Omit<AnalysisRequest, 'useLlm' | 'useLlmJudge'>) => {
    setApiError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/analysis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...request, useLlm, useLlmJudge }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data: { analysisId: string } = await res.json();
      setAnalysisId(data.analysisId);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Erro ao iniciar análise');
    } finally {
      setSubmitting(false);
    }
  }, [useLlm, useLlmJudge]);

  return (
    <div className="app" data-testid="app">
      <Header />

      <TabNav activeTab={activeTab} onTabChange={setActiveTab} />

      <div style={{ display: activeTab === 'user' ? 'block' : 'none' }}>
        <UserTab
          apiError={apiError}
          submitting={submitting}
          starText={ws.starText}
          hierText={ws.hierText}
          starLoading={ws.starLoading}
          hierLoading={ws.hierLoading}
          starError={ws.starError}
          hierError={ws.hierError}
          winner={winner}
          onSubmit={handleSubmit}
        />
      </div>

      <div style={{ display: activeTab === 'tech' ? 'block' : 'none' }}>
        <ErrorBoundary>
          <TechTab
            useLlm={useLlm}
            useLlmJudge={useLlmJudge}
            onUseLlmChange={setUseLlm}
            onUseLlmJudgeChange={setUseLlmJudge}
            ws={ws}
            submitting={submitting}
            apiError={apiError}
          />
        </ErrorBoundary>
      </div>
    </div>
  );
}

export default App;
