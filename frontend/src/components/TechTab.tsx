import { useEffect, useRef, useState } from 'react';
import { ArchitecturePanel } from './ArchitecturePanel';
import { LlmControls } from './LlmControls';
import { QualityMetricsSection } from './QualityMetricsSection';
import { ComparativeSection } from './ComparativeSection';
import { RoundSelector } from './RoundSelector';
import { INITIAL_STATE } from '../hooks/useWebSocket';
import type { ChatRound } from '../types';

/**
 * TechTab — aba técnica destinada a avaliadores e pesquisadores do TCC.
 * Exibe os toggles LLM, os dois painéis de arquitetura lado a lado, as
 * métricas de qualidade e o relatório comparativo — agora navegável por
 * rodada de chat, já que uma sessão pode disparar várias análises.
 *
 * A rodada mais recente é selecionada automaticamente assim que começa;
 * uma seleção manual do avaliador persiste até a próxima rodada começar.
 *
 * Requirements: 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 8.1
 */
export interface TechTabProps {
  useLlm: boolean;
  useLlmJudge: boolean;
  onUseLlmChange: (value: boolean) => void;
  onUseLlmJudgeChange: (value: boolean) => void;
  rounds: ChatRound[];
  isActiveRoundRunning: boolean;
}

export function TechTab({
  useLlm,
  useLlmJudge,
  onUseLlmChange,
  onUseLlmJudgeChange,
  rounds,
  isActiveRoundRunning,
}: TechTabProps): JSX.Element {
  const [selectedRoundId, setSelectedRoundId] = useState<string | null>(null);
  const prevRoundsLengthRef = useRef(0);

  // Segue automaticamente a rodada mais nova assim que ela começa; uma
  // seleção manual do avaliador (via RoundSelector) persiste até a
  // próxima rodada ser criada.
  useEffect(() => {
    if (rounds.length > prevRoundsLengthRef.current) {
      const latest = rounds[rounds.length - 1];
      setSelectedRoundId(latest.id);
    }
    prevRoundsLengthRef.current = rounds.length;
  }, [rounds]);

  const displayed =
    rounds.find((r) => r.id === selectedRoundId)?.snapshot ?? INITIAL_STATE;

  return (
    <div
      className="tech-tab"
      id="panel-tech"
      role="tabpanel"
      aria-labelledby="tab-tech"
      data-testid="tech-tab"
    >
      <RoundSelector
        rounds={rounds}
        selectedRoundId={selectedRoundId}
        onSelectRound={setSelectedRoundId}
      />

      <LlmControls
        useLlm={useLlm}
        useLlmJudge={useLlmJudge}
        disabled={isActiveRoundRunning}
        onUseLlmChange={onUseLlmChange}
        onUseLlmJudgeChange={onUseLlmJudgeChange}
      />

      <div className="panels-container" data-testid="panels-container">
        <ArchitecturePanel
          title="Hierárquica"
          text={displayed.hierText}
          benchmarks={displayed.hierBenchmarks}
          isLoading={displayed.hierLoading}
          error={displayed.hierError}
        />
        <ArchitecturePanel
          title="Estrela"
          text={displayed.starText}
          benchmarks={displayed.starBenchmarks}
          isLoading={displayed.starLoading}
          error={displayed.starError}
        />
      </div>

      <QualityMetricsSection qualityMetrics={displayed.qualityMetrics} />

      <ComparativeSection
        comparativeReport={displayed.comparativeReport}
        comparativeLoading={displayed.comparativeLoading}
        llmJudgeText={displayed.llmJudgeText}
        llmJudgeLoading={displayed.llmJudgeLoading}
      />
    </div>
  );
}
