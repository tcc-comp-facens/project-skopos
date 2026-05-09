import { ArchitecturePanel } from './ArchitecturePanel';
import { LlmControls } from './LlmControls';
import { QualityMetricsSection } from './QualityMetricsSection';
import { ComparativeSection } from './ComparativeSection';
import type { UseWebSocketState } from '../hooks/useWebSocket';

/**
 * TechTab — aba técnica destinada a avaliadores e pesquisadores do TCC.
 * Exibe os toggles LLM, os dois painéis de arquitetura lado a lado,
 * as métricas de qualidade e o relatório comparativo.
 *
 * Requirements: 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 8.1
 */
export interface TechTabProps {
  // Toggles LLM
  useLlm: boolean;
  useLlmJudge: boolean;
  onUseLlmChange: (value: boolean) => void;
  onUseLlmJudgeChange: (value: boolean) => void;
  // Estado WebSocket completo
  ws: UseWebSocketState;
  // Estado de submissão
  submitting: boolean;
  apiError: string | null;
}

export function TechTab({
  useLlm,
  useLlmJudge,
  onUseLlmChange,
  onUseLlmJudgeChange,
  ws,
  submitting,
  apiError,
}: TechTabProps): JSX.Element {
  const isAnalysisRunning = ws.starLoading || ws.hierLoading || ws.comparativeLoading;

  return (
    <div
      className="tech-tab"
      id="panel-tech"
      role="tabpanel"
      aria-labelledby="tab-tech"
      data-testid="tech-tab"
    >
      {apiError && (
        <div className="api-error" data-testid="api-error" role="alert">
          {apiError}
        </div>
      )}

      {submitting && (
        <div className="submitting" data-testid="submitting-indicator">
          Enviando...
        </div>
      )}

      <LlmControls
        useLlm={useLlm}
        useLlmJudge={useLlmJudge}
        disabled={isAnalysisRunning}
        onUseLlmChange={onUseLlmChange}
        onUseLlmJudgeChange={onUseLlmJudgeChange}
      />

      <div className="panels-container" data-testid="panels-container">
        <ArchitecturePanel
          title="Hierárquica"
          text={ws.hierText}
          benchmarks={ws.hierBenchmarks}
          isLoading={ws.hierLoading}
          error={ws.hierError}
        />
        <ArchitecturePanel
          title="Estrela"
          text={ws.starText}
          benchmarks={ws.starBenchmarks}
          isLoading={ws.starLoading}
          error={ws.starError}
        />
      </div>

      <QualityMetricsSection qualityMetrics={ws.qualityMetrics} />

      <ComparativeSection
        comparativeReport={ws.comparativeReport}
        comparativeLoading={ws.comparativeLoading}
        llmJudgeText={ws.llmJudgeText}
        llmJudgeLoading={ws.llmJudgeLoading}
      />
    </div>
  );
}
