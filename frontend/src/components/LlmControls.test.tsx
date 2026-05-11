/**
 * Tests for LlmControls component.
 * Validates toggle behavior and LLM Judge dependency on LLM toggle.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LlmControls } from './LlmControls';

describe('LlmControls', () => {
  const defaultProps = {
    useLlm: false,
    useLlmJudge: false,
    disabled: false,
    onUseLlmChange: vi.fn(),
    onUseLlmJudgeChange: vi.fn(),
  };

  it('renders LLM and LLM Judge toggles', () => {
    render(<LlmControls {...defaultProps} />);
    expect(screen.getByTestId('llm-toggle-input')).toBeInTheDocument();
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeInTheDocument();
  });

  it('LLM Judge is disabled when useLlm is false', () => {
    render(<LlmControls {...defaultProps} useLlm={false} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeDisabled();
  });

  it('LLM Judge is enabled when useLlm is true', () => {
    render(<LlmControls {...defaultProps} useLlm={true} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).not.toBeDisabled();
  });

  it('both toggles disabled when disabled prop is true', () => {
    render(<LlmControls {...defaultProps} disabled={true} />);
    expect(screen.getByTestId('llm-toggle-input')).toBeDisabled();
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeDisabled();
  });

  it('calls onUseLlmChange when LLM toggle clicked', () => {
    const onUseLlmChange = vi.fn();
    render(<LlmControls {...defaultProps} onUseLlmChange={onUseLlmChange} />);
    fireEvent.click(screen.getByTestId('llm-toggle-input'));
    expect(onUseLlmChange).toHaveBeenCalledWith(true);
  });

  it('LLM Judge checkbox unchecked when useLlm is false even if useLlmJudge is true', () => {
    render(<LlmControls {...defaultProps} useLlm={false} useLlmJudge={true} />);
    expect(screen.getByTestId('llm-judge-toggle-input')).not.toBeChecked();
  });
});
