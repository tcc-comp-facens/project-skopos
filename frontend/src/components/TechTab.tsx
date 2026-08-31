import { RoundOverview } from './RoundOverview';
import type { ChatRound } from '../types';

/**
 * TechTab — aba técnica destinada a avaliadores e pesquisadores do TCC.
 * Exibe o panorama de todas as rodadas da sessão (RoundOverview) — cada
 * rodada com seus dois painéis de arquitetura, métricas de qualidade,
 * avaliação RAGAS e relatório comparativo, expansível individualmente.
 *
 * Não há mais controles de LLM aqui: a síntese via LLM e a avaliação
 * RAGAS são ambas incondicionais, então não havia o que alternar.
 *
 * Requirements: 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 8.1
 */
export interface TechTabProps {
  rounds: ChatRound[];
}

export function TechTab({ rounds }: TechTabProps): JSX.Element {
  return (
    <div
      className="tech-tab"
      id="panel-tech"
      role="tabpanel"
      aria-labelledby="tab-tech"
      data-testid="tech-tab"
    >
      <RoundOverview rounds={rounds} />
    </div>
  );
}
