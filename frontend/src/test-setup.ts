import '@testing-library/jest-dom';

// jsdom não implementa scrollIntoView — usado pelo auto-scroll do chat.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// jsdom não implementa ResizeObserver — usado pelo ResponsiveContainer do
// Recharts (AgentDataCard). Sem isso os testes que renderizam gráficos falham.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
