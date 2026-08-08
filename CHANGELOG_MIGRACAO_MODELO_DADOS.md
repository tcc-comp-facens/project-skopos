# Changelog — Migração para o novo modelo de dados + reparticionamento de agentes CoALA

> Documenta o diff atualmente em stage/working tree (não commitado ainda), gerado a partir de
> `git diff` + arquivos novos. Contexto e racional completos em
> [PLANO_NOVO_MODELO_DADOS.md](PLANO_NOVO_MODELO_DADOS.md) e
> [DOCUMENTACAO_ETL_MODELO_DADOS.md](DOCUMENTACAO_ETL_MODELO_DADOS.md).

## Resumo

O grafo Neo4j deixou de ser alimentado por PySUS/FTP DataSUS + planilhas FNS e passou a ser
carregado a partir de extrações locais reais (execução orçamentária LAI + TabNet filtrado para
Sorocaba), com granularidade dimensional muito maior (faixa etária, capítulo CID-10, tipo de
estabelecimento, vacina, etc.). Esse novo modelo tornou o particionamento antigo de agentes
(4 agentes de domínio genéricos + 2 agentes analíticos) obsoleto: ele não conseguia expressar as
novas dimensões nem a granularidade por sistema DATASUS. Este diff reparticiona os agentes para
espelhar o novo modelo 1:1 e conclui itens que a documentação do ETL ainda listava como
"trabalho futuro" (`VARIACAO_ANUAL`).

Suíte completa: **395 testes passando**.

## 1. Reparticionamento dos agentes de domínio

**Removidos** (agentes genéricos por eixo temático, schema antigo):
- `backend/agents/domain/atencao_primaria.py`, `mortalidade.py`, `saude_hospitalar.py`,
  `vigilancia_epidemiologica.py`
- `backend/agents/analytical/anomalias.py`, `correlacao.py`

**Adicionados** — 1 agente por sistema de informação DATASUS + 1 por subfunção orçamentária:

| Agente | Sistema/escopo | Observação |
|---|---|---|
| `agente_sim.py` | SIM (mortalidade) | delibera dimensão faixa etária / capítulo CID |
| `agente_sih.py` | SIH (internações) | delibera capítulo CID / sem quebra |
| `agente_sinan.py` | SINAN (9 agravos: dengue, chikungunya, sífilis ×3, coqueluche, hepatites, tuberculose, hanseníase) | implementação de referência do padrão de deliberação de dimensão |
| `agente_sinasc.py` | SINASC (nascidos vivos) | delibera faixa etária / faixa de peso |
| `agente_sipni.py` | SI-PNI (cobertura vacinal + doses aplicadas) | delibera entre as duas métricas |
| `agente_sia.py` | SIA (produção ambulatorial) | sem dimensão |
| `agente_cnes.py` | CNES (12 subtipos: leitos, profissionais, estabelecimentos, equipes, equipamentos...) | o mais heterogêneo — delibera entre 6 dimensões válidas |
| `agente_covid.py` | COVID-19 (casos/óbitos) | sem dimensão |
| `agente_orcamento.py` (`AgenteOrcamentoSubfuncao`) | 1 instância por subfunção (122, 301–306) | absorve o papel de classificação de tendência antes exclusivo do `AgenteContextoOrcamentario`; lê `VARIACAO_ANUAL` pré-computada |

**Consolidado:**
- `backend/agents/analytical/analitico.py` (`AgenteAnalitico`) substitui `AgenteCorrelacao` +
  `AgenteAnomalias` por um único agente CoALA com duas ações de grounding
  (`calcular_correlacao` via `scipy.stats.spearmanr`, `detectar_anomalia` via mediana com
  polaridade), cálculos migrados 1:1 e mantidos determinísticos (nunca via LLM).

## 2. Novo módulo `backend/db/query_builder.py`

Construção segura de Cypher dinâmico: o LLM decide *qual* dimensão filtrar (via
`agents/domain/query_planning.py`), nunca escreve Cypher. `dimensao` é validada contra uma
allowlist (`SISTEMA_DIMENSOES` / `DESPESA_DIMENSOES`) antes de ser interpolada — única forma de
parametrizar nome de relacionamento no Neo4j — e qualquer valor inválido levanta
`DimensaoInvalida`, capturada pelo `Neo4jClient` para degradar graciosamente
(`dimensao=None`) em vez de propagar como erro fatal. Expõe `build_indicador_cypher()`,
`build_despesa_cypher()`, `dimensoes_validas()` e `dimensoes_validas_despesa()`.

## 3. Deliberação real de dimensão (CoALA `propose_actions`/`evaluate_and_select`)

`backend/agents/domain/query_planning.py` ganha `propose_dimensao_candidatos()`,
`score_dimensao_candidato()` (match determinístico por substring contra a intenção do usuário) e
`arbitrar_dimensao()` (score determinístico + arbitragem opcional via LLM, com fallback para o
maior score em qualquer falha). É o mecanismo usado pelos 6 agentes que deliberam dimensão
(SINAN, SIH, SIM, SI-PNI, SINASC, CNES, Orçamento).

## 4. Coordenador e supervisores (topologia hierárquica)

- `SupervisorDominio` foi dividido em `SupervisorOrcamento` (coordena as 7 instâncias de
  `AgenteOrcamentoSubfuncao`) e `SupervisorSaude` (coordena os 8 agentes de saúde).
- `CoordenadorGeral` passa de 3 para 4 supervisores; nova ação `_act_combinar_despesas`
  mescla/deduplica despesas vindas dos dois novos supervisores.
- Ações de comunicação lateral renomeadas (`comunicar_dominio_analitico` →
  `comunicar_saude_analitico`, etc.); métricas persistidas agora iteram sobre 4 supervisores.
- `SupervisorAnalitico` colapsa `calcular_correlacoes`/`detectar_anomalias` numa única ação
  `analisar`, delegando ao `AgenteAnalitico`.
- `_SUBFUNCAO_TOKENS`/`_subfuncao_ativa()` decide quais das 7 subfunções orçamentárias ativar
  conforme os `health_params` selecionados (a subfunção 304 só ativa via o subtipo CNES de
  vigilância epidemiológica; 122/306 ativam com qualquer subtipo CNES).

## 5. Orquestrador estrela (topologia star)

`backend/agents/star/orchestrator.py` espelha as mudanças acima: nova "Fase de Orçamento" (até 7
consultas a `AgenteOrcamentoSubfuncao`, ativação condicional) entre a fase de agentes de saúde e
o cruzamento de dados; ação `_act_analisar` única para correlação+anomalia; reutiliza a mesma
lógica de gating `_SUBFUNCAO_TOKENS`/`_subfuncao_ativa` (duplicação intencional com
`supervisors.py`, documentada em código).

## 6. Cruzamento de dados (`data_crossing.py`)

- `SUBFUNCAO_INDICADOR_MAP` expandido de 3 para as 7 subfunções, cada uma mapeada para os novos
  subtipos nativos por SI.
- Corrige bug de cruzamento por ano: antes, a chave era só `ano`, sobrescrevendo silenciosamente
  todas as fatias dimensionais exceto a última quando havia dimensão ativa; agora a chave é
  `(ano, dimensao_valor)` via novo helper `_by_year_and_dimensao()`.

## 7. Memória episódica real na interpretação de intenção

`backend/agents/intent/agente_interpretacao_intencao.py` ganha parâmetro opcional
`neo4j_client`: nova ação `_act_buscar_memoria_episodica` (proposta primeiro, se houver client)
busca até 3 análises passadas via `neo4j_client.get_past_analises()`, registra um episódio real
em `episodic_memory` (não apenas working memory) e alimenta o prompt do LLM com perguntas
anteriores como contexto de desambiguação — com fallback silencioso se o client estiver ausente
ou a busca falhar. `backend/api/chat_websocket.py` passa a instanciar um `Neo4jClient` por sessão
de chat para alimentar essa memória, fechando-o no `finally` da desconexão.

## 8. ETL (`backend/etl/orcamento_loader.py`)

- Implementa `VARIACAO_ANUAL` — item que `DOCUMENTACAO_ETL_MODELO_DADOS.md` ainda lista como
  "não implementado / trabalho futuro". Calculado em Python reaproveitando
  `compute_yoy_variation`/`classify_trend` (agora públicas em `contexto_orcamentario.py`, eram
  privadas) para não haver duas implementações divergentes do mesmo cálculo de tendência; persiste
  como `(atual:DespesaAnual)-[:VARIACAO_ANUAL {percentual, classificacao}]->(anterior:DespesaAnual)`.
- Implementa as quebras dimensionais `POR_APLICACAO`/`POR_NATUREZA` em `DespesaAnual`, e a
  persistência dos nós `Aplicacao` (`APLICADO_EM`) e `Natureza`.

## 9. `Neo4jClient` (`backend/db/neo4j_client.py`)

- Substitui `get_despesas()`/`get_indicadores()` (escopados a `Analise`, schema antigo) por
  `get_despesas_por_subfuncao()`/`get_indicadores_por_sistema()` (globais, novo schema), ambos
  delegando construção de query ao `query_builder` e absorvendo `DimensaoInvalida`.
- Adiciona `get_variacao_anual()` (lê a relação nova do ETL) e `get_past_analises()` (memória
  episódica, item 7).
- Remove helpers de escrita obsoletos `save_despesa()`/`save_indicador()` (schema antigo) e
  `get_correlacoes()`.
- `get_year_range()` passa a consultar os labels `Empenho`/`IndicadorSaude` em vez de
  `DespesaSIOPS`/`IndicadorDataSUS`.

## 10. API — remoção do endpoint REST de formulário

- `backend/api/routes.py`: remove `POST /api/analysis` (entrada "um botão por categoria de
  saúde") — o chat passa a ser o único ponto de entrada.
- `backend/api/dispatch.py`: remove as escritas Cypher que ligavam `Analise` a
  `DespesaSIOPS`/`IndicadorDataSUS` (labels que não existem mais no novo schema); docstrings
  atualizadas para refletir uso exclusivo pelo WebSocket de chat.
- `backend/api/models.py` removido por completo (modelos Pydantic do endpoint removido).
- `backend/main.py`: remove o shim de re-export de `api.models` mantido para compat de testes
  antigos.

## 11. Métricas de qualidade (`backend/core/quality_metrics.py`)

`SUBFUNCAO_NOMES` atualizado para as 7 subfunções; `FASE_DOMINIO` troca os 4 tipos de agente
legados pelos 8 novos tipos de SI de saúde + `orcamento_subfuncao`; `FASE_ANALITICO` troca
`correlacao`/`anomalias` por `analitico`; `FASE_SUPERVISORES` troca `supervisor_dominio` por
`supervisor_orcamento` + `supervisor_saude`.

## 12. Configuração — `.env` único na raiz

- `backend/.env.example` e `frontend/.env.example` removidos; substituídos por um único
  `.env.example` na raiz (superset direto dos dois, mesmas chaves consolidadas).
- `docker-compose.yml`: serviço `backend` passa a usar `env_file: .env` (raiz); serviço
  `frontend` permite override de `VITE_API_URL`/`VITE_WS_URL` via `${VAR:-default}`.
- `frontend/vite.config.ts`: adiciona `envDir: '..'` para ler o `.env` da raiz.
- `README.md`: quick-start atualizado para `cp .env.example .env` na raiz; remove o passo obsoleto
  `python -m etl.seed_data`; documenta a tabela de variáveis do `.env` único.

## 13. Testes

**Novos:** `test_agente_cnes.py`, `test_agente_covid.py`, `test_agente_orcamento.py`,
`test_agente_sia.py`, `test_agente_sih.py`, `test_agente_sim.py`, `test_agente_sinan.py`,
`test_agente_sinasc.py`, `test_agente_sipni.py`, `test_analitico.py`, `test_query_builder.py`.

**Removidos** (agente correspondente foi removido): `test_anomalias.py`, `test_correlacao.py`,
`test_domain_agents.py`.

**Modificados:**
- `test_agente_interpretacao_intencao.py` — nova classe `TestMemoriaEpisodicaReal` (4 testes).
- `test_data_crossing.py` — mapeamentos atualizados para os novos subtipos nativos; nova classe
  `TestCrossDomainDataDimensaoSlices` cobrindo a chave composta `(ano, dimensao_valor)`.
- `test_dispatch_analysis.py` — remove `TestCreateAnalysisEndpointBackwardCompat` (endpoint
  removido).
- `test_lateral_summaries.py` — `TestSupervisorDominioResumo` dividido em
  `TestSupervisorSaudeResumo` + `TestSupervisorOrcamentoResumo`.
- `test_orchestrator_star.py` — novas classes `TestSubfuncao304GatingRefinado` e
  `TestOrcamentoIntentSummaryWiring`.
- `test_scalability_benchmark.py` — mock atualizado para `get_despesas_por_subfuncao`/
  `get_indicadores_por_sistema`.

## Observação — documentação já um passo atrás do código

`DOCUMENTACAO_ETL_MODELO_DADOS.md` (gerado antes deste diff) ainda lista `VARIACAO_ANUAL` como
"desenhado mas não implementado". Este diff implementa. Vale atualizar aquele documento numa
próxima passada para refletir o estado atual.

## Fora do escopo deste diff (não staged)

Os arquivos `.claude/scheduled_tasks.lock` e `.claude/settings.local.json` estão como untracked
no working tree, mas são artefatos locais de tooling (Claude Code), não parte da funcionalidade —
não foram incluídos no stage.
