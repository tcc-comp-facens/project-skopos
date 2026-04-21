import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { AnalysisControls } from './AnalysisControls';

describe('AnalysisControls', () => {
  it('renders all form controls', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);

    expect(screen.getByTestId('date-from')).toBeInTheDocument();
    expect(screen.getByTestId('date-to')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-dengue')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-covid')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-vaccination')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-internacoes')).toBeInTheDocument();
    expect(screen.getByTestId('toggle-mortalidade')).toBeInTheDocument();
    expect(screen.getByTestId('submit-button')).toBeInTheDocument();
  });

  it('submit button is enabled by default (all toggles start checked)', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);
    expect(screen.getByTestId('submit-button')).toBeEnabled();
  });

  it('disables submit button when all toggles are unchecked', async () => {
    const user = userEvent.setup();
    render(<AnalysisControls onSubmit={vi.fn()} />);

    // Uncheck all (they start checked)
    await user.click(screen.getByTestId('toggle-dengue'));
    await user.click(screen.getByTestId('toggle-covid'));
    await user.click(screen.getByTestId('toggle-vaccination'));
    await user.click(screen.getByTestId('toggle-internacoes'));
    await user.click(screen.getByTestId('toggle-mortalidade'));
    expect(screen.getByTestId('submit-button')).toBeDisabled();
  });

  it('re-enables button when one toggle is checked again', async () => {
    const user = userEvent.setup();
    render(<AnalysisControls onSubmit={vi.fn()} />);

    // Uncheck all
    await user.click(screen.getByTestId('toggle-dengue'));
    await user.click(screen.getByTestId('toggle-covid'));
    await user.click(screen.getByTestId('toggle-vaccination'));
    await user.click(screen.getByTestId('toggle-internacoes'));
    await user.click(screen.getByTestId('toggle-mortalidade'));
    expect(screen.getByTestId('submit-button')).toBeDisabled();

    // Re-check one
    await user.click(screen.getByTestId('toggle-mortalidade'));
    expect(screen.getByTestId('submit-button')).toBeEnabled();
  });

  it('calls onSubmit with correct AnalysisRequest using defaults', async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<AnalysisControls onSubmit={onSubmit} />);

    // Submit with defaults (all checked, 2019-2021)
    await user.click(screen.getByTestId('submit-button'));

    expect(onSubmit).toHaveBeenCalledWith({
      dateFrom: 2019,
      dateTo: 2021,
      healthParams: {
        dengue: true,
        covid: true,
        vaccination: true,
        internacoes: true,
        mortalidade: true,
      },
    });
  });

  it('calls onSubmit with partial params after unchecking some', async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<AnalysisControls onSubmit={onSubmit} />);

    // Uncheck covid and internacoes
    await user.click(screen.getByTestId('toggle-covid'));
    await user.click(screen.getByTestId('toggle-internacoes'));
    await user.click(screen.getByTestId('submit-button'));

    expect(onSubmit).toHaveBeenCalledWith({
      dateFrom: 2019,
      dateTo: 2021,
      healthParams: {
        dengue: true,
        covid: false,
        vaccination: true,
        internacoes: false,
        mortalidade: true,
      },
    });
  });

  it('does not call onSubmit when button is disabled', async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<AnalysisControls onSubmit={onSubmit} />);

    // Uncheck all
    await user.click(screen.getByTestId('toggle-dengue'));
    await user.click(screen.getByTestId('toggle-covid'));
    await user.click(screen.getByTestId('toggle-vaccination'));
    await user.click(screen.getByTestId('toggle-internacoes'));
    await user.click(screen.getByTestId('toggle-mortalidade'));

    await user.click(screen.getByTestId('submit-button'));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('has accessible labels for all inputs', () => {
    render(<AnalysisControls onSubmit={vi.fn()} />);

    expect(screen.getByLabelText('Ano início')).toBeInTheDocument();
    expect(screen.getByLabelText('Ano fim')).toBeInTheDocument();
    expect(screen.getByLabelText('Dengue')).toBeInTheDocument();
    expect(screen.getByLabelText('COVID')).toBeInTheDocument();
    expect(screen.getByLabelText('Vacinação')).toBeInTheDocument();
    expect(screen.getByLabelText('Internações')).toBeInTheDocument();
    expect(screen.getByLabelText('Mortalidade')).toBeInTheDocument();
  });
});
