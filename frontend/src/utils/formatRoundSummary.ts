import { parseWinner } from './parseWinner';
import type { UseWebSocketState } from '../types';

/**
 * Monta o texto da bolha de resumo de uma rodada de chat a partir do
 * estado do useWebSocket (mesma fonte de dados da aba Agentes) — o chat
 * não recebe um stream de resultado próprio, reaproveita o /ws/{analysisId}
 * existente (ver plano: dedicated chat WS cuida só do turno de intenção).
 *
 * Requirements: 6.2, 6.3 (spec realtime-chat-interface)
 */
export function formatRoundSummary(ws: UseWebSocketState, question: string): string {
  const isLoading = ws.starLoading || ws.hierLoading || ws.comparativeLoading;
  if (isLoading) {
    return 'Analisando os dados…';
  }

  const bothFailed = !!(ws.starError && ws.hierError);
  if (bothFailed) {
    return `Não consegui concluir a análise para "${question}". Tente reformular a pergunta.`;
  }

  const winner = parseWinner(ws.comparativeReport);
  if (!winner) {
    return 'Análise concluída. Veja o resultado abaixo.';
  }

  const winnerLabel = winner === 'star' ? 'Estrela ⭐' : 'Hierárquica 🏛';
  return (
    `Análise concluída — arquitetura vencedora: ${winnerLabel}. ` +
    'Veja o resultado completo abaixo e os detalhes técnicos na aba Agentes.'
  );
}
