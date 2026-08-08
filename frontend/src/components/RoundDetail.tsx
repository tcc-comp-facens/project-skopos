import { ArchitecturePanel } from './ArchitecturePanel';
import { QualityMetricsSection } from './QualityMetricsSection';
import { ComparativeSection } from './ComparativeSection';
import type { UseWebSocketState } from '../types';

/**
 * RoundDetail — conteúdo técnico completo de UMA rodada (os dois
 * ArchitecturePanel, métricas de qualidade, relatório comparativo).
 * Extraído do antigo corpo fixo da TechTab para ser reaproveitado por
 * RoundCard uma vez por rodada expandida (RoundOverview).
 */
export interface RoundDetailProps {
  snapshot: UseWebSocketState;
}

export function RoundDetail({ snapshot }: RoundDetailProps): JSX.Element {
  return (
    <div className="round-detail" data-testid="round-detail">
      <div className="panels-container" data-testid="panels-container">
        <ArchitecturePanel
          title="Hierárquica"
          text={snapshot.hierText}
          benchmarks={snapshot.hierBenchmarks}
          isLoading={snapshot.hierLoading}
          error={snapshot.hierError}
          agentData={snapshot.hierAgentData}
        />
        <ArchitecturePanel
          title="Estrela"
          text={snapshot.starText}
          benchmarks={snapshot.starBenchmarks}
          isLoading={snapshot.starLoading}
          error={snapshot.starError}
          agentData={snapshot.starAgentData}
        />
      </div>

      <QualityMetricsSection qualityMetrics={snapshot.qualityMetrics} />

      <ComparativeSection
        comparativeReport={snapshot.comparativeReport}
        comparativeLoading={snapshot.comparativeLoading}
        llmJudgeText={snapshot.llmJudgeText}
        llmJudgeLoading={snapshot.llmJudgeLoading}
      />
    </div>
  );
}
