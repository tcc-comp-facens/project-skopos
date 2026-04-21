import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App } from './App';

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

// Mock useWebSocket
const mockWsState = {
  starText: '',
  hierText: '',
  starBenchmarks: null,
  hierBenchmarks: null,
  starLoading: false,
  hierLoading: false,
  starError: null,
  hierError: null,
  comparativeReport: '',
  comparativeLoading: false,
  qualityMetrics: null,
};

vi.mock('./hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(() => mockWsState),
}));

// We need the mock reference to assert calls
import { useWebSocket } from './hooks/useWebSocket';
const mockUseWebSocket = vi.mocked(useWebSocket);

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function submitAnalysis() {
  fireEvent.click(screen.getByTestId('submit-button'));
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockUseWebSocket.mockReturnValue({ ...mockWsState });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders controls and both architecture panels', () => {
    render(<App />);
    expect(screen.getByTestId('analysis-controls')).toBeInTheDocument();
    expect(screen.getByTestId('panels-container')).toBeInTheDocument();
    // Two panels: hierárquica (left) and estrela (right)
    expect(screen.getByText('Hierárquica')).toBeInTheDocument();
    expect(screen.getByText('Estrela')).toBeInTheDocument();
  });

  it('uses 50/50 flex layout for panels', () => {
    render(<App />);
    const container = screen.getByTestId('panels-container');
    expect(container.style.display).toBe('flex');
  });

  it('calls useWebSocket with null initially', () => {
    render(<App />);
    expect(mockUseWebSocket).toHaveBeenCalledWith(null);
  });

  it('calls POST /api/analysis on submit and sets analysisId', async () => {
    const fakeId = 'test-analysis-123';
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ analysisId: fakeId }),
    });

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      // After successful POST, useWebSocket should be called with the analysisId
      expect(mockUseWebSocket).toHaveBeenCalledWith(fakeId);
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/analysis'),
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });

  it('shows API error when POST fails', async () => {
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    });

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(screen.getByTestId('api-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('api-error').textContent).toBe('Internal Server Error');
  });

  it('shows API error on network failure', async () => {
    globalThis.fetch = vi.fn().mockRejectedValueOnce(new Error('Network error'));

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(screen.getByTestId('api-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('api-error').textContent).toBe('Network error');
  });

  it('passes WebSocket state to architecture panels', () => {
    mockUseWebSocket.mockReturnValue({
      ...mockWsState,
      starText: 'Star analysis text',
      hierText: 'Hier analysis text',
      starLoading: true,
      hierLoading: false,
    });

    render(<App />);

    // The panel text boxes should contain the text from the hook
    const textBoxes = screen.getAllByTestId('panel-text-box');
    expect(textBoxes[0].textContent).toContain('Hier analysis text');
    expect(textBoxes[1].textContent).toContain('Star analysis text');
  });

  it('shows submitting indicator while POST is in flight', async () => {
    // Create a fetch that never resolves immediately
    let resolveFetch!: (value: unknown) => void;
    globalThis.fetch = vi.fn().mockReturnValueOnce(
      new Promise((resolve) => { resolveFetch = resolve; }),
    );

    render(<App />);
    submitAnalysis();

    await waitFor(() => {
      expect(screen.getByTestId('submitting-indicator')).toBeInTheDocument();
    });

    // Resolve the fetch
    resolveFetch({ ok: true, json: async () => ({ analysisId: 'abc' }) });

    await waitFor(() => {
      expect(screen.queryByTestId('submitting-indicator')).not.toBeInTheDocument();
    });
  });
});
