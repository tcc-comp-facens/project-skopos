/**
 * Tests for AnalysisControls component.
 * Validates form behavior: health param toggles, date validation, submit logic.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AnalysisControls } from './AnalysisControls';

describe('AnalysisControls', () => {
  it('renders all 5 health param buttons', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    expect(screen.getByTestId('toggle-dengue')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-covid')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-vaccination')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-internacoes')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-mortalidade')).toBeInTheDocument();
  });

  it('submit button is disabled when no health param selected', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    expect(screen.getByTestId('submit-button')).toBeDisabled();
  });

  it('submit button is enabled when a health param is selected', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    fireEvent.click(screen.getByTestId('toggle-dengue'));
    expect(screen.getByTestId('submit-button')).not.toBeDisabled();
  });

  it('shows date error when dateFrom > dateTo', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByTestId('date-from'), { target: { value: '2025' } });
    fireEvent.change(screen.getByTestId('date-to'), { target: { value: '2020' } });
    expect(screen.getByTestId('date-error')).toBeInTheDocument();
  });

  it('shows date error when dateFrom equals dateTo (single year)', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByTestId('date-from'), { target: { value: '2020' } });
    fireEvent.change(screen.getByTestId('date-to'), { target: { value: '2020' } });
    expect(screen.getByTestId('date-error')).toBeInTheDocument();
  });

  it('does not show date error when dateFrom < dateTo', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    expect(screen.queryByTestId('date-error')).not.toBeInTheDocument();
  });

  it('calls onSubmit with correct params on form submit', () => {
    const onSubmit = vi.fn();
    render(<AnalysisControls onSubmit={onSubmit} />);
    fireEvent.click(screen.getByTestId('toggle-dengue'));
    fireEvent.click(screen.getByTestId('submit-button'));
    expect(onSubmit).toHaveBeenCalledWith({
      dateFrom: 2019,
      dateTo: 2021,
      healthParams: {
        dengue: true,
        covid: false,
        vaccination: false,
        internacoes: false,
        mortalidade: false,
      },
    });
  });

  it('toggles health param button aria-pressed state', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    const btn = screen.getByTestId('toggle-covid');
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-pressed', 'true');
  });
});
