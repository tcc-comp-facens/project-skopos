# Frontend

## Sumário

1. [Visão Geral](#visão-geral)
2. [Componentes — Chat (aba Saúde)](#componentes--chat-aba-saúde)
3. [Componentes — Aba Agentes](#componentes--aba-agentes)
4. [Componentes Compartilhados](#componentes-compartilhados)
5. [Hooks WebSocket](#hooks-websocket)
6. [Tipos TypeScript](#tipos-typescript)
7. [Configuração](#configuração)
8. [Estilização](#estilização)
9. [Acessibilidade](#acessibilidade)

---

## Visão Geral

SPA (Single Page Application) em React 18 com TypeScript, usando Vite como bundler. A interface é organizada em **duas abas** — rotuladas **"Saúde"** e **"Agentes"** na UI (identificadores internos permanecem `'user'`/`'tech'`) — que compartilham o mesmo estado de rodadas de chat. A aba Saúde é um **chat de texto livre** (`ChatInterface`) que interpreta a intenção do usuário e dispara análises — não há formulário estruturado. A aba Agentes preserva toda a profundidade técnica (dois painéis de arquitetura lado a lado, métricas de qualidade, avaliação RAGAS, relatório comparativo, dados brutos por agente), navegável por rodada — cada pergunta do chat vira uma "rodada" que pode ser revisitada.

**Tecnologias:**
- React 18.3.1 (hooks funcionais, sem classes — exceto `ErrorBoundary`, que exige classe em React)
- TypeScript 5.5.3 (strict mode)
- Vite 5.3.4 (bundler + HMR)
- Recharts (gráfico de barras nos dados brutos por agente)
- CSS puro (sem framework de estilização) com paleta Sophia

---

## Componentes — Chat (aba Saúde)

### App (`src/App.tsx`)

Componente principal. Gerencia o estado de **rodadas de chat** (`rounds: ChatRound[]`) — cada pergunta que dispara uma análise vira uma rodada:

- `analysisId` — id da análise ativa (rodada mais recente em andamento)
- `rounds` — histórico de rodadas, cada uma com `{ id, question, startedAt, snapshot }`
- `activeTab` (`'user'` | `'tech'`), iniciando em `'user'`
- `ws = useWebSocket(analysisId)` — um único hook de resultados, cujo estado é espelhado a cada atualização na rodada correspondente de `rounds` (via `useEffect`); quando a próxima rodada começa, `analysisId` muda, `useWebSocket` reseta sozinho, e o último snapshot espelhado fica **congelado** no array `rounds` — única fonte de verdade para rodadas passadas (a fila do backend é de consumidor único e é descartada ao terminar, então uma rodada antiga não pode ser "re-conectada")
- Envolve a aba Agentes (`TechTab`) num `<ErrorBoundary>` (chat fica fora — crash na aba técnica não deve derrubar o chat)

**Fluxo:**
1. Usuário digita uma pergunta no `ChatInterface` (aba Saúde)
2. `ChatInterface` dispara sua própria conexão de intenção (`useChatWebSocket`) e, quando a análise começa, chama `onAnalysisStarted(analysisId, question)` — `App` cria uma nova rodada e atualiza `analysisId`
3. `useWebSocket(analysisId)` conecta e começa a receber eventos de resultado (streaming, métricas, dados de agente, RAGAS)
4. A resposta da análise aparece **dentro da própria conversa**, como uma mensagem do chat (não mais num card separado abaixo dela — comportamento antigo do já removido `WinnerPanel`)
5. A aba Agentes exibe qualquer rodada (mais recente por padrão, ou selecionada manualmente) com ambas as arquiteturas lado a lado, métricas, dados brutos e relatório comparativo
6. Troca de aba não interrompe a análise em andamento (ambas as abas permanecem montadas no DOM; a visibilidade é controlada via `display: none/block`)

### ChatInterface (`src/components/ChatInterface.tsx`)

Chat de texto livre — autocontido, com sua própria conexão WebSocket (`useChatWebSocket`) para o turno de intenção. O resultado da análise disparada é acompanhado via `activeRoundWs` (o mesmo estado que alimenta a aba Agentes), evitando duplicar o streaming de resultados.

- Mensagem de boas-vindas busca `GET /api/data-range` para informar o intervalo de anos realmente disponível (best-effort — se falhar, mantém texto genérico)
- `handleSend`: valida (`isBlank`/`isTooLong`), adiciona a mensagem do usuário e uma bolha de sistema vazia em streaming, chama `chat.sendMessage(text)`
- **A resposta da análise aparece como uma mensagem normal do chat**, não num painel separado: assim que a rodada começa, uma bolha placeholder ("Analisando os dados…") é criada; assim que **qualquer uma** das duas arquiteturas produzir o primeiro chunk de texto, a bolha passa a acompanhar essa arquitetura ao vivo (`kind: 'architecture_answer'`), preenchida chunk a chunk pelo `starText`/`hierText` real do WebSocket — sem esperar a outra arquitetura nem o relatório comparativo terminar. Isso significa que a bolha de resposta tipicamente finaliza antes do veredito de "vencedor" estar pronto; quando o relatório comparativo chega a tempo e aponta a mesma arquitetura seguida como vencedora, um badge de emoji (⭐/🏛) é adicionado à bolha — caso contrário a resposta fica sem badge, nunca sem conteúdo
- Se nenhuma das duas arquiteturas chegar a produzir texto (falha total), a bolha é finalizada com um resumo de erro via `formatRoundSummary`
- Indicador de status de conexão (`connectionStatus`) quando não está `'connected'`
- Enter envia a mensagem (Shift+Enter quebra linha); textarea desabilitada enquanto aguarda resposta
- Contador de caracteres exibido quando o input não está vazio

### MessageBubble (`src/components/MessageBubble.tsx`)

Bolha de mensagem (usuário à direita, sistema à esquerda). O conteúdo passa por `FormattedText` (`src/utils/renderFormattedText.tsx`), que aplica Markdown básico (negrito, itálico, listas) construindo **só elementos React** — nunca `dangerouslySetInnerHTML`, mesmo para texto vindo do LLM, para não abrir espaço a XSS. Mostra `<TypingIndicator>` quando `message.isStreaming`; `role="alert"` quando `message.isError`; um badge de emoji (⭐ Estrela / 🏛 Hierárquica) quando `message.architecture` está definido (ver `ChatInterface`).

### TypingIndicator (`src/components/TypingIndicator.tsx`)

Pontinhos animados via CSS puro, sem `setInterval` em JS.

---

## Componentes — Aba Agentes

### TechTab (`src/components/TechTab.tsx`)

Destinada a avaliadores técnicos e pesquisadores do TCC. Não há mais controles de LLM aqui — a síntese via LLM e a avaliação RAGAS são incondicionais nesta versão do frontend, então não há o que alternar. Renderiza `<RoundOverview>` com todas as rodadas da sessão.

### RoundOverview (`src/components/RoundOverview.tsx`)

Panorama de **todas** as rodadas da sessão (substitui o antigo `RoundSelector`, que só permitia inspecionar uma rodada de cada vez via `<select>`). A rodada mais recente começa expandida; as anteriores começam fechadas mas mantêm seu próprio estado de expansão, sem serem recolhidas quando uma nova rodada chega. Mostra mensagem vazia quando `rounds` está vazio.

### RoundCard (`src/components/RoundCard.tsx`)

Uma rodada de pergunta/resposta, com cabeçalho sempre visível (índice, horário, pergunta truncada, badge de vencedor se houver, tempo total de cada arquitetura) e conteúdo pesado (`RoundDetail`) que só monta quando expandido — evita pesar a página conforme a sessão acumula rodadas.

### RoundDetail (`src/components/RoundDetail.tsx`)

Conteúdo técnico completo de uma rodada: os dois `<ArchitecturePanel>` (Hierárquica e Estrela), `<QualityMetricsSection>` e `<ComparativeSection>`, todos alimentados pelo `snapshot` congelado da rodada.

### ArchitecturePanel (`src/components/ArchitecturePanel.tsx`)

Painel de resultado por arquitetura:

| Elemento | Descrição |
|----------|-----------|
| Header | Ícone (🏛 hierárquica / ⭐ estrela) + título |
| Erro | Mensagem de erro com `role="alert"` |
| Caixa de texto | Streaming em tempo real com auto-scroll |
| Loading | Spinner + "Gerando análise..." |
| Benchmarks | Tabela com tempo, CPU por agente |
| Dados por agente | `AgentDataCard` por agente, se `agentData` foi recebido (evento `agent_data`) |

```typescript
export interface ArchitecturePanelProps {
  title: string;
  text: string;
  benchmarks: BenchmarkMetrics | null;
  isLoading: boolean;
  error: string | null;
  agentData?: AgentDataEntry[] | null;
}
```

### AgentDataCard (`src/components/AgentDataCard.tsx`)

Card expansível por agente, exibindo os dados brutos que ele consultou no Neo4j (evento WebSocket `agent_data`, alimentado por `Neo4jClient.query_log` no backend):
- **Gráfico de barras** (Recharts) por ano/valor, quando `buildAgentChartData` consegue extrair uma série numérica das linhas retornadas — senão, mensagem "Sem dado numérico para gráfico"
- **Consulta Cypher** (query + parâmetros) num `<details>` recolhível
- **Tabela** com as linhas cruas (até 50 visíveis, com nota de quantas foram omitidas)

### QualityMetricsSection (`src/components/QualityMetricsSection.tsx`)

Cards de métricas de qualidade (via `ScoreCard`) organizados em três grupos — só as métricas **determinísticas** (gratuitas, sem LLM):
- **Eficiência**: E1 (overhead de coordenação), E2 (latency breakdown)
- **Qualidade**: Q1 (consistência determinística), Q3 (completeness)
- **Resiliência**: R1 (partial result coverage)

Não há card de fidelidade aqui: ela vem do RAGAS, que é assíncrono, chega depois destes cards (eventos `ragas`/`ragas_done`) e é exibido no painel do relatório comparativo (`ComparativeSection`), não em `ScoreCard`.

### ComparativeSection (`src/components/ComparativeSection.tsx`)

Relatório comparativo e avaliação RAGAS:
- Parsing linha a linha do relatório comparativo, com formatação visual: títulos (`━━━`), vereditos (`→`), sucessos (`✓`), alertas (`✗`), bullets (`•`)
- Painel RAGAS separado (`ragasText`/`ragasLoading`), com destaque (`ragas-score good`) para scores ≥ 0.80; um score ausente aparece como "não disponível", nunca como zero. O RAGAS chega **antes** do relatório comparativo (o veredito depende dele), então a seção aparece assim que o primeiro chunk de RAGAS chega, com o corpo do relatório ainda vazio
- Loading cursor (`▍`) durante streaming de qualquer um dos dois

---

## Componentes Compartilhados

### Header (`src/components/Header.tsx`)

Identidade visual Sophia: barra superior com o brasão de Sorocaba, nome "Sophia" em destaque e subtítulo descritivo. Componente puramente visual, sem props.

### TabNav (`src/components/TabNav.tsx`)

Barra de navegação com duas abas rotuladas **"Saúde"** e **"Agentes"** (`data-testid="tab-user"`/`"tab-tech"`, identificadores internos inalterados). Usa `role="tablist"`, `role="tab"`, `aria-selected` e `aria-controls` para acessibilidade.

### ErrorBoundary (`src/components/ErrorBoundary.tsx`)

Error boundary genérico (classe React) para capturar crashes de componentes filhos e exibir uma mensagem de erro em vez de tela branca. Usado envolvendo a aba Agentes (`TechTab`) em `App.tsx`.

---

## Hooks WebSocket

### useWebSocket (`src/hooks/useWebSocket.ts`)

Streaming de **resultados** de uma análise (`/ws/{analysisId}`). O estado (`UseWebSocketState`, definido em `types/index.ts` para evitar import circular com `ChatRound`) inclui campos de RAGAS (`ragasText`, `ragasLoading`, alimentados pelos eventos `ragas`/`ragas_done`) e de dados por agente (`starAgentData`, `hierAgentData`, alimentados pelo evento `agent_data`).

```typescript
export const INITIAL_STATE: UseWebSocketState = {
  starText: '', hierText: '',
  starBenchmarks: null, hierBenchmarks: null,
  starLoading: false, hierLoading: false,
  starError: null, hierError: null,
  comparativeReport: '', comparativeLoading: false,
  qualityMetrics: null,
  ragasText: '', ragasLoading: false,
  starAgentData: null, hierAgentData: null,
};
```

**Comportamento:**
- Conecta automaticamente quando `analysisId` muda; reseta todo o estado (`INITIAL_STATE`) nessa troca
- Auto-reconnect em desconexão inesperada (máximo 3 tentativas, delay **linear**: `1000 * tentativa` ms)
- Não reconecta em close code 1000 (fechamento limpo)
- Processa eventos por `architecture` (`star` / `hierarchical` / `both`): `chunk`, `done`, `error`, `metric`, `agent_data`, `quality_metrics`, `ragas`, `ragas_done`

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
- `connectionStatus`: `'connected'` | `'disconnected'` | `'reconnecting'`, mais `hasEverConnected`

---

## Tipos TypeScript

**Arquivo:** `src/types/index.ts`

```typescript
// Resultados (WS /ws/{analysisId})
interface WSEvent {
  analysisId: string;
  architecture: 'star' | 'hierarchical' | 'both';
  type: 'chunk' | 'done' | 'error' | 'metric' | 'agent_data'
      | 'quality_metrics' | 'ragas' | 'ragas_done';
  payload: string | BenchmarkMetrics | AgentDataPayload | Record<string, unknown>;
}

// Query Cypher + dados brutos que um agente executou — capturado em
// Neo4jClient.query_log (backend), repassado via evento `agent_data`.
interface AgentQueryEntry {
  query: string;
  params: Record<string, unknown>;
  rowCount: number;
  rows: Record<string, unknown>[];
}
interface AgentDataEntry {
  agentName: string;
  agentLabel: string;
  queries: AgentQueryEntry[];
}
interface AgentDataPayload {
  architecture: 'star' | 'hierarchical';
  agents: AgentDataEntry[];
}

// Chat (WS /ws/chat/{sessionId})
interface ChatMessage {
  id: string;
  role: 'user' | 'system';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  isError?: boolean;
  // 'architecture_answer' marca a bolha que carrega o texto ao vivo da
  // arquitetura acompanhada nesta rodada (ver ChatInterface).
  kind?: 'text' | 'architecture_answer';
  architecture?: 'star' | 'hierarchical';
}

// Estado espelhado do hook useWebSocket para uma análise.
interface UseWebSocketState {
  starText: string; hierText: string;
  starBenchmarks: BenchmarkMetrics | null; hierBenchmarks: BenchmarkMetrics | null;
  starLoading: boolean; hierLoading: boolean;
  starError: string | null; hierError: string | null;
  comparativeReport: string; comparativeLoading: boolean;
  qualityMetrics: QualityMetrics | null;
  ragasText: string; ragasLoading: boolean;
  starAgentData: AgentDataEntry[] | null; hierAgentData: AgentDataEntry[] | null;
}

// Uma "rodada" = uma pergunta do chat que disparou uma análise.
interface ChatRound {
  id: string; // analysisId
  question: string;
  startedAt: string;
  snapshot: UseWebSocketState;
}

// Métricas de qualidade tipadas — só as determinísticas (ver QualityMetricsSection)
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

---

## Configuração

**Arquivo:** `src/config.ts`

```typescript
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
```

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
| Chat | `.chat-container`, `.message-bubble` (`.user` / `.system` / `.error` / `.architecture-answer`), `.chat-input-row`, `.connection-status` |
| Rodadas | `.round-card`, `.round-card-header` (expansível), `.round-detail` |
| Dados por agente | `.agent-data-card`, `.agent-data-chart`, `.agent-data-table` |
| Score Cards | Grid responsivo com valores coloridos por arquitetura |
| Scrollbar | Customizada (fina, escura) |
| Loading | Cursor `▍` com animação de blink; `.typing-indicator` (pontinhos animados) |
| Layout | Grid 2 colunas → 1 coluna em mobile |

---

## Acessibilidade

| Recurso | Implementação |
|---------|---------------|
| Navegação por abas | `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` |
| Conteúdo dinâmico | `aria-live="polite"` nas caixas de texto e nas mensagens do chat |
| Mensagens de erro | `role="alert"` (bolhas de erro, status de conexão, painel de erro) |
| Chat | `<textarea>` com `maxLength`, `disabled` durante aguardo de resposta; botão "Enviar" desabilitado se vazio ou aguardando |
| Rodadas | `aria-expanded` nos cabeçalhos expansíveis de `RoundCard`/`AgentDataCard` |
| data-testid | Em todos os elementos testáveis |
| Semântica | `<header>`, `<table>` com `<thead>`/`<tbody>`, `<details>`/`<summary>` |

---

## Testes

12 arquivos de teste (Vitest + @testing-library/react):

| Arquivo | Escopo |
|---------|--------|
| `src/App.integration.test.tsx` | Integração ponta a ponta (rodadas de chat, troca de aba) |
| `src/components/ChatInterface.test.tsx` | Chat (envio, streaming, validação de mensagem) |
| `src/components/TechTab.test.tsx` | Panorama de rodadas na aba Agentes |
| `src/components/RoundCard.test.tsx` | Cabeçalho de rodada, expansão |
| `src/components/RoundOverview.test.tsx` | Lista de rodadas, estado vazio |
| `src/components/AgentDataCard.test.tsx` | Dados por agente (gráfico, query, tabela) |
| `src/components/TabNav.test.tsx` | Navegação entre abas (acessibilidade) |
| `src/components/Header.test.tsx` | Identidade visual (Sophia, brasão) |
| `src/utils/parseWinner.test.ts` | Extração do vencedor do relatório comparativo |
| `src/utils/validateMessage.test.ts` | Validação de mensagem (vazio, tamanho máximo) |
| `src/utils/buildAgentChartData.test.ts` | Extração de série numérica para o gráfico do `AgentDataCard` |
| `src/utils/renderFormattedText.test.tsx` | Markdown básico → elementos React (`FormattedText`) |
| `src/hooks/useWebSocket.raceCondition.test.tsx` | Condição de corrida na troca de `analysisId` |

Não há testes dedicados para `UserTab`, `MessageBubble`, `TypingIndicator`, `ErrorBoundary`, `ScoreCard`, `QualityMetricsSection`, `ComparativeSection`, `RoundDetail`, `ArchitecturePanel`, `useChatWebSocket` ou `formatRoundSummary` — cobertura desses fica a cargo do teste de integração e dos testes dos componentes que os consomem.

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
