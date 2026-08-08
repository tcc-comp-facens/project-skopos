/**
 * Tests for buildAgentChartData utility.
 * Validates the generic year/value extraction used by AgentDataCard's chart.
 */
import { describe, it, expect } from 'vitest';
import { buildAgentChartData } from './buildAgentChartData';

describe('buildAgentChartData', () => {
  it('returns null for empty rows', () => {
    expect(buildAgentChartData([])).toBeNull();
  });

  it('builds points from indicador shape (ano/valor)', () => {
    const rows = [
      { tipo: 'dengue', ano: 2019, valor: 10 },
      { tipo: 'dengue', ano: 2020, valor: 25 },
    ];
    expect(buildAgentChartData(rows)).toEqual([
      { ano: 2019, valor: 10 },
      { ano: 2020, valor: 25 },
    ]);
  });

  it('builds points from despesa shape (ano/valor)', () => {
    const rows = [{ subfuncao: 301, subfuncaoNome: 'AB', ano: 2021, valor: 500.5 }];
    expect(buildAgentChartData(rows)).toEqual([{ ano: 2021, valor: 500.5 }]);
  });

  it('builds points from variacao_anual shape (ano_atual/percentual)', () => {
    const rows = [
      { subfuncao: 301, ano_atual: 2020, ano_anterior: 2019, percentual: 12.5, classificacao: 'alta' },
    ];
    expect(buildAgentChartData(rows)).toEqual([{ ano: 2020, valor: 12.5 }]);
  });

  it('groups and sums multiple dimensional rows for the same year', () => {
    const rows = [
      { tipo: 'internacoes', ano: 2020, valor: 10, dimensao_valor: '0-9' },
      { tipo: 'internacoes', ano: 2020, valor: 15, dimensao_valor: '10-19' },
      { tipo: 'internacoes', ano: 2021, valor: 40, dimensao_valor: '0-9' },
    ];
    expect(buildAgentChartData(rows)).toEqual([
      { ano: 2020, valor: 25 },
      { ano: 2021, valor: 40 },
    ]);
  });

  it('sorts points ascending by year regardless of input order', () => {
    const rows = [
      { ano: 2022, valor: 3 },
      { ano: 2019, valor: 1 },
      { ano: 2021, valor: 2 },
    ];
    expect(buildAgentChartData(rows)).toEqual([
      { ano: 2019, valor: 1 },
      { ano: 2021, valor: 2 },
      { ano: 2022, valor: 3 },
    ]);
  });

  it('returns null when rows have no known year or value keys', () => {
    const rows = [{ tipo: 'dengue', descricao: 'sem numero' }];
    expect(buildAgentChartData(rows)).toBeNull();
  });

  it('returns null when rows have a year key but no value key', () => {
    const rows = [{ ano: 2020, classificacao: 'estavel' }];
    expect(buildAgentChartData(rows)).toBeNull();
  });

  it('ignores malformed rows mixed with valid ones', () => {
    const rows = [
      { ano: 2020, valor: 10 },
      { ano: 'não é número', valor: 5 },
      { ano: 2021, valor: 'texto' },
    ];
    expect(buildAgentChartData(rows)).toEqual([{ ano: 2020, valor: 10 }]);
  });
});
