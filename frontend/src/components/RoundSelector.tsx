import type { ChatRound } from '../types';

/**
 * RoundSelector — permite inspecionar, na aba Agentes, os resultados
 * técnicos de qualquer rodada de chat já disparada (não só a mais
 * recente). Substitui a suposição antiga de "sempre a última análise".
 *
 * Requirements: reformulação da aba Agentes (não coberta pela spec
 * realtime-chat-interface original — ver plano).
 */
export interface RoundSelectorProps {
  rounds: ChatRound[];
  selectedRoundId: string | null;
  onSelectRound: (roundId: string) => void;
}

function truncate(text: string, max = 60): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export function RoundSelector({
  rounds,
  selectedRoundId,
  onSelectRound,
}: RoundSelectorProps): JSX.Element {
  if (rounds.length === 0) {
    return (
      <div className="round-selector" data-testid="round-selector">
        <span className="round-selector-label">Rodada</span>
        <span className="round-selector-empty" data-testid="round-selector-empty">
          Nenhuma pergunta feita ainda — use o chat na aba Saúde.
        </span>
      </div>
    );
  }

  return (
    <div className="round-selector" data-testid="round-selector">
      <span className="round-selector-label">Rodada</span>
      <select
        value={selectedRoundId ?? ''}
        onChange={(e) => onSelectRound(e.target.value)}
        data-testid="round-selector-select"
        aria-label="Selecionar rodada de análise"
      >
        {rounds.map((round, index) => (
          <option key={round.id} value={round.id} data-testid={`round-option-${round.id}`}>
            {`Rodada ${index + 1} — ${formatTime(round.startedAt)} — ${truncate(round.question)}`}
          </option>
        ))}
      </select>
    </div>
  );
}
