// Transforma as linhas brutas retornadas por uma query de agente
// (IndicadorSaude, DespesaAnual ou VARIACAO_ANUAL) num formato genérico
// {ano, valor}[] plotável, sem conhecer o shape específico de cada um dos
// 15 agentes de domínio. Se nenhuma chave conhecida de ano/valor existir,
// retorna null e o card cai no fallback "sem dado numérico para gráfico".

const YEAR_KEYS = ['ano', 'ano_atual'];
const VALUE_KEYS = ['valor', 'valorTotal', 'percentual'];

export interface ChartPoint {
  ano: number;
  valor: number;
}

function firstPresentKey(row: Record<string, unknown>, candidates: string[]): string | null {
  for (const key of candidates) {
    if (typeof row[key] === 'number') {
      return key;
    }
  }
  return null;
}

export function buildAgentChartData(rows: Record<string, unknown>[]): ChartPoint[] | null {
  if (!rows || rows.length === 0) {
    return null;
  }

  const yearKey = firstPresentKey(rows[0], YEAR_KEYS);
  const valueKey = firstPresentKey(rows[0], VALUE_KEYS);
  if (!yearKey || !valueKey) {
    return null;
  }

  const totalsByYear = new Map<number, number>();
  for (const row of rows) {
    const ano = row[yearKey];
    const valor = row[valueKey];
    if (typeof ano !== 'number' || typeof valor !== 'number') {
      continue;
    }
    totalsByYear.set(ano, (totalsByYear.get(ano) ?? 0) + valor);
  }

  if (totalsByYear.size === 0) {
    return null;
  }

  return Array.from(totalsByYear.entries())
    .sort(([a], [b]) => a - b)
    .map(([ano, valor]) => ({ ano, valor }));
}
