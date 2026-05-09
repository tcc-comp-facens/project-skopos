/**
 * LlmControls — toggles de LLM e LLM Judge para a aba Técnica.
 *
 * Regra: quando useLlm === false, o toggle LLM Judge é automaticamente
 * desabilitado e seu valor efetivo é false.
 *
 * Requirements: 5.1, 5.2, 5.3
 */
export interface LlmControlsProps {
  useLlm: boolean;
  useLlmJudge: boolean;
  disabled: boolean; // true enquanto análise está em andamento
  onUseLlmChange: (value: boolean) => void;
  onUseLlmJudgeChange: (value: boolean) => void;
}

export function LlmControls({
  useLlm,
  useLlmJudge,
  disabled,
  onUseLlmChange,
  onUseLlmJudgeChange,
}: LlmControlsProps): JSX.Element {
  const llmJudgeDisabled = disabled || !useLlm;

  return (
    <div className="llm-controls" data-testid="llm-controls">
      <label className="llm-toggle" data-testid="llm-toggle">
        <span className="llm-toggle-label">LLM</span>
        <input
          type="checkbox"
          checked={useLlm}
          disabled={disabled}
          onChange={(e) => onUseLlmChange(e.target.checked)}
          data-testid="llm-toggle-input"
        />
        <span className="llm-toggle-slider" />
      </label>

      <label className="llm-toggle" data-testid="llm-judge-toggle">
        <span className="llm-toggle-label">LLM Judge</span>
        <input
          type="checkbox"
          checked={useLlmJudge && useLlm}
          disabled={llmJudgeDisabled}
          onChange={(e) => onUseLlmJudgeChange(e.target.checked)}
          data-testid="llm-judge-toggle-input"
        />
        <span className="llm-toggle-slider" />
      </label>
    </div>
  );
}
