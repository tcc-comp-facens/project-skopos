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
  type:
    | 'chunk'
    | 'done'
    | 'error'
    | 'metric'
    | 'agent_data'
    | 'quality_metrics'
    | 'llm_judge'
    | 'llm_judge_done';
  payload: string | BenchmarkMetrics | AgentDataPayload | Record<string, unknown>;
}

// Query Cypher + dados brutos que um agente de domínio executou —
// capturado em Neo4jClient.query_log (backend) e repassado via evento
// `agent_data`, para a aba técnica exibir gráfico + query + linhas cruas.
export interface AgentQueryEntry {
  query: string;
  params: Record<string, unknown>;
  rowCount: number;
  rows: Record<string, unknown>[];
}

export interface AgentDataEntry {
  agentName: string;
  agentLabel: string;
  queries: AgentQueryEntry[];
}

export interface AgentDataPayload {
  architecture: 'star' | 'hierarchical';
  agents: AgentDataEntry[];
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
  // 'architecture_answer' marca a bolha que carrega o texto completo da
  // arquitetura vencedora de uma rodada (ver ChatInterface); default é
  // 'text' (mensagem comum). Continua renderizado como texto puro, nunca
  // dangerouslySetInnerHTML.
  kind?: 'text' | 'architecture_answer';
  // Setado só na bolha de resposta quando há vencedor — MessageBubble usa
  // isso para mostrar um badge de emoji no canto, em vez de mencionar o
  // nome da arquitetura no texto.
  architecture?: 'star' | 'hierarchical';
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
  starAgentData: AgentDataEntry[] | null;
  hierAgentData: AgentDataEntry[] | null;
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
