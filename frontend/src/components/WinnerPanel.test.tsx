import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { WinnerPanel } from './WinnerPanel';

describe('WinnerPanel', () => {
  it('renders the winner panel', () => {
    render(
      <WinnerPanel
        winner="star"
        starText="Análise da Estrela"
        hierText="Análise da Hierárquica"
        starError={null}
        hierError={null}
      />,
    );
    expect(screen.getByTestId('winner-panel')).toBeInTheDocument();
  });

  it('displays ⭐ badge when winner="star"', () => {
    render(
      <WinnerPanel
        winner="star"
        starText="Análise da Estrela"
        hierText="Análise da Hierárquica"
        starError={null}
        hierError={null}
      />,
    );
    const badge = screen.getByTestId('winner-badge');
    expect(badge.textContent).toContain('⭐');
  });

  it('displays 🏛 badge when winner="hierarchical"', () => {
    render(
      <WinnerPanel
        winner="hierarchical"
        starText="Análise da Estrela"
        hierText="Análise da Hierárquica"
        starError={null}
        hierError={null}
      />,
    );
    const badge = screen.getByTestId('winner-badge');
    expect(badge.textContent).toContain('🏛');
  });

  it('displays star text when winner="star"', () => {
    render(
      <WinnerPanel
        winner="star"
        starText="Texto da Estrela"
        hierText="Texto da Hierárquica"
        starError={null}
        hierError={null}
      />,
    );
    expect(screen.getByTestId('panel-text-box').textContent).toContain('Texto da Estrela');
  });

  it('displays hierarchical text when winner="hierarchical"', () => {
    render(
      <WinnerPanel
        winner="hierarchical"
        starText="Texto da Estrela"
        hierText="Texto da Hierárquica"
        starError={null}
        hierError={null}
      />,
    );
    expect(screen.getByTestId('panel-text-box').textContent).toContain('Texto da Hierárquica');
  });

  it('does not render benchmarks section', () => {
    render(
      <WinnerPanel
        winner="star"
        starText="Texto"
        hierText="Texto"
        starError={null}
        hierError={null}
      />,
    );
    expect(screen.queryByTestId('panel-benchmarks')).not.toBeInTheDocument();
  });

  it('shows error when star architecture has error and winner="star"', () => {
    render(
      <WinnerPanel
        winner="star"
        starText=""
        hierText=""
        starError="Erro na análise Estrela"
        hierError={null}
      />,
    );
    expect(screen.getByTestId('panel-error')).toHaveTextContent('Erro na análise Estrela');
  });

  it('shows error when hierarchical architecture has error and winner="hierarchical"', () => {
    render(
      <WinnerPanel
        winner="hierarchical"
        starText=""
        hierText=""
        starError={null}
        hierError="Erro na análise Hierárquica"
      />,
    );
    expect(screen.getByTestId('panel-error')).toHaveTextContent('Erro na análise Hierárquica');
  });
});
