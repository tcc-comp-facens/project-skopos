/**
 * Tests for RoundCard component.
 * Validates the collapsed header summary and expand/collapse of the full
 * RoundDetail content.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RoundCard } from './RoundCard';
import { INITIAL_STATE } from '../hooks/useWebSocket';
import type { ChatRound } from '../types';

function makeRound(overrides: Partial<ChatRound> = {}): ChatRound {
  return {
    id: 'round-1',
    question: 'compare dengue e vacinação de 2019 a 2022',
    startedAt: new Date().toISOString(),
    snapshot: { ...INITIAL_STATE },
    ...overrides,
  };
}

describe('RoundCard', () => {
  it('shows the round summary header when collapsed', () => {
    const round = makeRound();
    render(<RoundCard round={round} index={0} defaultExpanded={false} />);

    expect(screen.getByText('Rodada 1')).toBeInTheDocument();
    expect(screen.getByText(/compare dengue e vacinação/)).toBeInTheDocument();
    expect(screen.queryByTestId('round-detail')).not.toBeInTheDocument();
  });

  it('renders RoundDetail when defaultExpanded is true', () => {
    const round = makeRound();
    render(<RoundCard round={round} index={0} defaultExpanded={true} />);
    expect(screen.getByTestId('round-detail')).toBeInTheDocument();
  });

  it('toggles RoundDetail visibility on header click', () => {
    const round = makeRound();
    render(<RoundCard round={round} index={1} defaultExpanded={false} />);
    const toggle = screen.getByTestId('round-card-toggle-round-1');

    fireEvent.click(toggle);
    expect(screen.getByTestId('round-detail')).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByTestId('round-detail')).not.toBeInTheDocument();
  });

  it('shows a winner badge when the comparative report has a verdict', () => {
    const round = makeRound({
      snapshot: {
        ...INITIAL_STATE,
        comparativeReport: '→ Arquitetura Estrela venceu com 5 pontos',
      },
    });
    render(<RoundCard round={round} index={0} defaultExpanded={false} />);
    expect(screen.getByTestId('round-card-winner')).toHaveTextContent('Estrela');
  });

  it('does not show a winner badge when there is no verdict yet', () => {
    const round = makeRound();
    render(<RoundCard round={round} index={0} defaultExpanded={false} />);
    expect(screen.queryByTestId('round-card-winner')).not.toBeInTheDocument();
  });
});
