# Frontend

## Sumário

1. [Visão Geral](#visão-geral)
2. [Componentes](#componentes)
3. [Hook useWebSocket](#hook-usewebsocket)
4. [Tipos TypeScript](#tipos-typescript)
5. [Configuração](#configuração)
6. [Estilização](#estilização)
7. [Acessibilidade](#acessibilidade)

---

## Visão Geral

SPA (Single Page Application) em React 18 com TypeScript, usando Vite como bundler. A interface exibe os resultados de ambas as arquiteturas lado a lado com streaming em tempo real via WebSocket.

**Tecnologias:**
- React 18.3.1 (hooks funcionais, sem classes)
- TypeScript 5.5.3 (strict mode)
- Vite 5.3.4 (bundler + HMR)
- CSS puro (sem framework de estilização)

---

## Componentes

### App (`src/App.tsx`)

Componente principal que orquestra a interface:

- Gerencia estado da análise (`analysisId`, `apiError`, `submitting`)
- Integra com a API via `fetch` (POST /api/analysis)
- Conecta ao WebSocket via hook `useWebSocket`
- Renderiza controles, painéis e relatório comparativo

**Fluxo:**
1. Usuário preenche formulário e clica "Analisar"
2. `handleSubmit` envia POST → recebe `analysisId`
3. `useWebSocket(analysisId)` conecta e começa a receber eventos
4. Painéis exibem texto em streaming
5. Após ambas completarem: relatório comparativo aparece

### AnalysisControls (`src/components/AnalysisControls.tsx`)

Formulário de entrada com:

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `dateFrom` | number input | 2019 | Ano de início |
| `dateTo` | number input | 2021 | Ano de fim |
| `dengue` | checkbox | ✓ | Incluir notificações de dengue |
| `covid` | checkbox | ✓ | Incluir notificações de COVID |
| `vaccination` | checkbox | ✓ | Incluir cobertura vacinal |
| `internacoes` | checkbox | ✓ | Incluir internações |
| `mortalidade` | checkbox | ✓ | Incluir mortalidade |

**Validação:** Botão "Analisar" desabilitado se nenhum parâmetro de saúde estiver marcado.

### ArchitecturePanel (`src/components/ArchitecturePanel.tsx`)

Painel de resultado para cada arquitetura:

| Elemento | Descrição |
|----------|-----------|
| Header | Ícone (🏛 hierárquica / ⭐ estrela) + título |
| Erro | Mensagem de erro com `role="alert"` |
| Caixa de texto | Streaming em tempo real com auto-scroll |
| Loading cursor | `▍` piscando durante streaming |
| Benchmarks | Tabela com tempo, CPU e memória por agente |

**Props:**
```typescript
interface ArchitecturePanelProps {
  title: string;
  text: string;
  benchmarks: BenchmarkMetrics | null;
  isLoading: boolean;
  error: string | null;
}
```

### Seção Relatório Comparativo

Renderizada no `App.tsx` quando `ws.comparativeReport` ou `ws.comparativeLoading` são truthy:

- Parsing linha a linha do relatório textual
- Formatação visual: divisores (`=====`), títulos de seção (`━━━`), veredictos (`→`), sucessos (`✓`), alertas (`✗`), bullets (`•`)
- Loading cursor durante streaming

---

## Hook useWebSocket

**Arquivo:** `src/hooks/useWebSocket.ts`

### Estado gerenciado

```typescript
interface UseWebSocketState {
  starText: string;              // Texto acumulado da estrela
  hierText: string;              // Texto acumulado da hierárquica
  starBenchmarks: BenchmarkMetrics | null;
  hierBenchmarks: BenchmarkMetrics | null;
  starLoading: boolean;
  hierLoading: boolean;
  starError: string | null;
  hierError: string | null;
  comparativeReport: string;     // Texto do relatório comparativo
  comparativeLoading: boolean;
  qualityMetrics: Record<string, unknown> | null;
}
```

### Comportamento

- Conecta automaticamente quando `analysisId` muda
- Reseta todo o estado quando `analysisId` muda
- Auto-reconnect em desconexão inesperada (máximo 3 tentativas, delay incremental)
- Não reconecta em close code 1000 (fechamento limpo)
- Processa eventos por `architecture` (star / hierarchical / both)

### Tratamento de eventos por architecture

| Architecture | chunk | done | error | metric | quality_metrics |
|-------------|-------|------|-------|--------|-----------------|
| `star` | Acumula texto | Para loading | Seta erro | Seta benchmarks | — |
| `hierarchical` | Acumula texto | Para loading | Seta erro | Seta benchmarks | — |
| `both` | Acumula relatório | Para loading relatório | — | — | Seta qualityMetrics |

---

## Tipos TypeScript

**Arquivo:** `src/types/index.ts`

```typescript
interface AnalysisRequest {
  dateFrom: number;
  dateTo: number;
  healthParams: {
    dengue: boolean;
    covid: boolean;
    vaccination: boolean;
    internacoes: boolean;
    mortalidade: boolean;
  };
}

interface AgentMetric {
  agentName: string;
  executionTimeMs: number;
  cpuPercent: number;
}

interface BenchmarkMetrics {
  architecture: 'star' | 'hierarchical';
  totalExecutionTimeMs: number;
  agentMetrics: AgentMetric[];
}

interface WSEvent {
  analysisId: string;
  architecture: 'star' | 'hierarchical' | 'both';
  type: 'chunk' | 'done' | 'error' | 'metric' | 'quality_metrics';
  payload: string | BenchmarkMetrics | Record<string, unknown>;
}
```

---

## Configuração

**Arquivo:** `src/config.ts`

```typescript
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
```

Configurável via variáveis de ambiente Vite (prefixo `VITE_`).

---

## Estilização

**Arquivo:** `src/styles.css`

Tema dark com CSS puro:

| Aspecto | Implementação |
|---------|---------------|
| Fundo | `#0f1117` (escuro) |
| Cards | Bordas sutis `#30363d`, fundo `#161b22` |
| Título | Gradiente azul/roxo |
| Scrollbar | Customizada (fina, escura) |
| Loading | Cursor `▍` com animação de blink |
| Layout | Grid 2 colunas → 1 coluna em mobile |
| Responsividade | Media queries para telas menores |

---

## Acessibilidade

| Recurso | Implementação |
|---------|---------------|
| Conteúdo dinâmico | `aria-live="polite"` nas caixas de texto |
| Mensagens de erro | `role="alert"` |
| Labels | `<label htmlFor>` em todos os inputs |
| Fieldset | `<fieldset>` + `<legend>` para grupo de checkboxes |
| data-testid | Em todos os elementos testáveis |
| Semântica | `<header>`, `<form>`, `<table>` com `<thead>`/`<tbody>` |
| Botão desabilitado | `disabled` quando nenhum parâmetro selecionado |

---

## Docker

**Arquivo:** `frontend/Dockerfile`

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
```

Em produção, o Vite serve na porta 5173. As variáveis `VITE_API_URL` e `VITE_WS_URL` são injetadas via environment no `docker-compose.yml`.
