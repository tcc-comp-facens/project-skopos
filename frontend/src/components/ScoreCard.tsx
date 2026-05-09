/**
 * ScoreCard — card visual para uma métrica individual de qualidade.
 * Exibe o identificador, nome descritivo e valores de ambas as arquiteturas.
 *
 * Requirements: 8.2, 8.4
 */
export interface ScoreCardProps {
  id: string;           // ex: "E1", "Q2", "R1"
  label: string;        // nome descritivo da métrica
  starValue: number | null;
  hierValue: number | null;
}

export function ScoreCard({ id, label, starValue, hierValue }: ScoreCardProps): JSX.Element {
  const formatValue = (v: number | null): string => {
    if (v === null || v === undefined) return '—';
    if (typeof v !== 'number') return String(v);
    return v.toFixed(2);
  };

  return (
    <div className="score-card" data-testid={`score-card-${id}`}>
      <div className="score-card-id">{id}</div>
      <div className="score-card-label">{label}</div>
      <div className="score-card-values">
        <span className="score-card-value-star" title="Estrela">
          ⭐ {formatValue(starValue)}
        </span>
        <span className="score-card-value-hier" title="Hierárquica">
          🏛 {formatValue(hierValue)}
        </span>
      </div>
    </div>
  );
}
