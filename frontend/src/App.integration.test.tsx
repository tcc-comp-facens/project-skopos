/**
 * Integration test — modelo de rodadas do App.
 *
 * Garante a propriedade central do plano de reformulação: uma segunda
 * rodada de chat não deve sobrescrever o snapshot da primeira em
 * `rounds` (cada analysisId congela seu próprio estado assim que a
 * próxima rodada começa).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from './App';

const { EMPTY_WS_STATE, mockWsStates } = vi.hoisted(() => {
  const EMPTY_WS_STATE = {
    starText: '',
    hierText: '',
    starBenchmarks: null,
    hierBenchmarks: null,
    starLoading: false,
    hierLoading: false,
    starError: null,
    hierError: null,
    comparativeReport: '',
    comparativeLoading: false,
    qualityMetrics: null,
    ragasText: '',
    ragasLoading: false,
    starAgentData: null,
    hierAgentData: null,
  };
  return { EMPTY_WS_STATE, mockWsStates: {} as Record<string, typeof EMPTY_WS_STATE> };
});

vi.mock('./hooks/useWebSocket', () => ({
  INITIAL_STATE: EMPTY_WS_STATE,
  useWebSocket: (analysisId: string | null) =>
    analysisId ? mockWsStates[analysisId] ?? EMPTY_WS_STATE : EMPTY_WS_STATE,
}));

vi.mock('./components/ChatInterface', () => ({
  ChatInterface: (props: {
    onAnalysisStarted: (analysisId: string, question: string) => void;
  }) => (
    <div>
      <button onClick={() => props.onAnalysisStarted('analysis-1', 'pergunta 1')}>
        start-round-1
      </button>
      <button onClick={() => props.onAnalysisStarted('analysis-2', 'pergunta 2')}>
        start-round-2
      </button>
    </div>
  ),
}));

describe('App — modelo de rodadas', () => {
  it('a segunda rodada não sobrescreve o snapshot da primeira', () => {
    mockWsStates['analysis-1'] = { ...EMPTY_WS_STATE, starText: 'resultado da rodada 1' };

    render(<App />);

    fireEvent.click(screen.getByText('start-round-1'));

    mockWsStates['analysis-2'] = { ...EMPTY_WS_STATE, starText: 'resultado da rodada 2' };
    fireEvent.click(screen.getByText('start-round-2'));

    // Vai para a aba Agentes conferir o panorama de rodadas
    fireEvent.click(screen.getByTestId('tab-tech'));

    // Rodada 1 ficou expandida desde quando era a mais nova, e continua
    // assim mesmo depois que a rodada 2 começa (estado local não é
    // resetado por uma rodada nova chegando) — o snapshot dela segue
    // intacto, ambas visíveis ao mesmo tempo (panorama, não seleção única).
    expect(screen.getByText('resultado da rodada 1')).toBeInTheDocument();
    expect(screen.getByText('resultado da rodada 2')).toBeInTheDocument();
  });
});
