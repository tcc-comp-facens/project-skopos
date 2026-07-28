export const MAX_CHAT_MESSAGE_LENGTH = 1000;

/**
 * Verifica se uma mensagem de chat é apenas espaço em branco (ou vazia).
 *
 * Requirements: 1.4 (spec realtime-chat-interface)
 */
export function isBlank(text: string): boolean {
  return text.trim().length === 0;
}

/**
 * Verifica se a mensagem excede o limite de tamanho aceito pelo backend.
 */
export function isTooLong(text: string): boolean {
  return text.length > MAX_CHAT_MESSAGE_LENGTH;
}
