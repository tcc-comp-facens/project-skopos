/**
 * Tests for RoundOverview component.
 * Validates the empty state and that all rounds render at once, with only
 * the newest one expanded by default.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RoundOverview } from './RoundOverview';
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

describe('RoundOverview', () => {
  it('shows empty state when there are no rounds yet', () => {
    render(<RoundOverview rounds={[]} />);
    expect(screen.getByTestId('round-overview-empty')).toBeInTheDocument();
  });

  it('renders one RoundCard per round', () => {
    const rounds = [makeRound('round-1', 'pergunta 1'), makeRound('round-2', 'pergunta 2')];
    render(<RoundOverview rounds={rounds} />);

    expect(screen.getByTestId('round-card-round-1')).toBeInTheDocument();
    expect(screen.getByTestId('round-card-round-2')).toBeInTheDocument();
  });

  it('expands only the newest round by default', () => {
    const rounds = [makeRound('round-1', 'pergunta 1'), makeRound('round-2', 'pergunta 2')];
    render(<RoundOverview rounds={rounds} />);

    const firstDetails = screen
      .getByTestId('round-card-round-1')
      .querySelector('[data-testid="round-detail"]');
    const secondDetails = screen
      .getByTestId('round-card-round-2')
      .querySelector('[data-testid="round-detail"]');

    expect(firstDetails).not.toBeInTheDocument();
    expect(secondDetails).toBeInTheDocument();
  });

  it('shows all rounds together (session panorama), not one at a time', () => {
    const rounds = [
      makeRound('round-1', 'pergunta 1'),
      makeRound('round-2', 'pergunta 2'),
      makeRound('round-3', 'pergunta 3'),
    ];
    render(<RoundOverview rounds={rounds} />);

    expect(screen.getByText('Rodada 1')).toBeInTheDocument();
    expect(screen.getByText('Rodada 2')).toBeInTheDocument();
    expect(screen.getByText('Rodada 3')).toBeInTheDocument();
  });
});
