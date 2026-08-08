import { useState, useCallback, useEffect, useRef } from 'react';
import { Header } from './components/Header';
import { TabNav } from './components/TabNav';
import { UserTab } from './components/UserTab';
import { TechTab } from './components/TechTab';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useWebSocket, INITIAL_STATE } from './hooks/useWebSocket';
import type { ActiveTab, ChatRound } from './types';

export function App() {
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [useLlmJudge, setUseLlmJudge] = useState(false);
  const [activeTab, setActiveTab] = useState<ActiveTab>('user');
  const [rounds, setRounds] = useState<ChatRound[]>([]);

  const ws = useWebSocket(analysisId);

  // O chat (ChatInterface, dentro de UserTab) dispara a análise via seu
  // próprio WebSocket de intenção e avisa aqui quando ela começa. Cada
  // pergunta vira uma "rodada" — o streaming de resultados continua vindo
  // exclusivamente do /ws/{analysisId} existente (useWebSocket acima).
  const handleAnalysisStarted = useCallback(
    (newAnalysisId: string, question: string) => {
      setRounds((prev) => [
        ...prev,
        {
          id: newAnalysisId,
          question,
          startedAt: new Date().toISOString(),
          snapshot: INITIAL_STATE,
        },
      ]);
      setAnalysisId(newAnalysisId);
    },
    [],
  );

  // Espelha continuamente o estado ao vivo do useWebSocket na rodada
  // ativa correspondente. Quando a próxima rodada começa, analysisId muda,
  // useWebSocket reseta sozinho, e o último snapshot espelhado aqui fica
  // congelado no array `rounds` — única fonte de verdade para rodadas
  // passadas (a fila do backend é de consumidor único e é descartada ao
  // terminar, então uma rodada antiga não pode ser "re-conectada").
  const analysisIdRef = useRef(analysisId);
  analysisIdRef.current = analysisId;
  useEffect(() => {
    const currentId = analysisIdRef.current;
    if (!currentId) return;
    setRounds((prev) =>
      prev.map((r) => (r.id === currentId ? { ...r, snapshot: ws } : r)),
    );
  }, [ws]);

  const isActiveRoundRunning =
    !!analysisId && (ws.starLoading || ws.hierLoading || ws.comparativeLoading);

  return (
    <div className="app" data-testid="app">
      <Header />

      <TabNav activeTab={activeTab} onTabChange={setActiveTab} />

      <div style={{ display: activeTab === 'user' ? 'block' : 'none' }}>
        <UserTab
          useLlmJudge={useLlmJudge}
          onAnalysisStarted={handleAnalysisStarted}
          activeRoundId={analysisId}
          activeRoundWs={analysisId ? ws : null}
        />
      </div>

      <div style={{ display: activeTab === 'tech' ? 'block' : 'none' }}>
        <ErrorBoundary>
          <TechTab
            useLlmJudge={useLlmJudge}
            onUseLlmJudgeChange={setUseLlmJudge}
            rounds={rounds}
            isActiveRoundRunning={isActiveRoundRunning}
          />
        </ErrorBoundary>
      </div>
    </div>
  );
}

export default App;
