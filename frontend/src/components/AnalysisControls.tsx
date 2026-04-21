import { useState } from 'react';
import type { AnalysisRequest } from '../types';

interface AnalysisControlsProps {
  onSubmit: (request: AnalysisRequest) => void;
}

export function AnalysisControls({ onSubmit }: AnalysisControlsProps) {
  const [dateFrom, setDateFrom] = useState(2019);
  const [dateTo, setDateTo] = useState(2021);
  const [dengue, setDengue] = useState(true);
  const [covid, setCovid] = useState(true);
  const [vaccination, setVaccination] = useState(true);
  const [internacoes, setInternacoes] = useState(true);
  const [mortalidade, setMortalidade] = useState(true);

  const hasHealthParam = dengue || covid || vaccination || internacoes || mortalidade;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!hasHealthParam) return;
    onSubmit({
      dateFrom,
      dateTo,
      healthParams: { dengue, covid, vaccination, internacoes, mortalidade },
    });
  };

  return (
    <form className="controls-form" data-testid="analysis-controls" onSubmit={handleSubmit}>
      <div className="controls-row">
        <div className="control-group">
          <label htmlFor="dateFrom">Ano início</label>
          <input
            id="dateFrom"
            data-testid="date-from"
            type="number"
            value={dateFrom}
            onChange={(e) => setDateFrom(Number(e.target.value))}
          />
        </div>

        <div className="control-group">
          <label htmlFor="dateTo">Ano fim</label>
          <input
            id="dateTo"
            data-testid="date-to"
            type="number"
            value={dateTo}
            onChange={(e) => setDateTo(Number(e.target.value))}
          />
        </div>

        <fieldset className="health-fieldset">
          <legend>Parâmetros de saúde</legend>

          <label htmlFor="toggle-dengue">
            <input
              id="toggle-dengue"
              data-testid="toggle-dengue"
              type="checkbox"
              checked={dengue}
              onChange={(e) => setDengue(e.target.checked)}
            />
            Dengue
          </label>

          <label htmlFor="toggle-covid">
            <input
              id="toggle-covid"
              data-testid="toggle-covid"
              type="checkbox"
              checked={covid}
              onChange={(e) => setCovid(e.target.checked)}
            />
            COVID
          </label>

          <label htmlFor="toggle-vaccination">
            <input
              id="toggle-vaccination"
              data-testid="toggle-vaccination"
              type="checkbox"
              checked={vaccination}
              onChange={(e) => setVaccination(e.target.checked)}
            />
            Vacinação
          </label>

          <label htmlFor="toggle-internacoes">
            <input
              id="toggle-internacoes"
              data-testid="toggle-internacoes"
              type="checkbox"
              checked={internacoes}
              onChange={(e) => setInternacoes(e.target.checked)}
            />
            Internações
          </label>

          <label htmlFor="toggle-mortalidade">
            <input
              id="toggle-mortalidade"
              data-testid="toggle-mortalidade"
              type="checkbox"
              checked={mortalidade}
              onChange={(e) => setMortalidade(e.target.checked)}
            />
            Mortalidade
          </label>
        </fieldset>

        <button
          type="submit"
          className="submit-btn"
          data-testid="submit-button"
          disabled={!hasHealthParam}
        >
          Analisar
        </button>
      </div>
    </form>
  );
}
