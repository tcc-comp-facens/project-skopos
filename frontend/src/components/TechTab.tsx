import { LlmControls } from './LlmControls';
import { RoundOverview } from './RoundOverview';
import type { ChatRound } from '../types';

/**
 * TechTab — aba técnica destinada a avaliadores e pesquisadores do TCC.
 * Exibe o toggle do LLM Judge e o panorama de todas as rodadas da sessão
 * (RoundOverview) — cada rodada com seus dois painéis de arquitetura,
 * métricas de qualidade e relatório comparativo, expansível
 * individualmente.
 *
 * Requirements: 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 8.1
 */
export interface TechTabProps {
  useLlmJudge: boolean;
  onUseLlmJudgeChange: (value: boolean) => void;
  rounds: ChatRound[];
  isActiveRoundRunning: boolean;
}

export function TechTab({
  useLlmJudge,
  onUseLlmJudgeChange,
  rounds,
  isActiveRoundRunning,
}: TechTabProps): JSX.Element {
  return (
    <div
      className="tech-tab"
      id="panel-tech"
      role="tabpanel"
      aria-labelledby="tab-tech"
      data-testid="tech-tab"
    >
      <LlmControls
        useLlmJudge={useLlmJudge}
        disabled={isActiveRoundRunning}
        onUseLlmJudgeChange={onUseLlmJudgeChange}
      />

      <RoundOverview rounds={rounds} />
    </div>
  );
}
