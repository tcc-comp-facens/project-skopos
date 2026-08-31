# Frontend

## Sumário

1. [Visão Geral](#visão-geral)
2. [Componentes — Chat (aba Usuário)](#componentes--chat-aba-usuário)
3. [Componentes — Aba Técnica](#componentes--aba-técnica)
4. [Componentes Compartilhados](#componentes-compartilhados)
5. [Hooks WebSocket](#hooks-websocket)
6. [Tipos TypeScript](#tipos-typescript)
7. [Configuração](#configuração)
8. [Estilização](#estilização)
9. [Acessibilidade](#acessibilidade)

---

## Visão Geral

SPA (Single Page Application) em React 18 com TypeScript, usando Vite como bundler. A interface é organizada em **duas abas** — Usuário e Técnica — que compartilham o mesmo estado de rodadas de chat. A aba Usuário é um **chat de texto livre** (`ChatInterface`) que interpreta a intenção do usuário e dispara análises — não há mais formulário estruturado nessa aba (o antigo `AnalysisControls` foi removido). A aba Técnica preserva toda a profundidade técnica, com painéis lado a lado, métricas de qualidade e relatório comparativo, agora **navegável por rodada** — cada pergunta do chat vira uma "rodada" que pode ser revisitada.

**Tecnologias:**
- React 18.3.1 (hooks funcionais, sem classes — exceto `ErrorBoundary`, que exige classe em React)
- TypeScript 5.5.3 (strict mode)
- Vite 5.3.4 (bundler + HMR)
- CSS puro (sem framework de estilização) com paleta Sophia

---

## Componentes — Chat (aba Usuário)

### App (`src/App.tsx`)

Componente principal. Gerencia o estado de **rodadas de chat** (`rounds: ChatRound[]`) — cada pergunta que dispara uma análise vira uma rodada:

- `analysisId` — id da análise ativa (rodada mais recente em andamento)
- `rounds` — histórico de rodadas, cada uma com `{ id, question, startedAt, snapshot }`
- `activeTab` (`'user'` | `'tech'`), iniciando em `'user'`
- Deriva `winner` via `useMemo(() => parseWinner(ws.comparativeReport))`
- `ws = useWebSocket(analysisId)` — um único hook de resultados, cujo estado é espelhado a cada atualização na rodada correspondente de `rounds` (via `useEffect`); quando a próxima rodada começa, `analysisId` muda, `useWebSocket` reseta sozinho, e o último snapshot espelhado fica **congelado** no array `rounds` — única fonte de verdade para rodadas passadas (a fila do backend é de consumidor único e é descartada ao terminar, então uma rodada antiga não pode ser "re-conectada")
- Envolve `TechTab` num `<ErrorBoundary>` (chat fica fora — crash na aba técnica não deve derrubar o chat)

**Fluxo:**
1. Usuário digita uma pergunta no `ChatInterface` (aba Usuário)
2. `ChatInterface` dispara sua própria conexão de intenção (`useChatWebSocket`) e, quando a análise começa, chama `onAnalysisStarted(analysisId, question)` — `App` cria uma nova rodada e atualiza `analysisId`
3. `useWebSocket(analysisId)` conecta e começa a receber eventos de resultado (mesmo hook que já existia, sem mudança de protocolo)
4. Aba Usuário exibe o resultado da rodada ativa via `WinnerPanel`, abaixo da conversa
5. Aba Técnica exibe qualquer rodada (mais recente por padrão, ou selecionada manualmente via `RoundSelector`) com ambas as arquiteturas lado a lado, métricas e relatório comparativo
6. Troca de aba não interrompe a análise em andamento (ambas as abas permanecem montadas no DOM; a visibilidade é controlada via `display: none/block`)

### ChatInterface (`src/components/ChatInterface.tsx`)

Chat de texto livre — autocontido, com sua própria conexão WebSocket (`useChatWebSocket`) para o turno de intenção. O resultado da análise disparada é acompanhado via `activeRoundWs` (o mesmo estado que alimenta a aba técnica), evitando duplicar o streaming de resultados.

- Mensagem de boas-vindas busca `GET /api/data-range` para informar o intervalo de anos realmente disponível (best-effort — se falhar, mantém texto genérico)
- `handleSend`: valida (`isBlank`/`isTooLong`), adiciona a mensagem do usuário e uma bolha de sistema vazia em streaming, chama `chat.sendMessage(text)`
- Callbacks do `useChatWebSocket`: `onChunk` (acrescenta token à bolha em streaming), `onDone` (marca fim do streaming), `onError` (bolha de erro), `onAnalysisStarted` (propaga para `App` via `onAnalysisStarted` prop)
- Assim que a rodada ativa termina de carregar (`activeRoundWs` para de estar em loading), acrescenta uma bolha de **resumo** gerada por `formatRoundSummary` — cada `analysisId` só gera um resumo (guard via ref, evita duplicar em re-renders)
- Indicador de status de conexão (`connectionStatus`) quando não está `'connected'`
- Enter envia a mensagem (Shift+Enter quebra linha); textarea desabilitada enquanto aguarda resposta
- Contador de caracteres exibido quando o input não está vazio

### MessageBubble (`src/components/MessageBubble.tsx`)

Bolha de mensagem (usuário à direita, sistema à esquerda). Renderiza sempre como texto puro (nunca `dangerouslySetInnerHTML`), mesmo para conteúdo vindo do LLM, para não abrir espaço a XSS. Mostra `<TypingIndicator>` quando `message.isStreaming`; `role="alert"` quando `message.isError`.

### TypingIndicator (`src/components/TypingIndicator.tsx`)

Pontinhos animados via CSS puro, sem `setInterval` em JS.

### WinnerPanel (`src/components/WinnerPanel.tsx`)

Exibe o resultado da rodada ativa em painel com borda dourada, abaixo da conversa:
- Sem banner/badge — apenas o painel com destaque visual via borda `--sophia-gold`
- Renderiza `<ArchitecturePanel>` com `benchmarks={null}` (sem métricas técnicas)

### UserTab (`src/components/UserTab.tsx`)

Aba pública: `<ChatInterface>` sempre visível, mais (condicionalmente) um indicador de carregamento, o `<WinnerPanel>` da rodada ativa, ou uma mensagem de erro acessível se ambas as arquiteturas falharem.

---

## Componentes — Aba Técnica

### TechTab (`src/components/TechTab.tsx`)

Destinada a avaliadores técnicos e pesquisadores do TCC — agora **navegável por rodada**, já que uma sessão de chat pode disparar várias análises:

- `<RoundSelector>` — escolhe qual rodada inspecionar
- A rodada mais recente é selecionada automaticamente assim que começa (`useEffect` que observa o tamanho de `rounds`); uma seleção manual do avaliador persiste até a próxima rodada começar
- Dois `<ArchitecturePanel>` lado a lado, `<QualityMetricsSection>`, `<ComparativeSection>` — todos alimentados pelo `snapshot` da rodada selecionada (`INITIAL_STATE` se nenhuma rodada existe ainda)

### RoundSelector (`src/components/RoundSelector.tsx`)

Permite inspecionar, na aba técnica, os resultados de qualquer rodada de chat já disparada — não só a mais recente. Um `<select>` com uma opção por rodada (`"Rodada N — HH:MM — pergunta truncada"`); mostra mensagem vazia ("Nenhuma pergunta feita ainda") quando `rounds` está vazio.

### ArchitecturePanel (`src/components/ArchitecturePanel.tsx`)

Painel de resultado para cada arquitetura (usado em `TechTab` com benchmarks, e em `WinnerPanel` sem benchmarks):

| Elemento | Descrição |
|----------|-----------|
| Header | Ícone (🏛 hierárquica / ⭐ estrela) + título |
| Erro | Mensagem de erro com `role="alert"` |
| Caixa de texto | Streaming em tempo real com auto-scroll |
| Loading cursor | `▍` piscando durante streaming |
| Benchmarks | Tabela com tempo, CPU e memória por agente |

```typescript
interface ArchitecturePanelProps {
  title: string;
  text: string;
  benchmarks: BenchmarkMetrics | null;
  isLoading: boolean;
  error: string | null;
}
```

### QualityMetricsSection (`src/components/QualityMetricsSection.tsx`)

Cards de métricas de qualidade (via `ScoreCard`, `src/components/ScoreCard.tsx`) organizados em três grupos:
- **Eficiência**: E1, E2
- **Qualidade**: Q1, Q3
- **Resiliência**: R1

Cada `ScoreCard` exibe valores de ambas as arquiteturas lado a lado. Não há card de fidelidade: ela agora vem do RAGAS, que é opcional, chega depois destes cards (eventos `ragas`/`ragas_done`) e é exibida no painel do relatório comparativo.

### ComparativeSection (`src/components/ComparativeSection.tsx`)

Relatório comparativo e avaliação RAGAS:
- Parsing linha a linha com formatação visual: títulos (`━━━`), vereditos (`→`), sucessos (`✓`), alertas (`✗`), bullets (`•`)
- Painel RAGAS separado (`ragasText`/`ragasLoading`), com destaque para scores ≥ 0.80; um score ausente aparece como "não disponível", nunca como zero. O RAGAS chega **antes** do relatório (o veredito depende dele), então a seção aparece assim que o primeiro chunk de RAGAS chega, com o corpo do relatório ainda vazio
- Loading cursor durante streaming

---

## Componentes Compartilhados

### Header (`src/components/Header.tsx`)

Identidade visual Sophia: barra superior com o brasão de Sorocaba (`src/assets/brasao-sorocaba.svg`), nome "Sophia" em destaque e subtítulo descritivo. Componente puramente visual, sem props.

### TabNav (`src/components/TabNav.tsx`)

Barra de navegação com duas abas: "Usuário" e "Técnica". Usa `role="tablist"`, `role="tab"`, `aria-selected` e `aria-controls` para acessibilidade.

### ErrorBoundary (`src/components/ErrorBoundary.tsx`)

Error boundary genérico (classe React — é o único jeito de implementar `componentDidCatch`/`getDerivedStateFromError`) para capturar crashes de componentes filhos e exibir uma mensagem de erro em vez de tela branca. Usado envolvendo `<TechTab>` em `App.tsx`.

---

## Hooks WebSocket

### useWebSocket (`src/hooks/useWebSocket.ts`)

Streaming de **resultados** de uma análise (`/ws/{analysisId}`) — inalterado na essência desde antes do chat existir, mas o estado (`UseWebSocketState`) foi movido para `types/index.ts` (evita import circular com `ChatRound`, que também precisa desse tipo) e ganhou dois campos novos para a avaliação RAGAS (`ragasText`, `ragasLoading`, alimentados pelos eventos `ragas`/`ragas_done`).

```typescript
export const INITIAL_STATE: UseWebSocketState = {
  starText: '', hierText: '',
  starBenchmarks: null, hierBenchmarks: null,
  starLoading: false, hierLoading: false,
  starError: null, hierError: null,
  comparativeReport: '', comparativeLoading: false,
  qualityMetrics: null,
  llmJudgeText: '', llmJudgeLoading: false,
};
```

**Comportamento:**
- Conecta automaticamente quando `analysisId` muda; reseta todo o estado (`INITIAL_STATE`) nessa troca
- Auto-reconnect em desconexão inesperada (máximo 3 tentativas, delay **linear**: `1000 * tentativa` ms)
- Não reconecta em close code 1000 (fechamento limpo)
- Processa eventos por `architecture` (`star` / `hierarchical` / `both`), incluindo `ragas`/`ragas_done` (acumula `ragasText`, controla `ragasLoading`)

### useChatWebSocket (`src/hooks/useChatWebSocket.ts`)

Conexão com o **turno de intenção** do chat (`/ws/chat/{sessionId}`) — self-contained, gera seu próprio `sessionId` (`crypto.randomUUID()`, só em memória, sem persistência entre reloads).

```typescript
export interface UseChatWebSocketCallbacks {
  onAck?: () => void;
  onChunk: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
  onAnalysisStarted: (analysisId: string) => void;
}
```

**Comportamento:**
- Reconexão com **backoff exponencial** (1s/2s/4s, máx. 3 tentativas) — diferente do backoff linear de `useWebSocket`
- Fila de mensagens de saída (`outgoingQueueRef`) — se `sendMessage` é chamado antes da conexão abrir, a mensagem é enfileirada e entregue em ordem assim que a conexão volta (`flushQueue`)
- `connectionStatus`: `'connected'` | `'disconnected'` | `'reconnecting'`, mais `hasEverConnected` (distingue "conectando pela primeira vez" de "conexão perdida depois de já ter funcionado")

---

## Tipos TypeScript

**Arquivo:** `src/types/index.ts`

```typescript
// Resultados (WS /ws/{analysisId})
interface WSEvent {
  analysisId: string;
  architecture: 'star' | 'hierarchical' | 'both';
  type: 'chunk' | 'done' | 'error' | 'metric' | 'quality_metrics' | 'ragas' | 'ragas_done';
  payload: string | BenchmarkMetrics | Record<string, unknown>;
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

// Chat (WS /ws/chat/{sessionId})
interface ChatMessage {
  id: string;
  role: 'user' | 'system';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  isError?: boolean;
}

interface ChatWSEvent {
  type: 'user_ack' | 'system_chunk' | 'system_done' | 'error' | 'analysis_started';
  payload: string;
}

type ConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

// Uma "rodada" = uma pergunta do chat que disparou uma análise.
// snapshot é o estado do useWebSocket espelhado no momento mais recente,
// congelado assim que a próxima rodada começa.
interface ChatRound {
  id: string; // analysisId
  question: string;
  startedAt: string;
  snapshot: UseWebSocketState;
}

type ActiveTab = 'user' | 'tech';
type WinnerArchitecture = 'star' | 'hierarchical' | null;

// Métricas de qualidade tipadas (payload do evento quality_metrics)
interface QualityMetrics {
  star: ArchitectureQualityMetrics;
  hierarchical: ArchitectureQualityMetrics;
}
interface ArchitectureQualityMetrics {
  efficiency: { E1: number; E2: number };
  quality: { Q1: number; Q3: number };
  resilience: { R1: number };
}
```

`UseWebSocketState` (ver [Hooks WebSocket](#hooks-websocket)) também vive neste arquivo, não em `useWebSocket.ts`, especificamente para ser reaproveitado por `ChatRound` sem import circular.

---

## Configuração

**Arquivo:** `src/config.ts`

```typescript
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
```

Configurável via variáveis de ambiente Vite (prefixo `VITE_`).

**Arquivo:** `src/utils/validateMessage.ts`

```typescript
export const MAX_CHAT_MESSAGE_LENGTH = 1000; // espelha o limite do backend (chat_websocket.py)
export function isBlank(text: string): boolean;
export function isTooLong(text: string): boolean;
```

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
| Chat | `.chat-container`, `.message-bubble` (`.user` / `.system` / `.error`), `.chat-input-row`, `.connection-status` |
| Vencedor | `.winner-panel` com borda e badge `--sophia-gold` |
| Score Cards | Grid responsivo com valores coloridos por arquitetura |
| Scrollbar | Customizada (fina, escura) |
| Loading | Cursor `▍` com animação de blink; `.typing-indicator` (pontinhos animados) |
| Layout | Grid 2 colunas → 1 coluna em mobile |
| Responsividade | Media queries para telas menores |

---

## Acessibilidade

| Recurso | Implementação |
|---------|---------------|
| Navegação por abas | `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` |
| Conteúdo dinâmico | `aria-live="polite"` nas caixas de texto e nas mensagens do chat |
| Mensagens de erro | `role="alert"` (bolhas de erro, status de conexão, painel de erro) |
| Chat | `<textarea>` com `maxLength`, `disabled` durante aguardo de resposta; botão "Enviar" desabilitado se vazio ou aguardando |
| Seletor de rodada | `aria-label="Selecionar rodada de análise"` |
| Labels | `<label htmlFor>` em inputs remanescentes |
| Toggle buttons | `aria-pressed` |
| data-testid | Em todos os elementos testáveis |
| Semântica | `<header>`, `<table>` com `<thead>`/`<tbody>` |
| Botão desabilitado | `disabled` quando aguardando resposta, mensagem vazia/muito longa, ou rodada ativa em andamento (toggles LLM) |

---

## Testes

9 arquivos de teste (Vitest + @testing-library/react):

| Arquivo | Escopo |
|---------|--------|
| `src/App.integration.test.tsx` | Integração ponta a ponta (rodadas de chat, troca de aba) |
| `src/components/ChatInterface.test.tsx` | Chat (envio, streaming, validação de mensagem) |
| `src/components/TechTab.test.tsx` | Seleção de rodada, exibição de painéis |
| `src/components/TabNav.test.tsx` | Navegação entre abas (acessibilidade) |
| `src/components/WinnerPanel.test.tsx` | Painel do vencedor (texto, erro, título) |
| `src/components/Header.test.tsx` | Identidade visual (Sophia, brasão) |
| `src/utils/parseWinner.test.ts` | Extração do vencedor do relatório comparativo |
| `src/utils/validateMessage.test.ts` | Validação de mensagem (vazio, tamanho máximo) |

Não há testes dedicados para `UserTab`, `RoundSelector`, `MessageBubble`, `TypingIndicator`, `ErrorBoundary`, `ScoreCard`, `QualityMetricsSection`, `ComparativeSection`, `useWebSocket` ou `useChatWebSocket` — cobertura desses fica a cargo do teste de integração (`App.integration.test.tsx`) e dos testes dos componentes que os consomem.

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
