import { useState } from 'react';
import { RoundDetail } from './RoundDetail';
import { parseWinner } from '../utils/parseWinner';
import type { ChatRound } from '../types';

export interface RoundCardProps {
  round: ChatRound;
  index: number;
  defaultExpanded: boolean;
}

function truncate(text: string, max = 80): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

const WINNER_LABEL: Record<'star' | 'hierarchical', string> = {
  star: '⭐ Estrela',
  hierarchical: '🏛 Hierárquica',
};

/**
 * RoundCard — uma rodada de pergunta/resposta na aba técnica. Cabeçalho
 * sempre visível (resumo); o conteúdo pesado (RoundDetail — gráficos,
 * queries, dados) só monta quando expandido, para não pesar a página
 * conforme a sessão acumula rodadas.
 */
export function RoundCard({ round, index, defaultExpanded }: RoundCardProps): JSX.Element {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const winner = parseWinner(round.snapshot.comparativeReport);
  const starMs = round.snapshot.starBenchmarks?.totalExecutionTimeMs;
  const hierMs = round.snapshot.hierBenchmarks?.totalExecutionTimeMs;

  return (
    <div className="round-card" data-testid={`round-card-${round.id}`}>
      <button
        type="button"
        className="round-card-header"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid={`round-card-toggle-${round.id}`}
      >
        <span className={`round-card-chevron ${expanded ? 'expanded' : ''}`}>▸</span>
        <span className="round-card-index">Rodada {index + 1}</span>
        <span className="round-card-time">{formatTime(round.startedAt)}</span>
        <span className="round-card-question">{truncate(round.question)}</span>
        {winner && (
          <span className={`round-card-winner ${winner}`} data-testid="round-card-winner">
            {WINNER_LABEL[winner]}
          </span>
        )}
        <span className="round-card-metrics">
          {starMs != null && <span>⭐ {starMs}ms</span>}
          {hierMs != null && <span>🏛 {hierMs}ms</span>}
        </span>
      </button>

      {expanded && <RoundDetail snapshot={round.snapshot} />}
    </div>
  );
}
