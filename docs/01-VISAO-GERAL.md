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
| **Estrela** | Um agente central (orquestrador) coordena 8 agentes periféricos | Ponto único de controle, comunicação centralizada |
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
| LLM (fallback) | Google Gemini | gemini-2.0-flash | Fallback quando Groq atinge limite de cota |
| Métricas | psutil | latest | Coleta de CPU e memória por agente em tempo real |
| Estatística | SciPy | latest | Pearson, Spearman, Kendall Tau-b |
| ETL DataSUS | PySUS | latest | Download automatizado de dados do FTP DataSUS |
| ETL SIOPS | openpyxl + xlrd | latest | Leitura de planilhas .xlsx e .xls |
| Manipulação de dados | pandas | latest | Transformação e filtragem de DataFrames |
| Containerização | Docker + Docker Compose | latest | Orquestração de Neo4j, backend e frontend |
| Testes Backend | pytest + Hypothesis | latest | Testes unitários e property-based |
| Testes Frontend | Vitest + fast-check | 2.0.4 + 3.21.0 | Testes unitários e property-based |
| Testing Library | @testing-library/react | 16.0.0 | Testes de componentes React |

### Dependências Backend (requirements.txt)

```
fastapi, uvicorn[standard], neo4j, pysus, psutil, hypothesis, pytest,
pytest-asyncio, python-dotenv, httpx, google-genai, groq, openpyxl,
xlrd, pandas, scipy
```

### Dependências Frontend (package.json)

```
react ^18.3.1, react-dom ^18.3.1, typescript ^5.5.3, vite ^5.3.4,
vitest ^2.0.4, @testing-library/react ^16.0.0, fast-check ^3.21.0
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
│  │  (8 agentes)         (3 supervisores    │              │   │
│  │                       + 8 agentes)      │              │   │
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
6. Cada thread executa seu pipeline de 8 agentes BDI:
   - 4 agentes de domínio consultam Neo4j (despesas + indicadores)
   - 1 agente de contexto analisa tendências orçamentárias
   - 1 agente de correlação calcula Pearson/Spearman/Kendall
   - 1 agente de anomalias detecta ineficiências via mediana
   - 1 agente sintetizador gera texto via LLM com streaming
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
- (Opcional) Chave de API Groq e/ou Google Gemini para geração de texto via LLM
- (Opcional) Python 3.11+ e Node.js 20+ para desenvolvimento local

### Variáveis de Ambiente

**Backend (`backend/.env`):**

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI de conexão Bolt do Neo4j | `bolt://neo4j:7687` (Docker) ou `bolt://localhost:7687` (local) |
| `NEO4J_USER` | Usuário do Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Senha do Neo4j | `your_password_here` |
| `GROQ_API_KEY` | Chave API Groq (prioridade) | `gsk_...` |
| `GEMINI_API_KEY` | Chave API Google Gemini (fallback) | `AI...` |
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
| Backend | pytest + Hypothesis | Unitários + Property-Based | 19 | ~322 |
| Frontend | Vitest + fast-check + @testing-library/react | Unitários + Property-Based | 4 | — |

### Backend — Arquivos de Teste

| Arquivo | Escopo |
|---------|--------|
| `test_agent_base.py` | Classe AgenteBDI, ciclo BDI, recuperação de falhas |
| `test_anomalias.py` | Detecção de anomalias (unitário) |
| `test_anomalias_properties.py` | Propriedades de anomalias (Hypothesis) |
| `test_atencao_primaria.py` | Agente de domínio Atenção Primária |
| `test_contexto_orcamentario.py` | Agente de contexto orçamentário |
| `test_contexto_properties.py` | Propriedades de tendências (Hypothesis) |
| `test_correlacao.py` | Correlações estatísticas (unitário) |
| `test_correlacao_properties.py` | Propriedades de correlação (Hypothesis) |
| `test_data_crossing.py` | Cruzamento de dados e detecção de gaps |
| `test_hierarchical_coordinator.py` | Arquitetura hierárquica completa |
| `test_main.py` | Endpoints REST e WebSocket |
| `test_message_counter.py` | Contador de mensagens |
| `test_metrics.py` | MetricsCollector (unitário) |
| `test_metrics_properties.py` | Propriedades de métricas (Hypothesis) |
| `test_mortalidade.py` | Agente de domínio Mortalidade |
| `test_saude_hospitalar.py` | Agente de domínio Saúde Hospitalar |
| `test_sintetizador.py` | Agente sintetizador (LLM + fallback) |
| `test_star_orchestrator.py` | Arquitetura estrela completa |
| `test_vigilancia_epidemiologica.py` | Agente de domínio Vigilância Epidemiológica |

### Frontend — Arquivos de Teste

| Arquivo | Escopo |
|---------|--------|
| `src/App.test.tsx` | Componente principal, integração API |
| `src/components/AnalysisControls.test.tsx` | Formulário de entrada |
| `src/components/ArchitecturePanel.test.tsx` | Painel de resultado |
| `src/hooks/useWebSocket.test.ts` | Hook WebSocket |

### Como Rodar

```bash
# Backend — todos os testes
cd backend && pytest

# Backend — com cobertura
cd backend && pytest --cov=. --cov-report=term-missing

# Backend — apenas property-based
cd backend && pytest -k "properties"

# Backend — teste específico
cd backend && pytest tests/test_correlacao_properties.py -v

# Frontend — todos os testes
cd frontend && npm run test

# Frontend — watch mode
cd frontend && npm run test:watch
```

### Testes Property-Based (PBT)

O projeto usa PBT para validar propriedades formais de correção:

| Propriedade | Módulo | Framework |
|-------------|--------|-----------|
| Correlações sempre em [-1, 1] | correlacao | Hypothesis |
| Classificação consistente com Spearman | correlacao | Hypothesis |
| Pares com < 2 pontos retornam 0.0 | correlacao | Hypothesis |
| Anomalias só detectadas com ≥ 2 pontos | anomalias | Hypothesis |
| Tendência "insuficiente" com < 2 anos | contexto_orcamentario | Hypothesis |
| Variação YoY correta | contexto_orcamentario | Hypothesis |
| MetricsCollector sempre positivo | metrics | Hypothesis |
| MessageCounter thread-safe | message_counter | Hypothesis |

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
│   │   ├── data_crossing.py         # cross_domain_data() + detect_data_gaps()
│   │   ├── domain/
│   │   │   ├── vigilancia_epidemiologica.py  # Subfunção 305 (dengue, covid)
│   │   │   ├── saude_hospitalar.py           # Subfunção 302 (internações)
│   │   │   ├── atencao_primaria.py           # Subfunção 301 (vacinação)
│   │   │   └── mortalidade.py                # Transversal (todas subfunções)
│   │   ├── analytical/
│   │   │   ├── correlacao.py                 # Pearson, Spearman, Kendall
│   │   │   ├── anomalias.py                  # Detecção via mediana
│   │   │   └── sintetizador.py               # LLM + streaming + fallback
│   │   ├── context/
│   │   │   └── contexto_orcamentario.py      # Tendências YoY
│   │   ├── star/
│   │   │   └── orchestrator.py               # OrquestradorEstrela (hub)
│   │   └── hierarchical/
│   │       ├── coordinator.py                # CoordenadorGeral (nível 0)
│   │       └── supervisors.py                # 3 supervisores (nível 1)
│   │
│   ├── core/                        # Utilitários
│   │   ├── llm_client.py            # Cliente LLM (Groq + Gemini, rate limit, retry)
│   │   ├── metrics.py               # MetricsCollector (psutil)
│   │   ├── message_counter.py       # MessageCounter (thread-safe)
│   │   └── quality_metrics.py       # Métricas de qualidade (3 eixos) + relatório
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
│   └── tests/                       # 19 arquivos de teste (322 testes)
│       ├── test_agent_base.py
│       ├── test_anomalias.py
│       ├── test_anomalias_properties.py
│       ├── test_atencao_primaria.py
│       ├── test_contexto_orcamentario.py
│       ├── test_contexto_properties.py
│       ├── test_correlacao.py
│       ├── test_correlacao_properties.py
│       ├── test_data_crossing.py
│       ├── test_hierarchical_coordinator.py
│       ├── test_main.py
│       ├── test_message_counter.py
│       ├── test_metrics.py
│       ├── test_metrics_properties.py
│       ├── test_mortalidade.py
│       ├── test_saude_hospitalar.py
│       ├── test_sintetizador.py
│       ├── test_star_orchestrator.py
│       └── test_vigilancia_epidemiologica.py
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
│       ├── App.test.tsx
│       ├── config.ts               # API_URL, WS_URL
│       ├── styles.css              # Tema dark, responsivo
│       ├── components/
│       │   ├── AnalysisControls.tsx
│       │   ├── AnalysisControls.test.tsx
│       │   ├── ArchitecturePanel.tsx
│       │   └── ArchitecturePanel.test.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts
│       │   └── useWebSocket.test.ts
│       └── types/
│           └── index.ts             # AnalysisRequest, WSEvent, BenchmarkMetrics
│
└── datasus_cache/                   # Cache global pré-baixado (Parquet)
```
