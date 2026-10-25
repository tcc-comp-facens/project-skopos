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

Este sistema compara duas arquiteturas multiagente baseadas no framework **CoALA** (*Cognitive Architectures for Language Agents*, Sumers et al., 2023), aplicadas à análise de eficiência dos gastos públicos em saúde do município de Sorocaba-SP. É um Trabalho de Conclusão de Curso (TCC) de Engenharia de Computação da FACENS.

O sistema cruza a **execução orçamentária municipal real** (empenhos por subfunção de saúde, extraídos via Lei de Acesso à Informação) com **indicadores do DATASUS/TabNet** (SINAN, SIM, SIH, SI-PNI, SINASC, SIA, CNES e COVID-19) para avaliar a relação entre gasto e resultado em saúde. A entrada é sempre um **chat em linguagem natural**, interpretado por um agente dedicado, sem regex.

As duas arquiteturas, **Estrela** e **Árvore**, rodam em paralelo sobre os mesmos dados. Isso permite uma comparação objetiva de eficiência, overhead de coordenação, qualidade da resposta e resiliência.

## Problema

- Não existe ferramenta que correlacione automaticamente o gasto municipal em saúde com indicadores epidemiológicos e assistenciais.
- A avaliação de eficiência do gasto público em saúde é feita manualmente.
- Não há comparação empírica entre topologias multiagente para este domínio.
- Os dados de saúde estão dispersos em vários sistemas (SINAN, SIM, SIH, SI-PNI, SINASC, SIA, CNES), sem integração com o orçamento.

## Objetivo

Avaliar comparativamente duas topologias de sistemas multiagente CoALA:

| Topologia | Descrição | Característica principal |
|-----------|-----------|--------------------------|
| **Estrela** | Um orquestrador central instancia e chama todos os agentes (ativação condicional conforme a pergunta) | Ponto único de coordenação, comunicação sempre hub ↔ agente |
| **Árvore** | Árvore estrita em 3 níveis: coordenador, 4 supervisores e agentes-folha | Degradação graciosa por supervisor, overhead de coordenação mensurável |

Critérios de comparação (detalhes em [arquitetura/metricas-de-avaliacao.md](arquitetura/metricas-de-avaliacao.md)):
- **Eficiência**: tempo total e por agente, CPU, overhead de coordenação, breakdown por fase.
- **Qualidade**: consistência determinística entre topologias (Q1), completude do texto (Q3) e fidelidade, relevância e relevância de contexto via biblioteca RAGAS.
- **Resiliência**: cobertura de resultado parcial sob falha.
- **Custo e comunicação**: tokens de LLM por topologia, invocações de agentes e volume transportado.

## Stack Tecnológica

| Componente | Tecnologia | Uso |
|------------|-----------|-----|
| Backend | Python 3.11 + FastAPI | Agentes como classes Python no processo FastAPI. REST + WebSocket |
| Frontend | React 18 + TypeScript + Vite | SPA com dois WebSockets (chat e resultados) |
| Gráficos | Recharts | Dados por agente na aba técnica |
| Banco de dados | Neo4j 5 | Grafo de orçamento × indicadores de saúde + histórico de análises |
| LLM | DeepSeek (`deepseek-v4-flash`, default) ou OpenAI (`gpt-5.6-luna`), via `LLM_PROVIDER` | Interpretação de intenção, escolha de dimensão e ênfase, síntese de texto |
| Avaliação | RAGAS 0.4 (juiz OpenAI, `RAGAS_PROVIDER`) | Fidelidade, relevância da resposta e relevância de contexto |
| Estatística | SciPy | Correlação de Spearman |
| ETL | pandas + openpyxl/xlrd | Leitura dos CSVs da LAI e dos xlsx do TabNet |
| Métricas de execução | psutil | CPU e tempo por agente |
| Containers | Docker + Docker Compose | Neo4j, backend e frontend |
| Testes | pytest; Vitest + Testing Library | Backend; frontend |

**`backend/requirements.txt`:** `fastapi`, `uvicorn[standard]`, `neo4j`, `psutil`, `pytest`, `python-dotenv`, `httpx`, `openai`, `openpyxl`, `xlrd`, `pandas`, `scipy`, `hypothesis`, `ragas>=0.4,<0.5`, `langchain-community<0.4`. O SDK `openai` atende aos dois provedores, porque a API do DeepSeek é compatível e só muda a `base_url`.

**`frontend/package.json`:** `react`, `react-dom`, `recharts`. Desenvolvimento: `typescript`, `vite`, `vitest`, `@testing-library/*`, `jsdom`, `fast-check`.

## Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Frontend (React 18)                              │
│   Aba Saúde: ChatInterface               Aba Agentes: RoundOverview      │
│   (useChatWebSocket)                     (painéis, métricas, RAGAS)      │
│          │                                        ▲                      │
│          │ WS /ws/chat/{sessionId}                │ WS /ws/{analysisId}  │
└──────────┼────────────────────────────────────────┼──────────────────────┘
           ▼                                        │
┌───────────────────────────────────────────────────┼──────────────────────┐
│                  Backend (FastAPI + Python 3.11)  │                      │
│  AgenteInterpretacaoIntencao (guardrail + extração, 1 chamada LLM)       │
│        │                                          │                      │
│        ▼  dispatch_analysis()                     │                      │
│   ┌────────────────────┬────────────────────┐     │                      │
│   ▼ Thread A           ▼ Thread B           │     │                      │
│ OrquestradorEstrela   CoordenadorGeral      │     │                      │
│ (agentes ativados     (4 supervisores →     │     │                      │
│  pelos health_params)  agentes-folha)       │     │                      │
│   └──────────┬─────────┴────────────────────┘     │                      │
│              ▼ ws_queue (compartilhada)           │                      │
│   /ws/{analysisId}: eventos → métricas → RAGAS → relatório comparativo   │
└──────────────┬───────────────────────────────────────────────────────────┘
               │ Cypher (parametrizado)
┌──────────────▼───────────────────────────────────────────────────────────┐
│ Neo4j 5: Empenho, DespesaAnual, IndicadorSaude (+ dimensões),            │
│          Analise, MetricaExecucao                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### Fluxo de uma análise

1. O usuário pergunta no chat. O `AgenteInterpretacaoIntencao` classifica a mensagem em 3 vias (fora do escopo, conversa sem análise, pergunta de dados) e, se for pergunta de dados, extrai período, indicadores e um resumo da intenção na mesma chamada LLM.
2. `dispatch_analysis()` persiste o nó `Analise` e dispara **duas threads**, uma por topologia, com os mesmos parâmetros.
3. Cada topologia roda seu pipeline CoALA:
   - **agentes de saúde** (um por sistema DATASUS) e **agentes de orçamento** (um por subfunção), ativados conforme os indicadores pedidos, cada um escolhendo a dimensão de consulta;
   - cruzamento despesa × indicador e detecção de lacunas;
   - **contexto orçamentário** (tendência/CAGR) e **agente analítico** (Spearman + anomalias por mediana), ambos determinísticos;
   - **priorização** do ângulo de ênfase, **síntese de texto** via LLM em streaming e, opcionalmente, **verificação** pós-síntese.
4. Texto, métricas de execução e consultas por agente são transmitidos pelo `/ws/{analysisId}`.
5. Quando as duas terminam: métricas de qualidade determinísticas, avaliação **RAGAS** e **relatório comparativo** com a topologia vencedora.

Detalhes em [02-AGENTES.md](02-AGENTES.md), [04-BACKEND-API.md](04-BACKEND-API.md) e [arquitetura/ciclo-completo.md](arquitetura/ciclo-completo.md).

---

## Como Executar

### Pré-requisitos

- Docker Desktop instalado e rodando.
- (Opcional, mas necessário para o chat) chave do provedor de LLM (`DEEPSEEK_API_KEY` ou `OPENAI_API_KEY`). Sem ela, o sistema roda em modo determinístico/fallback: o chat não consegue interpretar mensagens e a síntese usa texto estruturado.
- (Opcional) chave OpenAI para o juiz do RAGAS. Sem ela, a avaliação reporta "indisponível".
- (Opcional) Python 3.11+ e Node.js 20+ para desenvolvimento local.

### Variáveis de ambiente

Há **um único `.env` na raiz do repositório**, compartilhado por backend, frontend e Docker Compose:

```bash
cp .env.example .env
```

O `.env.example` é comentado variável por variável. As principais:

| Variável | Descrição |
|----------|-----------|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Conexão com o Neo4j (`bolt://neo4j:7687` no Docker, `bolt://localhost:7687` fora dele) |
| `NEO4J_AUTH` | Usuário/senha com que o serviço `neo4j` do compose inicializa o banco (manter em sincronia) |
| `LLM_PROVIDER` | `deepseek` (default do código) ou `openai` |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` | Chave do provedor ativo |
| `RAGAS_PROVIDER`, `RAGAS_MODEL`, `RAGAS_EMBEDDING_MODEL`, `RAGAS_EMBEDDING_FALLBACKS` | Juiz da avaliação RAGAS, independente de `LLM_PROVIDER` |
| `LOG_LEVEL` | `INFO` (estágios, tempos, preview de prompts) ou `DEBUG` (prompts completos) |
| `VITE_API_URL`, `VITE_WS_URL` | Endereços do backend usados pelo frontend |

Lista completa (incluindo `OPENAI_*` avançadas, `CORS_ORIGINS` e `USE_LLM_QUERY_PLANNING`) no [README](../README.md#variáveis-de-ambiente) e no próprio `.env.example`.

### Execução com Docker (recomendado)

```bash
cp .env.example .env      # e preencha as credenciais
docker compose up --build
# Frontend: http://localhost:5173
```

O `backend/entrypoint.sh` executa automaticamente a cada subida:
1. Aguarda o Neo4j (até 30 tentativas, 2 s entre cada).
2. `python -m etl.orcamento_loader`: carrega `Dados/orcamento/`.
3. `python -m etl.saude_indicadores_loader`: carrega `Dados/Sorocaba_DATASUS_2015-2025/`.
4. Inicia o FastAPI (uvicorn, porta 8000).

O ETL é idempotente (só `MERGE`), então rodá-lo a cada subida não duplica dados. O compose monta `./Dados` em `/Dados` (somente leitura) e define `DADOS_DIR=/Dados`.

### Execução local (desenvolvimento)

```bash
# Terminal 1 — Neo4j
docker compose up neo4j -d

# Terminal 2 — ETL + backend (o .env da raiz é lido; use NEO4J_URI=bolt://localhost:7687)
cd backend
pip install -r requirements.txt
python run_etl.py                     # troca neo4j: por localhost: automaticamente
uvicorn main:app --reload --port 8000

# Terminal 3 — Frontend (vite.config.ts lê o .env da raiz via envDir)
cd frontend
npm ci
npm run dev
```

### Serviços e portas

| Serviço | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474 |
| Neo4j Bolt | bolt://localhost:7687 |

### Comandos úteis

```bash
docker compose logs -f backend                                   # logs do backend
docker compose up -d --build                                     # rebuild
docker compose down                                              # parar
docker compose exec backend python -m etl.orcamento_loader        # recarregar orçamento
docker compose exec backend python -m etl.saude_indicadores_loader
docker compose exec backend python -m scripts.check_embeddings    # diagnóstico de acesso aos embeddings do RAGAS
docker compose exec backend python -m scripts.batch_eval --limit 5   # bateria de avaliação (chamadas LLM reais)
docker compose exec backend python -m scripts.conferencia_etl        # confere o ETL contra as planilhas de origem
```

Os scripts de avaliação (`batch_eval`, `batch_report`, `analise_rodadas`) estão em [arquitetura/metricas-de-avaliacao.md](arquitetura/metricas-de-avaliacao.md#avaliação-em-lote-scripts-de-resultados), e a conferência do ETL em [03-DADOS-ETL.md](03-DADOS-ETL.md#conferência-independente-scriptsconferencia_etlpy).

---

## Testes

```bash
cd backend && pytest            # backend (pytest -v para verbose)
cd frontend && npm test         # frontend (npm run test:watch para modo watch)
```

### Backend (`backend/tests/`)

Unitários e de integração com mocks de Neo4j e LLM, sem dependências externas.

| Área | Arquivos |
|------|----------|
| Classe base CoALA | `test_coala_base.py` |
| Agente de intenção | `test_agente_interpretacao_intencao.py` |
| Agentes de saúde | `test_agente_sinan.py`, `test_agente_sim.py`, `test_agente_sih.py`, `test_agente_sipni.py`, `test_agente_sinasc.py`, `test_agente_sia.py`, `test_agente_cnes.py`, `test_agente_covid.py` |
| Agente de orçamento | `test_agente_orcamento.py` |
| Deliberação de dimensão / consultas | `test_query_planning.py`, `test_query_builder.py` |
| Analíticos e contexto | `test_analitico.py`, `test_priorizacao.py`, `test_sintetizador.py`, `test_contexto_orcamentario.py`, `test_data_crossing.py` |
| Topologias | `test_orchestrator_star.py`, `test_hierarchical_tree.py`, `test_scalability_benchmark.py`, `test_subfuncao_activation.py` |
| Verificação pós-síntese | `test_claim_verifier.py` |
| LLM e métricas | `test_llm_client.py`, `test_quality_metrics_etapa6.py`, `test_overhead_definition.py`, `test_ragas_metrics.py`, `test_guardrail_stats.py` |
| ETL e banco | `test_orcamento_loader_dispatch.py`, `test_neo4j_client_schema.py` |
| API e streaming | `test_dispatch_analysis.py`, `test_chat_websocket.py`, `test_websocket.py`, `test_websocket_ragas.py`, `test_streaming_adapter.py` |

### Frontend

Vitest + Testing Library: componentes (chat, abas, rodadas, dados por agente), o hook de resultados (condição de corrida entre rodadas), utilitários e um teste de integração do `App`. A lista está em [05-FRONTEND.md](05-FRONTEND.md#testes).

---

## Estrutura de Diretórios

```
project-skopos/
├── .env.example                     # Template do .env único (backend + frontend + compose)
├── docker-compose.yml               # Neo4j + backend + frontend
├── README.md
├── Dados/                           # Dados de entrada do ETL (montados em /Dados no container)
│   ├── orcamento/{2015-2019,2020-2022,2023-2025}/   # CSVs da LAI (empenhos; previsto × realizado não é carregado)
│   └── Sorocaba_DATASUS_2015-2025/                  # xlsx do TabNet por sistema
├── docs/
│   ├── 01-VISAO-GERAL.md            # Este arquivo
│   ├── 02-AGENTES.md                # Agentes CoALA e topologias
│   ├── 03-DADOS-ETL.md              # Fontes, ETL e modelo Neo4j
│   ├── 04-BACKEND-API.md            # API, WebSockets, LLM, métricas
│   ├── 05-FRONTEND.md               # Interface React
│   ├── arquitetura/                 # Ciclo completo, estrela, árvore, base CoALA, métricas
│   └── historico/                   # Planos e changelogs de sessões anteriores (não refletem o estado atual)
│
├── backend/
│   ├── Dockerfile                   # python:3.11-slim
│   ├── entrypoint.sh                # Aguarda Neo4j → ETL → uvicorn
│   ├── requirements.txt
│   ├── main.py                      # App FastAPI, CORS, routers, logging, constraints no startup
│   ├── conftest.py                  # sys.path para o pytest
│   ├── run_etl.py                   # ETL completo fora do Docker
│   ├── api/                         # routes, websocket, chat_websocket, chat_runner, dispatch, runners, state
│   ├── agents/
│   │   ├── base.py                  # AgenteCoALA + ActionFailure
│   │   ├── data_crossing.py         # Cruzamento, deduplicação, lacunas
│   │   ├── intent/                  # AgenteInterpretacaoIntencao
│   │   ├── domain/                  # 8 agentes de saúde + AgenteOrcamentoSubfuncao + query_planning
│   │   ├── analytical/              # AgenteAnalitico, AgentePriorizacaoAnalitica, TextSynthesizer
│   │   ├── context/                 # AgenteContextoOrcamentario
│   │   ├── star/                    # OrquestradorEstrela
│   │   └── hierarchical/            # CoordenadorGeral + 4 supervisores
│   ├── core/                        # llm_client, metrics, quality_metrics, ragas_metrics,
│   │                                # claim_verifier, guardrail_stats, streaming_adapter
│   ├── db/                          # neo4j_client, query_builder
│   ├── etl/                         # orcamento_loader, saude_indicadores_loader
│   ├── scripts/                     # check_embeddings, batch_eval, batch_report, analise_rodadas, conferencia_etl
│   ├── data/                        # Montado em /app/data no container (sem arquivos versionados)
│   └── tests/
│
└── frontend/
    ├── Dockerfile                   # node:20-alpine, servidor de desenvolvimento do Vite
    ├── package.json, vite.config.ts, tsconfig.json, index.html
    └── src/                         # App, components, hooks, utils, types — ver 05-FRONTEND.md
```
