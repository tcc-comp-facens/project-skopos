/**
 * Property-based tests for validateMessage — Property 2 do spec
 * realtime-chat-interface (rejeição de mensagens só com espaço em branco).
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { isBlank, isTooLong, MAX_CHAT_MESSAGE_LENGTH } from './validateMessage';

describe('isBlank', () => {
  it('Property 2: strings compostas só de espaço em branco são sempre bloqueadas', () => {
    fc.assert(
      fc.property(
        fc.stringOf(fc.constantFrom(' ', '\t', '\n', '\r'), { maxLength: 50 }),
        (text) => {
          expect(isBlank(text)).toBe(true);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('strings com ao menos um caractere não-espaço não são bloqueadas', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1 }).filter((s) => s.trim().length > 0),
        (text) => {
          expect(isBlank(text)).toBe(false);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('string vazia é considerada em branco', () => {
    expect(isBlank('')).toBe(true);
  });
});

describe('isTooLong', () => {
  it('rejeita mensagens acima do limite', () => {
    expect(isTooLong('a'.repeat(MAX_CHAT_MESSAGE_LENGTH + 1))).toBe(true);
  });

  it('aceita mensagens exatamente no limite', () => {
    expect(isTooLong('a'.repeat(MAX_CHAT_MESSAGE_LENGTH))).toBe(false);
  });
});
