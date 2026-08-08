/**
 * Tests for AgentDataCard component.
 * Validates the collapsed/expanded per-agent card: chart, Cypher query and
 * raw-rows table.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AgentDataCard } from './AgentDataCard';
import type { AgentDataEntry } from '../types';

function makeAgent(overrides: Partial<AgentDataEntry> = {}): AgentDataEntry {
  return {
    agentName: 'sinan',
    agentLabel: 'SINAN',
    queries: [
      {
        query: 'MATCH (i:IndicadorSaude) RETURN i',
        params: { sistema: 'SINAN' },
        rowCount: 2,
        rows: [
          { tipo: 'dengue', ano: 2019, valor: 10 },
          { tipo: 'dengue', ano: 2020, valor: 25 },
        ],
      },
    ],
    ...overrides,
  };
}

describe('AgentDataCard', () => {
  it('renders collapsed by default with label and row count', () => {
    render(<AgentDataCard agent={makeAgent()} />);
    expect(screen.getByText('SINAN')).toBeInTheDocument();
    expect(screen.getByText('2 registros')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-data-query-text')).not.toBeInTheDocument();
  });

  it('expands to show chart, query text and raw rows on click', () => {
    render(<AgentDataCard agent={makeAgent()} />);
    fireEvent.click(screen.getByTestId('agent-data-card-toggle-sinan'));

    expect(screen.getByTestId('agent-data-chart')).toBeInTheDocument();
    expect(screen.getByTestId('agent-data-query-text')).toHaveTextContent(
      'MATCH (i:IndicadorSaude) RETURN i',
    );
    expect(screen.getByTestId('agent-data-table')).toBeInTheDocument();
    expect(screen.getAllByText('dengue')).toHaveLength(2);
  });

  it('collapses again on second click', () => {
    render(<AgentDataCard agent={makeAgent()} />);
    const toggle = screen.getByTestId('agent-data-card-toggle-sinan');
    fireEvent.click(toggle);
    expect(screen.getByTestId('agent-data-table')).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByTestId('agent-data-table')).not.toBeInTheDocument();
  });

  it('falls back to a message instead of a chart when rows have no plottable value', () => {
    const agent = makeAgent({
      queries: [
        {
          query: 'MATCH (v) RETURN v',
          params: {},
          rowCount: 1,
          rows: [{ classificacao: 'estavel' }],
        },
      ],
    });
    render(<AgentDataCard agent={agent} />);
    fireEvent.click(screen.getByTestId('agent-data-card-toggle-sinan'));

    expect(screen.queryByTestId('agent-data-chart')).not.toBeInTheDocument();
    expect(screen.getByText('Sem dado numérico para gráfico.')).toBeInTheDocument();
  });

  it('shows a truncation note when rows exceed the visible cap', () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({ ano: 2000 + i, valor: i }));
    const agent = makeAgent({
      queries: [{ query: 'MATCH (x) RETURN x', params: {}, rowCount: 60, rows }],
    });
    render(<AgentDataCard agent={agent} />);
    fireEvent.click(screen.getByTestId('agent-data-card-toggle-sinan'));

    expect(screen.getByText('Mostrando 50 de 60 registros.')).toBeInTheDocument();
  });

  it('uses a singular label for exactly one row', () => {
    const agent = makeAgent({
      queries: [
        {
          query: 'MATCH (i) RETURN i',
          params: {},
          rowCount: 1,
          rows: [{ ano: 2020, valor: 1 }],
        },
      ],
    });
    render(<AgentDataCard agent={agent} />);
    expect(screen.getByText('1 registro')).toBeInTheDocument();
  });
});
