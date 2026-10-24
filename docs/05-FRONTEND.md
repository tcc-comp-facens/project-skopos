# Frontend

## Sumário

1. [Visão Geral](#visão-geral)
2. [Estrutura de Arquivos](#estrutura-de-arquivos)
3. [App e Rodadas](#app-e-rodadas)
4. [Aba Saúde (chat)](#aba-saúde-chat)
5. [Aba Agentes (técnica)](#aba-agentes-técnica)
6. [Componentes Compartilhados](#componentes-compartilhados)
7. [Hooks WebSocket](#hooks-websocket)
8. [Utilitários](#utilitários)
9. [Tipos TypeScript](#tipos-typescript)
10. [Configuração](#configuração)
11. [Estilização](#estilização)
12. [Acessibilidade](#acessibilidade)
13. [Testes](#testes)
14. [Docker](#docker)

---

## Visão Geral

SPA em **React 18 + TypeScript (strict) + Vite**, com CSS puro (paleta "Sophia") e **Recharts** para os gráficos da aba técnica. A interface tem **duas abas**, que compartilham o estado de rodadas:

| Aba | Rótulo | Público | Conteúdo |
|-----|--------|---------|----------|
| `user` | **Saúde** | Público geral / servidores | Chat em linguagem natural. A resposta é o texto da arquitetura que respondeu primeiro, em streaming |
| `tech` | **Agentes** | Avaliadores do TCC | Panorama de todas as rodadas: painéis das duas arquiteturas, métricas, consultas Cypher por agente, RAGAS e relatório comparativo |

Cada pergunta que dispara uma análise vira uma **rodada**. As duas abas ficam montadas no DOM e a troca de aba só alterna `display`, então trocar de aba não interrompe uma análise em andamento.

Não há formulário estruturado, toggle de LLM nem toggle de RAGAS. A análise é sempre disparada pelo chat, e a síntese via LLM e o RAGAS são incondicionais.

---

## Estrutura de Arquivos

```
frontend/src/
├── App.tsx                      # Estado de rodadas, abas, useWebSocket
├── main.tsx                     # Ponto de entrada React
├── config.ts                    # API_URL / WS_URL
├── styles.css                   # Tema e layout
├── assets/brasao-sorocaba.svg
├── components/
│   ├── Header.tsx               # Identidade visual Sophia
│   ├── TabNav.tsx               # Abas Saúde / Agentes
│   ├── ErrorBoundary.tsx        # Isola crashes da aba técnica
│   ├── UserTab.tsx              # Aba Saúde → ChatInterface
│   ├── ChatInterface.tsx        # Chat, com useChatWebSocket próprio
│   ├── MessageBubble.tsx        # Bolha (Markdown básico, badge de vencedor)
│   ├── TypingIndicator.tsx      # Pontinhos animados (CSS)
│   ├── TechTab.tsx              # Aba Agentes → RoundOverview
│   ├── RoundOverview.tsx        # Lista de rodadas
│   ├── RoundCard.tsx            # Cabeçalho expansível de uma rodada
│   ├── RoundDetail.tsx          # Conteúdo técnico de uma rodada
│   ├── ArchitecturePanel.tsx    # Texto + benchmarks + dados por agente
│   ├── AgentDataCard.tsx        # Consulta Cypher, gráfico e linhas de um agente
│   ├── QualityMetricsSection.tsx# Cards E1/E2/Q1/Q3/R1
│   ├── ScoreCard.tsx            # Card de uma métrica (estrela × hierárquica)
│   └── ComparativeSection.tsx   # RAGAS + relatório comparativo
├── hooks/
│   ├── useWebSocket.ts          # WS de resultados (/ws/{analysisId})
│   └── useChatWebSocket.ts      # WS de chat (/ws/chat/{sessionId})
├── types/index.ts
└── utils/
    ├── buildAgentChartData.ts   # Linhas de consulta → pontos {ano, valor}
    ├── formatRoundSummary.ts    # Texto de fallback da bolha de resposta
    ├── parseWinner.ts           # Vencedor a partir do relatório
    ├── renderFormattedText.tsx  # Markdown básico sem dangerouslySetInnerHTML
    └── validateMessage.ts       # Limites da mensagem de chat
```

---

## App e Rodadas

**Arquivo:** `src/App.tsx`

- `analysisId`: análise ativa (rodada mais recente).
- `rounds: ChatRound[]`: histórico, cada um com `{ id, question, startedAt, snapshot }`.
- `activeTab`: `'user'` | `'tech'`, começando em `'user'`.
- `ws = useWebSocket(analysisId)`: **um único** hook de resultados. Um `useEffect` espelha o estado ao vivo no `snapshot` da rodada ativa. Quando a próxima rodada começa, `analysisId` muda, o hook reseta e o último snapshot fica **congelado** em `rounds`. Esse snapshot é a única fonte de verdade de rodadas passadas: a fila do backend tem consumidor único e é descartada ao terminar, então uma rodada antiga não pode ser reconectada.
- `TechTab` fica dentro de um `<ErrorBoundary>`. O chat fica fora, para que um crash na aba técnica não derrube a conversa.

**Fluxo:**
1. O usuário envia uma pergunta no `ChatInterface`, que usa sua própria conexão `useChatWebSocket`.
2. Quando o backend responde `analysis_started`, o `ChatInterface` chama `onAnalysisStarted(analysisId, question)`, e o `App` cria a rodada e atualiza `analysisId`.
3. `useWebSocket(analysisId)` conecta em `/ws/{analysisId}` e recebe os eventos das duas topologias.
4. A aba Saúde mostra a resposta na conversa. A aba Agentes mostra todos os detalhes de todas as rodadas.

---

## Aba Saúde (chat)

### UserTab (`src/components/UserTab.tsx`)

Contém apenas o `<ChatInterface>`. A resposta da análise aparece como uma bolha da própria conversa, sem card separado.

### ChatInterface (`src/components/ChatInterface.tsx`)

Chat autocontido, com conexão `useChatWebSocket` para o turno de intenção. Acompanha o resultado pelo `activeRoundWs`, o mesmo estado que alimenta a aba técnica, sem duplicar o streaming.

- **Boas-vindas**: busca `GET /api/data-range` para informar o intervalo de anos disponível (best-effort).
- **Envio** (`handleSend`): valida com `isBlank`/`isTooLong`, adiciona a bolha do usuário e chama `chat.sendMessage(text)`. Enter envia e Shift+Enter quebra linha. O input fica desabilitado enquanto aguarda resposta, e um contador de caracteres aparece quando há texto.
- **Callbacks do chat**: `onChunk` (texto da resposta de intenção em streaming), `onDone`, `onError` (bolha de erro), `onAnalysisStarted` (propaga ao `App`).
- **Resposta da análise**: uma bolha "Analisando os dados…" é criada e, por rodada, o chat **acompanha a primeira arquitetura que produzir texto** (normalmente a estrela). A bolha passa a mostrar o `starText`/`hierText` real crescendo chunk a chunk (`kind: 'architecture_answer'`) e é finalizada quando essa arquitetura termina, sem esperar a outra nem o relatório.
- **Badge de vencedor**: se o relatório comparativo já tiver chegado e apontar a mesma arquitetura acompanhada (`parseWinner`), a bolha ganha um badge (⭐/🏛). No caso comum, o relatório ainda não chegou e a bolha fica sem badge.
- **Falha**: se nenhuma arquitetura produzir texto e ambas pararem, a bolha recebe o texto de `formatRoundSummary`.
- Indicador de `connectionStatus` quando a conexão não está `'connected'`.

### MessageBubble (`src/components/MessageBubble.tsx`)

Usuário à direita, sistema à esquerda. O conteúdo passa por `FormattedText` (Markdown básico construído como elementos React, **nunca** `dangerouslySetInnerHTML`). Mostra `<TypingIndicator>` em streaming, `role="alert"` em erro e o badge de arquitetura quando `message.architecture` está presente.

### TypingIndicator (`src/components/TypingIndicator.tsx`)

Pontinhos animados só com CSS.

---

## Aba Agentes (técnica)

### TechTab → RoundOverview → RoundCard → RoundDetail

| Componente | Papel |
|------------|-------|
| `TechTab` | Contêiner da aba. Renderiza `<RoundOverview rounds={rounds} />` |
| `RoundOverview` | Lista **todas** as rodadas da sessão. A mais recente começa expandida, e as anteriores mantêm seu próprio estado de expansão. Sem rodadas, mostra "Nenhuma pergunta feita ainda — use o chat na aba Saúde." |
| `RoundCard` | Cabeçalho sempre visível (Rodada N, horário, pergunta truncada, vencedor via `parseWinner`, tempo total de cada topologia). O `RoundDetail` só monta quando expandido, para não pesar a página |
| `RoundDetail` | Dois `ArchitecturePanel` (Hierárquica e Estrela), `QualityMetricsSection` e `ComparativeSection`, todos alimentados pelo `snapshot` da rodada |

### ArchitecturePanel (`src/components/ArchitecturePanel.tsx`)

```typescript
interface ArchitecturePanelProps {
  title: string;
  text: string;
  benchmarks: BenchmarkMetrics | null;
  isLoading: boolean;
  error: string | null;
  agentData?: AgentDataEntry[] | null;
}
```

| Elemento | Descrição |
|----------|-----------|
| Cabeçalho | Ícone (🏛 hierárquica / ⭐ estrela) + título |
| Erro | Mensagem com `role="alert"` |
| Caixa de texto | Texto da análise com auto-scroll e `aria-live="polite"`. Spinner "Gerando análise..." enquanto carrega |
| Benchmarks | Tempo total + tabela com tempo (ms) e CPU (%) por agente (do evento `metric`) |
| Dados por agente | Um `AgentDataCard` por agente (do evento `agent_data`) |

### AgentDataCard (`src/components/AgentDataCard.tsx`)

Card expansível por agente de saúde ou de orçamento, com o total de registros no cabeçalho. Expandido, mostra para cada consulta executada:
- um **gráfico de barras (Recharts)** ano × valor, quando `buildAgentChartData` reconhece as colunas;
- a **consulta Cypher** (em `<details>`);
- as **linhas brutas** em tabela (até 50).

### QualityMetricsSection + ScoreCard

`QualityMetricsSection` extrai o valor numérico principal de cada métrica do payload `quality_metrics` e renderiza `ScoreCard`s (estrela × hierárquica, 2 casas decimais, `—` quando ausente) em três grupos:
- **Eficiência**: E1 (overhead de coordenação), E2 (breakdown de latência)
- **Qualidade**: Q1 (consistência determinística), Q3 (completude)
- **Resiliência**: R1 (cobertura de resultado parcial)

Só exibe as métricas determinísticas. O RAGAS aparece na `ComparativeSection`, e custo, comunicação e outcome estão no payload, mas não têm card.

### ComparativeSection (`src/components/ComparativeSection.tsx`)

Painel do RAGAS (`ragasText`/`ragasLoading`) e do relatório comparativo, com formatação linha a linha (títulos, vereditos `→`, `✓`, `✗`, bullets). O RAGAS chega **antes** do relatório, então a seção aparece assim que o primeiro chunk de RAGAS chega, mesmo com o relatório ainda vazio.

---

## Componentes Compartilhados

| Componente | Descrição |
|------------|-----------|
| `Header` | Barra "Sophia" com subtítulo e brasão de Sorocaba (`src/assets/brasao-sorocaba.svg`). Puramente visual |
| `TabNav` | Abas **Saúde** e **Agentes**, com `role="tablist"`/`role="tab"`, `aria-selected`, `aria-controls` |
| `ErrorBoundary` | Classe React (`getDerivedStateFromError`/`componentDidCatch`) que mostra uma mensagem de erro em vez de tela branca. Envolve a `TechTab` |

---

## Hooks WebSocket

### useWebSocket (`src/hooks/useWebSocket.ts`)

Streaming de **resultados** (`/ws/{analysisId}`). O estado (`UseWebSocketState`) fica em `types/index.ts` para ser reaproveitado por `ChatRound` sem import circular.

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

- Conecta quando `analysisId` muda e reseta para `INITIAL_STATE`.
- Processa eventos por `architecture`: `chunk`, `done`, `error`, `metric` e `agent_data` para `star`/`hierarchical`, e `chunk`, `done`, `quality_metrics`, `ragas` e `ragas_done` para `both`.
- Reconexão automática em fechamento inesperado: até 3 tentativas, com atraso **linear** (`1000 × tentativa` ms). Não reconecta em close 1000.

### useChatWebSocket (`src/hooks/useChatWebSocket.ts`)

Conexão do **turno de intenção** (`/ws/chat/{sessionId}`). Gera o próprio `sessionId` com `crypto.randomUUID()`, só em memória.

```typescript
export interface UseChatWebSocketCallbacks {
  onAck?: () => void;
  onChunk: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
  onAnalysisStarted: (analysisId: string) => void;
}
```

- Reconexão com backoff **exponencial** (1s/2s/4s, até 3 tentativas).
- Fila de saída: se `sendMessage` é chamado antes da conexão abrir, a mensagem é entregue em ordem quando a conexão volta.
- `connectionStatus` (`'connected'` | `'disconnected'` | `'reconnecting'`) e `hasEverConnected`.
- Envia `{"type": "user_message", "payload": {"text"}}`. O backend aceita `useSelfCheck` no payload, mas **o frontend não envia esse campo**, então a verificação pós-síntese fica desligada nas análises disparadas pela interface.

---

## Utilitários

| Arquivo | Função |
|---------|--------|
| `utils/buildAgentChartData.ts` | `buildAgentChartData(rows)`: converte linhas de consulta (`IndicadorSaude`, `DespesaAnual`, `VARIACAO_ANUAL`) em `{ano, valor}[]` usando chaves conhecidas (`ano`/`ano_atual`, `valor`/`valorTotal`/`percentual`). Retorna `null` quando não há dado plotável |
| `utils/formatRoundSummary.ts` | Texto da bolha de resposta enquanto não há texto ("Analisando os dados…") ou quando as duas arquiteturas falham |
| `utils/parseWinner.ts` | `parseWinner(report)`: lê a **última** linha `→` do relatório e devolve `'star'` \| `'hierarchical'` \| `null` |
| `utils/renderFormattedText.tsx` | `FormattedText`: Markdown mínimo (negrito, itálico, cabeçalhos, listas) como elementos React, sem HTML injetável |
| `utils/validateMessage.ts` | `MAX_CHAT_MESSAGE_LENGTH = 1000` (espelha o backend), `isBlank`, `isTooLong` |

---

## Tipos TypeScript

**Arquivo:** `src/types/index.ts`

```typescript
// Resultados (WS /ws/{analysisId})
interface WSEvent {
  analysisId: string;
  architecture: 'star' | 'hierarchical' | 'both';
  type: 'chunk' | 'done' | 'error' | 'metric' | 'agent_data' | 'quality_metrics' | 'ragas' | 'ragas_done';
  payload: string | BenchmarkMetrics | AgentDataPayload | Record<string, unknown>;
}

interface AgentMetric { agentName: string; executionTimeMs: number; cpuPercent: number; }
interface BenchmarkMetrics { architecture: 'star' | 'hierarchical'; totalExecutionTimeMs: number; agentMetrics: AgentMetric[]; }

// Consultas por agente (evento agent_data, vindas de Neo4jClient.query_log)
interface AgentQueryEntry { query: string; params: Record<string, unknown>; rowCount: number; rows: Record<string, unknown>[]; }
interface AgentDataEntry { agentName: string; agentLabel: string; queries: AgentQueryEntry[]; }
interface AgentDataPayload { architecture: 'star' | 'hierarchical'; agents: AgentDataEntry[]; }

// Chat (WS /ws/chat/{sessionId})
interface ChatMessage {
  id: string; role: 'user' | 'system'; content: string; timestamp: string;
  isStreaming?: boolean; isError?: boolean;
  kind?: 'text' | 'architecture_answer';     // bolha com o texto da arquitetura
  architecture?: 'star' | 'hierarchical';    // badge de vencedor
}
interface ChatWSEvent { type: 'user_ack' | 'system_chunk' | 'system_done' | 'error' | 'analysis_started'; payload: string; }
type ConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

// Rodadas
interface ChatRound { id: string; question: string; startedAt: string; snapshot: UseWebSocketState; }
type ActiveTab = 'user' | 'tech';
type WinnerArchitecture = 'star' | 'hierarchical' | null;

// Métricas exibidas nos cards
interface ArchitectureQualityMetrics {
  efficiency: { E1: number; E2: number };
  quality: { Q1: number; Q3: number };
  resilience: { R1: number };
}
interface QualityMetrics { star: ArchitectureQualityMetrics; hierarchical: ArchitectureQualityMetrics; }
```

`QualityMetrics` tipa só o que os cards exibem. O payload real de `quality_metrics` é mais rico (ver [04-BACKEND-API.md](04-BACKEND-API.md#métricas-de-qualidade--resumo)), e `QualityMetricsSection` extrai os números de forma defensiva.

---

## Configuração

**Arquivo:** `src/config.ts`

```typescript
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
```

`vite.config.ts` define `envDir: '..'`, ou seja, o frontend lê o **mesmo `.env` da raiz do repositório** (não existe `frontend/.env`). O servidor de desenvolvimento escuta em `0.0.0.0:5173`, e os testes usam `jsdom` com `src/test-setup.ts`.

---

## Estilização

**Arquivo:** `src/styles.css`. Tema escuro em CSS puro, com tokens em `:root`:

| Token | Cor | Uso |
|-------|-----|-----|
| `--sophia-dark` | `#2D3945` | Fundo principal |
| `--sophia-mid` | `#295B75` | Fundo secundário, bordas |
| `--sophia-vivid` | `#489ECD` | Destaque, aba ativa |
| `--sophia-light` | `#7EA1C4` | Texto secundário |
| `--sophia-gold` | `#C4AA7E` | Acento dourado (vencedor) |
| `--sophia-gray` | `#767D85` | Texto terciário |
| `--sophia-warm` | `#6F685C` | Separadores |

Layout em grid de 2 colunas para os painéis, que vira 1 coluna abaixo de 768 px. Scrollbar customizada, cursor/spinner de carregamento e `.typing-indicator` animado.

---

## Acessibilidade

| Recurso | Implementação |
|---------|---------------|
| Abas | `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected`, `aria-controls`, `aria-labelledby` |
| Conteúdo dinâmico | `aria-live="polite"` nos textos das arquiteturas e no relatório |
| Erros | `role="alert"` (bolhas de erro, painel de erro, status de conexão) |
| Expansíveis | `aria-expanded` em `RoundCard` e `AgentDataCard` |
| Badge de vencedor | `aria-label` ("Arquitetura Estrela" / "Arquitetura Hierárquica") |
| Imagens | `alt="Brasão de Sorocaba"` |
| Chat | `<textarea>` com limite de tamanho, desabilitada enquanto aguarda. Botão de envio desabilitado com mensagem vazia ou longa demais |
| Testes | `data-testid` nos elementos testáveis |

---

## Testes

Vitest + Testing Library (+ `fast-check` em `validateMessage.test.ts`, baseado em propriedades). Rodar com `npm test` (ou `npm run test:watch`).

| Arquivo | Escopo |
|---------|--------|
| `src/App.integration.test.tsx` | Integração do modelo de rodadas (a segunda rodada não sobrescreve o snapshot da primeira) |
| `src/components/ChatInterface.test.tsx` | Boas-vindas, envio e validação, streaming da arquitetura acompanhada, erros e fallback |
| `src/components/TechTab.test.tsx` | Aba técnica com e sem rodadas |
| `src/components/RoundOverview.test.tsx` | Lista de rodadas e expansão padrão |
| `src/components/RoundCard.test.tsx` | Cabeçalho, vencedor, expansão |
| `src/components/AgentDataCard.test.tsx` | Consulta, gráfico e linhas por agente |
| `src/components/TabNav.test.tsx` | Navegação e acessibilidade das abas |
| `src/components/Header.test.tsx` | Identidade visual |
| `src/hooks/useWebSocket.raceCondition.test.tsx` | Troca de `analysisId` durante o streaming, sem vazamento entre rodadas |
| `src/utils/buildAgentChartData.test.ts` | Conversão de linhas em pontos plotáveis |
| `src/utils/parseWinner.test.ts` | Extração do vencedor do relatório |
| `src/utils/renderFormattedText.test.tsx` | Markdown básico e ausência de HTML injetável |
| `src/utils/validateMessage.test.ts` | Mensagem vazia e tamanho máximo |

`UserTab`, `MessageBubble`, `TypingIndicator`, `ErrorBoundary`, `ScoreCard`, `QualityMetricsSection`, `ComparativeSection` e `useChatWebSocket` não têm teste dedicado. São cobertos indiretamente pelos testes de integração e dos componentes que os usam.

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

O container roda o **servidor de desenvolvimento** do Vite na porta 5173. `VITE_API_URL` e `VITE_WS_URL` são injetadas pelo `docker-compose.yml`.
