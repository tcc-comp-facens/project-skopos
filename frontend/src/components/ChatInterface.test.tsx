/**
 * Tests for ChatInterface component.
 * Validates welcome message, blank-message guard, and input disabled
 * while awaiting a response (re-enabled on system_done).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ChatInterface } from './ChatInterface';
import { useChatWebSocket } from '../hooks/useChatWebSocket';
import { INITIAL_STATE } from '../hooks/useWebSocket';
import type { UseChatWebSocketCallbacks } from '../hooks/useChatWebSocket';

vi.mock('../hooks/useChatWebSocket');

describe('ChatInterface', () => {
  let capturedCallbacks: UseChatWebSocketCallbacks | null = null;
  const sendMessage = vi.fn();

  const defaultProps = {
    useLlmJudge: false,
    onAnalysisStarted: vi.fn(),
    activeRoundId: null,
    activeRoundWs: null,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;
    vi.mocked(useChatWebSocket).mockImplementation((callbacks) => {
      capturedCallbacks = callbacks;
      return { connectionStatus: 'connected', hasEverConnected: true, sendMessage };
    });
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false }) as unknown as typeof fetch;
  });

  it('shows a welcome message on mount', () => {
    render(<ChatInterface {...defaultProps} />);
    expect(screen.getByTestId('message-bubble-system')).toBeInTheDocument();
  });

  it('does not send blank messages', () => {
    render(<ChatInterface {...defaultProps} />);
    expect(screen.getByTestId('chat-send-button')).toBeDisabled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it('disables input while awaiting a response, re-enables after system_done', async () => {
    render(<ChatInterface {...defaultProps} />);
    const input = screen.getByTestId('chat-input');
    const sendBtn = screen.getByTestId('chat-send-button');

    fireEvent.change(input, { target: { value: 'compare dengue de 2019 a 2022' } });
    fireEvent.click(sendBtn);

    expect(input).toBeDisabled();
    expect(sendMessage).toHaveBeenCalledWith('compare dengue de 2019 a 2022', false);

    act(() => capturedCallbacks?.onDone());

    await waitFor(() => expect(input).not.toBeDisabled());
  });

  it('shows an error bubble when the server sends an error event', async () => {
    render(<ChatInterface {...defaultProps} />);
    const input = screen.getByTestId('chat-input');
    fireEvent.change(input, { target: { value: 'oi' } });
    fireEvent.click(screen.getByTestId('chat-send-button'));

    act(() => capturedCallbacks?.onError('Aguarde a resposta da pergunta anterior.'));

    await waitFor(() => {
      const alerts = screen.getAllByRole('alert');
      expect(alerts.some((el) => el.textContent?.includes('Aguarde a resposta'))).toBe(true);
    });
  });

  it('shows a placeholder while the round is loading, then reveals the winning architecture text progressively (never the loser, never before the backend resolves it)', () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <ChatInterface
          {...defaultProps}
          activeRoundId="analysis-1"
          activeRoundWs={{ ...INITIAL_STATE, starLoading: true, hierLoading: true }}
        />,
      );

      expect(screen.getByText('Analisando os dados…')).toBeInTheDocument();

      rerender(
        <ChatInterface
          {...defaultProps}
          activeRoundId="analysis-1"
          activeRoundWs={{
            ...INITIAL_STATE,
            starText: 'Resultado completo da arquitetura estrela.',
            hierText: 'Resultado completo da arquitetura hierárquica.',
            comparativeReport: '→ Arquitetura Estrela venceu com 5 pontos',
          }}
        />,
      );

      // Nada do texto completo aparece de uma vez — a revelação começa
      // vazia e a bolha é marcada com o badge do vencedor imediatamente.
      expect(screen.queryByText('Analisando os dados…')).not.toBeInTheDocument();
      expect(screen.queryByText('Resultado completo da arquitetura estrela.')).not.toBeInTheDocument();
      expect(screen.getByTestId('message-bubble-winner-badge')).toHaveAttribute(
        'aria-label',
        'Arquitetura Estrela',
      );

      // Depois de um "tick" da revelação, só uma fatia parcial do texto do
      // VENCEDOR está visível (nunca o texto da arquitetura perdedora).
      act(() => {
        vi.advanceTimersByTime(20);
      });
      expect(screen.getByText('Result')).toBeInTheDocument();
      expect(screen.queryByText('Resultado completo da arquitetura hierárquica.')).not.toBeInTheDocument();

      // Ao final da revelação, o texto completo do vencedor está visível.
      act(() => {
        vi.runAllTimers();
      });
      expect(screen.getByText('Resultado completo da arquitetura estrela.')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows a fallback message instead of the placeholder when both architectures fail', () => {
    const { rerender } = render(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-2"
        activeRoundWs={{ ...INITIAL_STATE, starLoading: true, hierLoading: true }}
      />,
    );

    rerender(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-2"
        activeRoundWs={{ ...INITIAL_STATE, starError: 'falhou', hierError: 'falhou' }}
      />,
    );

    expect(screen.queryByText('Analisando os dados…')).not.toBeInTheDocument();
    expect(screen.getByText(/Não consegui concluir a análise/)).toBeInTheDocument();
  });

  it('never produces more than one answer bubble for the same round across re-renders', () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <ChatInterface
          {...defaultProps}
          activeRoundId="analysis-3"
          activeRoundWs={{ ...INITIAL_STATE, starLoading: true }}
        />,
      );

      rerender(
        <ChatInterface
          {...defaultProps}
          activeRoundId="analysis-3"
          activeRoundWs={{ ...INITIAL_STATE, starLoading: true, hierLoading: true }}
        />,
      );
      rerender(
        <ChatInterface
          {...defaultProps}
          activeRoundId="analysis-3"
          activeRoundWs={{
            ...INITIAL_STATE,
            hierText: 'Resultado completo da arquitetura hierárquica.',
            comparativeReport: '→ Arquitetura Hierárquica venceu com 4 pontos',
          }}
        />,
      );

      act(() => {
        vi.runAllTimers();
      });

      expect(screen.getAllByText('Resultado completo da arquitetura hierárquica.')).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
