import type { UseWebSocketState } from '../types';

/**
 * Monta o texto de fallback da bolha de resposta de uma rodada de chat
 * quando ainda não há texto completo pra mostrar — enquanto a análise
 * está rodando, ou quando as duas arquiteturas falham e não há vencedor
 * pra exibir na íntegra (ver ChatInterface: o caso de sucesso substitui
 * essa bolha pelo texto completo da arquitetura vencedora, não usa mais
 * este resumo).
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

  return 'Análise concluída, mas não foi possível determinar um resultado completo. Veja os detalhes técnicos na aba Agentes.';
}
