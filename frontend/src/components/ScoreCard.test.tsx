import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ScoreCard } from './ScoreCard';

describe('ScoreCard', () => {
  it('renders the metric id', () => {
    render(<ScoreCard id="E1" label="Overhead de coordenação" starValue={0.85} hierValue={0.72} />);
    expect(screen.getByTestId('score-card-E1')).toBeInTheDocument();
    expect(screen.getByText('E1')).toBeInTheDocument();
  });

  it('renders the metric label', () => {
    render(<ScoreCard id="Q2" label="Faithfulness" starValue={0.9} hierValue={0.88} />);
    expect(screen.getByText('Faithfulness')).toBeInTheDocument();
  });

  it('renders star and hierarchical values', () => {
    render(<ScoreCard id="R1" label="Partial result coverage" starValue={1.0} hierValue={0.95} />);
    const card = screen.getByTestId('score-card-R1');
    expect(card.textContent).toContain('1.00');
    expect(card.textContent).toContain('0.95');
  });

  it('renders "—" for null star value', () => {
    render(<ScoreCard id="E2" label="Latency breakdown" starValue={null} hierValue={0.5} />);
    const card = screen.getByTestId('score-card-E2');
    expect(card.textContent).toContain('—');
  });

  it('renders "—" for null hier value', () => {
    render(<ScoreCard id="Q3" label="Completeness" starValue={0.75} hierValue={null} />);
    const card = screen.getByTestId('score-card-Q3');
    expect(card.textContent).toContain('—');
  });

  it('renders "—" for both null values', () => {
    render(<ScoreCard id="R2" label="Graceful degradation" starValue={null} hierValue={null} />);
    const card = screen.getByTestId('score-card-R2');
    const dashes = card.textContent?.match(/—/g);
    expect(dashes).toHaveLength(2);
  });

  it('uses correct data-testid format', () => {
    render(<ScoreCard id="Q4" label="Structural quality" starValue={0.8} hierValue={0.7} />);
    expect(screen.getByTestId('score-card-Q4')).toBeInTheDocument();
  });
});
