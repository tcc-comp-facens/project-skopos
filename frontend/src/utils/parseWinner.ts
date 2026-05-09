import type { WinnerArchitecture } from '../types';

/**
 * Extrai a arquitetura vencedora do relatório comparativo.
 *
 * O backend gera uma linha de veredito no formato:
 *   → Arquitetura Estrela venceu com X pontos
 *   → Arquitetura Hierárquica venceu com X pontos
 *
 * A função busca a primeira linha que começa com "→" e verifica
 * se contém "Estrela" ou "Hierárquica" (case-insensitive).
 *
 * @param report - Texto completo do relatório comparativo
 * @returns 'star' | 'hierarchical' | null
 *
 * Requirements: 4.2
 */
export function parseWinner(report: string): WinnerArchitecture {
  if (!report) return null;

  const lines = report.split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('→')) {
      const lower = trimmed.toLowerCase();
      if (lower.includes('estrela')) return 'star';
      if (lower.includes('hierárquica') || lower.includes('hierarquica')) {
        return 'hierarchical';
      }
    }
  }
  return null;
}
