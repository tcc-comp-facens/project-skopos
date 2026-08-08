/**
 * LlmControls — toggle do LLM Judge para a aba Técnica.
 *
 * O uso de LLM em si é sempre ativo (backend, `use_llm=True` fixo) — não
 * é mais controlável pelo usuário. Só o LLM Judge (avaliação extra,
 * opcional) continua exposto aqui.
 */
export interface LlmControlsProps {
  useLlmJudge: boolean;
  disabled: boolean; // true enquanto análise está em andamento
  onUseLlmJudgeChange: (value: boolean) => void;
}

export function LlmControls({
  useLlmJudge,
  disabled,
  onUseLlmJudgeChange,
}: LlmControlsProps): JSX.Element {
  return (
    <div className="llm-controls" data-testid="llm-controls">
      <label className="llm-toggle" data-testid="llm-judge-toggle">
        <span className="llm-toggle-label">LLM Judge</span>
        <input
          type="checkbox"
          checked={useLlmJudge}
          disabled={disabled}
          onChange={(e) => onUseLlmJudgeChange(e.target.checked)}
          data-testid="llm-judge-toggle-input"
        />
        <span className="llm-toggle-slider" />
      </label>
    </div>
  );
}
