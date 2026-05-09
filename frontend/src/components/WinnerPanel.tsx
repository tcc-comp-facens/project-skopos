import { useEffect, useRef } from 'react';

/**
 * WinnerPanel — exibe o resultado da arquitetura vencedora.
 * Painel único com borda dourada, sem wrappers extras.
 *
 * Requirements: 4.3, 4.4, 4.6
 */
export interface WinnerPanelProps {
  winner: 'star' | 'hierarchical';
  starText: string;
  hierText: string;
  starError: string | null;
  hierError: string | null;
}

export function WinnerPanel({
  winner,
  starText,
  hierText,
  starError,
  hierError,
}: WinnerPanelProps): JSX.Element {
  const isStar = winner === 'star';
  const title = isStar ? 'Estrela' : 'Hierárquica';
  const text = isStar ? starText : hierText;
  const error = isStar ? starError : hierError;
  const textBoxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (textBoxRef.current) {
      textBoxRef.current.scrollTop = textBoxRef.current.scrollHeight;
    }
  }, [text]);

  return (
    <div className="winner-panel" data-testid="winner-panel">
      <div className="panel-header">
        <div className={`panel-icon ${isStar ? 'star' : 'hier'}`}>
          {isStar ? '⭐' : '🏛'}
        </div>
        <h2 className="panel-title">{title}</h2>
      </div>

      {error && (
        <div className="panel-error" role="alert">{error}</div>
      )}

      <div
        ref={textBoxRef}
        className="panel-text-box"
        data-testid="panel-text-box"
        aria-live="polite"
      >
        {text || <span className="placeholder-text">Aguardando análise...</span>}
      </div>
    </div>
  );
}
