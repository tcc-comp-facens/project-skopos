import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { QualityMetricsSection } from './QualityMetricsSection';
import type { QualityMetrics } from '../types';

const mockQualityMetrics: QualityMetrics = {
  star: {
    efficiency: { E1: 0.85, E2: 0.72, E3: 0.91 },
    quality: { Q1: 1.0, Q2: 0.88, Q3: 0.75, Q4: 0.82 },
    resilience: { R1: 1.0, R2: 0.95 },
  },
  hierarchical: {
    efficiency: { E1: 0.78, E2: 0.65, E3: 0.88 },
    quality: { Q1: 1.0, Q2: 0.82, Q3: 0.70, Q4: 0.79 },
    resilience: { R1: 0.9, R2: 0.85 },
  },
};

describe('QualityMetricsSection', () => {
  it('does not render when qualityMetrics is null', () => {
    const { container } = render(<QualityMetricsSection qualityMetrics={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the section when qualityMetrics is provided', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByTestId('quality-metrics-section')).toBeInTheDocument();
  });

  it('renders the Eficiência group title', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByText('Eficiência')).toBeInTheDocument();
  });

  it('renders the Qualidade group title', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByText('Qualidade')).toBeInTheDocument();
  });

  it('renders the Resiliência group title', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByText('Resiliência')).toBeInTheDocument();
  });

  it('renders all efficiency score cards (E1, E2, E3)', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByTestId('score-card-E1')).toBeInTheDocument();
    expect(screen.getByTestId('score-card-E2')).toBeInTheDocument();
    expect(screen.getByTestId('score-card-E3')).toBeInTheDocument();
  });

  it('renders all quality score cards (Q1, Q2, Q3, Q4)', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByTestId('score-card-Q1')).toBeInTheDocument();
    expect(screen.getByTestId('score-card-Q2')).toBeInTheDocument();
    expect(screen.getByTestId('score-card-Q3')).toBeInTheDocument();
    expect(screen.getByTestId('score-card-Q4')).toBeInTheDocument();
  });

  it('renders all resilience score cards (R1, R2)', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    expect(screen.getByTestId('score-card-R1')).toBeInTheDocument();
    expect(screen.getByTestId('score-card-R2')).toBeInTheDocument();
  });

  it('renders a total of 9 score cards', () => {
    render(<QualityMetricsSection qualityMetrics={mockQualityMetrics} />);
    // E1, E2, E3, Q1, Q2, Q3, Q4, R1, R2
    const cards = ['E1', 'E2', 'E3', 'Q1', 'Q2', 'Q3', 'Q4', 'R1', 'R2'];
    cards.forEach((id) => {
      expect(screen.getByTestId(`score-card-${id}`)).toBeInTheDocument();
    });
  });
});
