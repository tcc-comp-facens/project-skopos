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
  messageCount?: number;
}

export interface WSEvent {
  analysisId: string;
  architecture: 'star' | 'hierarchical' | 'both';
  type: 'chunk' | 'done' | 'error' | 'metric' | 'quality_metrics' | 'llm_judge' | 'llm_judge_done';
  payload: string | BenchmarkMetrics | Record<string, unknown>;
}
