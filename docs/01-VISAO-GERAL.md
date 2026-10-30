# Visão Geral do Projeto

## Sumário

1. [Introdução](#introdução)
2. [Problema](#problema)
3. [Objetivo](#objetivo)
4. [Stack Tecnológica](#stack-tecnológica)
5. [Arquitetura do Sistema](#arquitetura-do-sistema)
6. [Como Executar](#como-executar)
7. [Testes](#testes)
8. [Estrutura de Diretórios](#estrutura-de-diretórios)

---

## Introdução

Este sistema compara duas arquiteturas multiagente baseadas no framework CoALA (Cognitive Architectures for Language Agents — Sumers et al., 2023) aplicadas à análise de eficiência dos gastos públicos em saúde do município de Sorocaba-SP. O projeto é um Trabalho de Conclusão de Curso (TCC) de Engenharia de Computação.

O sistema cruza a despesa municipal efetivamente executada em saúde (empenho a empenho, extraída de dados LAI/portal de transparência — inclui recurso próprio, transferências estaduais/federais, operações de crédito e emendas parlamentares) com indicadores epidemiológicos de 8 Sistemas de Informação DATASUS (SINAN, SIM, SIH, SI-PNI, SINASC, SIA, CNES, COVID) para avaliar a qualidade e eficiência do gasto público em saúde. A **única** entrada do sistema é o chat em linguagem natural, interpretado por um agente dedicado (`AgenteInterpretacaoIntencao`, sem regex) — o antigo formulário estruturado foi removido.

As duas arquiteturas — **Estrela** e **Hierárquica** — são executadas em paralelo sobre os mesmos dados, permitindo uma comparação objetiva de desempenho, escalabilidade, overhead de comunicação, custo de tokens e qualidade textual (via RAGAS).

## Problema

- Não existe ferramenta que correlacione automaticamente gastos municipais em saúde com indicadores epidemiológicos
- A avaliação de eficiência do gasto público em saúde é feita manualmente por auditores
- Não há comparação empírica entre topologias multiagente para este domínio
- Dados de saúde estão dispersos em múltiplos sistemas (SINAN, SIM, SIH, SI-PNI, SINASC, SIA, CNES) sem integração
- Fontes financeiras federais (FNS) sozinhas não refletem o gasto total do município — recurso próprio, repasses estaduais e operações de crédito ficam de fora

## Objetivo

Avaliar comparativamente duas topologias de sistemas multiagente CoALA:

| Topologia | Descrição | Característica principal |
|-----------|-----------|--------------------------|
| **Estrela** | Um agente central (`OrquestradorEstrela`) coordena até 15 agentes periféricos (8 de saúde + 7 orçamentários), ativados condicionalmente | Ponto único de controle, comunicação centralizada |
| **Hierárquica** | 4 supervisores (`SupervisorOrcamento`, `SupervisorSaude`, `SupervisorAnalitico`, `SupervisorContexto`) coordenados por `CoordenadorGeral` | Árvore estrita — nenhum supervisor troca dado com outro, o coordenador repassa tudo como parâmetro direto —, degradação graciosa por camada |

A comparação é feita com base em:
- Tempo de execução total e por agente
- Uso de CPU e memória por agente
- Overhead de coordenação (tempo em supervisores vs. agentes de trabalho)
- Contagem de mensagens entre agentes
- Custo de tokens LLM, por segmento (interpretação de intenção, cada topologia, avaliação)
- Qualidade da análise textual gerada — fidelidade/relevância medidas pela biblioteca RAGAS, completude medida deterministicamente
- Consistência determinística (resultados numéricos idênticos entre topologias)
- Resiliência (cobertura de resultados parciais sob falha) e sucesso agregado da análise

## Stack Tecnológica

| Componente | Tecnologia | Justificativa |
|------------|-----------|---------------|
| Backend | Python 3.11 + FastAPI | Agentes são classes Python dentro do processo FastAPI; async nativo |
| Frontend | React 18 + TypeScript | SPA com WebSocket client para streaming em tempo real |
| Bundler | Vite | Build rápido, HMR, suporte nativo a TypeScript |
| Banco de Dados | Neo4j 5.x | Grafo nativo para modelar relações entre gastos e indicadores |
| LLM | OpenAI: `gpt-5.6-luna` nas análises e `gpt-5-mini` como juiz RAGAS, fixos no código | Interpretação de intenção do chat, planejamento/arbitragem de dimensão, priorização de achados, síntese textual, self-check |
| Avaliação de qualidade textual | Biblioteca RAGAS (Es et al., 2024) | Fidelidade, relevância da resposta e relevância do contexto — reference-free |
| Métricas | psutil | Coleta de CPU e memória por agente em tempo real |
| Estatística | SciPy | Spearman (correlação) |
| ETL orçamento | pandas (CSV local, delimitador `\|`, encoding latin-1) | Extração LAI/portal de transparência já baixada, sem chamada de rede |
| ETL saúde | pandas + openpyxl (XLSX local) | Extratos TabNet DATASUS já baixados, sem FTP/PySUS |
| Containerização | Docker + Docker Compose | Orquestração de Neo4j, backend e frontend |
| Testes Backend | pytest + Hypothesis | Testes unitários e baseados em propriedade |
| Testes Frontend | Vitest + @testing-library/react | Testes de componentes |

Não há mais dependência do `pysus` nem do `openpyxl`/`xlrd` sobre planilhas FNS — o pipeline ETL atual lê exclusivamente arquivos locais já extraídos (ver [docs/03-DADOS-ETL.md](03-DADOS-ETL.md)).

## Arquitetura do Sistema

O sistema é composto por 3 camadas: Frontend (React), Backend (FastAPI) e Neo4j.

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend (React 18)                        │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────┐      │
│  │ ChatInterface  │ │  RoundOverview │ │  (aba Técnica) │      │
│  │(texto livre,   │ │  RoundCard /   │ │  ArchitecturePanel│    │
│  │ aba Usuário)   │ │  RoundDetail   │ │  ×2, métricas   │     │
│  └───────┬────────┘ └───────┬────────┘ └───────┬────────┘    │
│          │                  │                   │             │
│  useChatWebSocket    useWebSocket hook          │             │
└──────────┼──────────────────┼───────────────────┼─────────────┘
           │ WS /ws/chat/{id} │ WebSocket         │
┌──────────┼──────────────────┼───────────────────┼─────────────┐
│          ▼                  ▼                   ▼             │
│                Backend (FastAPI + Python 3.11)                 │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  AgenteInterpretacaoIntencao (guardrail 3 vias + LLM)   │   │
│  │  → dispatch_analysis() (único caminho de disparo)       │   │
│  │     ┌─────────────────┬─────────────────┐              │   │
│  │     ▼                 ▼                 │              │   │
│  │  Thread 1          Thread 2             │              │   │
│  │  (Estrela)         (Hierárquica)        │              │   │
│  │     │                 │                 │              │   │
│  │  OrquestradorEstrela  CoordenadorGeral  │              │   │
│  │  (até 15 agentes      (4 supervisores    │              │   │
│  │   conforme             + agentes conforme │              │   │
│  │   health_params)       health_params)     │              │   │
│  └─────────────┬─────────────────┬─────────┘              │   │
│                │  ws_queue        │                        │   │
│                ▼                  ▼                        │   │
│           WebSocket Server (/ws/{analysisId})              │   │
│           + quality_metrics + RAGAS + relatório comparativo│   │
└────────────────────────┬──────────────────────────────────┘
                         │ Cypher queries
┌────────────────────────▼──────────────────────────────────┐
│                       Neo4j 5.x                            │
│  Analise  DespesaAnual  Empenho  IndicadorSaude  MetricaExecucao │
└────────────────────────────────────────────────────────────┘
```

Ver [docs/02-AGENTES.md](02-AGENTES.md) para o detalhamento completo dos 20 agentes CoALA e das duas topologias.

### Fluxo Completo de uma Análise

1. O usuário digita uma pergunta em linguagem natural no chat (`WS /ws/chat/{sessionId}`). O `AgenteInterpretacaoIntencao` classifica em 3 vias — fora de escopo (recusa), dentro do escopo mas sem precisar de análise (chitchat, resposta direta), ou dentro do escopo precisando de análise (extrai período e `health_params` na mesma chamada LLM).
2. Se período/tema não forem especificados, o agente completa automaticamente com defaults sensatos (todo o período disponível, todos os indicadores) em vez de recusar — só pede para reformular no caso residual de não haver período algum disponível.
3. `dispatch_analysis()` valida os parâmetros, cria o nó `Analise` no Neo4j e dispara duas threads daemon em paralelo (uma por arquitetura).
4. Cada thread executa seu pipeline de agentes CoALA: agentes de saúde relevantes aos `health_params` consultam `IndicadorSaude` (deliberando dimensão via LLM quando aplicável); agentes orçamentários relevantes consultam `DespesaAnual` (idem); `AgenteContextoOrcamentario` calcula tendências (CAGR); `AgenteAnalitico` calcula correlação de Spearman e detecta anomalias; `AgentePriorizacaoAnalitica` escolhe um ângulo de ênfase; `TextSynthesizer` gera o texto via LLM com streaming; opcionalmente, o self-check (Chain-of-Verification) revisa o texto.
5. Chunks de texto são enviados ao frontend via WebSocket em tempo real.
6. Métricas de execução (tempo, CPU, memória, tokens) são coletadas por agente e persistidas no Neo4j.
7. Após ambas as topologias completarem: métricas de qualidade determinísticas são computadas, a avaliação RAGAS roda de forma assíncrona (fidelidade, relevância da resposta, relevância do contexto) e o relatório comparativo é gerado e transmitido via WebSocket.

### Modelo de Paralelismo

```
dispatch_analysis()  (chamado a partir do chat)
       │
       ├── Thread 1: OrquestradorEstrela.run()  ──┐
       │                                          ├── ws_queue (compartilhada)
       └── Thread 2: CoordenadorGeral.run()   ──┘
                                                   │
                                            WebSocket Server
                                                   │
                                              Frontend
```

Ambas as threads compartilham uma `Queue` para streaming de eventos WebSocket. O WebSocket server consome a fila e transmite ao frontend até receber 2 eventos `done` (um por arquitetura). O custo de tokens de cada thread é isolado via `core.llm_client.TokenBucket` (um `ContextVar` por escopo), mesmo rodando concorrentemente.

---

## Como Executar

### Pré-requisitos

- Docker Desktop instalado e rodando
- (Opcional) Chave de API da OpenAI — sem ela, o chat sempre pede esclarecimento (não há fallback por regex) e a síntese cai no texto estruturado determinístico
- (Opcional) Python 3.11+ e Node.js 20+ para desenvolvimento local

### Variáveis de Ambiente

Todas vivem num único `.env` na raiz do repo (`cp .env.example .env`), compartilhado por backend, frontend e docker-compose — não há mais `backend/.env`/`frontend/.env` separados.

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI de conexão Bolt do Neo4j | `bolt://neo4j:7687` (Docker) ou `bolt://localhost:7687` (local) |
| `NEO4J_USER` | Usuário do Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Senha do Neo4j | `your_password_here` |
| `OPENAI_API_KEY` | Chave OpenAI. Os modelos são fixos no código: análises com `gpt-5.6-luna`, juiz RAGAS com `gpt-5-mini` | `sk-proj-...` |
| `USE_LLM_QUERY_PLANNING` | Liga o planejamento de consulta via LLM (fast-path/cache) nos agentes de domínio | `false` (default) |
| `CORS_ORIGINS` | Origens CORS permitidas | `*` ou `http://localhost:5173` |
| `LOG_LEVEL` | Nível de log (`INFO` default; `DEBUG` mostra prompts completos enviados ao LLM) | `INFO` |
| `VITE_API_URL` / `VITE_WS_URL` | URLs lidas pelo frontend (Vite) | `http://localhost:8000` / `ws://localhost:8000` |

### Execução com Docker (recomendado)

```bash
cp .env.example .env
# Edite .env com suas credenciais Neo4j e a chave do provedor de LLM

docker compose up --build
# Frontend: http://localhost:5173
```

### Execução Local (desenvolvimento)

```bash
# Terminal 1: Neo4j (requer Docker)
docker compose up neo4j

# Terminal 2: Backend — rode o ETL antes, com o Neo4j acessível em localhost
cd backend
pip install -r requirements.txt
python -m etl.orcamento_loader
python -m etl.saude_indicadores_loader
uvicorn main:app --reload --port 8000

# Terminal 3: Frontend
cd frontend
npm ci
npm run dev
```

Ver [docs/03-DADOS-ETL.md](03-DADOS-ETL.md) para os detalhes do pipeline ETL (fontes, formato dos arquivos, variáveis `DADOS_DIR`).

### Serviços e Portas

| Serviço | URL | Descrição |
|---------|-----|-----------|
| Frontend | http://localhost:5173 | Interface web React |
| Backend API | http://localhost:8000 | API REST FastAPI |
| Swagger UI | http://localhost:8000/docs | Documentação interativa da API |
| Neo4j Browser | http://localhost:7474 | Interface do banco de dados |
| Neo4j Bolt | bolt://localhost:7687 | Conexão programática |

### Comandos Úteis

```bash
# Logs
docker compose logs -f              # todos os serviços
docker compose logs -f backend      # só backend

# Rebuild
docker compose up -d --build

# Parar
docker compose down

# ETL manual (dentro do container ou local)
docker compose exec backend python -m etl.orcamento_loader
docker compose exec backend python -m etl.saude_indicadores_loader
```

---

## Testes

### Visão Geral

| Camada | Framework | Arquivos | Observação |
|--------|-----------|----------|-----------|
| Backend | pytest + Hypothesis | ~32 | `test_ragas_metrics.py`/`test_websocket_ragas.py` exigem a dependência `ragas` instalada |
| Frontend | Vitest + @testing-library/react | 12 | Componentes + integração + utilitários |

### Como Rodar

```bash
# Backend — todos os testes
cd backend && pytest

# Backend — verbose
cd backend && pytest -v

# Frontend — todos os testes
cd frontend && npm run test

# Frontend — watch mode
cd frontend && npm run test:watch
```

---

## Estrutura de Diretórios

```
project-skopos/
├── docker-compose.yml                # Orquestração: Neo4j + Backend + Frontend
├── .env.example                      # Template de variáveis de ambiente (raiz, compartilhado)
├── README.md
├── docs/                             # Documentação modular
│   ├── 01-VISAO-GERAL.md            # Este arquivo
│   ├── 02-AGENTES.md                # Sistema multiagente CoALA (20 classes)
│   ├── 03-DADOS-ETL.md              # Fontes de dados e pipeline ETL
│   ├── 04-BACKEND-API.md            # Backend, API, chat, LLM, métricas
│   ├── 05-FRONTEND.md               # Frontend React (chat + painéis técnicos)
│   └── arquitetura/                  # Detalhamento das topologias e das métricas
│
├── PLANO_REFATORACAO.md              # Plano de amadurecimento de uso de LLM + novas métricas (6/6 etapas concluídas)
│
├── backend/
│   ├── Dockerfile                    # Python 3.11-slim
│   ├── entrypoint.sh                # ETL automático + uvicorn
│   ├── requirements.txt             # Dependências Python
│   ├── main.py                      # Entry point — cria app, CORS, registra routers, logging
│   ├── conftest.py                  # Configuração pytest (sys.path)
│   │
│   ├── api/                         # Camada de API
│   │   ├── routes.py                # Endpoints REST
│   │   ├── websocket.py             # WebSocket handler (streaming de resultados + RAGAS)
│   │   ├── chat_websocket.py        # WebSocket handler (turno de intenção do chat)
│   │   ├── chat_runner.py           # Disparo de análise a partir do chat
│   │   ├── dispatch.py              # Disparo de análise (único caminho de entrada)
│   │   ├── models.py                # Pydantic models + validação
│   │   ├── runners.py               # Thread runners (star, hierarchical)
│   │   └── state.py                 # Estado compartilhado (queues, threads, results)
│   │
│   ├── agents/                      # Sistema multiagente CoALA — 20 classes
│   │   ├── base.py                  # AgenteCoALA (classe base do framework CoALA)
│   │   ├── data_crossing.py         # cross_domain_data() + deduplicate_despesas() + detect_data_gaps()
│   │   ├── intent/
│   │   │   └── agente_interpretacao_intencao.py  # Guardrail de escopo em 3 vias + extração via LLM
│   │   ├── domain/
│   │   │   ├── agente_sinan.py, agente_sih.py, agente_sim.py, agente_sipni.py,
│   │   │   │   agente_covid.py, agente_sinasc.py, agente_sia.py, agente_cnes.py  # 8 agentes de saúde
│   │   │   ├── agente_orcamento.py  # AgenteOrcamentoSubfuncao (classe parametrizada, 7 instâncias)
│   │   │   └── query_planning.py    # Deliberação de dimensão (score + LLM opcional) + fast-path/cache
│   │   ├── analytical/
│   │   │   ├── analitico.py                  # AgenteAnalitico (Spearman + anomalias consolidados)
│   │   │   ├── priorizacao.py                 # AgentePriorizacaoAnalitica (ângulo de ênfase)
│   │   │   └── sintetizador.py               # TextSynthesizer (serviço LLM, não é agente CoALA)
│   │   ├── context/
│   │   │   └── contexto_orcamentario.py      # Tendências YoY + CAGR
│   │   ├── star/
│   │   │   └── orchestrator.py               # OrquestradorEstrela (hub)
│   │   └── hierarchical/
│   │       ├── coordinator.py                # CoordenadorGeral (nível 0)
│   │       └── supervisors.py                # 4 supervisores (nível 1)
│   │
│   ├── core/                        # Utilitários
│   │   ├── llm_client.py            # Cliente LLM (OpenAI, modelos fixos, TokenBucket, retry, logs)
│   │   ├── metrics.py               # MetricsCollector (psutil)
│   │   ├── quality_metrics.py       # Métricas determinísticas + relatório comparativo
│   │   ├── ragas_metrics.py         # Avaliação via biblioteca RAGAS
│   │   ├── claim_verifier.py        # Self-check pós-síntese (Chain-of-Verification)
│   │   ├── guardrail_stats.py       # Taxa de rejeição do guardrail de escopo
│   │   └── streaming_adapter.py     # StreamingAdapter (chunking de texto para ws_queue)
│   │
│   ├── db/
│   │   ├── neo4j_client.py          # Driver Neo4j + queries Cypher
│   │   └── query_builder.py         # Cypher parametrizado + allowlist de dimensões
│   │
│   ├── etl/
│   │   ├── orcamento_loader.py            # Empenhos LAI/portal de transparência
│   │   └── saude_indicadores_loader.py    # Extratos TabNet DATASUS locais
│   │
│   └── tests/                       # ~32 arquivos de teste
│
├── frontend/
│   ├── Dockerfile                   # Node 20-alpine
│   ├── package.json                 # React 18 + Vite + Vitest
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx                 # Entry point
│       ├── App.tsx                  # Layout principal + estado de rodadas de chat + integração API/WS
│       ├── App.integration.test.tsx
│       ├── config.ts               # API_URL, WS_URL
│       ├── styles.css              # Tema dark, responsivo
│       ├── test-setup.ts           # Setup Vitest (jest-dom)
│       ├── components/
│       │   ├── ChatInterface.tsx / .test.tsx    # Chat de texto livre (aba Usuário)
│       │   ├── MessageBubble.tsx, TypingIndicator.tsx, ErrorBoundary.tsx
│       │   ├── RoundOverview.tsx / .test.tsx    # Panorama de todas as rodadas (aba técnica)
│       │   ├── RoundCard.tsx / .test.tsx        # Uma rodada, expansível
│       │   ├── RoundDetail.tsx                  # Conteúdo técnico completo de uma rodada
│       │   ├── AgentDataCard.tsx / .test.tsx    # Dados brutos + query + gráfico por agente
│       │   ├── ArchitecturePanel.tsx
│       │   ├── UserTab.tsx, TechTab.tsx / .test.tsx
│       │   ├── ScoreCard.tsx, QualityMetricsSection.tsx, ComparativeSection.tsx
│       │   ├── Header.tsx / .test.tsx, TabNav.tsx / .test.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts       # Streaming de resultados + RAGAS (/ws/{analysisId})
│       │   └── useChatWebSocket.ts   # Turno de intenção do chat (/ws/chat/{sessionId})
│       ├── utils/
│       │   ├── parseWinner.ts / .test.ts, validateMessage.ts / .test.ts
│       │   ├── formatRoundSummary.ts, buildAgentChartData.ts / .test.ts
│       │   └── renderFormattedText.tsx / .test.tsx
│       └── types/
│           └── index.ts             # ChatMessage, ChatRound, WSEvent, AgentDataEntry, QualityMetrics, etc.
```
