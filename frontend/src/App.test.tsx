import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as fc from 'fast-check';
import { App } from './App';

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

const mockWsState = {
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

vi.mock('./hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(() => mockWsState),
}));

import { useWebSocket } from './hooks/useWebSocket';
const mockUseWebSocket = vi.mocked(useWebSocket);

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function submitAnalysis() {
  fireEvent.click(screen.getByTestId('submit-button'));
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockUseWebSocket.mockReturnValue({ ...mockWsState });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders header and tab navigation', () => {
    render(<App />);
    expect(screen.getByTestId('sophia-header')).toBeInTheDocument();
    expect(screen.getByTestId('tab-nav')).toBeInTheDocument();
  });

  it('starts with user tab active', () => {
    render(<App />);
    expect(screen.getByTestId('tab-user')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('user-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('tech-tab')).not.toBeInTheDocument();
  });

  it('renders controls in user tab by default', () => {
    render(<App />);
    expect(screen.getByTestId('analysis-controls')).toBeInTheDocument();
  });

  it('switches to tech tab when clicked', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByTestId('tab-tech'));

    expect(screen.getByTestId('tech-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('user-tab')).not.toBeInTheDocument();
    expect(screen.getByTestId('llm-controls')).toBeInTheDocument();
  });

  it('switches back to user tab', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByTestId('tab-tech'));
    await user.click(screen.getByTestId('tab-user'));

    expect(screen.getByTestId('user-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('tech-tab')).not.toBeInTheDocument();
  });

  it('calls useWebSocket with null initially', () => {
    render(<App />);
    expect(mockUseWebSocket).toHaveBeenCalledWith(null);
  });

  it('calls POST /api/analysis on submit and sets analysisId', async () => {
    const fakeId = 'test-analysis-123';
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ analysisId: fakeId }),
    });

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(mockUseWebSocket).toHaveBeenCalledWith(fakeId);
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/analysis'),
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });

  it('shows API error when POST fails', async () => {
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    });

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(screen.getByTestId('api-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('api-error').textContent).toBe('Internal Server Error');
  });

  it('shows API error on network failure', async () => {
    globalThis.fetch = vi.fn().mockRejectedValueOnce(new Error('Network error'));

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(screen.getByTestId('api-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('api-error').textContent).toBe('Network error');
  });

  it('shows submitting indicator while POST is in flight', async () => {
    let resolveFetch!: (value: unknown) => void;
    globalThis.fetch = vi.fn().mockReturnValueOnce(
      new Promise((resolve) => { resolveFetch = resolve; }),
    );

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(screen.getByTestId('submitting-indicator')).toBeInTheDocument();
    });

    resolveFetch({ ok: true, json: async () => ({ analysisId: 'abc' }) });

    await waitFor(() => {
      expect(screen.queryByTestId('submitting-indicator')).not.toBeInTheDocument();
    });
  });

  it('shows WinnerPanel in user tab when comparative report has verdict', () => {
    mockUseWebSocket.mockReturnValue({
      ...mockWsState,
      starText: 'Análise da Estrela',
      comparativeReport: '→ Arquitetura Estrela venceu com 5 pontos',
      starLoading: false,
      hierLoading: false,
    });

    render(<App />);
    expect(screen.getByTestId('winner-panel')).toBeInTheDocument();
  });

  it('shows loading state in user tab when analysis is running', () => {
    mockUseWebSocket.mockReturnValue({
      ...mockWsState,
      starLoading: true,
      hierLoading: true,
    });

    render(<App />);
    expect(screen.getByTestId('user-tab-loading')).toBeInTheDocument();
  });

  // Feature: frontend-redesign, Property 6
  // Propriedade 6: Troca de aba não altera o estado da análise em andamento
  it('Property 6: switching tabs does not affect analysis state', async () => {
    const analysisState = {
      ...mockWsState,
      starText: 'Texto parcial da Estrela',
      hierText: 'Texto parcial da Hierárquica',
      starLoading: true,
      hierLoading: true,
      comparativeReport: '',
    };

    fc.assert(
      fc.property(
        fc.constantFrom('user' as const, 'tech' as const),
        (targetTab) => {
          mockUseWebSocket.mockReturnValue({ ...analysisState });
          const { unmount } = render(<App />);

          // Switch to target tab
          const tabBtn = document.querySelector(
            `[data-testid="tab-${targetTab}"]`,
          ) as HTMLButtonElement;
          if (tabBtn) fireEvent.click(tabBtn);

          // Verify useWebSocket was called with the same analysisId (null in this case)
          // The key invariant: switching tabs doesn't change the analysisId passed to useWebSocket
          const calls = mockUseWebSocket.mock.calls;
          const allCallsWithNull = calls.every((call) => call[0] === null);

          unmount();
          mockUseWebSocket.mockReturnValue({ ...mockWsState });
          return allCallsWithNull;
        },
      ),
      { numRuns: 10 },
    );
  });

  // Feature: frontend-redesign, Property 7
  // Propriedade 7: Acumulação correta de chunks de texto
  it('Property 7: text accumulation is correct for any sequence of chunks', () => {
    fc.assert(
      fc.property(
        fc.array(fc.string({ maxLength: 80 }), { minLength: 1, maxLength: 10 }),
        (chunks) => {
          const accumulated = chunks.join('');
          mockUseWebSocket.mockReturnValue({
            ...mockWsState,
            starText: accumulated,
          });

          const { unmount } = render(<App />);

          // Switch to tech tab to see the panels
          const techTab = document.querySelector('[data-testid="tab-tech"]') as HTMLButtonElement;
          if (techTab) fireEvent.click(techTab);

          const textBoxes = document.querySelectorAll('[data-testid="panel-text-box"]');
          const hasAccumulatedText = Array.from(textBoxes).some(
            (box) => box.textContent?.includes(accumulated),
          );

          unmount();
          mockUseWebSocket.mockReturnValue({ ...mockWsState });
          return hasAccumulatedText || accumulated === '';
        },
      ),
      { numRuns: 50 },
    );
  });

  // Feature: frontend-redesign, Property 8
  // Propriedade 8: Reset completo do estado quando analysisId muda
  it('Property 8: useWebSocket is called with new analysisId after submit', async () => {
    const fakeId = 'test-prop8-id';
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ analysisId: fakeId }),
    });

    render(<App />);
    fireEvent.click(screen.getAllByTestId('submit-button')[0]);

    await waitFor(() => {
      const calls = mockUseWebSocket.mock.calls;
      return calls.some((call) => call[0] === fakeId);
    });

    // Verify that after a new analysisId, useWebSocket was called with it
    expect(mockUseWebSocket).toHaveBeenCalledWith(fakeId);
  });
});
