/**
 * ComparativeSection — seção do relatório comparativo e da avaliação RAGAS.
 * Extraído de App.tsx para uso na TechTab.
 *
 * Requirements: 7.1, 7.2, 7.3, 7.4
 */
export interface ComparativeSectionProps {
  comparativeReport: string;
  comparativeLoading: boolean;
  ragasText: string;
  ragasLoading: boolean;
}

export function ComparativeSection({
  comparativeReport,
  comparativeLoading,
  ragasText,
  ragasLoading,
}: ComparativeSectionProps): JSX.Element | null {
  // A avaliação RAGAS chega ANTES do relatório (o veredito do relatório
  // depende dela), então checar só o relatório esconderia a seção inteira
  // justamente enquanto o RAGAS está streamando.
  if (!comparativeReport && !comparativeLoading && !ragasText && !ragasLoading) {
    return null;
  }

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

      {(ragasText || ragasLoading) && (
        <div
          className="ragas-body"
          data-testid="ragas-report"
          aria-live="polite"
        >
          {!ragasText && ragasLoading && (
            <div className="panel-loading-indicator">
              <div className="spinner purple" />
              <span>RAGAS avaliando a qualidade das respostas...</span>
            </div>
          )}
          {ragasText.split('\n').map((line, i) => {
            const trimmed = line.trim();
            if (line.startsWith('━━━')) {
              const title = line.replace(/━/g, '').trim();
              return <h4 key={`j${i}`} className="report-section-title">{title}</h4>;
            }
            if (trimmed === '') {
              return <div key={`j${i}`} className="report-spacer" />;
            }
            if (trimmed.startsWith('SCORES')) {
              return <p key={`j${i}`} className="ragas-subtitle">{trimmed}</p>;
            }
            if (trimmed.startsWith('★') || trimmed.startsWith('◆')) {
              // Scores vêm em [0,1] (escala do RAGAS). O destaque marca os
              // altos; "não disponível" nunca conta como alto.
              const value = Number(trimmed.split(':').pop());
              const cls =
                Number.isFinite(value) && value >= 0.8 ? 'ragas-score good' : 'ragas-score';
              return <p key={`j${i}`} className={cls}>{trimmed}</p>;
            }
            return <p key={`j${i}`} className="ragas-arch-label">{trimmed}</p>;
          })}
          {ragasLoading && (
            <span className="loading-cursor" data-testid="ragas-loading">▍</span>
          )}
        </div>
      )}
    </div>
  );
}
