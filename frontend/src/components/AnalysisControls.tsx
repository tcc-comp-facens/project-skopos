import { useState } from 'react';
import type { AnalysisRequest } from '../types';

interface AnalysisControlsProps {
  onSubmit: (request: Omit<AnalysisRequest, 'useLlm' | 'useLlmJudge'>) => void;
}

/**
 * Mapeamento dos parâmetros de saúde com sigla SUS e nome acessível.
 * Requirements: 3.1, 3.2
 */
const HEALTH_PARAMS = [
  { key: 'dengue' as const,      acronym: 'SINAN-DENG', label: 'Dengue' },
  { key: 'covid' as const,       acronym: 'SINAN-INFL', label: 'COVID-19' },
  { key: 'vaccination' as const, acronym: 'SI-PNI',     label: 'Vacinação' },
  { key: 'internacoes' as const, acronym: 'SIH',        label: 'Internações Hospitalares' },
  { key: 'mortalidade' as const, acronym: 'SIM',        label: 'Mortalidade' },
] as const;

type HealthParamKey = typeof HEALTH_PARAMS[number]['key'];

export function AnalysisControls({ onSubmit }: AnalysisControlsProps) {
  const [dateFrom, setDateFrom] = useState(2019);
  const [dateTo, setDateTo] = useState(2021);
  const [params, setParams] = useState<Record<HealthParamKey, boolean>>({
    dengue: false,
    covid: false,
    vaccination: false,
    internacoes: false,
    mortalidade: false,
  });

  const hasHealthParam = Object.values(params).some(Boolean);
  const dateError = dateFrom > dateTo;
  const isSubmitDisabled = !hasHealthParam || dateError;

  const handleParamChange = (key: HealthParamKey, checked: boolean) => {
    setParams((prev) => ({ ...prev, [key]: checked }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitDisabled) return;
    onSubmit({
      dateFrom,
      dateTo,
      healthParams: params,
    });
  };

  return (
    <form className="controls-form" data-testid="analysis-controls" onSubmit={handleSubmit}>
      <div className="controls-row">
        <div className="control-group">
          <label htmlFor="dateFrom">De:</label>
          <input
            id="dateFrom"
            data-testid="date-from"
            type="number"
            value={dateFrom}
            onChange={(e) => setDateFrom(Number(e.target.value))}
          />
        </div>

        <div className="control-group">
          <label htmlFor="dateTo">Até:</label>
          <input
            id="dateTo"
            data-testid="date-to"
            type="number"
            value={dateTo}
            onChange={(e) => setDateTo(Number(e.target.value))}
          />
        </div>

        <fieldset className="health-fieldset">
          <legend>Quais parâmetros consultar?</legend>

          <div className="health-param-buttons">
            {HEALTH_PARAMS.map(({ key, acronym, label }) => (
              <button
                key={key}
                type="button"
                data-testid={`toggle-${key}`}
                className={`health-param-btn${params[key] ? ' active' : ''}`}
                aria-pressed={params[key]}
                onClick={() => handleParamChange(key, !params[key])}
              >
                <span className="health-param-acronym">{acronym}</span>
                <span className="health-param-name">{label}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <button
          type="submit"
          className="submit-btn"
          data-testid="submit-button"
          disabled={isSubmitDisabled}
        >
          Analisar
        </button>
      </div>

      {dateError && (
        <div
          className="date-error"
          data-testid="date-error"
          role="alert"
        >
          O ano de início não pode ser maior que o ano de fim.
        </div>
      )}
    </form>
  );
}
