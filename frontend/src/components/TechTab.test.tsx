import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { TechTab } from './TechTab';
import type { UseWebSocketState } from '../hooks/useWebSocket';

const mockWs: UseWebSocketState = {
  starText: '',
  hierText: '',
  starBenchmarks: null,
  hierBenchmarks: null,
  starLoading: false,
  hierLoading: false,
  starError: null,
  hierError: null,
  comparativeReport: '',
  comparativeLoading: false,
  qualityMetrics: null,
  llmJudgeText: '',
  llmJudgeLoading: false,
};

const defaultProps = {
  useLlm: true,
  useLlmJudge: false,
  onUseLlmChange: vi.fn(),
  onUseLlmJudgeChange: vi.fn(),
  ws: mockWs,
  submitting: false,
  apiError: null,
};

describe('TechTab', () => {
  it('renders the tech tab container', () => {
    render(<TechTab {...defaultProps} />);
    expect(screen.getByTestId('tech-tab')).toBeInTheDocument();
  });

  it('renders LlmControls', () => {
    render(<TechTab {...defaultProps} />);
    expect(screen.getByTestId('llm-controls')).toBeInTheDocument();
  });

  it('renders both architecture panels', () => {
    render(<TechTab {...defaultProps} />);
    expect(screen.getByTestId('panels-container')).toBeInTheDocument();
    // Two panels: Hierárquica and Estrela
    expect(screen.getByText('Hierárquica')).toBeInTheDocument();
    expect(screen.getByText('Estrela')).toBeInTheDocument();
  });

  it('passes correct useLlm and useLlmJudge to LlmControls', () => {
    render(<TechTab {...defaultProps} useLlm={false} useLlmJudge={false} />);
    const llmInput = screen.getByTestId('llm-toggle-input') as HTMLInputElement;
    expect(llmInput.checked).toBe(false);
  });

  it('disables LlmControls when analysis is running', () => {
    render(
      <TechTab
        {...defaultProps}
        ws={{ ...mockWs, starLoading: true }}
      />,
    );
    const llmInput = screen.getByTestId('llm-toggle-input') as HTMLInputElement;
    expect(llmInput.disabled).toBe(true);
  });

  it('does not render AnalysisControls (date/health params)', () => {
    render(<TechTab {...defaultProps} />);
    expect(screen.queryByTestId('analysis-controls')).not.toBeInTheDocument();
  });

  it('does not render date fields', () => {
    render(<TechTab {...defaultProps} />);
    expect(screen.queryByTestId('date-from')).not.toBeInTheDocument();
    expect(screen.queryByTestId('date-to')).not.toBeInTheDocument();
  });

  it('shows apiError when present', () => {
    render(<TechTab {...defaultProps} apiError="Erro de API" />);
    expect(screen.getByTestId('api-error')).toHaveTextContent('Erro de API');
  });

  it('shows submitting indicator when submitting=true', () => {
    render(<TechTab {...defaultProps} submitting={true} />);
    expect(screen.getByTestId('submitting-indicator')).toBeInTheDocument();
  });

  it('renders star text in the star panel', () => {
    render(
      <TechTab
        {...defaultProps}
        ws={{ ...mockWs, starText: 'Análise Estrela aqui' }}
      />,
    );
    const textBoxes = screen.getAllByTestId('panel-text-box');
    const hasStarText = textBoxes.some((box) => box.textContent?.includes('Análise Estrela aqui'));
    expect(hasStarText).toBe(true);
  });

  it('renders hier text in the hierarchical panel', () => {
    render(
      <TechTab
        {...defaultProps}
        ws={{ ...mockWs, hierText: 'Análise Hierárquica aqui' }}
      />,
    );
    const textBoxes = screen.getAllByTestId('panel-text-box');
    const hasHierText = textBoxes.some((box) => box.textContent?.includes('Análise Hierárquica aqui'));
    expect(hasHierText).toBe(true);
  });
});
