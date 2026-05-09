import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { parseWinner } from './parseWinner';

describe('parseWinner', () => {
  // Example-based tests
  it('returns null for empty string', () => {
    expect(parseWinner('')).toBeNull();
  });

  it('returns null when no verdict line present', () => {
    expect(parseWinner('Análise completa\nSem veredito aqui')).toBeNull();
  });

  it('returns "star" for Estrela verdict', () => {
    expect(parseWinner('→ Arquitetura Estrela venceu com 5 pontos')).toBe('star');
  });

  it('returns "hierarchical" for Hierárquica verdict', () => {
    expect(parseWinner('→ Arquitetura Hierárquica venceu com 3 pontos')).toBe('hierarchical');
  });

  it('returns "hierarchical" for Hierarquica (without accent)', () => {
    expect(parseWinner('→ Arquitetura Hierarquica venceu com 3 pontos')).toBe('hierarchical');
  });

  it('is case-insensitive for ESTRELA', () => {
    expect(parseWinner('→ ARQUITETURA ESTRELA VENCEU')).toBe('star');
  });

  it('is case-insensitive for HIERÁRQUICA', () => {
    expect(parseWinner('→ ARQUITETURA HIERÁRQUICA VENCEU')).toBe('hierarchical');
  });

  it('ignores lines not starting with →', () => {
    const report = 'Estrela ganhou\nHierárquica perdeu\n→ Arquitetura Estrela venceu';
    expect(parseWinner(report)).toBe('star');
  });

  it('returns first verdict found when multiple → lines exist', () => {
    const report = '→ Arquitetura Estrela venceu\n→ Arquitetura Hierárquica venceu';
    expect(parseWinner(report)).toBe('star');
  });

  // Feature: frontend-redesign, Property 1
  // Propriedade 1: Identificação correta do vencedor a partir da linha de veredito
  it('Property 1: correctly identifies star from verdict line', () => {
    fc.assert(
      fc.property(
        fc.string({ maxLength: 50 }),
        (suffix) => {
          const report = `→ Arquitetura Estrela venceu ${suffix}`;
          expect(parseWinner(report)).toBe('star');
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 1: correctly identifies hierarchical from verdict line', () => {
    fc.assert(
      fc.property(
        fc.string({ maxLength: 50 }),
        (suffix) => {
          const report = `→ Arquitetura Hierárquica venceu ${suffix}`;
          expect(parseWinner(report)).toBe('hierarchical');
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: frontend-redesign, Property 2
  // Propriedade 2: Robustez do parser a conteúdo arbitrário ao redor do veredito
  it('Property 2: arbitrary prefix/suffix text does not affect star verdict', () => {
    fc.assert(
      fc.property(
        fc.string({ maxLength: 200 }).filter((s) => !s.includes('→')),
        fc.string({ maxLength: 200 }).filter((s) => !s.includes('→')),
        (prefix, suffix) => {
          const verdict = '→ Arquitetura Estrela venceu com 5 pontos';
          const isolated = parseWinner(verdict);
          const withContext = parseWinner(`${prefix}\n${verdict}\n${suffix}`);
          expect(withContext).toBe(isolated);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('Property 2: arbitrary prefix/suffix text does not affect hierarchical verdict', () => {
    fc.assert(
      fc.property(
        fc.string({ maxLength: 200 }).filter((s) => !s.includes('→')),
        fc.string({ maxLength: 200 }).filter((s) => !s.includes('→')),
        (prefix, suffix) => {
          const verdict = '→ Arquitetura Hierárquica venceu com 3 pontos';
          const isolated = parseWinner(verdict);
          const withContext = parseWinner(`${prefix}\n${verdict}\n${suffix}`);
          expect(withContext).toBe(isolated);
        },
      ),
      { numRuns: 100 },
    );
  });
});
