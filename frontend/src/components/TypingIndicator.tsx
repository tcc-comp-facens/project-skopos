/**
 * TypingIndicator — pontinhos animados via CSS, sem intervalo em JS.
 *
 * Requirements: 2.2 (spec realtime-chat-interface)
 */
export function TypingIndicator(): JSX.Element {
  return (
    <span className="typing-indicator" data-testid="typing-indicator" aria-label="Digitando">
      <span />
      <span />
      <span />
    </span>
  );
}
