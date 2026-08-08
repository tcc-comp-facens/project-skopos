import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatWebSocket } from '../hooks/useChatWebSocket';
import { MessageBubble } from './MessageBubble';
import { formatRoundSummary } from '../utils/formatRoundSummary';
import { parseWinner } from '../utils/parseWinner';
import { isBlank, isTooLong, MAX_CHAT_MESSAGE_LENGTH } from '../utils/validateMessage';
import { API_URL } from '../config';
import type { ChatMessage, UseWebSocketState, WinnerArchitecture } from '../types';

export interface ChatInterfaceProps {
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

// Revelação progressiva simulada da resposta vencedora na aba Saúde — o
// texto completo já chegou do backend antes de sabermos quem venceu (só
// depois que as duas arquiteturas terminam), então "streaming" aqui é um
// replay client-side do texto final, não os chunks reais do WebSocket
// (esses viram visíveis só na aba Agentes, sem crescimento — ver
// ArchitecturePanel).
const REVEAL_CHUNK_SIZE = 6;
const REVEAL_INTERVAL_MS = 20;

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
  const answerMessageIdRef = useRef<Map<string, string>>(new Map());
  const finalizedRoundIdRef = useRef<Set<string>>(new Set());
  const revealTimersRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Limpa qualquer revelação em andamento ao desmontar (defensivo — hoje
  // ChatInterface nunca desmonta de fato, as duas abas ficam sempre
  // montadas e só trocam `display`, mas evita timers órfãos se isso mudar).
  useEffect(() => {
    return () => {
      revealTimersRef.current.forEach(clearInterval);
      revealTimersRef.current.clear();
    };
  }, []);

  const startReveal = useCallback(
    (roundId: string, messageId: string, fullText: string, winner: WinnerArchitecture) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? { ...m, content: '', isStreaming: true, kind: 'architecture_answer', architecture: winner ?? undefined }
            : m,
        ),
      );

      let revealed = 0;
      const timer = setInterval(() => {
        revealed = Math.min(revealed + REVEAL_CHUNK_SIZE, fullText.length);
        const slice = fullText.slice(0, revealed);
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, content: slice } : m)),
        );
        if (revealed >= fullText.length) {
          clearInterval(timer);
          revealTimersRef.current.delete(roundId);
          setMessages((prev) =>
            prev.map((m) => (m.id === messageId ? { ...m, isStreaming: false } : m)),
          );
        }
      }, REVEAL_INTERVAL_MS);
      revealTimersRef.current.set(roundId, timer);
    },
    [],
  );

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

  // Assim que uma rodada começa, acrescenta uma bolha de resposta (como
  // uma mensagem normal do chat, não um card separado abaixo dele) —
  // primeiro um placeholder "Analisando…", depois substituída pelo texto
  // completo da arquitetura vencedora assim que a rodada termina. Cada
  // analysisId tem no máximo uma bolha de resposta, atualizada in place
  // (guard via refs — nunca duplicada, nunca re-finalizada).
  useEffect(() => {
    if (!activeRoundId || !activeRoundWs) return;
    if (finalizedRoundIdRef.current.has(activeRoundId)) return;

    let messageId = answerMessageIdRef.current.get(activeRoundId);
    if (!messageId) {
      messageId = nextId();
      answerMessageIdRef.current.set(activeRoundId, messageId);
      appendMessage({
        id: messageId,
        role: 'system',
        content: 'Analisando os dados…',
        timestamp: new Date().toISOString(),
        isStreaming: true,
      });
    }

    const isLoading =
      activeRoundWs.starLoading ||
      activeRoundWs.hierLoading ||
      activeRoundWs.comparativeLoading;
    if (isLoading) return;

    finalizedRoundIdRef.current.add(activeRoundId);
    const winner = parseWinner(activeRoundWs.comparativeReport);

    if (winner) {
      const fullText = winner === 'star' ? activeRoundWs.starText : activeRoundWs.hierText;
      startReveal(activeRoundId, messageId, fullText, winner);
      return;
    }

    // Sem vencedor (ambas falharam) — mensagem curta de fallback, sem
    // revelação progressiva (não é "a resposta", é um aviso de erro).
    const finalMessageId = messageId;
    const content = formatRoundSummary(activeRoundWs, pendingQuestionRef.current);
    setMessages((prev) =>
      prev.map((m) =>
        m.id === finalMessageId ? { ...m, content, isStreaming: false, kind: 'text' } : m,
      ),
    );
  }, [activeRoundId, activeRoundWs, appendMessage, startReveal]);

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
    chat.sendMessage(text, useLlmJudge);
  }, [inputText, isAwaitingResponse, useLlmJudge, chat, appendMessage]);

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
