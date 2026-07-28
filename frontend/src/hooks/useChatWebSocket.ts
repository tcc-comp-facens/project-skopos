import { useEffect, useRef, useState, useCallback } from 'react';
import { WS_URL } from '../config';
import type { ChatWSEvent, ConnectionStatus } from '../types';

const MAX_RECONNECT_ATTEMPTS = 3;

interface OutgoingMessage {
  text: string;
  useLlm: boolean;
  useLlmJudge: boolean;
}

export interface UseChatWebSocketCallbacks {
  onAck?: () => void;
  onChunk: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
  onAnalysisStarted: (analysisId: string) => void;
}

export interface UseChatWebSocketState {
  connectionStatus: ConnectionStatus;
  hasEverConnected: boolean;
  sendMessage: (text: string, useLlm: boolean, useLlmJudge: boolean) => void;
}

/**
 * Hook de conexão com /ws/chat/{session_id} — self-contained, gera seu
 * próprio sessionId (só em memória, sem persistência entre reloads).
 *
 * Reconexão com backoff exponencial (1s/2s/4s, máx. 3 tentativas) e fila
 * de mensagens de saída entregues em ordem assim que a conexão volta.
 *
 * Requirements: 4.5, 4.6, 8.5 (spec realtime-chat-interface)
 */
export function useChatWebSocket(
  callbacks: UseChatWebSocketCallbacks,
): UseChatWebSocketState {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const outgoingQueueRef = useRef<OutgoingMessage[]>([]);
  const sessionIdRef = useRef<string>(crypto.randomUUID());
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const flushQueue = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    while (outgoingQueueRef.current.length > 0) {
      const msg = outgoingQueueRef.current.shift();
      if (!msg) break;
      ws.send(JSON.stringify({
        type: 'user_message',
        payload: { text: msg.text, useLlm: msg.useLlm, useLlmJudge: msg.useLlmJudge },
      }));
    }
  }, []);

  const connect = useCallback(() => {
    const ws = new WebSocket(`${WS_URL}/ws/chat/${sessionIdRef.current}`);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttemptsRef.current = 0;
      setConnectionStatus('connected');
      setHasEverConnected(true);
      flushQueue();
    };

    ws.onmessage = (msgEvent) => {
      try {
        const data: ChatWSEvent = JSON.parse(msgEvent.data as string);
        switch (data.type) {
          case 'user_ack':
            callbacksRef.current.onAck?.();
            break;
          case 'system_chunk':
            callbacksRef.current.onChunk(data.payload);
            break;
          case 'system_done':
            callbacksRef.current.onDone();
            break;
          case 'error':
            callbacksRef.current.onError(data.payload);
            break;
          case 'analysis_started':
            callbacksRef.current.onAnalysisStarted(data.payload);
            break;
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = (closeEvent) => {
      setConnectionStatus('disconnected');
      if (
        closeEvent.code !== 1000 &&
        reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS
      ) {
        reconnectAttemptsRef.current += 1;
        setConnectionStatus('reconnecting');
        const delay = 1000 * 2 ** (reconnectAttemptsRef.current - 1); // 1s, 2s, 4s
        reconnectTimerRef.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      // onerror é sempre seguido de onclose — lógica de reconexão vive lá
    };
  }, [flushQueue]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close(1000);
        wsRef.current = null;
      }
    };
  }, [connect]);

  const sendMessage = useCallback(
    (text: string, useLlm: boolean, useLlmJudge: boolean) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'user_message',
          payload: { text, useLlm, useLlmJudge },
        }));
      } else {
        outgoingQueueRef.current.push({ text, useLlm, useLlmJudge });
      }
    },
    [],
  );

  return { connectionStatus, hasEverConnected, sendMessage };
}
