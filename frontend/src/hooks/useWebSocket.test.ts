import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWebSocket } from './useWebSocket';
import type { WSEvent, BenchmarkMetrics } from '../types';

// --- Mock WebSocket ---

type WSListener = ((ev: { data: string }) => void) | null;
type WSCloseListener = ((ev: { code: number }) => void) | null;
type WSOpenListener = (() => void) | null;

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  url: string;
  onmessage: WSListener = null;
  onclose: WSCloseListener = null;
  onopen: WSOpenListener = null;
  onerror: (() => void) | null = null;
  readyState = 0;
  closeCalled = false;
  closeCode?: number;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(code?: number) {
    this.closeCalled = true;
    this.closeCode = code;
  }

  simulateOpen() {
    this.readyState = 1;
    this.onopen?.();
  }

  simulateMessage(data: WSEvent) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  simulateClose(code = 1006) {
    this.onclose?.({ code });
  }
}

vi.stubGlobal('WebSocket', MockWebSocket);

function latestWs(): MockWebSocket {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1];
}

function makeEvent(
  overrides: Partial<WSEvent> & Pick<WSEvent, 'architecture' | 'type' | 'payload'>,
): WSEvent {
  return { analysisId: 'test-id', ...overrides };
}

describe('useWebSocket', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should not connect when analysisId is null', () => {
    renderHook(() => useWebSocket(null));
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('should connect when analysisId is provided', () => {
    renderHook(() => useWebSocket('abc-123'));
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latestWs().url).toContain('/ws/abc-123');
  });

  it('should return initial loading state when analysisId is set', () => {
    const { result } = renderHook(() => useWebSocket('abc-123'));
    expect(result.current.starLoading).toBe(true);
    expect(result.current.hierLoading).toBe(true);
    expect(result.current.starText).toBe('');
    expect(result.current.hierText).toBe('');
    expect(result.current.starBenchmarks).toBeNull();
    expect(result.current.hierBenchmarks).toBeNull();
    expect(result.current.starError).toBeNull();
    expect(result.current.hierError).toBeNull();
  });

  it('should accumulate star chunk text', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'chunk', payload: 'Hello ' }));
    });
    expect(result.current.starText).toBe('Hello ');

    act(() => {
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'chunk', payload: 'World' }));
    });
    expect(result.current.starText).toBe('Hello World');
    expect(result.current.starLoading).toBe(true);
  });

  it('should accumulate hierarchical chunk text', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(
        makeEvent({ architecture: 'hierarchical', type: 'chunk', payload: 'Análise ' }),
      );
      ws.simulateMessage(
        makeEvent({ architecture: 'hierarchical', type: 'chunk', payload: 'completa' }),
      );
    });
    expect(result.current.hierText).toBe('Análise completa');
    expect(result.current.hierLoading).toBe(true);
  });

  it('should set starLoading to false on done event', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'chunk', payload: 'text' }));
    });
    expect(result.current.starLoading).toBe(true);

    act(() => {
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'done', payload: '' }));
    });
    expect(result.current.starLoading).toBe(false);
  });

  it('should set hierLoading to false on done event', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(
        makeEvent({ architecture: 'hierarchical', type: 'done', payload: '' }),
      );
    });
    expect(result.current.hierLoading).toBe(false);
  });

  it('should store star error and stop loading', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(
        makeEvent({ architecture: 'star', type: 'error', payload: 'Neo4j timeout' }),
      );
    });
    expect(result.current.starError).toBe('Neo4j timeout');
    expect(result.current.starLoading).toBe(false);
  });

  it('should store hierarchical error and stop loading', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(
        makeEvent({ architecture: 'hierarchical', type: 'error', payload: 'LLM failure' }),
      );
    });
    expect(result.current.hierError).toBe('LLM failure');
    expect(result.current.hierLoading).toBe(false);
  });

  it('should store star benchmark metrics', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    const metrics: BenchmarkMetrics = {
      architecture: 'star',
      totalExecutionTimeMs: 1500,
      agentMetrics: [
        { agentName: 'consultor', executionTimeMs: 500, cpuPercent: 12, memoryMb: 64 },
      ],
    };

    act(() => {
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'metric', payload: metrics }));
    });
    expect(result.current.starBenchmarks).toEqual(metrics);
  });

  it('should store hierarchical benchmark metrics', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    const metrics: BenchmarkMetrics = {
      architecture: 'hierarchical',
      totalExecutionTimeMs: 2000,
      agentMetrics: [
        { agentName: 'coordenador', executionTimeMs: 800, cpuPercent: 20, memoryMb: 128 },
      ],
    };

    act(() => {
      ws.simulateMessage(
        makeEvent({ architecture: 'hierarchical', type: 'metric', payload: metrics }),
      );
    });
    expect(result.current.hierBenchmarks).toEqual(metrics);
  });

  it('should handle both architectures independently', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'chunk', payload: 'Star ' }));
      ws.simulateMessage(
        makeEvent({ architecture: 'hierarchical', type: 'chunk', payload: 'Hier ' }),
      );
      ws.simulateMessage(makeEvent({ architecture: 'star', type: 'done', payload: '' }));
    });

    expect(result.current.starText).toBe('Star ');
    expect(result.current.hierText).toBe('Hier ');
    expect(result.current.starLoading).toBe(false);
    expect(result.current.hierLoading).toBe(true);
  });

  it('should attempt reconnection up to 3 times on unexpected close', () => {
    renderHook(() => useWebSocket('id-1'));
    expect(MockWebSocket.instances).toHaveLength(1);

    // Simulate unexpected close
    act(() => {
      latestWs().simulateClose(1006);
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => {
      latestWs().simulateClose(1006);
    });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(MockWebSocket.instances).toHaveLength(3);

    act(() => {
      latestWs().simulateClose(1006);
    });
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(MockWebSocket.instances).toHaveLength(4);

    // 4th close should NOT reconnect (max 3 retries)
    act(() => {
      latestWs().simulateClose(1006);
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(MockWebSocket.instances).toHaveLength(4);
  });

  it('should not reconnect on clean close (code 1000)', () => {
    renderHook(() => useWebSocket('id-1'));
    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => {
      latestWs().simulateClose(1000);
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('should reset reconnect counter on successful open', () => {
    renderHook(() => useWebSocket('id-1'));

    // First unexpected close + reconnect
    act(() => {
      latestWs().simulateClose(1006);
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    // Successful open resets counter
    act(() => {
      latestWs().simulateOpen();
    });

    // Another unexpected close — should reconnect again from attempt 1
    act(() => {
      latestWs().simulateClose(1006);
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it('should close WebSocket and reset state when analysisId changes', () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useWebSocket(id),
      { initialProps: { id: 'id-1' as string | null } },
    );

    const ws1 = latestWs();
    ws1.simulateOpen();

    act(() => {
      ws1.simulateMessage(makeEvent({ architecture: 'star', type: 'chunk', payload: 'data' }));
    });
    expect(result.current.starText).toBe('data');

    // Change analysisId
    rerender({ id: 'id-2' });

    expect(ws1.closeCalled).toBe(true);
    expect(result.current.starText).toBe('');
    expect(result.current.starLoading).toBe(true);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(latestWs().url).toContain('/ws/id-2');
  });

  it('should close WebSocket when analysisId becomes null', () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useWebSocket(id),
      { initialProps: { id: 'id-1' as string | null } },
    );

    const ws1 = latestWs();

    rerender({ id: null });

    expect(ws1.closeCalled).toBe(true);
    expect(result.current.starLoading).toBe(false);
    expect(result.current.hierLoading).toBe(false);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('should ignore malformed messages', () => {
    const { result } = renderHook(() => useWebSocket('id-1'));
    const ws = latestWs();
    ws.simulateOpen();

    act(() => {
      ws.onmessage?.({ data: 'not-json{{{' });
    });

    expect(result.current.starText).toBe('');
    expect(result.current.hierText).toBe('');
  });
});
