import { TypingIndicator } from './TypingIndicator';
import { FormattedText } from '../utils/renderFormattedText';
import type { ChatMessage } from '../types';

/**
 * MessageBubble — bolha de mensagem do chat (usuário à direita, sistema à
 * esquerda). Conteúdo passa por FormattedText, que aplica Markdown básico
 * (negrito, itálico, listas) construindo só elementos React — nunca
 * dangerouslySetInnerHTML, mesmo para texto vindo do LLM, para não abrir
 * espaço a XSS.
 *
 * Requirements: 5.4, 7.6 (spec realtime-chat-interface)
 */
export interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps): JSX.Element {
  const isUser = message.role === 'user';
  const classes = [
    'message-bubble',
    isUser ? 'user' : 'system',
    message.isError ? 'error' : '',
    message.kind === 'architecture_answer' ? 'architecture-answer' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      className={classes}
      data-testid={`message-bubble-${message.role}`}
      role={message.isError ? 'alert' : undefined}
    >
      {message.architecture && (
        <span
          className={`message-bubble-winner-badge ${message.architecture}`}
          data-testid="message-bubble-winner-badge"
          aria-label={message.architecture === 'star' ? 'Arquitetura Estrela' : 'Arquitetura Hierárquica'}
        >
          {message.architecture === 'star' ? '⭐' : '🏛'}
        </span>
      )}
      <div className="message-bubble-content">
        <FormattedText content={message.content} />
        {message.isStreaming && <TypingIndicator />}
      </div>
    </div>
  );
}
