# Project Skopos

Sistema de comparação de duas arquiteturas multiagente BDI (Estrela e Hierárquica) aplicado à análise de eficiência dos gastos públicos em saúde de Sorocaba-SP.

**TCC** — Engenharia de Computação, FACENS.

---

## Início rápido

```bash
# 1. Configurar variáveis de ambiente
cp backend/.env.example backend/.env
# Edite backend/.env com suas credenciais Neo4j e (opcionalmente) Groq/Gemini

# 2. Subir todos os serviços
docker compose up --build

# 3. Acessar
# Frontend: http://localhost:5173
# API:      http://localhost:8000
# Swagger:  http://localhost:8000/docs
# Neo4j:    http://localhost:7474
```

---

## Estrutura do projeto

```
project-skopos/
├── backend/                  # Python 3.11 + FastAPI
│   ├── api/                  # Camada de API (routes, WebSocket, models, state, runners)
│   ├── agents/               # Sistema multiagente BDI (8 agentes por topologia)
│   │   ├── domain/           # 4 agentes de domínio
│   │   ├── analytical/       # 3 agentes analíticos
│   │   ├── context/          # 1 agente de contexto orçamentário
│   │   ├── star/             # Topologia estrela (OrquestradorEstrela)
│   │   └── hierarchical/     # Topologia hierárquica (CoordenadorGeral + 3 supervisores)
│   ├── core/                 # Utilitários (métricas, LLM, qualidade, mensagens)
│   ├── db/                   # Cliente Neo4j
│   ├── etl/                  # Pipeline ETL (FNS, DataSUS, seed)
│   ├── tests/                # 322 testes (pytest + Hypothesis)
│   └── data/                 # Planilhas FNS + cache DataSUS
├── frontend/                 # React 18 + TypeScript + Vite
│   └── src/
│       ├── components/       # AnalysisControls, ArchitecturePanel
│       ├── hooks/            # useWebSocket
│       └── types/            # Interfaces TypeScript
├── docs/                     # Documentação modular
└── docker-compose.yml        # Neo4j + Backend + Frontend
```

---

## Backend

**Python 3.11 + FastAPI** — agentes BDI, API REST, WebSocket, integração LLM.

### Execução local

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # edite com credenciais Neo4j

python -m etl.seed_data       # popular dados mínimos (requer Neo4j rodando)
uvicorn main:app --reload --port 8000
```

### Variáveis de ambiente

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI Bolt do Neo4j | `bolt://neo4j:7687` (Docker) / `bolt://localhost:7687` (local) |
| `NEO4J_USER` | Usuário Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Senha Neo4j | `your_password_here` |
| `GROQ_API_KEY` | Chave Groq (primário) | `gsk_...` |
| `GEMINI_API_KEY` | Chave Gemini (fallback) | `AI...` |
| `CORS_ORIGINS` | Origens CORS | `*` |

### Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/api/analysis` | Inicia análise comparativa |
| `GET` | `/api/analysis/{id}` | Resultado de uma análise |
| `GET` | `/api/analysis/{id}/quality` | Métricas de qualidade (3 eixos) |
| `GET` | `/api/analysis/{id}/report` | Relatório comparativo textual |
| `GET` | `/api/benchmarks` | Métricas de todas as análises |
| `WS` | `/ws/{analysisId}` | Streaming em tempo real |

### Estrutura do backend

```
backend/
├── main.py                       # Entry point — cria app, CORS, registra routers
├── api/                          # Camada de API
│   ├── routes.py                 # 5 endpoints REST
│   ├── websocket.py              # WebSocket handler (streaming)
│   ├── models.py                 # Pydantic models + validação
│   ├── runners.py                # Thread runners (star, hierarchical)
│   └── state.py                  # Estado compartilhado
├── agents/                       # Sistema multiagente BDI
│   ├── base.py                   # AgenteBDI (classe base)
│   ├── data_crossing.py          # Cruzamento de dados + detecção de gaps
│   ├── domain/                   # 4 agentes de domínio
│   ├── analytical/               # 3 agentes analíticos
│   ├── context/                  # 1 agente de contexto
│   ├── star/                     # Topologia estrela
│   └── hierarchical/             # Topologia hierárquica
├── core/                         # Utilitários
│   ├── llm_client.py             # Cliente LLM (Groq + Gemini)
│   ├── metrics.py                # MetricsCollector (psutil)
│   ├── message_counter.py        # MessageCounter (thread-safe)
│   └── quality_metrics.py        # Métricas de qualidade + relatório
├── db/
│   └── neo4j_client.py           # Driver Neo4j
├── etl/                          # Pipeline ETL
│   ├── siops_loader.py           # Planilhas FNS (.xls/.xlsx)
│   ├── datasus_loader.py         # DataSUS (PySUS + cache)
│   ├── seed_data.py              # Dados fallback 2019-2021
│   └── detect_years.py           # Auto-detecção de anos
├── data/                         # Planilhas + cache
└── tests/                        # 322 testes
```

---

## Frontend

**React 18 + TypeScript + Vite** — interface com streaming em tempo real.

### Execução local

```bash
cd frontend
npm ci
npm run dev                   # http://localhost:5173
```

### Variáveis de ambiente

| Variável | Descrição | Default |
|----------|-----------|---------|
| `VITE_API_URL` | URL da API REST | `http://localhost:8000` |
| `VITE_WS_URL` | URL do WebSocket | `ws://localhost:8000` |

### Componentes

| Componente | Arquivo | Responsabilidade |
|------------|---------|------------------|
| `App` | `src/App.tsx` | Layout principal, integração API/WS |
| `AnalysisControls` | `src/components/AnalysisControls.tsx` | Formulário (período + parâmetros) |
| `ArchitecturePanel` | `src/components/ArchitecturePanel.tsx` | Painel de resultado por arquitetura |
| `useWebSocket` | `src/hooks/useWebSocket.ts` | Hook WS com reconexão automática |

---

## Testes

```bash
# Backend — todos (322 testes)
cd backend && pytest

# Backend — com cobertura
cd backend && pytest --cov=. --cov-report=term-missing

# Backend — apenas property-based (Hypothesis)
cd backend && pytest -k "properties"

# Frontend
cd frontend && npm test
```

---

## Docker

```bash
docker compose up --build     # subir tudo
docker compose logs -f        # logs
docker compose down           # parar
```

| Serviço | Porta | URL |
|---------|-------|-----|
| Frontend | 5173 | http://localhost:5173 |
| Backend | 8000 | http://localhost:8000 |
| Swagger | 8000 | http://localhost:8000/docs |
| Neo4j Browser | 7474 | http://localhost:7474 |
| Neo4j Bolt | 7687 | bolt://localhost:7687 |

---

## Documentação

Documentação detalhada em [`docs/`](docs/):

| Arquivo | Conteúdo |
|---------|----------|
| [01-VISAO-GERAL.md](docs/01-VISAO-GERAL.md) | Introdução, stack, arquitetura, como executar, testes, estrutura |
| [02-AGENTES.md](docs/02-AGENTES.md) | Modelo BDI, 8 agentes, topologias, regras de negócio |
| [03-DADOS-ETL.md](docs/03-DADOS-ETL.md) | Fontes institucionais (FNS, DataSUS), ETL, Neo4j, limitações |
| [04-BACKEND-API.md](docs/04-BACKEND-API.md) | API REST, WebSocket, LLM, métricas de qualidade, erros |
| [05-FRONTEND.md](docs/05-FRONTEND.md) | Componentes, hook WS, tipos, acessibilidade |

---

## Licença

Projeto acadêmico — TCC FACENS.
