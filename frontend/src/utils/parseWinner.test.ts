/**
 * Tests for parseWinner utility.
 * Validates extraction of winning architecture from comparative report text.
 */
import { describe, it, expect } from 'vitest';
import { parseWinner } from './parseWinner';

describe('parseWinner', () => {
  it('returns null for empty string', () => {
    expect(parseWinner('')).toBeNull();
  });

  it('returns null when no verdict line exists', () => {
    expect(parseWinner('Relatório sem veredito\nApenas texto normal')).toBeNull();
  });

  it('returns "star" when verdict mentions Estrela', () => {
    const report = 'Resumo\n→ Arquitetura Estrela venceu com 5 pontos\nFim';
    expect(parseWinner(report)).toBe('star');
  });

  it('returns "hierarchical" when verdict mentions Hierárquica', () => {
    const report = 'Resumo\n→ Arquitetura Hierárquica venceu com 4 pontos\nFim';
    expect(parseWinner(report)).toBe('hierarchical');
  });

  it('is case-insensitive for architecture name', () => {
    const report = '→ arquitetura estrela venceu com 3 pontos';
    expect(parseWinner(report)).toBe('star');
  });

  it('handles "hierarquica" without accent', () => {
    const report = '→ Arquitetura hierarquica venceu com 6 pontos';
    expect(parseWinner(report)).toBe('hierarchical');
  });

  it('ignores lines that do not start with →', () => {
    const report = 'Estrela teve melhor desempenho\nHierárquica ficou atrás';
    expect(parseWinner(report)).toBeNull();
  });
});
