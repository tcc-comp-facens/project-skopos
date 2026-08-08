import { useEffect, useRef, useState, useCallback } from 'react';
import { WS_URL } from '../config';
import type {
  WSEvent,
  BenchmarkMetrics,
  AgentDataPayload,
  QualityMetrics,
  UseWebSocketState,
} from '../types';

const MAX_RECONNECT_ATTEMPTS = 3;

export type { UseWebSocketState };

export const INITIAL_STATE: UseWebSocketState = {
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
  starAgentData: null,
  hierAgentData: null,
};

export function useWebSocket(analysisId: string | null): UseWebSocketState {
  const [state, setState] = useState<UseWebSocketState>(INITIAL_STATE);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const analysisIdRef = useRef(analysisId);
  analysisIdRef.current = analysisId;

  // Reset síncrono, durante o render (não num useEffect) — quando
  // analysisId muda, `state` ainda reflete a rodada ANTERIOR (já
  // finalizada: loading=false, texto completo) até o efeito de conexão
  // abaixo rodar, o que só acontece depois do commit. Sem isso, existe
  // pelo menos um render em que quem consome este hook (App -> UserTab ->
  // ChatInterface) recebe o `analysisId` NOVO junto com o estado
  // COMPLETO da rodada velha — e como "isLoading" já vem false (sobra da
  // rodada anterior), o ChatInterface finaliza a rodada nova
  // imediatamente com a resposta antiga (bug real observado: a resposta
  // exibida ficava "uma pergunta atrasada", incluindo o caso inicial em
  // que nem havia rodada anterior — aí o estado "velho" é o INITIAL_STATE
  // com loading=false, caindo direto no fallback de erro mesmo com a
  // análise tendo funcionado).
  //
  // A guarda precisa ser `useState`, não `useRef` (tentativa anterior,
  // não segurou em produção — só passava no teste isolado): o padrão
  // oficial do React para "ajustar estado quando uma prop muda" compara
  // contra ESTADO, porque mutação de ref sobrevive mesmo a um render
  // "descartado" (ex.: o double-render de desenvolvimento do React
  // StrictMode chama o corpo do componente duas vezes por commit) — a
  // guarda por ref podia achar "já corrigido" numa das duas chamadas
  // sem a correção de `state` de fato ter se estabilizado, deixando
  // `starLoading` voltar a `false` bem no primeiro render da rodada
  // nova. Com `useState`, as duas atualizações (`setStateForId` +
  // `setState`) ficam no mesmo lote e o React garante consistência
  // entre elas mesmo sob o double-render.
  const [stateForId, setStateForId] = useState<string | null>(null);
  if (stateForId !== analysisId) {
    setStateForId(analysisId);
    setState(
      analysisId
        ? { ...INITIAL_STATE, starLoading: true, hierLoading: true }
        : INITIAL_STATE,
    );
  }

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
        case 'agent_data':
          {
            const data = event.payload as AgentDataPayload;
            if (data && Array.isArray(data.agents)) {
              setState((prev) => ({ ...prev, starAgentData: data.agents }));
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
        case 'agent_data':
          {
            const data = event.payload as AgentDataPayload;
            if (data && Array.isArray(data.agents)) {
              setState((prev) => ({ ...prev, hierAgentData: data.agents }));
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
            qualityMetrics: event.payload as unknown as QualityMetrics,
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
        // Ignora o close de uma conexão já substituída — sem isso, o
        // double-connect de desenvolvimento do React StrictMode
        // (mount->cleanup->mount abre uma conexão "canário", fecha,
        // abre a de verdade) faz o close ASSÍNCRONO da conexão canário
        // chegar DEPOIS que a conexão real já está no ar, e esse close
        // agendava uma reconexão (uma 3ª conexão concorrente com a
        // real, disputando os mesmos eventos do backend — sintoma real
        // observado: a análise "sumia" mesmo com o backend gerando o
        // resultado certinho, ver api/websocket.py). `wsRef.current`
        // só aponta pra conexão mais recente — se não for `ws` (quem
        // disparou este onclose), essa conexão já foi superada, ignora.
        if (wsRef.current !== ws) return;
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
    // O reset de `state` para a rodada nova já acontece de forma síncrona
    // acima (durante o render, via stateForIdRef) — este efeito cuida só
    // da conexão em si (fechar a anterior, abrir a nova).
    reconnectAttemptsRef.current = 0;

    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close(1000);
      wsRef.current = null;
    }

    if (!analysisId) {
      return;
    }

    connect(analysisId);

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        // Código 1000 explícito: fechamento intencional nosso, nunca
        // deve por si só agendar reconexão (mesmo raciocínio do guard
        // em onclose acima — redundância deliberada).
        wsRef.current.close(1000);
        wsRef.current = null;
      }
    };
  }, [analysisId, connect]);

  return state;
}
