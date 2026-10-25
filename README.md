# Project Skopos

Sistema de comparação de duas arquiteturas multiagente CoALA (Cognitive Architectures for Language Agents — Estrela e Árvore) aplicado à análise de eficiência dos gastos públicos em saúde de Sorocaba-SP.

**TCC** — Engenharia de Computação, FACENS.

O usuário faz uma pergunta em linguagem natural; as duas topologias rodam em paralelo sobre os mesmos dados (execução orçamentária municipal via LAI × indicadores DATASUS/TabNet), e o sistema compara eficiência, qualidade da resposta (incluindo avaliação RAGAS), resiliência e custo.

---

## Início rápido

```bash
# 1. Configurar variáveis de ambiente (arquivo único na raiz do repo,
#    compartilhado por backend, frontend e docker-compose)
cp .env.example .env
# Edite .env com suas credenciais Neo4j e a chave do provedor de LLM
# (DeepSeek por default no código, ou OpenAI via LLM_PROVIDER=openai)

# 2. Subir todos os serviços — o ETL carrega Dados/ no Neo4j
#    automaticamente a cada subida (idempotente)
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
├── .env.example              # Template do .env único (backend + frontend + compose)
├── docker-compose.yml        # Neo4j + Backend + Frontend
├── Dados/                    # Entrada do ETL: orcamento/ (CSVs LAI) + Sorocaba_DATASUS_2015-2025/ (xlsx TabNet)
├── backend/                  # Python 3.11 + FastAPI
│   ├── api/                  # REST, WebSocket de resultados, WebSocket de chat, dispatch, runners, estado
│   ├── agents/               # Sistema multiagente CoALA (ativação condicional por indicador)
│   │   ├── intent/           # Agente de interpretação de intenção (LLM, sem regex)
│   │   ├── domain/           # 8 agentes de saúde (1 por sistema DATASUS) + agente de orçamento por subfunção
│   │   ├── analytical/       # AgenteAnalitico (Spearman + anomalias), priorização + TextSynthesizer (serviço)
│   │   ├── context/          # Agente de contexto orçamentário (tendência/CAGR)
│   │   ├── star/             # Topologia estrela (OrquestradorEstrela)
│   │   └── hierarchical/     # Topologia em árvore (CoordenadorGeral + 4 supervisores)
│   ├── core/                 # LLM, métricas de execução e qualidade, RAGAS, verificação, streaming
│   ├── db/                   # Cliente Neo4j + construção segura de Cypher
│   ├── etl/                  # Loaders de orçamento (LAI) e indicadores de saúde (TabNet)
│   ├── scripts/              # Diagnóstico (check_embeddings) e avaliação (batch_eval, analise_rodadas, conferencia_etl…)
│   └── tests/                # pytest
├── frontend/                 # React 18 + TypeScript + Vite
│   └── src/
│       ├── components/       # ChatInterface, RoundOverview/RoundCard, ArchitecturePanel, AgentDataCard, etc.
│       ├── hooks/            # useWebSocket, useChatWebSocket
│       ├── utils/            # parseWinner, buildAgentChartData, renderFormattedText, etc.
│       └── types/            # Interfaces TypeScript
└── docs/                     # Documentação modular (+ arquitetura/ e historico/)
```

---

## Backend

**Python 3.11 + FastAPI** — agentes CoALA, API REST, WebSocket (resultados + chat), integração LLM, avaliação RAGAS.

### Execução local

```bash
# .env fica na raiz do repo, não em backend/ — ver "Início rápido" acima.
# Rodando fora do Docker, use NEO4J_URI=bolt://localhost:7687
# (o valor default no .env é o hostname interno do Docker Compose).
docker compose up neo4j -d          # só o Neo4j
cd backend
pip install -r requirements.txt
python run_etl.py                   # carrega Dados/ no Neo4j (troca neo4j: por localhost: sozinho)

uvicorn main:app --reload --port 8000
```

### Variáveis de ambiente

Todas vivem num único `.env` na raiz do repo (`cp .env.example .env`) — não há `backend/.env`/`frontend/.env` separados. O `.env.example` documenta cada uma em detalhe.

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `NEO4J_URI` | URI Bolt do Neo4j | `bolt://neo4j:7687` (Docker) / `bolt://localhost:7687` (local) |
| `NEO4J_USER` / `NEO4J_PASSWORD` | Credenciais do Neo4j | `neo4j` / `your_password_here` |
| `NEO4J_AUTH` | Usuário/senha com que o serviço `neo4j` do compose inicializa o banco (manter em sincronia com os dois acima) | `neo4j/your_password_here` |
| `LLM_PROVIDER` | Provedor de LLM ativo (`deepseek` — default do código — ou `openai`) | `openai` |
| `DEEPSEEK_API_KEY` | Chave DeepSeek (API compatível OpenAI) | `sk-...` |
| `OPENAI_API_KEY` | Chave OpenAI — usada quando `LLM_PROVIDER=openai` e pelo juiz do RAGAS | `sk-proj-...` |
| `DEEPSEEK_MODEL` / `OPENAI_MODEL` | Sobrescreve o modelo default do provedor | `deepseek-v4-flash` / `gpt-5.6-luna` |
| `OPENAI_BASE_URL`, `OPENAI_REASONING_EFFORT`, `OPENAI_MAX_COMPLETION_TOKENS`, `OPENAI_STORE_LOGS` | Ajustes opcionais da OpenAI (gateway, esforço de raciocínio, teto de saída, retenção nos logs) | `low`, `16384` |
| `RAGAS_PROVIDER` / `RAGAS_MODEL` | Juiz da avaliação RAGAS — independente de `LLM_PROVIDER` | `openai` / `gpt-5.6-luna` |
| `RAGAS_EMBEDDING_MODEL` / `RAGAS_EMBEDDING_FALLBACKS` | Embeddings da métrica answer relevancy e cadeia de fallback | `text-embedding-3-large` |
| `LOG_LEVEL` | `INFO` mostra estágios/timing/preview de prompts; `DEBUG` mostra prompts completos | `INFO` |
| `CORS_ORIGINS` | Origens CORS | `*` |
| `USE_LLM_QUERY_PLANNING` | Flag do planejamento de consulta via LLM — **sem efeito hoje** (nenhum agente chama `plan_query` desde a migração do modelo de dados; ver [docs/02](docs/02-AGENTES.md#deliberação-de-dimensão)) | `false` |
| `VITE_API_URL` / `VITE_WS_URL` | URLs do backend REST e WebSocket, lidas pelo frontend (Vite) | `http://localhost:8000` / `ws://localhost:8000` |

`DADOS_DIR` (onde os loaders procuram `Dados/`) é definida pelo `docker-compose.yml` como `/Dados`; fora do Docker o default é a pasta `Dados/` da raiz.

### Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| `WS` | `/ws/chat/{sessionId}` | Chat em linguagem natural — interpreta a intenção e dispara a análise (único caminho de entrada) |
| `WS` | `/ws/{analysisId}` | Streaming da análise: texto das duas topologias, métricas, consultas por agente, RAGAS e relatório comparativo |
| `GET` | `/api/analysis/{id}` | Nó `Analise` persistido no Neo4j |
| `GET` | `/api/analysis/{id}/quality` | Métricas de qualidade (eficiência, qualidade + RAGAS, resiliência, custo, comunicação, outcome) |
| `GET` | `/api/analysis/{id}/report` | Relatório comparativo textual |
| `GET` | `/api/benchmarks` | Métricas de execução de todas as análises |
| `GET` | `/api/data-range` | Intervalo de anos com dados disponíveis no Neo4j |
| `GET` | `/api/metrics/guardrail` | Taxa de rejeição do guardrail de escopo do chat |
| `GET` | `/api/metrics/query-planning-cache` | Origem dos planos de consulta (inativo — contador fica zerado) |

### Estrutura do backend

```
backend/
├── main.py                       # Entry point — cria app, CORS, registra routers, logging, constraints no startup
├── run_etl.py                    # ETL completo fora do Docker
├── entrypoint.sh                 # Container: aguarda Neo4j → ETL → uvicorn
├── conftest.py                   # sys.path para o pytest
├── api/                          # Camada de API
│   ├── routes.py                 # Endpoints REST
│   ├── websocket.py              # WebSocket de resultados (eventos → métricas → RAGAS → relatório)
│   ├── chat_websocket.py         # WebSocket do chat (turno de intenção)
│   ├── chat_runner.py            # Disparo de análise a partir do chat
│   ├── dispatch.py               # dispatch_analysis(): persiste Analise + threads star/hierarchical
│   ├── runners.py                # Thread runners (star, hierarchical)
│   └── state.py                  # Estado compartilhado em memória
├── agents/                       # Sistema multiagente CoALA
│   ├── base.py                   # AgenteCoALA (classe base) + ActionFailure
│   ├── data_crossing.py          # Cruzamento despesa × indicador + detecção de lacunas
│   ├── intent/                   # AgenteInterpretacaoIntencao (guardrail em 3 vias + extração, sem regex)
│   ├── domain/                   # AgenteSINAN, SIM, SIH, SIPNI, SINASC, SIA, CNES, COVID,
│   │                             # AgenteOrcamentoSubfuncao, query_planning (deliberação de dimensão)
│   ├── analytical/               # AgenteAnalitico, AgentePriorizacaoAnalitica, TextSynthesizer
│   ├── context/                  # AgenteContextoOrcamentario
│   ├── star/                     # OrquestradorEstrela
│   └── hierarchical/             # CoordenadorGeral + SupervisorOrcamento/Saude/Contexto/Analitico
├── core/                         # Utilitários
│   ├── llm_client.py             # Cliente LLM (DeepSeek/OpenAI via LLM_PROVIDER, retry, TokenBucket, logs)
│   ├── metrics.py                # MetricsCollector (tempo + CPU via psutil)
│   ├── quality_metrics.py        # Métricas determinísticas + relatório comparativo
│   ├── ragas_metrics.py          # Avaliação RAGAS (fidelidade, relevância, relevância de contexto)
│   ├── claim_verifier.py         # Verificação pós-síntese (Chain-of-Verification, opcional)
│   ├── guardrail_stats.py        # Contador do guardrail de escopo
│   └── streaming_adapter.py      # StreamingAdapter (chunking para ws_queue)
├── db/
│   ├── neo4j_client.py           # Driver Neo4j + consultas
│   └── query_builder.py          # Cypher parametrizado com allowlist de dimensões
├── etl/                          # Pipeline ETL
│   ├── orcamento_loader.py       # Dados/orcamento/ → Empenho, DespesaAnual, VARIACAO_ANUAL, ...
│   └── saude_indicadores_loader.py # Dados/Sorocaba_DATASUS_2015-2025/ → IndicadorSaude + dimensões
├── scripts/
│   ├── check_embeddings.py       # Diagnóstico de acesso aos embeddings do RAGAS
│   ├── batch_eval.py             # Bateria de cenários: fluxo do chat + métricas + RAGAS + veredito, fora da UI
│   ├── batch_report.py           # XLSX com tabelas e gráficos a partir do .jsonl do batch_eval
│   ├── analise_rodadas.py        # Consolida N rodadas: estatísticas, tabelas e figuras do capítulo de resultados
│   └── conferencia_etl.py        # Confere o ETL contra as planilhas de origem, com leitor independente
├── data/                         # Montado como volume no container (sem arquivos versionados)
└── tests/                        # Testes pytest (unitários + integração com mocks)
```

---

## Frontend

**React 18 + TypeScript + Vite** — chat em linguagem natural com streaming em tempo real e aba técnica com todas as rodadas.

### Execução local

```bash
# vite.config.ts lê o .env da raiz (envDir: '..') — VITE_API_URL / VITE_WS_URL
cd frontend
npm ci
npm run dev                   # http://localhost:5173
```

### Componentes principais

| Componente | Arquivo | Responsabilidade |
|------------|---------|------------------|
| `App` | `src/App.tsx` | Estado de rodadas de chat, abas, integração com o WS de resultados |
| `ChatInterface` | `src/components/ChatInterface.tsx` | Chat de texto livre (aba Saúde) — dispara análises e mostra a resposta em streaming |
| `RoundOverview` / `RoundCard` | `src/components/RoundOverview.tsx`, `RoundCard.tsx` | Panorama de todas as rodadas na aba Agentes |
| `ArchitecturePanel` | `src/components/ArchitecturePanel.tsx` | Texto, benchmarks e dados por agente de cada arquitetura |
| `AgentDataCard` | `src/components/AgentDataCard.tsx` | Consulta Cypher, gráfico e linhas retornadas por agente |
| `useWebSocket` | `src/hooks/useWebSocket.ts` | Hook WS de resultados, com reconexão automática |
| `useChatWebSocket` | `src/hooks/useChatWebSocket.ts` | Hook WS do turno de intenção do chat |

Ver [docs/05-FRONTEND.md](docs/05-FRONTEND.md) para a lista completa de componentes.

---

## Testes

```bash
# Backend
cd backend && pytest
cd backend && pytest -v       # verbose

# Frontend
cd frontend && npm test
```

---

## Docker

```bash
docker compose up --build     # subir tudo (ETL roda no entrypoint do backend)
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
| [02-AGENTES.md](docs/02-AGENTES.md) | Modelo CoALA, agentes, deliberação, topologias, regras de negócio |
| [03-DADOS-ETL.md](docs/03-DADOS-ETL.md) | Fontes (LAI, DATASUS/TabNet), ETL, modelo Neo4j, limitações |
| [04-BACKEND-API.md](docs/04-BACKEND-API.md) | API REST, WebSockets, chat, LLM, métricas, relatório, erros |
| [05-FRONTEND.md](docs/05-FRONTEND.md) | Componentes, chat, rodadas, hooks WS, tipos, acessibilidade |

Aprofundamento em [`docs/arquitetura/`](docs/arquitetura/):

| Arquivo | Conteúdo |
|---------|----------|
| [ciclo-completo.md](docs/arquitetura/ciclo-completo.md) | Da mensagem do usuário ao relatório comparativo |
| [arquitetura-estrela.md](docs/arquitetura/arquitetura-estrela.md) | Pipeline do OrquestradorEstrela |
| [arquitetura-arvore.md](docs/arquitetura/arquitetura-arvore.md) | Pipeline do CoordenadorGeral e dos supervisores (topologia em árvore) |
| [agente-coala-e-llm.md](docs/arquitetura/agente-coala-e-llm.md) | Classe base CoALA e como um agente chama o LLM |
| [metricas-de-avaliacao.md](docs/arquitetura/metricas-de-avaliacao.md) | Métricas, RAGAS e justificativa acadêmica de cada uma |

Em [`docs/historico/`](docs/historico/) ficam os planos e changelogs das sessões de desenvolvimento (plano de refatoração CoALA/LLM, plano do novo modelo de dados etc.) — registram o raciocínio de decisões ainda vigentes, mas **não** descrevem o estado atual do sistema.

---

## Licença

Projeto acadêmico — TCC FACENS.
