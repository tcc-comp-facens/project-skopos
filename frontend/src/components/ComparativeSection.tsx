/**
 * ComparativeSection — seção do relatório comparativo e LLM Judge.
 * Extraído de App.tsx para uso na TechTab.
 *
 * Requirements: 7.1, 7.2, 7.3, 7.4
 */
export interface ComparativeSectionProps {
  comparativeReport: string;
  comparativeLoading: boolean;
  llmJudgeText: string;
  llmJudgeLoading: boolean;
}

export function ComparativeSection({
  comparativeReport,
  comparativeLoading,
  llmJudgeText,
  llmJudgeLoading,
}: ComparativeSectionProps): JSX.Element | null {
  if (!comparativeReport && !comparativeLoading) return null;

  return (
    <div className="comparative-section" data-testid="comparative-section">
      <div className="comparative-header">
        <div className="comparative-icon">📊</div>
        <h2>Relatório Comparativo</h2>
      </div>

      <div
        className="comparative-body"
        data-testid="comparative-report"
        aria-live="polite"
      >
        {comparativeReport.split('\n').map((line, i) => {
          if (line.startsWith('=====')) {
            return <hr key={i} className="report-divider" />;
          }
          if (line.includes('RELATÓRIO COMPARATIVO')) {
            return <h3 key={i} className="report-main-title">{line.trim()}</h3>;
          }
          if (line.startsWith('━━━')) {
            const title = line.replace(/━/g, '').trim();
            return <h4 key={i} className="report-section-title">{title}</h4>;
          }
          if (line.trim().startsWith('→')) {
            return <p key={i} className="report-verdict">{line.trim()}</p>;
          }
          if (line.trim().startsWith('✓')) {
            return <p key={i} className="report-success">{line.trim()}</p>;
          }
          if (line.trim().startsWith('✗')) {
            return <p key={i} className="report-warning">{line.trim()}</p>;
          }
          if (line.trim().startsWith('•')) {
            return <p key={i} className="report-bullet">{line.trim()}</p>;
          }
          if (line.trim() === '') {
            return <div key={i} className="report-spacer" />;
          }
          return <p key={i} className="report-line">{line}</p>;
        })}

        {comparativeLoading && !comparativeReport && (
          <div className="panel-loading-indicator">
            <div className="spinner" />
            <span>Computando métricas de qualidade...</span>
          </div>
        )}
        {comparativeLoading && comparativeReport && (
          <span className="loading-cursor" data-testid="comparative-loading">▍</span>
        )}
      </div>

      {(llmJudgeText || llmJudgeLoading) && (
        <div
          className="llm-judge-body"
          data-testid="llm-judge-report"
          aria-live="polite"
        >
          {!llmJudgeText && llmJudgeLoading && (
            <div className="panel-loading-indicator">
              <div className="spinner purple" />
              <span>LLM Judge avaliando fidelidade dos textos...</span>
            </div>
          )}
          {llmJudgeText.split('\n').map((line, i) => {
            if (line.startsWith('━━━')) {
              const title = line.replace(/━/g, '').trim();
              return <h4 key={`j${i}`} className="report-section-title">{title}</h4>;
            }
            if (line === 'SCORES' || line === 'JUSTIFICATIVAS') {
              return <p key={`j${i}`} className="judge-subtitle">{line}</p>;
            }
            if (line.startsWith('★ Estrela:') || line.startsWith('◆ Hierárquica:')) {
              const isHigh = line.includes('5/5') || line.includes('4/5');
              const cls = isHigh ? 'judge-score good' : 'judge-score';
              return <p key={`j${i}`} className={cls}>{line}</p>;
            }
            if (line === '★ Estrela' || line === '◆ Hierárquica') {
              return <p key={`j${i}`} className="judge-arch-label">{line}</p>;
            }
            if (line.trim() === '') {
              return <div key={`j${i}`} className="report-spacer" />;
            }
            return <p key={`j${i}`} className="judge-justification">{line}</p>;
          })}
          {llmJudgeLoading && (
            <span className="loading-cursor" data-testid="llm-judge-loading">▍</span>
          )}
        </div>
      )}
    </div>
  );
}
