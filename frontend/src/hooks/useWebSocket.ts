import { useEffect, useRef, useState, useCallback } from 'react';
import { WS_URL } from '../config';
import type { WSEvent, BenchmarkMetrics } from '../types';

const MAX_RECONNECT_ATTEMPTS = 3;

export interface UseWebSocketState {
  starText: string;
  hierText: string;
  starBenchmarks: BenchmarkMetrics | null;
  hierBenchmarks: BenchmarkMetrics | null;
  starLoading: boolean;
  hierLoading: boolean;
  starError: string | null;
  hierError: string | null;
  comparativeReport: string;
  comparativeLoading: boolean;
  qualityMetrics: Record<string, unknown> | null;
  llmJudgeText: string;
  llmJudgeLoading: boolean;
}

const INITIAL_STATE: UseWebSocketState = {
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

export function useWebSocket(analysisId: string | null): UseWebSocketState {
  const [state, setState] = useState<UseWebSocketState>(INITIAL_STATE);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const analysisIdRef = useRef(analysisId);
  analysisIdRef.current = analysisId;

  const handleEvent = useCallback((event: WSEvent) => {
    const arch = event.architecture;

    if (arch === 'star') {
      switch (event.type) {
        case 'chunk':
          setState((prev) => ({
            ...prev,
            starText: prev.starText + (event.payload as string),
            starLoading: true,
          }));
          break;
        case 'done':
          setState((prev) => ({ ...prev, starLoading: false }));
          break;
        case 'error':
          setState((prev) => ({
            ...prev,
            starError: event.payload as string,
            starLoading: false,
          }));
          break;
        case 'metric':
          {
            const metrics = event.payload as BenchmarkMetrics;
            if (metrics && typeof metrics === 'object' && Array.isArray(metrics.agentMetrics)) {
              setState((prev) => ({
                ...prev,
                starBenchmarks: metrics,
              }));
            }
          }
          break;
      }
    } else if (arch === 'hierarchical') {
      switch (event.type) {
        case 'chunk':
          setState((prev) => ({
            ...prev,
            hierText: prev.hierText + (event.payload as string),
            hierLoading: true,
          }));
          break;
        case 'done':
          setState((prev) => ({ ...prev, hierLoading: false }));
          break;
        case 'error':
          setState((prev) => ({
            ...prev,
            hierError: event.payload as string,
            hierLoading: false,
          }));
          break;
        case 'metric':
          {
            const metrics = event.payload as BenchmarkMetrics;
            if (metrics && typeof metrics === 'object' && Array.isArray(metrics.agentMetrics)) {
              setState((prev) => ({
                ...prev,
                hierBenchmarks: metrics,
              }));
            }
          }
          break;
      }
    } else if (arch === 'both') {
      switch (event.type) {
        case 'chunk':
          setState((prev) => ({
            ...prev,
            comparativeReport: prev.comparativeReport + (event.payload as string),
            comparativeLoading: true,
          }));
          break;
        case 'done':
          setState((prev) => ({ ...prev, comparativeLoading: false }));
          break;
        case 'quality_metrics':
          setState((prev) => ({
            ...prev,
            qualityMetrics: event.payload as Record<string, unknown>,
          }));
          break;
        case 'llm_judge':
          setState((prev) => ({
            ...prev,
            llmJudgeText: prev.llmJudgeText + (event.payload as string),
            llmJudgeLoading: true,
          }));
          break;
        case 'llm_judge_done':
          setState((prev) => ({ ...prev, llmJudgeLoading: false }));
          break;
      }
    }
  }, []);

  const connect = useCallback(
    (id: string) => {
      const ws = new WebSocket(`${WS_URL}/ws/${id}`);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
      };

      ws.onmessage = (msgEvent) => {
        try {
          const data: WSEvent = JSON.parse(msgEvent.data as string);
          handleEvent(data);
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = (closeEvent) => {
        // Only reconnect on unexpected close (not clean close code 1000)
        if (
          closeEvent.code !== 1000 &&
          analysisIdRef.current === id &&
          reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS
        ) {
          reconnectAttemptsRef.current += 1;
          const delay = 1000 * reconnectAttemptsRef.current;
          reconnectTimerRef.current = setTimeout(() => {
            if (analysisIdRef.current === id) {
              connect(id);
            }
          }, delay);
        }
      };

      ws.onerror = () => {
        // onerror is always followed by onclose, so reconnect logic lives there
      };
    },
    [handleEvent],
  );

  useEffect(() => {
    // Reset state when analysisId changes
    setState(INITIAL_STATE);
    reconnectAttemptsRef.current = 0;

    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    if (!analysisId) {
      return;
    }

    // Set loading true for both architectures when connecting
    setState((prev) => ({
      ...prev,
      starLoading: true,
      hierLoading: true,
    }));

    connect(analysisId);

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [analysisId, connect]);

  return state;
}
