import { ChatInterface } from './ChatInterface';
import type { UseWebSocketState } from '../types';

/**
 * UserTab — aba pública destinada ao público geral e servidores públicos.
 * O chat substitui o formulário AnalysisControls; a resposta da análise
 * (texto completo da arquitetura vencedora) aparece como uma mensagem
 * normal dentro da própria conversa (ChatInterface), não mais num card
 * separado abaixo dela.
 *
 * Requirements: 3.6, 4.1, 4.2, 4.3, 4.5, 10.3, 10.4 + spec realtime-chat-interface
 */
export interface UserTabProps {
  onAnalysisStarted: (analysisId: string, question: string) => void;
  activeRoundId: string | null;
  activeRoundWs: UseWebSocketState | null;
}

export function UserTab({
  onAnalysisStarted,
  activeRoundId,
  activeRoundWs,
}: UserTabProps): JSX.Element {
  return (
    <div
      className="user-tab"
      id="panel-user"
      role="tabpanel"
      aria-labelledby="tab-user"
      data-testid="user-tab"
    >
      <ChatInterface
        onAnalysisStarted={onAnalysisStarted}
        activeRoundId={activeRoundId}
        activeRoundWs={activeRoundWs}
      />
    </div>
  );
}
