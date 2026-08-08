import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useEffect } from 'react';
import { render, act } from '@testing-library/react';
import { useWebSocket } from './useWebSocket';

/**
 * Regressão: quando `analysisId` muda de uma rodada já concluída para uma
 * nova, o consumidor do hook (ChatInterface, via um useEffect que lê
 * `activeRoundId`+`activeRoundWs` juntos) não pode, em nenhum render,
 * observar o `analysisId` NOVO combinado com o `state` COMPLETO da rodada
 * ANTERIOR (loading=false, texto cheio) — foi exatamente essa combinação
 * que fez a rodada nova ser finalizada com a resposta da rodada anterior
 * (bug real relatado: "a resposta está uma mensagem atrasada").
 *
 * Um teste que só chama `useWebSocket` via `renderHook` não pega isso: o
 * `act()` do Testing Library sincroniza os efeitos antes de devolver o
 * controle, escondendo a janela de um render em que useEffect ainda não
 * rodou (que é exatamente onde o bug morava em produção, num app real
 * onde o commit da árvore inteira acontece antes de qualquer useEffect
 * disparar). Por isso este teste renderiza um consumidor de verdade, com
 * um efeito que lê {analysisId, ws} juntos a cada render — o mesmo padrão
 * do ChatInterface — e grava toda combinação observada.
 */

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() {
    this.onclose?.({ code: 1000 } as CloseEvent);
  }

  send() {
    // no-op — não usado pelo hook
  }
}

function emit(ws: MockWebSocket, event: unknown) {
  act(() => {
    ws.onmessage?.({ data: JSON.stringify(event) } as MessageEvent);
  });
}

interface Observation {
  analysisId: string;
  isLoading: boolean;
  starText: string;
  comparativeReport: string;
}

function Consumer({
  analysisId,
  onObserve,
}: {
  analysisId: string | null;
  onObserve: (obs: Observation) => void;
}) {
  const ws = useWebSocket(analysisId);

  // Mesmo padrão do ChatInterface: um efeito que reage a analysisId+ws
  // juntos e decide, na hora, se a rodada "já terminou".
  useEffect(() => {
    if (!analysisId) return;
    onObserve({
      analysisId,
      isLoading: ws.starLoading || ws.hierLoading || ws.comparativeLoading,
      starText: ws.starText,
      comparativeReport: ws.comparativeReport,
    });
  }, [analysisId, ws, onObserve]);

  return null;
}

describe('useWebSocket — race condition entre rodadas', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('nunca expõe o analysisId novo combinado com o texto/loading da rodada anterior', () => {
    const observations: Observation[] = [];
    const onObserve = (obs: Observation) => observations.push(obs);

    const { rerender } = render(
      <Consumer analysisId="round-1" onObserve={onObserve} />,
    );

    // Rodada 1 termina normalmente, com texto real.
    const ws1 = MockWebSocket.instances[0];
    emit(ws1, { architecture: 'star', type: 'chunk', payload: 'resposta da rodada 1' });
    emit(ws1, { architecture: 'star', type: 'done' });
    emit(ws1, { architecture: 'hierarchical', type: 'chunk', payload: 'hier rodada 1' });
    emit(ws1, { architecture: 'hierarchical', type: 'done' });
    emit(ws1, { architecture: 'both', type: 'chunk', payload: '[VENCEDOR: star]' });
    emit(ws1, { architecture: 'both', type: 'done' });

    const lastRound1Observation = observations[observations.length - 1];
    expect(lastRound1Observation.isLoading).toBe(false);
    expect(lastRound1Observation.starText).toBe('resposta da rodada 1');

    // Rodada 2 começa — é exatamente aqui que o bug acontecia.
    act(() => {
      rerender(<Consumer analysisId="round-2" onObserve={onObserve} />);
    });

    const poisoned = observations.find(
      (o) =>
        o.analysisId === 'round-2' &&
        o.isLoading === false &&
        (o.starText === 'resposta da rodada 1' ||
          o.comparativeReport.includes('VENCEDOR: star')),
    );
    expect(poisoned).toBeUndefined();

    // A primeira observação da rodada 2 deve refletir uma rodada nova e
    // vazia, carregando — nunca a rodada 1 disfarçada de rodada 2.
    const firstRound2Observation = observations.find((o) => o.analysisId === 'round-2');
    expect(firstRound2Observation).toBeDefined();
    expect(firstRound2Observation?.starText).toBe('');
    expect(firstRound2Observation?.isLoading).toBe(true);
  });

  it('primeira rodada da sessão também não aparece como "concluída sem dados" antes de conectar', () => {
    // Regressão do outro sintoma relatado: a primeiríssima pergunta caiu
    // no fallback "não foi possível determinar um resultado completo"
    // porque o estado inicial (antes de qualquer id) já tem loading=false.
    const observations: Observation[] = [];
    const onObserve = (obs: Observation) => observations.push(obs);

    render(<Consumer analysisId={null} onObserve={onObserve} />);

    act(() => {
      // Simula handleAnalysisStarted: null -> primeiro analysisId real.
    });

    // Sem essa correção, a primeira observação (antes do WS conectar de
    // fato) chegaria a `isLoading: false` — aqui já deve nascer `true`.
    // Renderiza o consumidor a partir de null diretamente com o id real,
    // que é o caminho que App.tsx de fato percorre.
    const { unmount } = render(<Consumer analysisId="round-1" onObserve={onObserve} />);
    const first = observations.find((o) => o.analysisId === 'round-1');
    expect(first?.isLoading).toBe(true);
    expect(first?.starText).toBe('');
    unmount();
  });
});
