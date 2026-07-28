import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatWebSocket } from '../hooks/useChatWebSocket';
import { MessageBubble } from './MessageBubble';
import { formatRoundSummary } from '../utils/formatRoundSummary';
import { isBlank, isTooLong, MAX_CHAT_MESSAGE_LENGTH } from '../utils/validateMessage';
import { API_URL } from '../config';
import type { ChatMessage, UseWebSocketState } from '../types';

export interface ChatInterfaceProps {
  useLlm: boolean;
  useLlmJudge: boolean;
  onAnalysisStarted: (analysisId: string, question: string) => void;
  activeRoundId: string | null;
  activeRoundWs: UseWebSocketState | null;
}

let messageCounter = 0;
function nextId(): string {
  messageCounter += 1;
  return `msg-${Date.now()}-${messageCounter}`;
}

const WELCOME_TEXT =
  'Olá! Pergunte sobre gastos públicos em saúde de Sorocaba-SP — por ' +
  'exemplo: "compare dengue e vacinação de 2019 a 2022".';

/**
 * ChatInterface — chat de texto livre que substitui o formulário
 * AnalysisControls na aba Saúde. Autocontido: possui sua própria conexão
 * WebSocket (useChatWebSocket) para o turno de intenção; o resultado da
 * análise disparada é acompanhado via activeRoundWs (o mesmo estado que
 * alimenta a aba Agentes), evitando duplicar o streaming de resultados.
 *
 * Requirements: 1.1-1.5, 2.1-2.5, 5.1-5.4, 7.1-7.6 (spec realtime-chat-interface)
 */
export function ChatInterface({
  useLlm,
  useLlmJudge,
  onAnalysisStarted,
  activeRoundId,
  activeRoundWs,
}: ChatInterfaceProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: nextId(),
      role: 'system',
      content: WELCOME_TEXT,
      timestamp: new Date().toISOString(),
    },
  ]);
  const [inputText, setInputText] = useState('');
  const [isAwaitingResponse, setIsAwaitingResponse] = useState(false);
  const streamingMessageIdRef = useRef<string | null>(null);
  const pendingQuestionRef = useRef<string>('');
  const summarizedRoundIdRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Mensagem de boas-vindas com o intervalo de anos realmente disponível
  // (best-effort — se o endpoint falhar, mantém o texto genérico).
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/data-range`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { minYear: number | null; maxYear: number | null } | null) => {
        if (cancelled || !data?.minYear || !data?.maxYear) return;
        setMessages((prev) =>
          prev.map((m, idx) =>
            idx === 0
              ? { ...m, content: `${WELCOME_TEXT} Tenho dados de ${data.minYear} a ${data.maxYear}.` }
              : m,
          ),
        );
      })
      .catch(() => {
        // mantém a mensagem de boas-vindas genérica
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const appendMessage = useCallback((message: ChatMessage) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  const chat = useChatWebSocket({
    onChunk: (token) => {
      const id = streamingMessageIdRef.current;
      if (!id) return;
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, content: m.content + token } : m)),
      );
    },
    onDone: () => {
      const id = streamingMessageIdRef.current;
      if (id) {
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, isStreaming: false } : m)),
        );
      }
      streamingMessageIdRef.current = null;
      setIsAwaitingResponse(false);
    },
    onError: (message) => {
      appendMessage({
        id: nextId(),
        role: 'system',
        content: message,
        timestamp: new Date().toISOString(),
        isError: true,
      });
      streamingMessageIdRef.current = null;
      setIsAwaitingResponse(false);
    },
    onAnalysisStarted: (analysisId) => {
      onAnalysisStarted(analysisId, pendingQuestionRef.current);
    },
  });

  // Assim que a rodada ativa terminar (deixa de carregar), acrescenta uma
  // bolha de resumo — cada analysisId só gera um resumo (guard via ref).
  useEffect(() => {
    if (!activeRoundId || !activeRoundWs) return;
    const isLoading =
      activeRoundWs.starLoading ||
      activeRoundWs.hierLoading ||
      activeRoundWs.comparativeLoading;
    if (isLoading) return;
    if (summarizedRoundIdRef.current === activeRoundId) return;

    summarizedRoundIdRef.current = activeRoundId;
    appendMessage({
      id: nextId(),
      role: 'system',
      content: formatRoundSummary(activeRoundWs, pendingQuestionRef.current),
      timestamp: new Date().toISOString(),
    });
  }, [activeRoundId, activeRoundWs, appendMessage]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (isBlank(text) || isTooLong(text) || isAwaitingResponse) return;

    pendingQuestionRef.current = text;
    appendMessage({
      id: nextId(),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    });

    const confirmationId = nextId();
    streamingMessageIdRef.current = confirmationId;
    appendMessage({
      id: confirmationId,
      role: 'system',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true,
    });

    setIsAwaitingResponse(true);
    setInputText('');
    chat.sendMessage(text, useLlm, useLlmJudge);
  }, [inputText, isAwaitingResponse, useLlm, useLlmJudge, chat, appendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="chat-container" data-testid="chat-interface">
      {chat.connectionStatus !== 'connected' && (
        <div className="connection-status" data-testid="connection-status" role="alert">
          {chat.connectionStatus === 'reconnecting' &&  'Reconectando…'}
          {chat.connectionStatus === 'disconnected' && !chat.hasEverConnected && 'Conectando ao chat…'}
          {chat.connectionStatus === 'disconnected' && chat.hasEverConnected &&
            'Conexão perdida. Recarregue a página para tentar novamente.'}
        </div>
      )}

      <div className="chat-messages" aria-live="polite" data-testid="chat-messages">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-row">
        <textarea
          className="chat-input"
          data-testid="chat-input"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Digite sua pergunta sobre saúde pública…"
          maxLength={MAX_CHAT_MESSAGE_LENGTH}
          disabled={isAwaitingResponse}
          rows={2}
        />
        <button
          type="button"
          className="chat-send-btn"
          data-testid="chat-send-button"
          onClick={handleSend}
          disabled={isAwaitingResponse || isBlank(inputText)}
        >
          Enviar
        </button>
      </div>

      {inputText.length > 0 && (
        <div className="chat-char-count" data-testid="chat-char-count">
          {inputText.length}/{MAX_CHAT_MESSAGE_LENGTH}
        </div>
      )}
    </div>
  );
}
