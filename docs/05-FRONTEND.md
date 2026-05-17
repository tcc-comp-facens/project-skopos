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

SPA (Single Page Application) em React 18 com TypeScript, usando Vite como bundler. A interface é organizada em **duas abas** — Usuário e Técnica — que compartilham o mesmo estado de análise. A aba Usuário apresenta os resultados de forma acessível ao público geral (exibindo apenas a arquitetura vencedora). A aba Técnica preserva toda a profundidade técnica, com painéis lado a lado, métricas de qualidade e relatório comparativo. Os resultados são exibidos em streaming em tempo real via WebSocket.

**Tecnologias:**
- React 18.3.1 (hooks funcionais, sem classes)
- TypeScript 5.5.3 (strict mode)
- Vite 5.3.4 (bundler + HMR)
- CSS puro (sem framework de estilização) com paleta Sophia

---

## Componentes

### App (`src/App.tsx`)

Componente principal que orquestra a interface com duas abas:

- Gerencia estado da análise (`analysisId`, `apiError`, `submitting`)
- Gerencia `activeTab` (`'user'` | `'tech'`), iniciando em `'user'`
- Gerencia toggles `useLlm` (default `true`) e `useLlmJudge` (default `false`)
- Deriva `winner` via `useMemo(() => parseWinner(ws.comparativeReport))`
- Integra com a API via `fetch` (POST /api/analysis), enviando `useLlm` e `useLlmJudge` no body
- Conecta ao WebSocket via hook `useWebSocket`
- Renderiza `<Header>`, `<TabNav>`, `<UserTab>` e `<TechTab>` (ambas sempre montadas, visibilidade controlada via CSS `display`)

**Fluxo:**
1. Usuário preenche formulário na aba Usuário e clica "Analisar"
2. `handleSubmit` envia POST com `{ ...request, useLlm, useLlmJudge }` → recebe `analysisId`
3. `useWebSocket(analysisId)` conecta e começa a receber eventos
4. Aba Usuário exibe resultado da arquitetura vencedora
5. Aba Técnica exibe ambas as arquiteturas lado a lado, métricas e relatório comparativo
6. Troca de aba não interrompe a análise em andamento (ambas as abas permanecem montadas no DOM; a visibilidade é controlada via `display: none/block`)

### Header (`src/components/Header.tsx`)

Identidade visual Sophia: barra superior com o brasão de Sorocaba (`src/assets/brasao-sorocaba.svg`), nome "Sophia" em destaque e subtítulo descritivo. Componente puramente visual, sem props.

### TabNav (`src/components/TabNav.tsx`)

Barra de navegação com duas abas: "Usuário" e "Técnica". Usa `role="tablist"`, `role="tab"`, `aria-selected` e `aria-controls` para acessibilidade.

### UserTab (`src/components/UserTab.tsx`)

Aba destinada ao público geral e servidores públicos:

- `<AnalysisControls>` é sempre visível (renderizado em todos os estados)
- Se análise em andamento → exibe indicador de carregamento ("Aguardando análise...")
- Se vencedor identificado → exibe `<WinnerPanel>` com resultado da arquitetura vencedora
- Se ambas as arquiteturas falharam → exibe mensagem de erro acessível
- NÃO exibe controles LLM, métricas técnicas ou benchmarks

### TechTab (`src/components/TechTab.tsx`)

Aba destinada a avaliadores técnicos e contexto do TCC:

- `<LlmControls>` — toggles LLM e LLM Judge
- Dois `<ArchitecturePanel>` lado a lado (Estrela e Hierárquica) com benchmarks
- `<QualityMetricsSection>` — cards de métricas E1-E2, Q1-Q3, R1
- `<ComparativeSection>` — relatório comparativo + LLM Judge
- NÃO exibe controles de data/parâmetros de saúde

### WinnerPanel (`src/components/WinnerPanel.tsx`)

Exibe o resultado da arquitetura vencedora em painel com borda dourada:
- Sem banner/badge — apenas o painel com destaque visual via borda `--sophia-gold`
- Renderiza `<ArchitecturePanel>` com `benchmarks={null}` (sem métricas técnicas)

### LlmControls (`src/components/LlmControls.tsx`)

Toggles de LLM e LLM Judge:
- **LLM** — habilita/desabilita síntese textual via LLM (Groq)
- **LLM Judge** — habilita avaliação Q2+ (LLM-as-Judge). **Desabilitado automaticamente quando o toggle LLM está desligado.**

### QualityMetricsSection (`src/components/QualityMetricsSection.tsx`)

Cards de métricas de qualidade organizados em três grupos:
- **Eficiência**: E1, E2
- **Qualidade**: Q1, Q2, Q3
- **Resiliência**: R1

Cada `<ScoreCard>` exibe valores de ambas as arquiteturas lado a lado.

### ComparativeSection (`src/components/ComparativeSection.tsx`)

Relatório comparativo e LLM Judge:
- Parsing linha a linha com formatação visual: títulos (`━━━`), vereditos (`→`), sucessos (`✓`), alertas (`✗`), bullets (`•`)
- Loading cursor durante streaming

### AnalysisControls (`src/components/AnalysisControls.tsx`)

Formulário de entrada (renderizado dentro de `UserTab`, sempre visível):

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `dateFrom` | number input | 2019 | Ano de início |
| `dateTo` | number input | 2021 | Ano de fim |
| `dengue` | toggle button | ativo | SINAN-DENG — Dengue |
| `covid` | toggle button | ativo | SINAN-INFL — COVID-19 |
| `vaccination` | toggle button | ativo | SI-PNI — Vacinação |
| `internacoes` | toggle button | ativo | SIH — Internações Hospitalares |
| `mortalidade` | toggle button | ativo | SIM — Mortalidade |

Os parâmetros de saúde são exibidos como botões de alternância (`<button aria-pressed>`), mostrando a sigla do sistema SUS e o nome em linguagem acessível. O estado ativo é indicado visualmente pela classe CSS `active`.

**Validação:**
- Botão "Analisar" desabilitado se nenhum parâmetro de saúde estiver ativo.
- Botão "Analisar" desabilitado e mensagem de erro exibida se `dateFrom > dateTo`.

### ArchitecturePanel (`src/components/ArchitecturePanel.tsx`)

Painel de resultado para cada arquitetura (usado em TechTab com benchmarks, e em WinnerPanel sem benchmarks):

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
  useLlm: boolean;
  useLlmJudge: boolean;
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
  type: 'chunk' | 'done' | 'error' | 'metric' | 'quality_metrics' | 'llm_judge' | 'llm_judge_done';
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

Tema dark com CSS puro e paleta Sophia:

| Aspecto | Implementação |
|---------|---------------|
| Fundo | `#2D3945` (azul escuro — `--sophia-dark`) |
| Cards/Painéis | `--surface-base` (#1E2A35), bordas `--sophia-mid` |
| Paleta | Variáveis CSS: `--sophia-dark`, `--sophia-mid`, `--sophia-vivid`, `--sophia-light`, `--sophia-gold`, `--sophia-gray`, `--sophia-warm` |
| Abas | `.tab-nav` com destaque `--sophia-vivid` na aba ativa |
| Vencedor | `.winner-panel` com borda e badge `--sophia-gold` |
| Score Cards | Grid responsivo com valores coloridos por arquitetura |
| Scrollbar | Customizada (fina, escura) |
| Loading | Cursor `▍` com animação de blink |
| Layout | Grid 2 colunas → 1 coluna em mobile |
| Responsividade | Media queries para telas menores |

---

## Acessibilidade

| Recurso | Implementação |
|---------|---------------|
| Navegação por abas | `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` |
| Conteúdo dinâmico | `aria-live="polite"` nas caixas de texto |
| Mensagens de erro | `role="alert"` |
| Labels | `<label htmlFor>` em todos os inputs |
| Fieldset | `<fieldset>` + `<legend>` para grupo de parâmetros de saúde |
| Toggle buttons | `aria-pressed` nos botões de parâmetros de saúde |
| data-testid | Em todos os elementos testáveis |
| Semântica | `<header>`, `<form>`, `<table>` com `<thead>`/`<tbody>` |
| Botão desabilitado | `disabled` quando nenhum parâmetro selecionado ou datas inválidas |

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
