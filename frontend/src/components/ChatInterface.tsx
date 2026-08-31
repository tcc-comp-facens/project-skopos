import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatWebSocket } from '../hooks/useChatWebSocket';
import { MessageBubble } from './MessageBubble';
import { formatRoundSummary } from '../utils/formatRoundSummary';
import { parseWinner } from '../utils/parseWinner';
import { isBlank, isTooLong, MAX_CHAT_MESSAGE_LENGTH } from '../utils/validateMessage';
import { API_URL } from '../config';
import type { ChatMessage, UseWebSocketState, WinnerArchitecture } from '../types';

export interface ChatInterfaceProps {
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
  // Qual arquitetura esta rodada está acompanhando ao vivo na bolha de
  // resposta — decidida uma vez (a primeira que produzir texto) e nunca
  // trocada depois, para o conteúdo exibido nunca "pular" de uma
  // arquitetura pra outra no meio do streaming.
  const streamingArchRef = useRef<Map<string, 'star' | 'hierarchical'>>(new Map());
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

  // Assim que uma rodada começa, acrescenta uma bolha de resposta (como
  // uma mensagem normal do chat, não um card separado abaixo dele) —
  // primeiro um placeholder "Analisando…", depois preenchida ao vivo,
  // chunk a chunk, com o texto real de uma das arquiteturas assim que ela
  // começa a chegar (ver decisão de `followedArch` abaixo). Cada
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

    // Decide (uma única vez por rodada) qual arquitetura acompanhar ao
    // vivo na bolha — a primeira que produzir algum texto. Diferente do
    // comportamento anterior (esperar as duas terminarem + o relatório
    // comparativo pra só então revelar o texto do "vencedor" com uma
    // animação simulada no cliente), aqui o conteúdo da bolha É o próprio
    // `starText`/`hierText` do WebSocket crescendo em tempo real — os
    // mesmos chunks que já apareciam ao vivo na aba Agentes, sem
    // replay/simulação. O preço dessa responsividade: como normalmente a
    // estrela termina bem antes da hierárquica, a rodada costuma
    // finalizar (abaixo) antes do relatório comparativo — que só fica
    // pronto depois que as DUAS terminam — decidir quem "venceu"; nesse
    // caso o badge de vencedor fica de fora (ver mais abaixo), mas a
    // resposta não fica esperando por ele.
    let followedArch = streamingArchRef.current.get(activeRoundId);
    if (!followedArch) {
      if (activeRoundWs.starText) {
        followedArch = 'star';
      } else if (activeRoundWs.hierText) {
        followedArch = 'hierarchical';
      }
      if (followedArch) {
        streamingArchRef.current.set(activeRoundId, followedArch);
      }
    }

    if (!followedArch) {
      // Nenhuma arquitetura produziu texto ainda. Enquanto pelo menos uma
      // ainda estiver rodando, mantém "Analisando…" e espera o próximo
      // chunk. Só cai no resumo de erro quando as duas já pararam sem
      // nunca terem produzido texto.
      const stillRunning =
        activeRoundWs.starLoading || activeRoundWs.hierLoading || activeRoundWs.comparativeLoading;
      if (stillRunning) return;

      finalizedRoundIdRef.current.add(activeRoundId);
      const finalMessageId = messageId;
      const content = formatRoundSummary(activeRoundWs, pendingQuestionRef.current);
      setMessages((prev) =>
        prev.map((m) => (m.id === finalMessageId ? { ...m, content, isStreaming: false, kind: 'text' } : m)),
      );
      return;
    }

    const text = followedArch === 'star' ? activeRoundWs.starText : activeRoundWs.hierText;
    const archLoading = followedArch === 'star' ? activeRoundWs.starLoading : activeRoundWs.hierLoading;
    const finalMessageId = messageId;

    if (archLoading) {
      // Ainda transmitindo — atualiza a bolha com o texto real acumulado
      // até agora, chunk a chunk, a cada render (o WebSocket já entrega
      // um novo `starText`/`hierText` por chunk recebido).
      setMessages((prev) =>
        prev.map((m) =>
          m.id === finalMessageId ? { ...m, content: text, isStreaming: true, kind: 'architecture_answer' } : m,
        ),
      );
      return;
    }

    // A arquitetura acompanhada terminou — finaliza a bolha com o texto
    // final dela, sem esperar a outra arquitetura nem o relatório
    // comparativo (esses continuam disponíveis na aba Agentes). Se o
    // relatório comparativo já tiver chegado a tempo e apontar esta
    // mesma arquitetura como vencedora, mostra o badge; caso contrário
    // (o caso comum — a hierárquica ainda não terminou) fica sem badge,
    // não sem resposta.
    finalizedRoundIdRef.current.add(activeRoundId);
    const winner: WinnerArchitecture = parseWinner(activeRoundWs.comparativeReport);
    setMessages((prev) =>
      prev.map((m) =>
        m.id === finalMessageId
          ? {
              ...m,
              content: text,
              isStreaming: false,
              kind: 'architecture_answer',
              architecture: winner === followedArch ? followedArch : undefined,
            }
          : m,
      ),
    );
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
    chat.sendMessage(text);
  }, [inputText, isAwaitingResponse, chat, appendMessage]);

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
