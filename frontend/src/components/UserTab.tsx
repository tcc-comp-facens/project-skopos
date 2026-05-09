import { AnalysisControls } from './AnalysisControls';
import { WinnerPanel } from './WinnerPanel';
import type { AnalysisRequest } from '../types';

/**
 * UserTab — aba pública destinada ao público geral e servidores públicos.
 * Exibe os controles de análise e o resultado da arquitetura vencedora.
 *
 * Requirements: 3.6, 4.1, 4.2, 4.3, 4.5, 10.3, 10.4
 */
export interface UserTabProps {
  // Estado da análise
  apiError: string | null;
  submitting: boolean;
  // Estado WebSocket (apenas o necessário para a aba usuário)
  starText: string;
  hierText: string;
  starLoading: boolean;
  hierLoading: boolean;
  starError: string | null;
  hierError: string | null;
  winner: 'star' | 'hierarchical' | null;
  // Callbacks
  onSubmit: (request: Omit<AnalysisRequest, 'useLlm' | 'useLlmJudge'>) => void;
}

export function UserTab({
  apiError,
  submitting,
  starText,
  hierText,
  starLoading,
  hierLoading,
  starError,
  hierError,
  winner,
  onSubmit,
}: UserTabProps): JSX.Element {
  const isLoading = starLoading || hierLoading;
  const bothFailed = !!(starError && hierError);

  return (
    <div
      className="user-tab"
      id="panel-user"
      role="tabpanel"
      aria-labelledby="tab-user"
      data-testid="user-tab"
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

      {/* Controles sempre visíveis */}
      <AnalysisControls onSubmit={onSubmit} />

      {/* Estado de carregamento */}
      {isLoading && (
        <div className="user-tab-loading" data-testid="user-tab-loading">
          <div className="spinner" />
          <span>Aguardando análise...</span>
        </div>
      )}

      {/* Resultado vencedor */}
      {!isLoading && winner !== null && (
        <WinnerPanel
          winner={winner}
          starText={starText}
          hierText={hierText}
          starError={starError}
          hierError={hierError}
        />
      )}

      {/* Erro em ambas as arquiteturas */}
      {!isLoading && winner === null && bothFailed && (
        <div className="user-tab-error" data-testid="user-tab-error" role="alert">
          Ocorreu um erro na análise. Por favor, tente novamente.
          {starError && <div>{starError}</div>}
          {hierError && <div>{hierError}</div>}
        </div>
      )}
    </div>
  );
}
