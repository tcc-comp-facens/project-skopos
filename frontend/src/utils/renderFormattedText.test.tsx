/**
 * Tests for FormattedText.
 * Validates the safe Markdown-lite parser used by MessageBubble: bold,
 * italic, headings, bullets, and — critically — that it never injects
 * real HTML, even when the LLM-generated text contains tag-like strings.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FormattedText } from './renderFormattedText';

describe('FormattedText', () => {
  it('renders plain text with no Markdown unchanged', () => {
    render(<FormattedText content="Análise concluída sem achados relevantes." />);
    expect(screen.getByText('Análise concluída sem achados relevantes.')).toBeInTheDocument();
  });

  it('renders **bold** as a <strong> element', () => {
    const { container } = render(<FormattedText content="Gasto **muito alto** em vigilância." />);
    const strong = container.querySelector('strong');
    expect(strong).not.toBeNull();
    expect(strong?.textContent).toBe('muito alto');
  });

  it('renders *italic* as an <em> element', () => {
    const { container } = render(<FormattedText content="Isso é *importante* notar." />);
    const em = container.querySelector('em');
    expect(em).not.toBeNull();
    expect(em?.textContent).toBe('importante');
  });

  it('does not treat a lone asterisk as formatting', () => {
    render(<FormattedText content="A conta é 5 * 3 = 15." />);
    expect(screen.getByText(/A conta é 5 \* 3 = 15\./)).toBeInTheDocument();
  });

  it('renders a heading line (#) with heading styling', () => {
    const { container } = render(<FormattedText content="# O que os dados mostram" />);
    const heading = container.querySelector('.chat-text-heading');
    expect(heading?.textContent).toBe('O que os dados mostram');
  });

  it('renders a bullet line (-) with bullet styling', () => {
    const { container } = render(<FormattedText content="- gasto subiu 10% em 2021" />);
    const bullet = container.querySelector('.chat-text-bullet');
    expect(bullet?.textContent).toBe('gasto subiu 10% em 2021');
  });

  it('renders multiple lines as separate blocks', () => {
    const { container } = render(<FormattedText content={'Primeira linha\nSegunda linha'} />);
    const lines = container.querySelectorAll('.chat-text-line');
    expect(lines).toHaveLength(2);
    expect(lines[0].textContent).toBe('Primeira linha');
    expect(lines[1].textContent).toBe('Segunda linha');
  });

  it('inserts a spacer for blank lines between paragraphs', () => {
    const { container } = render(<FormattedText content={'Parágrafo 1\n\nParágrafo 2'} />);
    expect(container.querySelectorAll('.chat-text-spacer')).toHaveLength(1);
  });

  it('never renders a real <script> element, even if the text contains one', () => {
    const { container } = render(
      <FormattedText content="Texto malicioso: <script>alert(1)</script> fim." />,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  });

  it('never renders a real <img> element with an event handler', () => {
    const { container } = render(
      <FormattedText content='Veja: <img src=x onerror="alert(1)"> depois disso.' />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/<img src=x onerror="alert\(1\)">/)).toBeInTheDocument();
  });
});
