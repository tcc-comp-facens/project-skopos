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

  it('streams the first architecture to produce text live, chunk by chunk, in place', () => {
    // Regressão do redesenho pra streaming real: nada de animação
    // simulada no cliente nem espera pelas duas arquiteturas — a bolha
    // reflete o próprio starText/hierText do WebSocket crescendo.
    const { rerender } = render(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-1"
        activeRoundWs={{ ...INITIAL_STATE, starLoading: true, hierLoading: true }}
      />,
    );

    expect(screen.getByText('Analisando os dados…')).toBeInTheDocument();

    // Estrela começa a produzir chunks — a bolha já mostra o texto
    // parcial, ainda com o indicador de "streaming" ligado.
    rerender(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-1"
        activeRoundWs={{
          ...INITIAL_STATE,
          starText: 'Resultado parcial',
          starLoading: true,
          hierLoading: true,
        }}
      />,
    );
    expect(screen.queryByText('Analisando os dados…')).not.toBeInTheDocument();
    expect(screen.getByText('Resultado parcial')).toBeInTheDocument();
    expect(screen.queryByTestId('message-bubble-winner-badge')).not.toBeInTheDocument();

    // Mais chunks chegam — o texto cresce em cima da mesma bolha (nunca
    // duplica, nunca troca pra hierárquica mesmo que ela comece a
    // produzir texto depois).
    rerender(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-1"
        activeRoundWs={{
          ...INITIAL_STATE,
          starText: 'Resultado parcial e agora mais completo',
          starLoading: true,
          hierText: 'Texto da hierárquica, que não deve aparecer',
          hierLoading: true,
        }}
      />,
    );
    expect(screen.getByText('Resultado parcial e agora mais completo')).toBeInTheDocument();
    expect(screen.queryByText('Texto da hierárquica, que não deve aparecer')).not.toBeInTheDocument();

    // Estrela termina, com o comparativo já apontando ela como vencedora
    // — finaliza com o badge, sem esperar a hierárquica.
    rerender(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-1"
        activeRoundWs={{
          ...INITIAL_STATE,
          starText: 'Resultado completo da arquitetura estrela.',
          starLoading: false,
          hierText: 'Texto da hierárquica, que não deve aparecer',
          hierLoading: true,
          comparativeReport: '→ Arquitetura Estrela venceu com 5 pontos',
        }}
      />,
    );
    expect(screen.getByText('Resultado completo da arquitetura estrela.')).toBeInTheDocument();
    expect(screen.getByTestId('message-bubble-winner-badge')).toHaveAttribute(
      'aria-label',
      'Arquitetura Estrela',
    );
  });

  it('finalizes with the followed architecture text even when the comparative report is not ready yet (no winner badge, but no fallback either)', () => {
    // Caso comum: a estrela termina bem antes da hierárquica, então o
    // relatório comparativo (que só existe depois que as DUAS terminam)
    // ainda não chegou quando a rodada finaliza — a resposta não deve
    // ficar esperando por ele.
    const { rerender } = render(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-4"
        activeRoundWs={{ ...INITIAL_STATE, starLoading: true, hierLoading: true }}
      />,
    );

    rerender(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-4"
        activeRoundWs={{
          ...INITIAL_STATE,
          starText: 'Resposta válida da arquitetura estrela.',
          starLoading: false,
          hierLoading: true,
          comparativeReport: '', // ainda não computado
        }}
      />,
    );

    expect(
      screen.queryByText('Análise concluída, mas não foi possível determinar um resultado completo. Veja os detalhes técnicos na aba Agentes.'),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('message-bubble-winner-badge')).not.toBeInTheDocument();
    expect(screen.getByText('Resposta válida da arquitetura estrela.')).toBeInTheDocument();
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
          hierLoading: false,
          comparativeReport: '→ Arquitetura Hierárquica venceu com 4 pontos',
        }}
      />,
    );

    expect(screen.getAllByText('Resultado completo da arquitetura hierárquica.')).toHaveLength(1);
  });

  it('does not switch which architecture it follows once one has started producing text, even if it later errors out', () => {
    // A estrela começa a transmitir primeiro; mesmo que ela erre depois
    // (sem apagar o texto já acumulado), a bolha continua mostrando o
    // texto real da estrela — não pula pra hierárquica no meio do
    // caminho, o que produziria uma resposta com a voz trocada.
    const { rerender } = render(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-5"
        activeRoundWs={{ ...INITIAL_STATE, starText: 'Início da resposta estrela', starLoading: true, hierLoading: true }}
      />,
    );
    expect(screen.getByText('Início da resposta estrela')).toBeInTheDocument();

    rerender(
      <ChatInterface
        {...defaultProps}
        activeRoundId="analysis-5"
        activeRoundWs={{
          ...INITIAL_STATE,
          starText: 'Início da resposta estrela',
          starLoading: false,
          starError: 'falha tardia',
          hierText: 'Resposta da hierárquica chegou depois',
          hierLoading: false,
        }}
      />,
    );

    expect(screen.getByText('Início da resposta estrela')).toBeInTheDocument();
    expect(screen.queryByText('Resposta da hierárquica chegou depois')).not.toBeInTheDocument();
  });
});
