import { ChatInterface } from './ChatInterface';
import { WinnerPanel } from './WinnerPanel';
import type { UseWebSocketState, WinnerArchitecture } from '../types';

/**
 * UserTab — aba pública destinada ao público geral e servidores públicos.
 * O chat substitui o formulário AnalysisControls; o resultado da rodada
 * mais recente continua exibido em destaque via WinnerPanel abaixo da
 * conversa, para manter a leitura completa e acessível ao público leigo.
 *
 * Requirements: 3.6, 4.1, 4.2, 4.3, 4.5, 10.3, 10.4 + spec realtime-chat-interface
 */
export interface UserTabProps {
  useLlm: boolean;
  useLlmJudge: boolean;
  onAnalysisStarted: (analysisId: string, question: string) => void;
  activeRoundId: string | null;
  activeRoundWs: UseWebSocketState | null;
  winner: WinnerArchitecture;
}

export function UserTab({
  useLlm,
  useLlmJudge,
  onAnalysisStarted,
  activeRoundId,
  activeRoundWs,
  winner,
}: UserTabProps): JSX.Element {
  const isLoading =
    !!activeRoundWs && (activeRoundWs.starLoading || activeRoundWs.hierLoading);
  const bothFailed =
    !!activeRoundWs && !!(activeRoundWs.starError && activeRoundWs.hierError);

  return (
    <div
      className="user-tab"
      id="panel-user"
      role="tabpanel"
      aria-labelledby="tab-user"
      data-testid="user-tab"
    >
      <ChatInterface
        useLlm={useLlm}
        useLlmJudge={useLlmJudge}
        onAnalysisStarted={onAnalysisStarted}
        activeRoundId={activeRoundId}
        activeRoundWs={activeRoundWs}
      />

      {isLoading && (
        <div className="user-tab-loading" data-testid="user-tab-loading">
          <div className="spinner" />
          <span>Aguardando análise...</span>
        </div>
      )}

      {!isLoading && activeRoundWs && winner !== null && (
        <WinnerPanel
          winner={winner}
          starText={activeRoundWs.starText}
          hierText={activeRoundWs.hierText}
          starError={activeRoundWs.starError}
          hierError={activeRoundWs.hierError}
        />
      )}

      {!isLoading && winner === null && bothFailed && (
        <div className="user-tab-error" data-testid="user-tab-error" role="alert">
          Ocorreu um erro na análise. Por favor, tente novamente.
          {activeRoundWs?.starError && <div>{activeRoundWs.starError}</div>}
          {activeRoundWs?.hierError && <div>{activeRoundWs.hierError}</div>}
        </div>
      )}
    </div>
  );
}
