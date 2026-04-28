import { useState, useCallback } from 'react';
import { AnalysisControls } from './components/AnalysisControls';
import { ArchitecturePanel } from './components/ArchitecturePanel';
import { useWebSocket } from './hooks/useWebSocket';
import { API_URL } from './config';
import type { AnalysisRequest } from './types';

export function App() {
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [useLlm, setUseLlm] = useState(true);

  const ws = useWebSocket(analysisId);

  const handleSubmit = useCallback(async (request: Omit<AnalysisRequest, 'useLlm'>) => {
    setApiError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/analysis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...request, useLlm }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data: { analysisId: string } = await res.json();
      setAnalysisId(data.analysisId);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Erro ao iniciar análise');
    } finally {
      setSubmitting(false);
    }
  }, [useLlm]);

  return (
    <div className="app" data-testid="app">
      <header className="app-header">
        <div className="header-content">
          <div>
            <h1>Análise Multiagente</h1>
            <p>Comparação de arquiteturas BDI — Gastos em Saúde de Sorocaba-SP</p>
          </div>
          <label className="llm-toggle" data-testid="llm-toggle">
            <span className="llm-toggle-label">LLM</span>
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              data-testid="llm-toggle-input"
            />
            <span className="llm-toggle-slider" />
          </label>
        </div>
      </header>

      <AnalysisControls onSubmit={handleSubmit} />

      {apiError && (
        <div className="api-error" data-testid="api-error" role="alert">
          {apiError}
        </div>
      )}

      {submitting && (
        <div className="submitting" data-testid="submitting-indicator">
          Enviando...
        </div>
      )}

      <div className="panels-container" data-testid="panels-container">
        <ArchitecturePanel
          title="Hierárquica"
          text={ws.hierText}
          benchmarks={ws.hierBenchmarks}
          isLoading={ws.hierLoading}
          error={ws.hierError}
        />
        <ArchitecturePanel
          title="Estrela"
          text={ws.starText}
          benchmarks={ws.starBenchmarks}
          isLoading={ws.starLoading}
          error={ws.starError}
        />
      </div>

      {(ws.comparativeReport || ws.comparativeLoading) && (
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
            {ws.comparativeReport.split('\n').map((line, i) => {
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
            {ws.comparativeLoading && <span className="loading-cursor" data-testid="comparative-loading">▍</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
