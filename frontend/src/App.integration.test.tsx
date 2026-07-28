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
    llmJudgeText: '',
    llmJudgeLoading: false,
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

    // Vai para a aba Agentes conferir o histórico de rodadas
    fireEvent.click(screen.getByTestId('tab-tech'));

    // Rodada mais nova (2) selecionada automaticamente
    expect(screen.getByText('resultado da rodada 2')).toBeInTheDocument();

    // Volta para a rodada 1 — o snapshot dela deve seguir intacto
    fireEvent.change(screen.getByTestId('round-selector-select'), {
      target: { value: 'analysis-1' },
    });
    expect(screen.getByText('resultado da rodada 1')).toBeInTheDocument();
  });
});
