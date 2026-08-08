import { useState } from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import type { AgentDataEntry } from '../types';
import { buildAgentChartData } from '../utils/buildAgentChartData';

const MAX_VISIBLE_ROWS = 50;

export interface AgentDataCardProps {
  agent: AgentDataEntry;
}

export function AgentDataCard({ agent }: AgentDataCardProps) {
  const [expanded, setExpanded] = useState(false);
  const totalRows = agent.queries.reduce((sum, q) => sum + q.rowCount, 0);

  return (
    <div className="agent-data-card" data-testid={`agent-data-card-${agent.agentName}`}>
      <button
        type="button"
        className="agent-data-card-header"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        data-testid={`agent-data-card-toggle-${agent.agentName}`}
      >
        <span className={`agent-data-chevron ${expanded ? 'expanded' : ''}`}>▸</span>
        <span className="agent-data-label">{agent.agentLabel}</span>
        <span className="agent-data-row-count">{totalRows} registro{totalRows === 1 ? '' : 's'}</span>
      </button>

      {expanded && (
        <div className="agent-data-card-body">
          {agent.queries.map((q, idx) => {
            const chartData = buildAgentChartData(q.rows);
            const visibleRows = q.rows.slice(0, MAX_VISIBLE_ROWS);
            const columns = q.rows.length > 0 ? Object.keys(q.rows[0]) : [];

            return (
              <div className="agent-data-query-block" key={idx}>
                {chartData ? (
                  <div className="agent-data-chart" data-testid="agent-data-chart">
                    <ResponsiveContainer width="100%" height={200}>
                      <BarChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--sophia-mid)" />
                        <XAxis dataKey="ano" stroke="var(--text-secondary)" fontSize={12} />
                        <YAxis stroke="var(--text-secondary)" fontSize={12} />
                        <Tooltip
                          contentStyle={{
                            background: 'var(--surface-base)',
                            border: '1px solid var(--sophia-mid)',
                            color: 'var(--text-primary)',
                          }}
                        />
                        <Bar dataKey="valor" fill="var(--sophia-vivid)" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <p className="agent-data-no-chart">Sem dado numérico para gráfico.</p>
                )}

                <details className="agent-data-query">
                  <summary>Consulta Cypher</summary>
                  <pre data-testid="agent-data-query-text">{q.query}</pre>
                  <pre>{JSON.stringify(q.params, null, 2)}</pre>
                </details>

                <div className="agent-data-table-wrap">
                  <table className="agent-data-table" data-testid="agent-data-table">
                    <thead>
                      <tr>
                        {columns.map((col) => (
                          <th key={col}>{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {visibleRows.map((row, rowIdx) => (
                        <tr key={rowIdx}>
                          {columns.map((col) => (
                            <td key={col}>{String(row[col] ?? '—')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {q.rowCount > MAX_VISIBLE_ROWS && (
                    <p className="agent-data-table-note">
                      Mostrando {MAX_VISIBLE_ROWS} de {q.rowCount} registros.
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
