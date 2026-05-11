/**
 * Tests for WinnerPanel component.
 * Validates display of winning architecture's text and error handling.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WinnerPanel } from './WinnerPanel';

describe('WinnerPanel', () => {
  it('displays star text when winner is star', () => {
    render(
      <WinnerPanel
        winner="star"
        starText="Análise estrela completa"
        hierText="Análise hierárquica"
        starError={null}
        hierError={null}
      />
    );
    expect(screen.getByTestId('panel-text-box')).toHaveTextContent('Análise estrela completa');
  });

  it('displays hierarchical text when winner is hierarchical', () => {
    render(
      <WinnerPanel
        winner="hierarchical"
        starText="Análise estrela"
        hierText="Análise hierárquica completa"
        starError={null}
        hierError={null}
      />
    );
    expect(screen.getByTestId('panel-text-box')).toHaveTextContent('Análise hierárquica completa');
  });

  it('shows placeholder when text is empty', () => {
    render(
      <WinnerPanel
        winner="star"
        starText=""
        hierText=""
        starError={null}
        hierError={null}
      />
    );
    expect(screen.getByTestId('panel-text-box')).toHaveTextContent('Aguardando análise...');
  });

  it('displays error when winner architecture has error', () => {
    render(
      <WinnerPanel
        winner="star"
        starText=""
        hierText=""
        starError="Neo4j timeout"
        hierError={null}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Neo4j timeout');
  });

  it('shows title Estrela for star winner', () => {
    render(
      <WinnerPanel
        winner="star"
        starText="text"
        hierText=""
        starError={null}
        hierError={null}
      />
    );
    expect(screen.getByText('Estrela')).toBeInTheDocument();
  });

  it('shows title Hierárquica for hierarchical winner', () => {
    render(
      <WinnerPanel
        winner="hierarchical"
        starText=""
        hierText="text"
        starError={null}
        hierError={null}
      />
    );
    expect(screen.getByText('Hierárquica')).toBeInTheDocument();
  });
});
