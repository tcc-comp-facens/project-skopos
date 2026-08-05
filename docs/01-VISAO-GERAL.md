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

O sistema cruza dados financeiros do SIOPS (despesas municipais por subfunção orçamentária) com indicadores epidemiológicos do DataSUS (dengue, COVID-19, vacinação, internações, mortalidade) para avaliar a qualidade e eficiência do gasto público em saúde. A entrada é feita via chat em linguagem natural (interpretado por um agente dedicado, sem regex) ou via formulário estruturado.

As duas arquiteturas — **Estrela** e **Hierárquica** — são executadas em paralelo sobre os mesmos dados, permitindo uma comparação objetiva de desempenho, escalabilidade, overhead de comunicação e adequação ao cenário proposto.

## Problema

- Não existe ferramenta que correlacione automaticamente gastos municipais em saúde com indicadores epidemiológicos
- A avaliação de eficiência do gasto público em saúde é feita manualmente por auditores
- Não há comparação empírica entre topologias multiagente para este domínio
- Dados de saúde estão dispersos em múltiplos sistemas (SIOPS, SINAN, SIM, SIH, SI-PNI) sem integração

## Objetivo

Avaliar comparativamente duas topologias de sistemas multiagente CoALA:

| Topologia | Descrição | Característica principal |
|-----------|-----------|--------------------------|
| **Estrela** | Um agente central (orquestrador) coordena agentes periféricos (ativação condicional de domínio) | Ponto único de controle, comunicação centralizada |
| **Hierárquica** | Agentes organizados em 3 níveis com supervisores intermediários | Comunicação lateral entre supervisores, degradação graciosa |

A comparação é feita com base em:
- Tempo de execução total e por agente
- Uso de CPU e memória por agente
- Overhead de coordenação (tempo em supervisores vs. agentes de trabalho)
- Contagem de mensagens entre agentes
- Qualidade da análise textual gerada (fidelidade, completude, estrutura)
- Consistência determinística (resultados numéricos idênticos entre topologias)
- Resiliência (cobertura de resultados parciais sob falha)

## Stack Tecnológica

| Componente | Tecnologia | Versão | Justificativa |
|------------|-----------|--------|---------------|
| Backend | Python + FastAPI | 3.11 + latest | Agentes são classes Python dentro do processo FastAPI; async nativo |
| Frontend | React + TypeScript | 18.3.1 + 5.5.3 | SPA com WebSocket client para streaming em tempo real |
| Bundler | Vite | 5.3.4 | Build rápido, HMR, suporte nativo a TypeScript |
| Banco de Dados | Neo4j | 5.x | Grafo nativo para modelar relações entre gastos e indicadores |
| LLM | DeepSeek (API compatível OpenAI) | deepseek-v4-flash | Interpretação de intenção do chat e geração de análises textuais |
| Métricas | psutil | latest | Coleta de CPU e memória por agente em tempo real |
| Estatística | SciPy | latest | Spearman (correlação) |
| ETL DataSUS | PySUS | >=0.11.0 | Download automatizado de dados do FTP DataSUS |
| ETL SIOPS | openpyxl + xlrd | latest | Leitura de planilhas .xlsx e .xls |
| Manipulação de dados | pandas | latest | Transformação e filtragem de DataFrames |
| Containerização | Docker + Docker Compose | latest | Orquestração de Neo4j, backend e frontend |
| Testes Backend | pytest | latest | Testes unitários |
| Testes Frontend | Vitest | 2.0.4 | Testes de componentes |
| Testing Library | @testing-library/react | 16.0.0 | Testes de componentes React |

### Dependências Backend (requirements.txt)

```
fastapi, uvicorn[standard], neo4j, pysus>=0.11.0, psutil, pytest,
python-dotenv, httpx, openai, openpyxl,
xlrd, pandas, scipy, hypothesis
```

`openai` é usado como SDK do cliente LLM — a API do DeepSeek é compatível com o formato OpenAI (`base_url="https://api.deepseek.com"`). `hypothesis` é usado para os testes baseados em propriedade do agente de interpretação de intenção.

### Dependências Frontend (package.json)

```
react ^18.3.1, react-dom ^18.3.1, typescript ^5.5.3, vite ^5.3.4,
vitest ^2.0.4, @testing-library/react ^16.0.0
```

## Arquitetura do Sistema

O sistema é composto por 3 camadas: Frontend (React), Backend (FastAPI) e Neo4j.

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend (React 18)                        │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────┐      │
│  │ ChatInterface  │ │ArchitecturePanel│ │ArchitecturePanel│    │
│  │(texto livre,   │ │  Hierárquica   │ │    Estrela     │     │
│  │ aba Usuário)   │ │  (streaming)   │ │  (streaming)   │     │
│  └───────┬────────┘ └───────┬────────┘ └───────┬────────┘    │
│          │                  │                   │             │
│  useChatWebSocket    useWebSocket hook          │             │
└──────────┼──────────────────┼───────────────────┼─────────────┘
           │ WS /ws/chat/{id} │ WebSocket         │
┌──────────┼──────────────────┼───────────────────┼─────────────┐
│          ▼                  ▼                   ▼             │
│                Backend (FastAPI + Python 3.11)                 │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  AgenteInterpretacaoIntencao (guardrail + extração LLM) │   │
│  │  → dispatch_analysis() (compartilhado com POST REST)    │   │
│  │     ┌─────────────────┬─────────────────┐              │   │
│  │     ▼                 ▼                 │              │   │
│  │  Thread 1          Thread 2             │              │   │
│  │  (Estrela)         (Hierárquica)        │              │   │
│  │     │                 │                 │              │   │
│  │  OrquestradorEstrela  CoordenadorGeral  │              │   │
│  │  (agentes conforme    (3 supervisores    │              │   │
│  │   health_params)       + agentes conforme │              │   │
│  │                        health_params)     │              │   │
│  └─────────────┬─────────────────┬─────────┘              │   │
│                │  ws_queue        │                        │   │
│                ▼                  ▼                        │   │
│           WebSocket Server (/ws/{analysisId})              │   │
│           + quality_metrics + comparative report           │   │
└────────────────────────┬──────────────────────────────────┘
                         │ Cypher queries
┌────────────────────────▼──────────────────────────────────┐
│                       Neo4j 5.x                            │
│  Analise  DespesaSIOPS  IndicadorDataSUS  MetricaExecucao  │
└────────────────────────────────────────────────────────────┘
```

`POST /api/analysis` (formulário estruturado) continua disponível como caminho alternativo, sem passar pelo `AgenteInterpretacaoIntencao` — ambos convergem no mesmo `dispatch_analysis()`.

### Fluxo Completo de uma Análise

Há duas portas de entrada equivalentes — o chat (`WS /ws/chat/{sessionId}`) e o formulário REST (`POST /api/analysis`) — que convergem no mesmo disparo de análise (`dispatch_analysis`, compartilhado):

1. **Via chat**: o usuário digita uma pergunta em linguagem natural. O `AgenteInterpretacaoIntencao` (agente CoALA, sem regex) classifica se a mensagem está dentro do escopo do assistente e, se estiver, extrai período e parâmetros de saúde numa única chamada LLM. Fora do escopo ou incompleta → mensagem de esclarecimento/recusa, nenhuma análise é criada. **Via formulário**: o usuário seleciona período e parâmetros de saúde diretamente; parâmetros já estruturados, sem interpretação de linguagem natural.
2. O backend valida os parâmetros (retorna erro se inválidos)
3. Cria nó `Analise` no Neo4j e vincula `DespesaSIOPS` e `IndicadorDataSUS` existentes
4. Duas threads daemon são disparadas em paralelo (uma por arquitetura)
5. Cada thread executa seu pipeline de agentes CoALA:
   - Agentes de domínio relevantes aos health_params consultam Neo4j (despesas + indicadores)
   - 1 agente de contexto analisa tendências orçamentárias
   - 1 agente de correlação calcula Spearman entre gastos e indicadores
   - 1 agente de anomalias detecta ineficiências via mediana
   - 1 sintetizador de texto (`TextSynthesizer`, serviço — não é um agente CoALA) gera texto via LLM com streaming
6. Chunks de texto (~80 chars) são enviados ao frontend via WebSocket em tempo real
7. Métricas de execução (tempo, CPU, memória) são coletadas por agente e persistidas no Neo4j
8. Contagem de mensagens entre agentes é registrada
9. Após ambas completarem: métricas de qualidade são computadas e relatório comparativo é gerado e transmitido via WebSocket

### Modelo de Paralelismo

```
POST /api/analysis
       │
       ├── Thread 1: OrquestradorEstrela.run()  ──┐
       │                                          ├── ws_queue (compartilhada)
       └── Thread 2: CoordenadorGeral.run()   ──┘
                                                   │
                                            WebSocket Server
                                                   │
                                              Frontend
```

Ambas as threads compartilham uma `Queue` para streaming de eventos WebSocket. O WebSocket server consome a fila e transmite ao frontend até receber 2 eventos `done` (um por arquitetura).

---

## Como Executar

### Pré-requisitos

- Docker Desktop instalado e rodando
- (Opcional) Chave de API DeepSeek para interpretação de intenção no chat e geração de texto via LLM — sem ela, o chat sempre pede esclarecimento (não há mais fallback por regex) e a síntese cai no texto estruturado determinístico
- (Opcional) Python 3.11+ e Node.js 20+ para desenvolvimento local

### Variáveis de Ambiente

**Backend (`backend/.env`):**

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI de conexão Bolt do Neo4j | `bolt://neo4j:7687` (Docker) ou `bolt://localhost:7687` (local) |
| `NEO4J_USER` | Usuário do Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Senha do Neo4j | `your_password_here` |
| `DEEPSEEK_API_KEY` | Chave API DeepSeek | `sk-...` |
| `CORS_ORIGINS` | Origens CORS permitidas | `*` ou `http://localhost:5173` |
| `LOG_LEVEL` | Nível de log (`INFO` default; `DEBUG` mostra prompts completos enviados ao LLM) | `INFO` |

**Frontend (variáveis Vite):**

| Variável | Descrição | Default |
|----------|-----------|---------|
| `VITE_API_URL` | URL da API REST | `http://localhost:8000` |
| `VITE_WS_URL` | URL do WebSocket | `ws://localhost:8000` |

### Execução com Docker (recomendado)

```bash
# 1. Configurar variáveis de ambiente
cp backend/.env.example backend/.env
# Edite backend/.env com suas credenciais

# 2. Subir todos os serviços
docker compose up --build

# 3. Acessar o frontend
# http://localhost:5173
```

O `entrypoint.sh` do backend executa automaticamente:
1. Aguarda Neo4j ficar pronto (30 tentativas, 2s entre cada)
2. Carrega planilhas SIOPS de `backend/data/*.xls` e `*.xlsx`
3. Baixa/cacheia dados DataSUS para os anos detectados
4. Executa seed de fallback (indicadores COVID 2018-2022)
5. Inicia FastAPI via uvicorn na porta 8000

### Execução Local (desenvolvimento)

```bash
# Terminal 1: Neo4j (requer Docker)
docker compose up neo4j

# Terminal 2: Backend
cd backend
pip install -r requirements.txt
python -m etl.seed_data          # popular dados mínimos
uvicorn main:app --reload --port 8000

# Terminal 3: Frontend
cd frontend
npm ci
npm run dev
```

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

# ETL manual
docker compose exec backend python -m etl.siops_loader data/PlanilhaDetalhada.xls
docker compose exec backend python -m etl.datasus_loader 2019 2023
docker compose exec backend python -m etl.seed_data

# Download direto do FTP DataSUS (fora do Docker)
python download_pysus.py 2019 2025
```

---

## Testes

### Visão Geral

| Camada | Framework | Tipo | Arquivos | Total de testes |
|--------|-----------|------|----------|-----------------|
| Backend | pytest + Hypothesis | Unitários + baseados em propriedade | 13 | 102 |
| Frontend | Vitest + @testing-library/react | Componentes + Integração + Utilitários | 9 | ~41 |

### Backend — Arquivos de Teste

| Arquivo | Escopo |
|---------|--------|
| `test_agente_interpretacao_intencao.py` | AgenteInterpretacaoIntencao (guardrail de escopo, extração via LLM, resiliência, sem regex) |
| `test_coala_base.py` | Ciclo cognitivo CoALA na classe base (AgenteCoALA) |
| `test_correlacao.py` | Correlações Spearman (vazio, ponto único, perfeita ±, classificação) |
| `test_anomalias.py` | Detecção de anomalias (mediana, tipos, regra <2 pontos) |
| `test_contexto_orcamentario.py` | Tendências orçamentárias (crescimento, corte, estagnação) |
| `test_data_crossing.py` | Cruzamento de dados, deduplicação, detecção de gaps |
| `test_domain_agents.py` | Agente de domínio (filtro subfunção, fallback) |
| `test_sintetizador.py` | TextSynthesizer (fallback, seções, streaming) |
| `test_streaming_adapter.py` | StreamingAdapter (chunking, formato de evento) |
| `test_orchestrator_star.py` | OrquestradorEstrela (health_params filtering, degradação, métricas) |
| `test_dispatch_analysis.py` | Disparo de análise compartilhado (REST + chat), compatibilidade retroativa |
| `test_chat_websocket.py` | Protocolo do WebSocket de chat (`/ws/chat/{sessionId}`) |

### Frontend — Arquivos de Teste

| Arquivo | Escopo |
|---------|--------|
| `src/App.integration.test.tsx` | Integração ponta a ponta (rodadas de chat, abas) |
| `src/components/ChatInterface.test.tsx` | Chat (envio de mensagem, streaming, validação) |
| `src/components/TechTab.test.tsx` | Aba técnica (seleção de rodada, painéis) |
| `src/utils/parseWinner.test.ts` | Extração do vencedor do relatório comparativo |
| `src/utils/validateMessage.test.ts` | Validação de mensagem de chat (vazio, tamanho máximo) |
| `src/components/TabNav.test.tsx` | Navegação entre abas (acessibilidade) |
| `src/components/LlmControls.test.tsx` | Toggles LLM/Judge (dependência, disabled) |
| `src/components/WinnerPanel.test.tsx` | Painel do vencedor (texto, erro, título) |
| `src/components/Header.test.tsx` | Identidade visual (Sophia, brasão) |

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
├── download_pysus.py                 # Script standalone de download FTP DataSUS
├── README.md                         # Este projeto
├── docs/                             # Documentação modular
│   ├── 01-VISAO-GERAL.md            # Este arquivo
│   ├── 02-AGENTES.md                # Sistema multiagente CoALA
│   ├── 03-DADOS-ETL.md              # Fontes de dados e pipeline ETL
│   ├── 04-BACKEND-API.md            # Backend, API, chat, LLM, métricas
│   └── 05-FRONTEND.md               # Frontend React (chat + painéis técnicos)
│
├── PLANO_REFATORACAO.md              # Plano de amadurecimento de uso de LLM + novas métricas
│
├── backend/
│   ├── Dockerfile                    # Python 3.11-slim
│   ├── entrypoint.sh                # ETL automático + uvicorn
│   ├── requirements.txt             # Dependências Python
│   ├── .env.example                 # Template de variáveis de ambiente
│   ├── main.py                      # Entry point — cria app, CORS, registra routers, logging
│   ├── conftest.py                  # Configuração pytest (sys.path)
│   │
│   ├── api/                         # Camada de API
│   │   ├── routes.py                # Endpoints REST
│   │   ├── websocket.py             # WebSocket handler (streaming de resultados)
│   │   ├── chat_websocket.py        # WebSocket handler (turno de intenção do chat)
│   │   ├── chat_runner.py           # Disparo de análise a partir do chat
│   │   ├── dispatch.py              # Disparo de análise compartilhado (REST + chat)
│   │   ├── models.py                # Pydantic models + validação
│   │   ├── runners.py               # Thread runners (star, hierarchical)
│   │   └── state.py                 # Estado compartilhado (queues, threads, results)
│   │
│   ├── agents/                      # Sistema multiagente CoALA
│   │   ├── base.py                  # AgenteCoALA (classe base do framework CoALA)
│   │   ├── data_crossing.py         # cross_domain_data() + deduplicate_despesas() + detect_data_gaps()
│   │   ├── intent/
│   │   │   └── agente_interpretacao_intencao.py  # Guardrail de escopo + extração via LLM (sem regex)
│   │   ├── domain/
│   │   │   ├── vigilancia_epidemiologica.py  # Subfunção 305 (dengue, covid)
│   │   │   ├── saude_hospitalar.py           # Subfunção 302 (internações)
│   │   │   ├── atencao_primaria.py           # Subfunção 301 (vacinação)
│   │   │   └── mortalidade.py                # Transversal (todas subfunções)
│   │   ├── analytical/
│   │   │   ├── correlacao.py                 # Spearman (correlação por par)
│   │   │   ├── anomalias.py                  # Detecção via mediana
│   │   │   └── sintetizador.py               # TextSynthesizer (serviço LLM, não é agente CoALA)
│   │   ├── context/
│   │   │   └── contexto_orcamentario.py      # Tendências YoY
│   │   ├── star/
│   │   │   └── orchestrator.py               # OrquestradorEstrela (hub)
│   │   └── hierarchical/
│   │       ├── coordinator.py                # CoordenadorGeral (nível 0)
│   │       └── supervisors.py                # 3 supervisores (nível 1)
│   │
│   ├── core/                        # Utilitários
│   │   ├── llm_client.py            # Cliente LLM (DeepSeek, retry, tokens, logs de chamada com preview de prompt)
│   │   ├── metrics.py               # MetricsCollector (psutil)
│   │   ├── quality_metrics.py       # Métricas de qualidade (3 eixos) + relatório
│   │   └── streaming_adapter.py     # StreamingAdapter (chunking de texto para ws_queue)
│   │
│   ├── db/
│   │   └── neo4j_client.py          # Driver Neo4j + queries Cypher
│   │
│   ├── etl/
│   │   ├── siops_loader.py          # Ingestão planilhas FNS (.xls/.xlsx)
│   │   ├── datasus_loader.py        # Ingestão DataSUS (PySUS + cache)
│   │   ├── seed_data.py             # Seed COVID (fallback — dados não disponíveis via PySUS)
│   │   └── detect_years.py          # Auto-detecção de anos
│   │
│   ├── data/                        # Planilhas FNS + cache DataSUS
│   │   └── datasus/                 # Cache local Parquet
│   │
│   └── tests/                       # 13 arquivos de teste (102 testes)
│       ├── test_agente_interpretacao_intencao.py
│       ├── test_coala_base.py
│       ├── test_anomalias.py
│       ├── test_contexto_orcamentario.py
│       ├── test_correlacao.py
│       ├── test_data_crossing.py
│       ├── test_domain_agents.py
│       ├── test_orchestrator_star.py
│       ├── test_sintetizador.py
│       ├── test_streaming_adapter.py
│       ├── test_dispatch_analysis.py
│       └── test_chat_websocket.py
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
│       │   ├── MessageBubble.tsx                # Bolha de mensagem (usuário/sistema)
│       │   ├── TypingIndicator.tsx              # Indicador de streaming
│       │   ├── ErrorBoundary.tsx                # Captura de crash de componentes filhos
│       │   ├── RoundSelector.tsx                # Navegação entre rodadas de chat (aba técnica)
│       │   ├── ArchitecturePanel.tsx
│       │   ├── UserTab.tsx                      # Aba pública (chat + WinnerPanel)
│       │   ├── TechTab.tsx / .test.tsx          # Aba técnica (painéis + métricas + rodadas)
│       │   ├── ScoreCard.tsx
│       │   ├── QualityMetricsSection.tsx
│       │   ├── ComparativeSection.tsx
│       │   ├── Header.tsx / .test.tsx
│       │   ├── LlmControls.tsx / .test.tsx
│       │   ├── TabNav.tsx / .test.tsx
│       │   └── WinnerPanel.tsx / .test.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts       # Streaming de resultados (/ws/{analysisId})
│       │   └── useChatWebSocket.ts   # Turno de intenção do chat (/ws/chat/{sessionId})
│       ├── utils/
│       │   ├── parseWinner.ts / .test.ts
│       │   ├── validateMessage.ts / .test.ts
│       │   └── formatRoundSummary.ts
│       └── types/
│           └── index.ts             # ChatMessage, ChatRound, WSEvent, ChatWSEvent, QualityMetrics, etc.
│
└── datasus_cache/                   # Cache global pré-baixado (Parquet)
```
