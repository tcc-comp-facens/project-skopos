# Project Skopos

Sistema de comparação de duas arquiteturas multiagente CoALA (Cognitive Architectures for Language Agents — Estrela e Hierárquica) aplicado à análise de eficiência dos gastos públicos em saúde de Sorocaba-SP.

**TCC** — Engenharia de Computação, FACENS.

---

## Início rápido

```bash
# 1. Configurar variáveis de ambiente (arquivo único na raiz do repo,
#    compartilhado por backend, frontend e docker-compose)
cp .env.example .env
# Edite .env com suas credenciais Neo4j e (opcionalmente) DeepSeek

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
│   ├── api/                  # Camada de API (routes, WebSocket, chat WS, models, state, runners)
│   ├── agents/               # Sistema multiagente CoALA (ativação condicional de domínio)
│   │   ├── intent/           # Agente de interpretação de intenção (chat, sem regex)
│   │   ├── domain/           # 4 agentes de domínio
│   │   ├── analytical/       # 2 agentes CoALA + TextSynthesizer (serviço)
│   │   ├── context/          # 1 agente de contexto orçamentário
│   │   ├── star/              # Topologia estrela (OrquestradorEstrela)
│   │   └── hierarchical/     # Topologia hierárquica (CoordenadorGeral + 3 supervisores)
│   ├── core/                 # Utilitários (métricas, LLM, qualidade, streaming)
│   ├── db/                   # Cliente Neo4j
│   ├── etl/                  # Pipeline ETL (FNS, DataSUS, seed)
│   ├── tests/                # 102 testes (pytest)
│   └── data/                 # Planilhas FNS + cache DataSUS
├── frontend/                 # React 18 + TypeScript + Vite
│   └── src/
│       ├── components/       # ChatInterface, ArchitecturePanel, RoundSelector, etc.
│       ├── hooks/            # useWebSocket, useChatWebSocket
│       └── types/            # Interfaces TypeScript
├── docs/                     # Documentação modular
└── docker-compose.yml        # Neo4j + Backend + Frontend
```

---

## Backend

**Python 3.11 + FastAPI** — agentes CoALA, API REST, WebSocket (resultados + chat), integração LLM.

### Execução local

```bash
# .env fica na raiz do repo, não em backend/ — ver "Início rápido" acima.
# Rodando fora do Docker, sobrescreva NEO4J_URI para bolt://localhost:7687
# (o valor default no .env é o hostname interno do Docker Compose).
cd backend
pip install -r requirements.txt

uvicorn main:app --reload --port 8000
```

### Variáveis de ambiente

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI Bolt do Neo4j | `bolt://neo4j:7687` (Docker) / `bolt://localhost:7687` (local) |
| `NEO4J_USER` | Usuário Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Senha Neo4j | `your_password_here` |
| `DEEPSEEK_API_KEY` | Chave DeepSeek (API compatível OpenAI) | `sk-...` |
| `CORS_ORIGINS` | Origens CORS | `*` |
| `LOG_LEVEL` | Nível de log (`INFO` mostra estágios/timing/preview de prompts; `DEBUG` mostra prompts completos enviados ao LLM) | `INFO` |
| `USE_LLM_QUERY_PLANNING` | Liga planejamento de consulta via LLM nos agentes de domínio (desnecessário hoje — mapeamento trivial) | `false` |
| `VITE_API_URL` | URL base do backend REST, lida pelo frontend (Vite) | `http://localhost:8000` |
| `VITE_WS_URL` | URL base do WebSocket do backend, lida pelo frontend (Vite) | `ws://localhost:8000` |

Todas vivem num único `.env` na raiz do repo (`cp .env.example .env`) — não há mais `backend/.env`/`frontend/.env` separados.

### Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/api/analysis` | Inicia análise comparativa (formulário estruturado) |
| `GET` | `/api/analysis/{id}` | Resultado de uma análise |
| `GET` | `/api/analysis/{id}/quality` | Métricas de qualidade (3 eixos) |
| `GET` | `/api/analysis/{id}/report` | Relatório comparativo textual |
| `GET` | `/api/benchmarks` | Métricas de todas as análises |
| `GET` | `/api/data-range` | Intervalo de anos com dados disponíveis no Neo4j |
| `WS` | `/ws/{analysisId}` | Streaming em tempo real do resultado da análise |
| `WS` | `/ws/chat/{sessionId}` | Chat em linguagem natural — interpreta a intenção e dispara a análise |

### Estrutura do backend

```
backend/
├── main.py                       # Entry point — cria app, CORS, registra routers, logging
├── api/                          # Camada de API
│   ├── routes.py                 # Endpoints REST
│   ├── websocket.py              # WebSocket handler (streaming de resultados)
│   ├── chat_websocket.py         # WebSocket handler (turno de intenção do chat)
│   ├── chat_runner.py            # Disparo de análise a partir do chat
│   ├── dispatch.py               # Disparo de análise compartilhado (REST + chat)
│   ├── models.py                 # Pydantic models + validação
│   ├── runners.py                # Thread runners (star, hierarchical)
│   └── state.py                  # Estado compartilhado
├── agents/                       # Sistema multiagente CoALA
│   ├── base.py                   # AgenteCoALA (classe base do framework CoALA)
│   ├── data_crossing.py          # Cruzamento de dados + detecção de gaps
│   ├── intent/                   # Agente de interpretação de intenção (LLM, sem regex)
│   ├── domain/                   # 4 agentes de domínio
│   ├── analytical/               # 3 agentes analíticos
│   ├── context/                  # 1 agente de contexto
│   ├── star/                     # Topologia estrela
│   └── hierarchical/             # Topologia hierárquica
├── core/                         # Utilitários
│   ├── llm_client.py             # Cliente LLM (DeepSeek, retry, contagem de tokens, logs de chamada)
│   ├── metrics.py                # MetricsCollector (psutil)
│   ├── quality_metrics.py        # Métricas de qualidade + relatório
│   └── streaming_adapter.py      # StreamingAdapter (chunking para ws_queue)
├── db/
│   └── neo4j_client.py           # Driver Neo4j
├── etl/                          # Pipeline ETL
│   ├── siops_loader.py           # Planilhas FNS (.xls/.xlsx)
│   ├── datasus_loader.py         # DataSUS (PySUS + cache)
│   ├── seed_data.py              # Seed COVID (fallback)
│   └── detect_years.py           # Auto-detecção de anos
├── data/                         # Planilhas + cache
└── tests/                        # 13 arquivos de teste (102 testes)
```

---

## Frontend

**React 18 + TypeScript + Vite** — chat em linguagem natural com streaming em tempo real.

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

### Componentes principais

| Componente | Arquivo | Responsabilidade |
|------------|---------|------------------|
| `App` | `src/App.tsx` | Layout principal, estado de rodadas de chat, integração API/WS |
| `ChatInterface` | `src/components/ChatInterface.tsx` | Chat de texto livre (aba Usuário) — dispara análises |
| `RoundSelector` | `src/components/RoundSelector.tsx` | Navegação entre rodadas de chat na aba técnica |
| `ArchitecturePanel` | `src/components/ArchitecturePanel.tsx` | Painel de resultado por arquitetura |
| `useWebSocket` | `src/hooks/useWebSocket.ts` | Hook WS de resultados, com reconexão automática |
| `useChatWebSocket` | `src/hooks/useChatWebSocket.ts` | Hook WS do turno de intenção do chat |

Ver [docs/05-FRONTEND.md](docs/05-FRONTEND.md) para a lista completa de componentes.

---

## Testes

```bash
# Backend — todos (102 testes)
cd backend && pytest

# Backend — verbose
cd backend && pytest -v

# Frontend (9 arquivos de teste)
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
| [02-AGENTES.md](docs/02-AGENTES.md) | Modelo CoALA, agentes, topologias, regras de negócio |
| [03-DADOS-ETL.md](docs/03-DADOS-ETL.md) | Fontes institucionais (FNS, DataSUS), ETL, Neo4j, limitações |
| [04-BACKEND-API.md](docs/04-BACKEND-API.md) | API REST, WebSocket, chat, LLM, métricas de qualidade, erros |
| [05-FRONTEND.md](docs/05-FRONTEND.md) | Componentes, chat, hooks WS, tipos, acessibilidade |

Também há o [PLANO_REFATORACAO.md](PLANO_REFATORACAO.md), com o plano (em andamento) de amadurecimento do uso de LLM nos agentes e de novas métricas de avaliação, fundamentado em literatura acadêmica.

---

## Licença

Projeto acadêmico — TCC FACENS.
