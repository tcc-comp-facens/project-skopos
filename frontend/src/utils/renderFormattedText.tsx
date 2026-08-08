import type { ReactNode } from 'react';

/**
 * FormattedText — parser minimalista de Markdown básico (negrito, itálico,
 * cabeçalhos, listas) para o texto gerado pelo LLM (sintetizador pede
 * "títulos claros" no prompt, e LLMs tipicamente respondem em Markdown).
 *
 * Nunca usa `dangerouslySetInnerHTML` — só constrói elementos React a
 * partir de texto puro (mesma garantia de segurança que MessageBubble já
 * tinha antes desta função existir): uma tentativa de injetar
 * `<script>`/`<img onerror=...>` no texto simplesmente aparece como texto
 * literal na tela, nunca é interpretada como HTML real.
 */

// Ordem importa: **negrito** é testado antes de *itálico* na mesma
// alternância, então "**x**" nunca é lido como itálico de "*x*".
const INLINE_REGEX = /\*\*(.+?)\*\*|\*(.+?)\*/g;

function parseInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let idx = 0;
  INLINE_REGEX.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = INLINE_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    if (match[1] !== undefined) {
      nodes.push(<strong key={`${keyPrefix}-b-${idx}`}>{match[1]}</strong>);
    } else if (match[2] !== undefined) {
      nodes.push(<em key={`${keyPrefix}-i-${idx}`}>{match[2]}</em>);
    }
    lastIndex = INLINE_REGEX.lastIndex;
    idx += 1;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

const HEADING_RE = /^#{1,6}\s+(.*)$/;
const BULLET_RE = /^[-•*]\s+(.*)$/;

export interface FormattedTextProps {
  content: string;
}

export function FormattedText({ content }: FormattedTextProps): JSX.Element {
  const lines = content.split('\n');

  return (
    <>
      {lines.map((line, i) => {
        const key = `line-${i}`;
        const trimmed = line.trim();

        if (trimmed === '') {
          return <div key={key} className="chat-text-spacer" />;
        }

        const headingMatch = HEADING_RE.exec(trimmed);
        if (headingMatch) {
          return (
            <p key={key} className="chat-text-heading">
              {parseInline(headingMatch[1], key)}
            </p>
          );
        }

        const bulletMatch = BULLET_RE.exec(trimmed);
        if (bulletMatch) {
          return (
            <p key={key} className="chat-text-bullet">
              {parseInline(bulletMatch[1], key)}
            </p>
          );
        }

        return (
          <p key={key} className="chat-text-line">
            {parseInline(line, key)}
          </p>
        );
      })}
    </>
  );
}
