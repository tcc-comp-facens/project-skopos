import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ComparativeSection } from './ComparativeSection';

describe('ComparativeSection', () => {
  it('does not render when report is empty and not loading', () => {
    const { container } = render(
      <ComparativeSection
        comparativeReport=""
        comparativeLoading={false}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders when comparativeLoading is true even with empty report', () => {
    render(
      <ComparativeSection
        comparativeReport=""
        comparativeLoading={true}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    expect(screen.getByTestId('comparative-section')).toBeInTheDocument();
  });

  it('renders when report has content', () => {
    render(
      <ComparativeSection
        comparativeReport="Análise completa"
        comparativeLoading={false}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    expect(screen.getByTestId('comparative-section')).toBeInTheDocument();
    expect(screen.getByTestId('comparative-report')).toBeInTheDocument();
  });

  it('shows loading cursor while streaming report', () => {
    render(
      <ComparativeSection
        comparativeReport="Texto parcial"
        comparativeLoading={true}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    expect(screen.getByTestId('comparative-loading')).toBeInTheDocument();
    expect(screen.getByTestId('comparative-loading').textContent).toBe('▍');
  });

  it('does not show loading cursor when not loading', () => {
    render(
      <ComparativeSection
        comparativeReport="Texto completo"
        comparativeLoading={false}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    expect(screen.queryByTestId('comparative-loading')).not.toBeInTheDocument();
  });

  it('formats → lines as report-verdict', () => {
    render(
      <ComparativeSection
        comparativeReport="→ Arquitetura Estrela venceu com 5 pontos"
        comparativeLoading={false}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    const verdict = screen.getByText('→ Arquitetura Estrela venceu com 5 pontos');
    expect(verdict.className).toBe('report-verdict');
  });

  it('formats ━━━ lines as section titles', () => {
    render(
      <ComparativeSection
        comparativeReport="━━━ Seção de Análise ━━━"
        comparativeLoading={false}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    const title = screen.getByText('Seção de Análise');
    expect(title.className).toBe('report-section-title');
  });

  it('renders LLM Judge section when llmJudgeText is present', () => {
    render(
      <ComparativeSection
        comparativeReport="Relatório"
        comparativeLoading={false}
        llmJudgeText="Avaliação do juiz"
        llmJudgeLoading={false}
      />,
    );
    expect(screen.getByTestId('llm-judge-report')).toBeInTheDocument();
  });

  it('does not render LLM Judge section when both text and loading are falsy', () => {
    render(
      <ComparativeSection
        comparativeReport="Relatório"
        comparativeLoading={false}
        llmJudgeText=""
        llmJudgeLoading={false}
      />,
    );
    expect(screen.queryByTestId('llm-judge-report')).not.toBeInTheDocument();
  });
});
