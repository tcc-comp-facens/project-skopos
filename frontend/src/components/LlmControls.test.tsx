/**
 * Tests for LlmControls component.
 * O uso de LLM em si é sempre ativo (não é mais um toggle) — só o LLM
 * Judge continua controlável pelo usuário.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LlmControls } from './LlmControls';

describe('LlmControls', () => {
  const defaultProps = {
    useLlmJudge: false,
    disabled: false,
    onUseLlmJudgeChange: vi.fn(),
  };

  it('renders only the LLM Judge toggle', () => {
    render(<LlmControls {...defaultProps} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeInTheDocument();
    expect(screen.queryByTestId('llm-toggle-input')).not.toBeInTheDocument();
  });

  it('LLM Judge toggle is disabled when disabled prop is true', () => {
    render(<LlmControls {...defaultProps} disabled={true} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeDisabled();
  });

  it('LLM Judge toggle is enabled when disabled prop is false', () => {
    render(<LlmControls {...defaultProps} disabled={false} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).not.toBeDisabled();
  });

  it('calls onUseLlmJudgeChange when toggle clicked', () => {
    const onUseLlmJudgeChange = vi.fn();
    render(<LlmControls {...defaultProps} onUseLlmJudgeChange={onUseLlmJudgeChange} />);
    fireEvent.click(screen.getByTestId('llm-judge-toggle-input'));
    expect(onUseLlmJudgeChange).toHaveBeenCalledWith(true);
  });

  it('reflects the checked state from useLlmJudge prop', () => {
    render(<LlmControls {...defaultProps} useLlmJudge={true} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeChecked();
  });
});
