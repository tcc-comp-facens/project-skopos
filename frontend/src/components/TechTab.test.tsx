/**
 * Tests for TechTab — navegação por rodada de chat.
 * Garante que trocar de rodada no seletor exibe os dados daquela rodada
 * (e não sempre da mais recente), e que a rodada mais nova é selecionada
 * automaticamente quando surge.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TechTab } from './TechTab';
import { INITIAL_STATE } from '../hooks/useWebSocket';
import type { ChatRound } from '../types';

function makeRound(
  id: string,
  question: string,
  starText: string,
  hierText: string,
): ChatRound {
  return {
    id,
    question,
    startedAt: new Date().toISOString(),
    snapshot: { ...INITIAL_STATE, starText, hierText },
  };
}

describe('TechTab', () => {
  const defaultProps = {
    useLlm: true,
    useLlmJudge: false,
    onUseLlmChange: vi.fn(),
    onUseLlmJudgeChange: vi.fn(),
    isActiveRoundRunning: false,
  };

  it('shows empty state when there are no rounds yet', () => {
    render(<TechTab {...defaultProps} rounds={[]} />);
    expect(screen.getByTestId('round-selector-empty')).toBeInTheDocument();
  });

  it('auto-selects the newest round and displays its data', () => {
    const rounds = [
      makeRound('round-1', 'pergunta 1', 'texto estrela 1', 'texto hier 1'),
      makeRound('round-2', 'pergunta 2', 'texto estrela 2', 'texto hier 2'),
    ];
    render(<TechTab {...defaultProps} rounds={rounds} />);

    expect(screen.getByText('texto estrela 2')).toBeInTheDocument();
    expect(screen.getByText('texto hier 2')).toBeInTheDocument();
  });

  it('switching the selected round updates the displayed panels', () => {
    const rounds = [
      makeRound('round-1', 'pergunta 1', 'texto estrela 1', 'texto hier 1'),
      makeRound('round-2', 'pergunta 2', 'texto estrela 2', 'texto hier 2'),
    ];
    render(<TechTab {...defaultProps} rounds={rounds} />);

    fireEvent.change(screen.getByTestId('round-selector-select'), {
      target: { value: 'round-1' },
    });

    expect(screen.getByText('texto estrela 1')).toBeInTheDocument();
    expect(screen.getByText('texto hier 1')).toBeInTheDocument();
  });
});
