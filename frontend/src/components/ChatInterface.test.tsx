/**
 * Tests for ChatInterface component.
 * Validates welcome message, blank-message guard, and input disabled
 * while awaiting a response (re-enabled on system_done).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ChatInterface } from './ChatInterface';
import { useChatWebSocket } from '../hooks/useChatWebSocket';
import type { UseChatWebSocketCallbacks } from '../hooks/useChatWebSocket';

vi.mock('../hooks/useChatWebSocket');

describe('ChatInterface', () => {
  let capturedCallbacks: UseChatWebSocketCallbacks | null = null;
  const sendMessage = vi.fn();

  const defaultProps = {
    useLlm: true,
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
    expect(sendMessage).toHaveBeenCalledWith('compare dengue de 2019 a 2022', true, false);

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
});
