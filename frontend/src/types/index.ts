export interface AgentMetric {
  agentName: string;
  executionTimeMs: number;
  cpuPercent: number;
}

export interface BenchmarkMetrics {
  architecture: 'star' | 'hierarchical';
  totalExecutionTimeMs: number;
  agentMetrics: AgentMetric[];
}

export interface WSEvent {
  analysisId: string;
  architecture: 'star' | 'hierarchical' | 'both';
  type: 'chunk' | 'done' | 'error' | 'metric' | 'quality_metrics' | 'llm_judge' | 'llm_judge_done';
  payload: string | BenchmarkMetrics | Record<string, unknown>;
}

// Aba ativa
export type ActiveTab = 'user' | 'tech';

// ---------------------------------------------------------------------
// Chat (aba Saúde) — substitui o formulário AnalysisControls
// ---------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: 'user' | 'system';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  isError?: boolean;
}

export interface ChatWSEvent {
  type:
    | 'user_ack'
    | 'system_chunk'
    | 'system_done'
    | 'error'
    | 'analysis_started';
  payload: string;
}

export type ConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

// Estado espelhado do hook useWebSocket para uma análise (star/hier/both).
// Movido para types/index.ts (em vez de definido em useWebSocket.ts) para
// ser reaproveitado por ChatRound sem import circular.
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
  qualityMetrics: QualityMetrics | null;
  llmJudgeText: string;
  llmJudgeLoading: boolean;
}

// Uma "rodada" = uma pergunta do chat que disparou uma análise.
// snapshot é o estado do useWebSocket espelhado no momento mais recente
// (ver App.tsx) — congelado assim que a próxima rodada começa.
export interface ChatRound {
  id: string; // analysisId
  question: string;
  startedAt: string;
  snapshot: UseWebSocketState;
}

// Arquitetura vencedora identificada pelo parser
export type WinnerArchitecture = 'star' | 'hierarchical' | null;

// Estrutura tipada das métricas de qualidade recebidas via WebSocket
export interface EfficiencyMetrics {
  E1: number; // overhead de coordenação
  E2: number; // latency breakdown
}

export interface QualityScores {
  Q1: number; // consistência determinística
  Q2: number; // faithfulness
  Q3: number; // completeness
}

export interface ResilienceMetrics {
  R1: number; // partial result coverage
}

export interface ArchitectureQualityMetrics {
  efficiency: EfficiencyMetrics;
  quality: QualityScores;
  resilience: ResilienceMetrics;
}

export interface QualityMetrics {
  star: ArchitectureQualityMetrics;
  hierarchical: ArchitectureQualityMetrics;
}
