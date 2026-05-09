import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { UserTab } from './UserTab';

const defaultProps = {
  apiError: null,
  submitting: false,
  starText: '',
  hierText: '',
  starLoading: false,
  hierLoading: false,
  starError: null,
  hierError: null,
  winner: null as 'star' | 'hierarchical' | null,
  onSubmit: vi.fn(),
};

describe('UserTab', () => {
  it('renders the user tab container', () => {
    render(<UserTab {...defaultProps} />);
    expect(screen.getByTestId('user-tab')).toBeInTheDocument();
  });

  it('shows AnalysisControls in initial state (no loading, no winner, no errors)', () => {
    render(<UserTab {...defaultProps} />);
    expect(screen.getByTestId('analysis-controls')).toBeInTheDocument();
  });

  it('shows loading indicator when starLoading=true', () => {
    render(<UserTab {...defaultProps} starLoading={true} />);
    expect(screen.getByTestId('user-tab-loading')).toBeInTheDocument();
    expect(screen.getByText('Aguardando análise...')).toBeInTheDocument();
  });

  it('shows loading indicator when hierLoading=true', () => {
    render(<UserTab {...defaultProps} hierLoading={true} />);
    expect(screen.getByTestId('user-tab-loading')).toBeInTheDocument();
  });

  it('shows AnalysisControls even while loading', () => {
    render(<UserTab {...defaultProps} starLoading={true} />);
    expect(screen.getByTestId('analysis-controls')).toBeInTheDocument();
  });

  it('shows WinnerPanel when winner is "star" and not loading', () => {
    render(
      <UserTab
        {...defaultProps}
        winner="star"
        starText="Análise da Estrela completa"
        starLoading={false}
        hierLoading={false}
      />,
    );
    expect(screen.getByTestId('winner-panel')).toBeInTheDocument();
  });

  it('shows WinnerPanel when winner is "hierarchical" and not loading', () => {
    render(
      <UserTab
        {...defaultProps}
        winner="hierarchical"
        hierText="Análise da Hierárquica completa"
        starLoading={false}
        hierLoading={false}
      />,
    );
    expect(screen.getByTestId('winner-panel')).toBeInTheDocument();
  });

  it('shows AnalysisControls even when winner is set', () => {
    render(<UserTab {...defaultProps} winner="star" starText="texto" />);
    expect(screen.getByTestId('analysis-controls')).toBeInTheDocument();
  });

  it('shows error when both architectures failed', () => {
    render(
      <UserTab
        {...defaultProps}
        starError="Erro Estrela"
        hierError="Erro Hierárquica"
        starLoading={false}
        hierLoading={false}
      />,
    );
    expect(screen.getByTestId('user-tab-error')).toBeInTheDocument();
    expect(screen.getByRole('alert', { name: '' })).toBeInTheDocument();
  });

  it('does not show error when only one architecture failed', () => {
    render(
      <UserTab
        {...defaultProps}
        starError="Erro Estrela"
        hierError={null}
        starLoading={false}
        hierLoading={false}
      />,
    );
    expect(screen.queryByTestId('user-tab-error')).not.toBeInTheDocument();
  });

  it('shows apiError when present', () => {
    render(<UserTab {...defaultProps} apiError="Erro de conexão" />);
    expect(screen.getByTestId('api-error')).toHaveTextContent('Erro de conexão');
  });

  it('shows submitting indicator when submitting=true', () => {
    render(<UserTab {...defaultProps} submitting={true} />);
    expect(screen.getByTestId('submitting-indicator')).toBeInTheDocument();
  });

  it('does not show LLM controls', () => {
    render(<UserTab {...defaultProps} />);
    expect(screen.queryByTestId('llm-controls')).not.toBeInTheDocument();
    expect(screen.queryByTestId('llm-toggle')).not.toBeInTheDocument();
  });
});
