import { RoundCard } from './RoundCard';
import type { ChatRound } from '../types';

/**
 * RoundOverview — panorama de todas as rodadas da sessão (substitui o
 * antigo RoundSelector de rodada única). A rodada mais recente começa
 * expandida; as anteriores começam fechadas mas mantêm seu próprio
 * estado de expansão (RoundCard), sem serem recolhidas quando uma nova
 * rodada chega.
 */
export interface RoundOverviewProps {
  rounds: ChatRound[];
}

export function RoundOverview({ rounds }: RoundOverviewProps): JSX.Element {
  if (rounds.length === 0) {
    return (
      <div className="round-overview" data-testid="round-overview">
        <span className="round-overview-empty" data-testid="round-overview-empty">
          Nenhuma pergunta feita ainda — use o chat na aba Saúde.
        </span>
      </div>
    );
  }

  return (
    <div className="round-overview" data-testid="round-overview">
      {rounds.map((round, index) => (
        <RoundCard
          key={round.id}
          round={round}
          index={index}
          defaultExpanded={index === rounds.length - 1}
        />
      ))}
    </div>
  );
}
