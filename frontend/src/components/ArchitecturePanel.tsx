import { useEffect, useRef } from 'react';
import type { BenchmarkMetrics } from '../types';

export interface ArchitecturePanelProps {
  title: string;
  text: string;
  benchmarks: BenchmarkMetrics | null;
  isLoading: boolean;
  error: string | null;
}

export function ArchitecturePanel({
  title,
  text,
  benchmarks,
  isLoading,
  error,
}: ArchitecturePanelProps) {
  const textBoxRef = useRef<HTMLDivElement>(null);
  const isHier = title.toLowerCase().includes('hierárquica') || title.toLowerCase().includes('hierarquica');

  useEffect(() => {
    if (textBoxRef.current) {
      textBoxRef.current.scrollTop = textBoxRef.current.scrollHeight;
    }
  }, [text]);

  return (
    <div className="arch-panel" data-testid={`architecture-panel-${title.toLowerCase()}`}>
      <div className="panel-header">
        <div className={`panel-icon ${isHier ? 'hier' : 'star'}`}>
          {isHier ? '🏛' : '⭐'}
        </div>
        <h2 className="panel-title" data-testid="panel-title">{title}</h2>
      </div>

      {error && (
        <div className="panel-error" data-testid="panel-error" role="alert">
          {error}
        </div>
      )}

      <div
        ref={textBoxRef}
        className="panel-text-box"
        data-testid="panel-text-box"
        aria-live="polite"
      >
        {text || (!isLoading && !error && (
          <span className="placeholder-text">Aguardando análise...</span>
        ))}
        {isLoading && <span className="loading-cursor" data-testid="loading-indicator">▍</span>}
      </div>

      {benchmarks && (
        <div className="benchmarks" data-testid="panel-benchmarks">
          <div className="total-time" data-testid="total-time">
            Tempo total: {benchmarks.totalExecutionTimeMs}ms
          </div>
          <table className="metrics-table" data-testid="agent-metrics-table">
            <thead>
              <tr>
                <th>Agente</th>
                <th>Tempo (ms)</th>
                <th>CPU (%)</th>
                <th>Memória (MB)</th>
              </tr>
            </thead>
            <tbody>
              {benchmarks.agentMetrics.map((agent) => (
                <tr key={agent.agentName} data-testid={`agent-row-${agent.agentName}`}>
                  <td>{agent.agentName}</td>
                  <td>{agent.executionTimeMs}</td>
                  <td>{agent.cpuPercent.toFixed(1)}</td>
                  <td>{agent.memoryMb.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
