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

Este sistema compara duas arquiteturas multiagente baseadas no modelo BDI (Belief-Desire-Intention) aplicadas à análise de eficiência dos gastos públicos em saúde do município de Sorocaba-SP. O projeto é um Trabalho de Conclusão de Curso (TCC) de Engenharia de Computação.

O sistema cruza dados financeiros do SIOPS (despesas municipais por subfunção orçamentária) com indicadores epidemiológicos do DataSUS (dengue, COVID-19, vacinação, internações, mortalidade) para avaliar a qualidade e eficiência do gasto público em saúde.

As duas arquiteturas — **Estrela** e **Hierárquica** — são executadas em paralelo sobre os mesmos dados, permitindo uma comparação objetiva de desempenho, escalabilidade, overhead de comunicação e adequação ao cenário proposto.

## Problema

- Não existe ferramenta que correlacione automaticamente gastos municipais em saúde com indicadores epidemiológicos
- A avaliação de eficiência do gasto público em saúde é feita manualmente por auditores
- Não há comparação empírica entre topologias multiagente BDI para este domínio
- Dados de saúde estão dispersos em múltiplos sistemas (SIOPS, SINAN, SIM, SIH, SI-PNI) sem integração

## Objetivo

Avaliar comparativamente duas topologias de sistemas multiagente BDI:

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
| LLM (primário) | Groq | llama-3.3-70b-versatile | Geração de análises textuais; baixa latência |
| Métricas | psutil | latest | Coleta de CPU e memória por agente em tempo real |
| Estatística | SciPy | latest | Spearman (correlação) |
| ETL DataSUS | PySUS | latest | Download automatizado de dados do FTP DataSUS |
| ETL SIOPS | openpyxl + xlrd | latest | Leitura de planilhas .xlsx e .xls |
| Manipulação de dados | pandas | latest | Transformação e filtragem de DataFrames |
| Containerização | Docker + Docker Compose | latest | Orquestração de Neo4j, backend e frontend |
| Testes Backend | pytest | latest | Testes unitários |
| Testes Frontend | Vitest | 2.0.4 | Testes de componentes |
| Testing Library | @testing-library/react | 16.0.0 | Testes de componentes React |

### Dependências Backend (requirements.txt)

```
fastapi, uvicorn[standard], neo4j, pysus, psutil, pytest,
python-dotenv, httpx, groq, openpyxl,
xlrd, pandas, scipy
```

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
│  │AnalysisControls│ │ArchitecturePanel│ │ArchitecturePanel│    │
│  │(período,       │ │  Hierárquica   │ │    Estrela     │     │
│  │ parâmetros)    │ │  (streaming)   │ │  (streaming)   │     │
│  └───────┬────────┘ └───────┬────────┘ └───────┬────────┘    │
│          │                  │                   │             │
│          │          useWebSocket hook           │             │
└──────────┼──────────────────┼───────────────────┼─────────────┘
           │ HTTP POST        │ WebSocket         │
┌──────────┼──────────────────┼───────────────────┼─────────────┐
│          ▼                  ▼                   ▼             │
│                Backend (FastAPI + Python 3.11)                 │
│  ┌────────────────────────────────────────────────────────┐   │
│  │              POST /api/analysis                         │   │
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

### Fluxo Completo de uma Análise

1. O usuário seleciona período (ano início/fim) e parâmetros de saúde no frontend
2. O frontend envia `POST /api/analysis` ao backend
3. O backend valida parâmetros (retorna 400 se inválidos)
4. Cria nó `Analise` no Neo4j e vincula `DespesaSIOPS` e `IndicadorDataSUS` existentes
5. Duas threads daemon são disparadas em paralelo (uma por arquitetura)
6. Cada thread executa seu pipeline de agentes BDI:
   - Agentes de domínio relevantes aos health_params consultam Neo4j (despesas + indicadores)
   - 1 agente de contexto analisa tendências orçamentárias
   - 1 agente de correlação calcula Spearman entre gastos e indicadores
   - 1 agente de anomalias detecta ineficiências via mediana
   - 1 sintetizador de texto (`TextSynthesizer`, serviço não-BDI) gera texto via LLM com streaming
7. Chunks de texto (~80 chars) são enviados ao frontend via WebSocket em tempo real
8. Métricas de execução (tempo, CPU, memória) são coletadas por agente e persistidas no Neo4j
9. Contagem de mensagens entre agentes é registrada
10. Após ambas completarem: métricas de qualidade são computadas e relatório comparativo é gerado e transmitido via WebSocket

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
- (Opcional) Chave de API Groq para geração de texto via LLM
- (Opcional) Python 3.11+ e Node.js 20+ para desenvolvimento local

### Variáveis de Ambiente

**Backend (`backend/.env`):**

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI de conexão Bolt do Neo4j | `bolt://neo4j:7687` (Docker) ou `bolt://localhost:7687` (local) |
| `NEO4J_USER` | Usuário do Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Senha do Neo4j | `your_password_here` |
| `GROQ_API_KEY` | Chave API Groq | `gsk_...` |
| `CORS_ORIGINS` | Origens CORS permitidas | `*` ou `http://localhost:5173` |

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
4. Executa seed de fallback (dados mínimos 2019-2021)
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
| Backend | pytest | Unitários | 9 | 61 |
| Frontend | Vitest + @testing-library/react | Componentes + Utilitários | 6 | 35 |

### Backend — Arquivos de Teste

| Arquivo | Escopo |
|---------|--------|
| `test_correlacao.py` | Correlações Spearman (vazio, ponto único, perfeita ±, classificação) |
| `test_anomalias.py` | Detecção de anomalias (mediana, tipos, regra <2 pontos) |
| `test_contexto_orcamentario.py` | Tendências orçamentárias (crescimento, corte, estagnação) |
| `test_data_crossing.py` | Cruzamento de dados, deduplicação, detecção de gaps |
| `test_sintetizador.py` | TextSynthesizer (fallback, seções, streaming) |
| `test_streaming_adapter.py` | StreamingAdapter (chunking, formato de evento) |
| `test_message_counter.py` | MessageCounter (thread-safety, incremento) |
| `test_orchestrator_star.py` | OrquestradorEstrela (health_params filtering, degradação, métricas) |
| `test_domain_agents.py` | Agente de domínio (filtro subfunção, fallback) |

### Frontend — Arquivos de Teste

| Arquivo | Escopo |
|---------|--------|
| `src/utils/parseWinner.test.ts` | Extração do vencedor do relatório comparativo |
| `src/components/AnalysisControls.test.tsx` | Formulário de entrada (validação, toggles) |
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
│   ├── 02-AGENTES.md                # Sistema multiagente BDI
│   ├── 03-DADOS-ETL.md              # Fontes de dados e pipeline ETL
│   ├── 04-BACKEND-API.md            # Backend, API, LLM, métricas
│   └── 05-FRONTEND.md               # Frontend React
│
├── backend/
│   ├── Dockerfile                    # Python 3.11-slim
│   ├── entrypoint.sh                # ETL automático + uvicorn
│   ├── requirements.txt             # 16 dependências Python
│   ├── .env.example                 # Template de variáveis de ambiente
│   ├── main.py                      # Entry point — cria app, CORS, registra routers
│   ├── conftest.py                  # Configuração pytest (sys.path)
│   │
│   ├── api/                         # Camada de API
│   │   ├── routes.py                # 5 endpoints REST
│   │   ├── websocket.py             # WebSocket handler (streaming)
│   │   ├── models.py                # Pydantic models + validação
│   │   ├── runners.py               # Thread runners (star, hierarchical)
│   │   └── state.py                 # Estado compartilhado (queues, threads, results)
│   │
│   ├── agents/                      # Sistema multiagente BDI
│   │   ├── base.py                  # AgenteBDI (modelo BDI base)
│   │   ├── data_crossing.py         # cross_domain_data() + deduplicate_despesas() + detect_data_gaps()
│   │   ├── domain/
│   │   │   ├── vigilancia_epidemiologica.py  # Subfunção 305 (dengue, covid)
│   │   │   ├── saude_hospitalar.py           # Subfunção 302 (internações)
│   │   │   ├── atencao_primaria.py           # Subfunção 301 (vacinação)
│   │   │   └── mortalidade.py                # Transversal (todas subfunções)
│   │   ├── analytical/
│   │   │   ├── correlacao.py                 # Spearman (correlação por par)
│   │   │   ├── anomalias.py                  # Detecção via mediana
│   │   │   └── sintetizador.py               # TextSynthesizer (serviço LLM, não-BDI)
│   │   ├── context/
│   │   │   └── contexto_orcamentario.py      # Tendências YoY
│   │   ├── star/
│   │   │   └── orchestrator.py               # OrquestradorEstrela (hub)
│   │   └── hierarchical/
│   │       ├── coordinator.py                # CoordenadorGeral (nível 0)
│   │       └── supervisors.py                # 3 supervisores (nível 1)
│   │
│   ├── core/                        # Utilitários
│   │   ├── llm_client.py            # Cliente LLM (Groq, cadeia de fallback entre modelos)
│   │   ├── metrics.py               # MetricsCollector (psutil)
│   │   ├── message_counter.py       # MessageCounter (thread-safe)
│   │   ├── quality_metrics.py       # Métricas de qualidade (3 eixos) + relatório
│   │   └── streaming_adapter.py     # StreamingAdapter (chunking de texto para ws_queue)
│   │
│   ├── db/
│   │   └── neo4j_client.py          # Driver Neo4j + queries Cypher
│   │
│   ├── etl/
│   │   ├── siops_loader.py          # Ingestão planilhas FNS (.xls/.xlsx)
│   │   ├── datasus_loader.py        # Ingestão DataSUS (PySUS + cache)
│   │   ├── seed_data.py             # Dados fallback Sorocaba 2019-2021
│   │   └── detect_years.py          # Auto-detecção de anos
│   │
│   ├── data/                        # Planilhas FNS + cache DataSUS
│   │   └── datasus/                 # Cache local Parquet
│   │
│   └── tests/                       # 9 arquivos de teste (61 testes)
│       ├── test_anomalias.py
│       ├── test_contexto_orcamentario.py
│       ├── test_correlacao.py
│       ├── test_data_crossing.py
│       ├── test_domain_agents.py
│       ├── test_message_counter.py
│       ├── test_orchestrator_star.py
│       ├── test_sintetizador.py
│       └── test_streaming_adapter.py
│
├── frontend/
│   ├── Dockerfile                   # Node 20-alpine
│   ├── package.json                 # React 18 + Vite + Vitest
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx                 # Entry point
│       ├── App.tsx                  # Layout principal + integração API/WS
│       ├── config.ts               # API_URL, WS_URL
│       ├── styles.css              # Tema dark, responsivo
│       ├── test-setup.ts           # Setup Vitest (jest-dom)
│       ├── components/
│       │   ├── AnalysisControls.tsx
│       │   ├── AnalysisControls.test.tsx
│       │   ├── ArchitecturePanel.tsx
│       │   ├── Header.tsx
│       │   ├── Header.test.tsx
│       │   ├── LlmControls.tsx
│       │   ├── LlmControls.test.tsx
│       │   ├── TabNav.tsx
│       │   ├── TabNav.test.tsx
│       │   ├── WinnerPanel.tsx
│       │   └── WinnerPanel.test.tsx
│       ├── hooks/
│       │   └── useWebSocket.ts
│       ├── utils/
│       │   ├── parseWinner.ts
│       │   └── parseWinner.test.ts
│       └── types/
│           └── index.ts             # AnalysisRequest, WSEvent, BenchmarkMetrics
│
└── datasus_cache/                   # Cache global pré-baixado (Parquet)
```
