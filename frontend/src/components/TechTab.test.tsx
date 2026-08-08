/**
 * Tests for TechTab — só o LLM Judge toggle + o panorama de todas as
 * rodadas da sessão (RoundOverview substituiu o antigo seletor de rodada
 * única — cobertura detalhada de expand/collapse está em
 * RoundOverview.test.tsx / RoundCard.test.tsx).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TechTab } from './TechTab';
import { INITIAL_STATE } from '../hooks/useWebSocket';
import type { ChatRound } from '../types';

function makeRound(id: string, question: string): ChatRound {
  return {
    id,
    question,
    startedAt: new Date().toISOString(),
    snapshot: { ...INITIAL_STATE },
  };
}

describe('TechTab', () => {
  const defaultProps = {
    useLlmJudge: false,
    onUseLlmJudgeChange: vi.fn(),
    isActiveRoundRunning: false,
  };

  it('renders only the LLM Judge toggle (LLM toggle was removed)', () => {
    render(<TechTab {...defaultProps} rounds={[]} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeInTheDocument();
    expect(screen.queryByTestId('llm-toggle-input')).not.toBeInTheDocument();
  });

  it('shows empty state when there are no rounds yet', () => {
    render(<TechTab {...defaultProps} rounds={[]} />);
    expect(screen.getByTestId('round-overview-empty')).toBeInTheDocument();
  });

  it('shows every round of the session at once (panorama), not just the latest', () => {
    const rounds = [makeRound('round-1', 'pergunta 1'), makeRound('round-2', 'pergunta 2')];
    render(<TechTab {...defaultProps} rounds={rounds} />);

    expect(screen.getByTestId('round-card-round-1')).toBeInTheDocument();
    expect(screen.getByTestId('round-card-round-2')).toBeInTheDocument();
  });
});
