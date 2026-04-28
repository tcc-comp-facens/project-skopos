import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ArchitecturePanel } from './ArchitecturePanel';
import type { BenchmarkMetrics } from '../types';

const baseBenchmarks: BenchmarkMetrics = {
  architecture: 'star',
  totalExecutionTimeMs: 1500,
  agentMetrics: [
    { agentName: 'consultor', executionTimeMs: 600, cpuPercent: 12.3 },
    { agentName: 'analisador', executionTimeMs: 900, cpuPercent: 25.1 },
  ],
};

describe('ArchitecturePanel', () => {
  it('renders the title', () => {
    render(
      <ArchitecturePanel title="Estrela" text="" benchmarks={null} isLoading={false} error={null} />
    );
    expect(screen.getByTestId('panel-title')).toHaveTextContent('Estrela');
  });

  it('shows placeholder when idle with no text', () => {
    render(
      <ArchitecturePanel title="Estrela" text="" benchmarks={null} isLoading={false} error={null} />
    );
    expect(screen.getByText('Aguardando análise...')).toBeInTheDocument();
  });

  it('displays streaming text in the text box', () => {
    render(
      <ArchitecturePanel title="Estrela" text="Análise parcial..." benchmarks={null} isLoading={true} error={null} />
    );
    expect(screen.getByTestId('panel-text-box')).toHaveTextContent('Análise parcial...');
  });

  it('shows loading indicator when isLoading is true', () => {
    render(
      <ArchitecturePanel title="Estrela" text="texto" benchmarks={null} isLoading={true} error={null} />
    );
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });

  it('hides loading indicator when isLoading is false', () => {
    render(
      <ArchitecturePanel title="Estrela" text="texto" benchmarks={null} isLoading={false} error={null} />
    );
    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('displays error message when error is provided', () => {
    render(
      <ArchitecturePanel title="Estrela" text="" benchmarks={null} isLoading={false} error="Conexão perdida" />
    );
    const errorEl = screen.getByTestId('panel-error');
    expect(errorEl).toHaveTextContent('Conexão perdida');
    expect(errorEl).toHaveAttribute('role', 'alert');
  });

  it('does not render error section when error is null', () => {
    render(
      <ArchitecturePanel title="Estrela" text="" benchmarks={null} isLoading={false} error={null} />
    );
    expect(screen.queryByTestId('panel-error')).not.toBeInTheDocument();
  });

  it('does not render benchmarks when null', () => {
    render(
      <ArchitecturePanel title="Estrela" text="done" benchmarks={null} isLoading={false} error={null} />
    );
    expect(screen.queryByTestId('panel-benchmarks')).not.toBeInTheDocument();
  });

  it('renders benchmark total time', () => {
    render(
      <ArchitecturePanel title="Estrela" text="done" benchmarks={baseBenchmarks} isLoading={false} error={null} />
    );
    expect(screen.getByTestId('total-time')).toHaveTextContent('Tempo total: 1500ms');
  });

  it('renders per-agent metrics in a table', () => {
    render(
      <ArchitecturePanel title="Estrela" text="done" benchmarks={baseBenchmarks} isLoading={false} error={null} />
    );
    expect(screen.getByTestId('agent-metrics-table')).toBeInTheDocument();
    expect(screen.getByTestId('agent-row-consultor')).toBeInTheDocument();
    expect(screen.getByTestId('agent-row-analisador')).toBeInTheDocument();

    // Check consultor row values
    const consultorRow = screen.getByTestId('agent-row-consultor');
    expect(consultorRow).toHaveTextContent('consultor');
    expect(consultorRow).toHaveTextContent('600');
    expect(consultorRow).toHaveTextContent('12.3');
    expect(consultorRow).toHaveTextContent('45.6');
  });

  it('uses correct data-testid based on lowercase title', () => {
    render(
      <ArchitecturePanel title="Hierárquica" text="" benchmarks={null} isLoading={false} error={null} />
    );
    expect(screen.getByTestId('architecture-panel-hierárquica')).toBeInTheDocument();
  });

  it('text box has scrollable container', () => {
    render(
      <ArchitecturePanel title="Estrela" text="long text" benchmarks={null} isLoading={false} error={null} />
    );
    const textBox = screen.getByTestId('panel-text-box');
    expect(textBox.style.overflowY).toBe('auto');
  });
});
