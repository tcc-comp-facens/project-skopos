# Contexto do Projeto — Skopos (TCC)

> Arquivo gerado por exploração automatizada do repositório em 2026-08-02. Serve como documento de referência rápida sobre objetivo, arquitetura e estado do projeto.

## 1. O que é

**Projeto Skopos** — TCC de Engenharia de Computação (FACENS). Compara empiricamente **duas topologias de sistemas multiagentes BDI (Star vs. Hierárquica)** aplicadas à análise da eficiência dos gastos públicos em saúde no município de **Sorocaba-SP**, correlacionando dados orçamentários com indicadores epidemiológicos.

**Problema que motiva o TCC:**
- Não existe ferramenta que correlacione automaticamente gasto municipal em saúde com indicadores epidemiológicos.
- Avaliação de eficiência hoje é manual.
- Não há comparação empírica de topologias multiagentes para esse domínio.
- Dados de saúde estão espalhados em sistemas desconexos (SIOPS/FNS, SINAN, SIM, SIH, SI-PNI).

As duas arquiteturas rodam em paralelo sobre os mesmos dados e devem produzir **resultados numéricos idênticos** (isso é validado pela métrica Q1) — a única diferença testada é orquestração/comunicação, overhead e resiliência a falhas, não a correção analítica.

## 2. Arquitetura de agentes (modelo BDI)

Classe raiz `backend/agents/base.py`: `AgenteBDI` implementa o ciclo clássico **Belief-Desire-Intention** (`perceive → update_beliefs → deliberate → plan → execute`), com `IntentionFailure` e `_recover_intention()` por agente (degradação graciosa).

**8 agentes BDI + 1 serviço não-BDI, em 4 categorias:**

- **Domínio** (`agents/domain/`) — coletores de dados via Neo4j, um por subfunção de saúde:
  - `AgenteVigilanciaEpidemiologica` (subfunção 305 → dengue, covid)
  - `AgenteSaudeHospitalar` (302 → internações)
  - `AgenteAtencaoPrimaria` (301 → vacinação)
  - `AgenteMortalidade` (transversal, todas as subfunções → mortalidade)
- **Analíticos** (`agents/analytical/`):
  - `AgenteCorrelacao` — correlação de Spearman por par subfunção×indicador
  - `AgenteAnomalias` — detecção de anomalias por mediana (`alto_gasto_baixo_resultado` / `baixo_gasto_alto_resultado`), consciente da polaridade do indicador (negativos: dengue/covid/internações/mortalidade; positivo: vacinação)
  - `TextSynthesizer` (`sintetizador.py`) — **não é agente BDI**, é serviço simples; gera narrativa em português via LLM com streaming, com fallback determinístico
- **Contexto** (`agents/context/contexto_orcamentario.py`) — `AgenteContextoOrcamentario`, calcula tendências ano-a-ano (crescimento/corte/estagnação/insuficiente)
- **Coordenadores de topologia**:
  - **Star** (`agents/star/orchestrator.py`) — `OrquestradorEstrela`, hub único, pipeline linear determinístico (`run()`), ativação condicional de agentes de domínio via `INDICADOR_TO_AGENT`
  - **Hierárquica** (`agents/hierarchical/coordinator.py`, `supervisors.py`) — `CoordenadorGeral` (nível 0) → 3 supervisores (`SupervisorDominio`, `SupervisorContexto`, `SupervisorAnalitico`, nível 1) → agentes-folha (nível 2); supervisores se comunicam **lateralmente** (peer-to-peer via `receive_from_peer()`) sem passar pelo coordenador

Ambos os orquestradores herdam `AgenteBDI` por uniformidade de interface, mas executam pipelines determinísticos — a deliberação BDI de fato ocorre nos agentes-folha.

Helper compartilhado `agents/data_crossing.py`: `cross_domain_data()`, `deduplicate_despesas()`, `detect_data_gaps()`.

## 3. Camada de API (`backend/api/`)

- REST (`routes.py`): `POST /api/analysis`, `GET /api/data-range`, `GET /api/analysis/{id}`, `/quality`, `/report`, `GET /api/benchmarks`
- WebSocket (`websocket.py`): `/ws/{analysisId}` streaming (chunk/done/error/metric/quality_metrics/llm_judge)
- **Chat** (`chat_runner.py`, `chat_websocket.py`, rota tipo `/ws/chat/{session_id}`): fluxo conversacional — usuário digita em linguagem natural ("compare dengue e vacinação de 2019 a 2022") em vez de preencher formulário. É a feature mais recente (branch atual: `feature/chat-analise-saude`).
- `dispatch.py`: lógica compartilhada entre fluxo form-based e chat-based, dispara as duas threads (star + hierárquica)
- `state.py`: estado em memória (`active_queues`/`active_threads`/`active_results`)

**`core/intent_interpreter.py`** — `IntentInterpreter.parse()`: extrai `AnalysisParams` (período + parâmetros de saúde) de texto livre em português. Estratégia: **regex primeiro, LLM como fallback** (para não competir pelo rate-limit da Groq). Segurança: saída do LLM tratada como não confiável, parse via whitelist estrita de chaves JSON; texto do usuário tratado sempre como dado, nunca como instrução (mitigação de prompt injection).

## 4. Integração LLM (`backend/core/llm_client.py`)

Provider: **Groq**, cadeia de fallback de 3 modelos: `llama-3.3-70b-versatile` → `qwen/qwen3-32b` → `llama-4-scout-17b-16e`. Lock global + intervalo mínimo de 2s (respeita free tier: 30 RPM, 1000 RPD, 100K TPD). Retry: 2 tentativas/modelo, backoff linear de 10s em 429. Remove tags `<think>` de modelos de raciocínio. Suporta batch (`generate`) e streaming (`generate_stream`).

## 5. Fontes de dados

Duas pipelines paralelas existem no repo:

**A) Pipeline ETL documentada/legada** (`backend/etl/`, alimenta **Neo4j**):
- FNS (Fundo Nacional de Saúde) — repasses fundo a fundo para Sorocaba (IBGE 355220), carregado via `siops_loader.py` (nome é enganoso — dado é FNS, não SIOPS real) em nós `DespesaSIOPS`, mapeado para subfunções orçamentárias (301 Atenção Básica, 302 Assistência Hospitalar, 303 Suporte Profilático, 305 Vigilância Epidemiológica)
- DataSUS via PySUS (`datasus_loader.py`): SINAN (dengue/covid), SIM (mortalidade), SIH (internações), SI-PNI (vacinação) → nós `IndicadorDataSUS`
- **Limitação documentada e importante**: dados FNS = repasses federais recebidos, **não** a despesa total executada pelo município (recursos próprios municipais, ~50-70% do gasto em saúde, ficam de fora). Isso está sinalizado como limitação conhecida em `docs/03-DADOS-ETL.md` — relevante para qualquer discussão sobre validade dos resultados.

**B) Pastas de dados brutos na raiz do projeto** (ainda não integradas à documentação formal, provavelmente para expansão/atualização):
- `2015-2019/`, `2020-2022/`, `2023-2025/` (na raiz, fora de `Dados/`) — CSVs "empenhado" (linhas de empenho orçamentário) e "previsto e empenhado por programa e secretaria" — exports do portal de transparência/LAI de Sorocaba
- `Dados/orcamento/{períodos}/` — equivalente organizado dentro de `Dados/`
- `Dados/Sorocaba_DATASUS_2015-2025/` — conjunto DataSUS mais amplo que os 4 agentes de domínio atuais usam: COVID, Internações (SIH), Mortalidade (SIM), Nascidos Vivos (SINASC), SIA (ambulatorial), SINAN, SI-PNI, CNES — sugere expansão planejada
- `dados_raw/` — cache parquet por sistema/ano (sih, sim, sinan, si_pni) + `download_datasus_raw.py`

Isso sugere transição em andamento para dados mais recentes/ricos (2015-2025) além da pipeline original.

**Nota:** os arquivos CSV de orçamento na raiz do projeto (`2015-2019/`, `2020-2022/`, `2023-2025/`) aparecem no git status como **A + D simultaneamente** (added then deleted) — parecem ter sido movidos/reorganizados para dentro de `Dados/orcamento/`. Vale confirmar a intenção antes de commitar.

## 6. Stack técnica

- **Backend:** Python 3.11 + FastAPI, driver Neo4j 5.x, SciPy (Spearman), pandas, psutil (métricas), PySUS (ETL DataSUS), openpyxl/xlrd, Groq SDK, pytest + hypothesis
- **Frontend:** React 18.3.1 + TypeScript 5.5.3 + Vite 5.3.4, Vitest 2.0.4 + @testing-library/react — componentes: `App`, `AnalysisControls`, `ArchitecturePanel`, `Header`, `LlmControls`, `TabNav`, `WinnerPanel`; hook `useWebSocket` com auto-reconnect
- **DB:** Neo4j (nós `Analise`, `DespesaSIOPS`, `IndicadorDataSUS`, `MetricaExecucao`)
- **Deploy:** Docker Compose (neo4j + backend + frontend); Dockerfile backend = `python:3.11-slim`, `entrypoint.sh` espera Neo4j, roda ETL, inicia uvicorn; env vars em `backend/.env` (`NEO4J_URI/USER/PASSWORD`, `GROQ_API_KEY`, `CORS_ORIGINS`)

## 7. Métricas de qualidade/benchmark (núcleo empírico do TCC)

`backend/core/quality_metrics.py` — 3 eixos, 9 métricas:
- **E**ficiência: E1 overhead de coordenação, E2 latência por fase
- **Q**ualidade: Q1 consistência determinística (valida que as 2 topologias dão o mesmo resultado numérico), Q2 checklist de fidelidade, Q2+ LLM-as-judge, Q3 completude
- **R**esiliência: R1 cobertura de resultado parcial

`generate_comparative_report()` declara vencedor por eixo e geral (prioridade: qualidade > eficiência). Essa é a base empírica que sustenta a argumentação da tese a favor de uma topologia.

## 8. Testes

61 testes backend (pytest) em 8 arquivos (correlação, anomalias, contexto orçamentário, cruzamento de dados, sintetizador, streaming adapter, orquestrador star, agentes de domínio) + 35 testes frontend (Vitest).

## 9. Notas adicionais

- `showcase.zip`/`showcase/` na raiz — provável asset de demo/gravação (~56MB)
- `.kiro/` presente — indica workflow de desenvolvimento orientado a spec (docstrings referenciam "Requisitos: 3.1, 3.2...")
- Honestidade acadêmica explícita nos docs sobre limitação dos dados (FNS ≠ despesa municipal total real) — importante preservar essa ressalva em qualquer texto do TCC que discuta os resultados
- Branch atual de trabalho: `feature/chat-analise-saude` — desenvolvimento da interface de chat conversacional (mais recente que a documentação formal em `docs/`)
