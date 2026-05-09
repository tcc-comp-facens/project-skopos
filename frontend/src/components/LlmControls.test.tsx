import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import * as fc from 'fast-check';
import { LlmControls } from './LlmControls';

describe('LlmControls', () => {
  it('renders both toggles', () => {
    render(
      <LlmControls
        useLlm={true}
        useLlmJudge={false}
        disabled={false}
        onUseLlmChange={vi.fn()}
        onUseLlmJudgeChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('llm-toggle')).toBeInTheDocument();
    expect(screen.getByTestId('llm-judge-toggle')).toBeInTheDocument();
  });

  it('LLM Judge is enabled when useLlm=true', () => {
    render(
      <LlmControls
        useLlm={true}
        useLlmJudge={false}
        disabled={false}
        onUseLlmChange={vi.fn()}
        onUseLlmJudgeChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('llm-judge-toggle-input')).not.toBeDisabled();
  });

  it('LLM Judge is disabled when useLlm=false', () => {
    render(
      <LlmControls
        useLlm={false}
        useLlmJudge={true}
        disabled={false}
        onUseLlmChange={vi.fn()}
        onUseLlmJudgeChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeDisabled();
  });

  it('both toggles are disabled when disabled=true', () => {
    render(
      <LlmControls
        useLlm={true}
        useLlmJudge={true}
        disabled={true}
        onUseLlmChange={vi.fn()}
        onUseLlmJudgeChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('llm-toggle-input')).toBeDisabled();
    expect(screen.getByTestId('llm-judge-toggle-input')).toBeDisabled();
  });

  it('calls onUseLlmChange when LLM toggle is clicked', async () => {
    const onUseLlmChange = vi.fn();
    const user = userEvent.setup();
    render(
      <LlmControls
        useLlm={true}
        useLlmJudge={false}
        disabled={false}
        onUseLlmChange={onUseLlmChange}
        onUseLlmJudgeChange={vi.fn()}
      />,
    );
    await user.click(screen.getByTestId('llm-toggle-input'));
    expect(onUseLlmChange).toHaveBeenCalledWith(false);
  });

  it('calls onUseLlmJudgeChange when LLM Judge toggle is clicked', async () => {
    const onUseLlmJudgeChange = vi.fn();
    const user = userEvent.setup();
    render(
      <LlmControls
        useLlm={true}
        useLlmJudge={false}
        disabled={false}
        onUseLlmChange={vi.fn()}
        onUseLlmJudgeChange={onUseLlmJudgeChange}
      />,
    );
    await user.click(screen.getByTestId('llm-judge-toggle-input'));
    expect(onUseLlmJudgeChange).toHaveBeenCalledWith(true);
  });

  // Feature: frontend-redesign, Property 5
  // Propriedade 5: Toggle LLM Judge desabilitado quando LLM está desabilitado
  it('Property 5: LLM Judge input is always disabled when useLlm=false, regardless of useLlmJudge', () => {
    fc.assert(
      fc.property(
        fc.boolean(), // useLlmJudge
        (useLlmJudge) => {
          const { unmount } = render(
            <LlmControls
              useLlm={false}
              useLlmJudge={useLlmJudge}
              disabled={false}
              onUseLlmChange={vi.fn()}
              onUseLlmJudgeChange={vi.fn()}
            />,
          );
          const input = screen.getByTestId('llm-judge-toggle-input') as HTMLInputElement;
          const isDisabled = input.disabled;
          unmount();
          return isDisabled === true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
