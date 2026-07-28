import { TypingIndicator } from './TypingIndicator';
import type { ChatMessage } from '../types';

/**
 * MessageBubble — bolha de mensagem do chat (usuário à direita, sistema à
 * esquerda). Renderiza sempre como texto (nunca dangerouslySetInnerHTML),
 * mesmo para conteúdo vindo do LLM, para não abrir espaço a XSS.
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
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      className={classes}
      data-testid={`message-bubble-${message.role}`}
      role={message.isError ? 'alert' : undefined}
    >
      <div className="message-bubble-content">
        {message.content}
        {message.isStreaming && <TypingIndicator />}
      </div>
    </div>
  );
}
