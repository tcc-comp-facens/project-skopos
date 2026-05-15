export interface AnalysisRequest {
  dateFrom: number;
  dateTo: number;
  healthParams: {
    dengue: boolean;
    covid: boolean;
    vaccination: boolean;
    internacoes: boolean;
    mortalidade: boolean;
  };
  useLlm: boolean;
  useLlmJudge: boolean;
}

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

// Arquitetura vencedora identificada pelo parser
export type WinnerArchitecture = 'star' | 'hierarchical' | null;

// Estrutura tipada das métricas de qualidade recebidas via WebSocket
export interface EfficiencyMetrics {
  E1: number; // overhead de coordenação
  E2: number; // latency breakdown
  E3: number; // communication efficiency
}

export interface QualityScores {
  Q1: number; // consistência determinística
  Q2: number; // faithfulness
  Q3: number; // completeness
  Q4: number; // structural quality
}

export interface ResilienceMetrics {
  R1: number; // partial result coverage
  R2: number; // graceful degradation score
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
