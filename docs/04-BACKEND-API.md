# Backend, API e Integração LLM

## Sumário

1. [Visão Geral do Backend](#visão-geral-do-backend)
2. [API REST](#api-rest)
3. [WebSocket de Chat](#websocket-de-chat)
4. [WebSocket de Resultados](#websocket-de-resultados)
5. [Integração com LLM](#integração-com-llm)
6. [Métricas de Execução](#métricas-de-execução)
7. [Métricas de Qualidade — resumo](#métricas-de-qualidade--resumo)
8. [Relatório Comparativo](#relatório-comparativo)
9. [Tratamento de Erros](#tratamento-de-erros)

---

## Visão Geral do Backend

`backend/main.py` cria o app FastAPI, configura CORS e logging (nível via `LOG_LEVEL`) e registra três routers:

| Router | Arquivo | Papel |
|--------|---------|-------|
| REST | `api/routes.py` | Consulta de resultados, métricas e intervalo de dados |
| WS de chat | `api/chat_websocket.py` | Turno de conversa: interpreta a mensagem e dispara a análise |
| WS de resultados | `api/websocket.py` | Streaming dos eventos das duas topologias, métricas, RAGAS e relatório |

Demais módulos de `api/`:

| Arquivo | Papel |
|---------|-------|
| `chat_runner.py` | `run_chat_analysis()`: ponte entre o chat e `dispatch_analysis()` |
| `dispatch.py` | `dispatch_analysis()` persiste o nó `Analise` e dispara as threads estrela e árvore. `get_available_year_range()` |
| `runners.py` | `run_star()` / `run_hierarchical()`: uma thread por topologia, com `TokenBucket` próprio. `_persist_topology_result()` |
| `state.py` | Estado em memória compartilhado (ver abaixo) |

**O chat é o único caminho de entrada.** O endpoint REST de formulário (`POST /api/analysis`) foi removido em `f4838d2`. A análise é sempre disparada por uma mensagem em `WS /ws/chat/{sessionId}`.

### Estado compartilhado (`backend/api/state.py`)

```python
active_queues: dict[str, Queue]                 # analysisId → fila de eventos (consumidor único)
active_ws_generation: dict[str, int]            # analysisId → geração da conexão WS atual (reconexão assume a fila)
active_threads: dict[str, list[Thread]]         # analysisId → [thread_star, thread_hier]
active_results: dict[str, dict[str, Any]]       # analysisId → resultados, métricas, custo, pergunta original…
active_chat_sessions: dict[str, bool]           # sessionId → rodada de chat em andamento
```

Tudo vive em memória: reiniciar o backend descarta as análises ativas. O nó `Analise` e as `MetricaExecucao` persistem no Neo4j.

### CORS

Configurado via `CORS_ORIGINS` (default `*`), com múltiplas origens separadas por vírgula.

---

## API REST

**Arquivo:** `backend/api/routes.py`. Todos os endpoints são `GET`.

| Rota | Descrição | Retorno |
|------|-----------|---------|
| `/api/data-range` | Intervalo de anos carregados no Neo4j (entre `Empenho` e `IndicadorSaude`) | `{ "minYear": int \| null, "maxYear": int \| null }` |
| `/api/analysis/{id}` | Nó `Analise` persistido | Propriedades do nó (status, textos, timestamps, pergunta original). 404 se não existir |
| `/api/analysis/{id}/quality` | Métricas de qualidade e eficiência da análise | Payload de `compute_all_quality_metrics` (+ `quality.{arch}.ragas` e `cost.ragas`) |
| `/api/analysis/{id}/report` | Relatório comparativo textual | `{ "report": "texto..." }` |
| `/api/benchmarks` | `MetricaExecucao` de todas as análises | Lista `{analysisId, architecture, agentId, executionTimeMs, cpuPercent}` |
| `/api/metrics/guardrail` | Taxa de rejeição do guardrail de escopo (process-wide) | `{ total_messages, rejected, rejection_rate }` |
| `/api/metrics/query-planning-cache` | Origem dos planos de consulta (process-wide) | `{ total, fast_path, cache, llm, llm_failed_fallback, cache_or_fastpath_rate }`. **Inativo**: nenhum agente chama `plan_query` desde `f4838d2`, então `total` fica em 0 |

Swagger em `http://localhost:8000/docs`.

### `GET /api/analysis/{id}/quality`

- Devolve o **cache** em `active_results[id]["quality_metrics"]` quando existe. O WebSocket de resultados o preenche, já com o RAGAS encaixado em `quality.{arch}.ragas`. O endpoint nunca recomputa o RAGAS.
- Sem cache, calcula as métricas determinísticas a partir dos resultados das duas topologias (wall-clock e consumo de tokens de cada uma, mais o custo da interpretação de intenção). Se alguma topologia não terminou, devolve 404.

### `GET /api/analysis/{id}/report`

Devolve o relatório já gerado pelo WebSocket, se existir. Senão, gera-o a partir de `quality_metrics`, que precisa ter sido calculado antes (404 caso contrário).

---

## WebSocket de Chat

**Endpoint:** `WS /ws/chat/{sessionId}`. **Arquivos:** `api/chat_websocket.py`, `api/chat_runner.py`, `agents/intent/agente_interpretacao_intencao.py`

Cuida apenas do turno de conversa: interpreta a mensagem, dispara a análise e confirma em texto. O streaming dos resultados vai por um **segundo** WebSocket (`/ws/{analysisId}`). O chat não lê a `ws_queue` da análise, que tem consumidor único.

### Protocolo

```
cliente → servidor:
  {"type": "user_message", "payload": {"text": str, "useSelfCheck": bool?}}

servidor → cliente:
  {"type": "user_ack", "payload": ""}
  {"type": "system_chunk", "payload": str}        # texto em ~80 chars
  {"type": "system_done", "payload": ""}
  {"type": "analysis_started", "payload": analysisId}
  {"type": "error", "payload": str}
```

### Validações no servidor

- `sessionId` precisa ser um UUID válido. Caso contrário, a conexão é fechada com código 4001.
- Mensagens acima de `MAX_MESSAGE_LENGTH` (1000 caracteres) são rejeitadas com `error`.
- Só uma rodada por vez por sessão (`active_chat_sessions`). O servidor não confia apenas no frontend desabilitar o input.

### Fluxo por conexão e por mensagem

1. Ao conectar, o servidor consulta `get_available_year_range()` e instancia **um** `AgenteInterpretacaoIntencao` para a sessão (`intent-…`), com `min_year`/`max_year` e cliente Neo4j (memória episódica).
2. Para cada `user_message`, responde `user_ack` imediatamente.
3. `parse(texto)` roda o ciclo CoALA do agente: **uma** chamada LLM classifica a mensagem em 3 vias e extrai os parâmetros (ver [02-AGENTES.md](02-AGENTES.md#agente-de-interpretação-de-intenção)). A decisão de escopo é registrada em `core/guardrail_stats.py`.
4. **Fora do escopo, conversa sem análise ou LLM indisponível**: `system_chunk`/`system_done` com a resposta escrita pelo LLM (ou a mensagem de fallback). Nenhuma análise é criada.
5. **Pergunta de dados**: `run_chat_analysis()` chama `dispatch_analysis()` com `use_llm=True` (o toggle saiu do frontend) e `use_self_check` vindo do payload. O servidor envia `analysis_started` com o `analysisId` e um texto de confirmação com o período e os indicadores interpretados.
6. O cliente abre `/ws/{analysisId}` para acompanhar a análise.

---

## WebSocket de Resultados

**Endpoint:** `WS /ws/{analysisId}`. **Arquivo:** `api/websocket.py`

As duas threads (estrela e árvore) escrevem na mesma `ws_queue`. O WebSocket multiplexa os eventos num único stream, marcados por `architecture`.

### Formato de evento

```json
{
  "analysisId": "uuid",
  "architecture": "star" | "hierarchical" | "both",
  "type": "chunk" | "done" | "error" | "metric" | "agent_data" | "quality_metrics" | "ragas" | "ragas_done",
  "payload": "string ou objeto"
}
```

### Tipos de evento

| Tipo | `architecture` | Payload | Origem |
|------|----------------|---------|--------|
| `chunk` | `star` / `hierarchical` | `string` (~80 chars) | Texto da análise em streaming (`StreamingAdapter`) |
| `error` | `star` / `hierarchical` | `string` | Falha de agente ou supervisor (o pipeline continua) |
| `metric` | `star` / `hierarchical` | `{totalExecutionTimeMs, workersTimeMs, overheadTimeMs, agentMetrics[]}` | `persistir_metricas` de cada topologia |
| `agent_data` | `star` / `hierarchical` | `{architecture, agents: [{agentName, agentLabel, queries[]}]}` | Consultas Cypher por agente (aba técnica) |
| `done` | `star` / `hierarchical` | `""` | Topologia terminou (enviado também em caso de exceção) |
| `quality_metrics` | `both` | Payload de `compute_all_quality_metrics` | Métricas determinísticas |
| `ragas` | `both` | `string` (~80 chars) | Resultado formatado da avaliação RAGAS em streaming |
| `ragas_done` | `both` | `""` | Fim do streaming do RAGAS |
| `chunk` | `both` | `string` (~80 chars) | Relatório comparativo em streaming |
| `done` | `both` | `""` | Relatório concluído (fim do ciclo) |

### Ciclo de vida

1. O cliente conecta e o servidor busca a `Queue` ativa. Cada conexão recebe uma "geração" (`active_ws_generation`), e uma reconexão mais nova assume a fila, fazendo a antiga parar de consumir.
2. Loop: consome eventos da fila (timeout de 1 s) e os repassa ao cliente. De cada `metric`, guarda o `agentMetrics` e o `totalExecutionTimeMs` da topologia.
3. Encerra o loop ao receber 2 `done` (um por topologia).
4. **Métricas determinísticas**: `compute_all_quality_metrics(...)` → evento `quality_metrics`. Não gasta LLM.
5. **Avaliação RAGAS** (sempre): `core.ragas_metrics.evaluate_architecture` para as duas topologias, concorrentes, cada uma sob seu `TokenBucket`. O resultado vai para `quality.{arch}.ragas` e o custo para `cost.ragas`. O texto formatado sai em eventos `ragas` + `ragas_done`.
6. **Relatório comparativo**: `generate_comparative_report(...)`, cujo veredito depende do RAGAS, em eventos `chunk` (`both`) + `done` final.
7. Na desconexão, limpa `active_queues`/`active_threads` e mantém `active_results`.

O fluxo de ponta a ponta está detalhado em [arquitetura/ciclo-completo.md](arquitetura/ciclo-completo.md).

---

## Integração com LLM

**Arquivo:** `backend/core/llm_client.py`

### Provedor

| Provedor | Modelo default | Variáveis |
|----------|----------------|-----------|
| **DeepSeek** (API compatível com OpenAI), default do código | `deepseek-v4-flash` | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` |
| **OpenAI** | `gpt-5.6-luna` | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`, `OPENAI_REASONING_EFFORT`, `OPENAI_MAX_COMPLETION_TOKENS`, `OPENAI_STORE_LOGS` |

`LLM_PROVIDER` (`deepseek` | `openai`) escolhe o provedor ativo e é lido a cada chamada. Nenhum call site muda: todos chamam `generate()`/`generate_stream()`. O SDK é o `openai` nos dois casos.

- Um único modelo por provedor, sem cadeia de fallback entre modelos.
- **DeepSeek**: `thinking` desabilitado (`extra_body={"thinking": {"type": "disabled"}}`).
- **OpenAI, modelos de raciocínio** (`gpt-5*`, série `o*`): `max_completion_tokens` (`OPENAI_MAX_COMPLETION_TOKENS`, default 16384) e `reasoning_effort` (`OPENAI_REASONING_EFFORT`, default `low`), sem `temperature`. O teto é compartilhado com os tokens de raciocínio, e com esforço alto um prompt grande volta vazio.
- `OPENAI_STORE_LOGS=true` (opt-in): envia `store=True` e `metadata={"app": "skopos", "caller": ...}`, tornando as chamadas visíveis na aba Logs da OpenAI, filtráveis por agente.

Sem a chave do provedor escolhido, o sistema roda em modo determinístico/fallback: sem síntese via LLM, sem arbitragem de dimensão ou ângulo via LLM e sem interpretação de intenção no chat.

### Concorrência e retry

Não há lock global nem intervalo mínimo entre chamadas (removidos em `4505fd0`, depois de uma chamada ficar 177 s presa atrás do lock de outra topologia). Chamadas de threads diferentes correm em paralelo.

| Parâmetro | Valor |
|-----------|-------|
| Max retries | 2 (no mesmo modelo) |
| Base delay | 10 s |
| Backoff | `delay × (tentativa + 1)` (linear) |
| Erros retryable | 429 / rate limit do provedor |
| Outros erros | `generate` retorna `None` e `generate_stream` encerra, sem exceção |

### Modos de geração

| Modo | Função | Uso |
|------|--------|-----|
| **Batch** | `generate(prompt, model=None, *, caller="desconhecido", provider=None, temperature=None)` | Intenção, arbitragem de dimensão, priorização, verificação, juiz RAGAS (que passa `provider` explícito, independente de `LLM_PROVIDER`) |
| **Streaming** | `generate_stream(prompt, model=None, *, caller="desconhecido")` | `TextSynthesizer`, com tokens incrementais enviados ao WebSocket |

### Observabilidade

`caller` identifica quem disparou a chamada (`agent_id`, às vezes com sufixo de propósito, como `"star-sinan-a1b2c3d4:arbitrar_dimensao"`). Serve só para logs:

- **INFO**: preview de uma linha do prompt (~300 chars), tamanho total, tamanho da resposta e tokens consumidos.
- **DEBUG** (`LOG_LEVEL=DEBUG`): prompt completo.

O texto da resposta não é logado, só o seu tamanho.

### Pós-processamento

Tags `<think>...</think>` são removidas defensivamente: por regex no modo batch e por buffer incremental no streaming.

### Contabilização de tokens — `TokenBucket`

`TokenBucket` (baseado em `contextvars.ContextVar`) isola o consumo de LLM de quem o ativou, mesmo com concorrência real. Cada runner abre um bucket por topologia, o chat abre um para a interpretação de intenção e o RAGAS abre um por arquitetura. O payload final separa `cost.star`, `cost.hierarchical`, `cost.intent_interpretation` e `cost.ragas` (embeddings incluídos, registrados à mão via `record_token_usage`).

### Fallback

- `TextSynthesizer`: se o LLM retorna `None`/vazio, gera texto estruturado determinístico (resumo, cobertura de dados, correlações, anomalias, contexto orçamentário).
- `AgenteInterpretacaoIntencao`: o fallback registrado grava `escopo = "indisponivel"` e pede para tentar de novo. Não há interpretação determinística de linguagem natural.
- Arbitragem de dimensão, priorização: caem no maior score determinístico.
- Verificação pós-síntese: preserva o texto original.

### Consumo por análise (via chat)

| Etapa | Chamadas |
|-------|----------|
| Interpretação de intenção | 1 por mensagem |
| Arbitragem de dimensão | Até 1 por agente de saúde com dimensões + 1 por agente de orçamento ativado, **em cada topologia** |
| Priorização de achados | 1 por topologia |
| Síntese de texto | 1 por topologia (streaming) |
| Verificação pós-síntese | Até 3 por topologia, só com `useSelfCheck` |
| Avaliação RAGAS | Custo fixo por topologia: 2 (faithfulness) + 3 (answer relevancy) + 2 (context relevance), mais embeddings |

---

## Métricas de Execução

### MetricsCollector (`backend/core/metrics.py`)

| Métrica | Fonte | Descrição |
|---------|-------|-----------|
| `executionTimeMs` | `time.time()` | Tempo de execução em milissegundos |
| `cpuPercent` | `psutil.Process.cpu_percent()` | Uso de CPU do processo |

```python
mc = MetricsCollector(agent_id, "analitico")
mc.start()
# ... trabalho do agente ...
mc.stop()
mc.persist(neo4j_client, analysis_id, "star")   # MetricaExecucao + (:Analise)-[:GEROU_METRICA]->
```

Também funciona como context manager (`with MetricsCollector(...) as mc:`).

### Wall-clock: o que fica de fora

Sintetizador, priorização e verificação rodam **depois** de `capturar_wallclock` e envolvem LLM. Por isso ficam fora de `workers_time_ms` e do `totalExecutionTimeMs`: a latência da API não reflete a eficiência da arquitetura.

| Topologia | Estratégia |
|-----------|-----------|
| **Estrela** | `_orch_end` é capturado em `capturar_wallclock`, antes da priorização. O wall-clock mede só o trecho determinístico |
| **Árvore** | O coordenador mede até o fim e **subtrai** os tempos de `sintetizador`, `priorizacao` e `verificacao` dos collectors do `SupervisorAnalitico`. O tempo do supervisor analítico termina em `_coala_leaf_end_time` |

Detalhes em [arquitetura/arquitetura-estrela.md](arquitetura/arquitetura-estrela.md#9-persistir_metricas) e [arquitetura/arquitetura-arvore.md](arquitetura/arquitetura-arvore.md#contabilização-de-métricas-e-overhead).

### StreamingAdapter (`backend/core/streaming_adapter.py`)

Infraestrutura de transporte (não é agente): divide texto em chunks (~80 chars) e os publica na `ws_queue` como eventos `chunk`.

```python
adapter = StreamingAdapter(ws_queue, analysis_id, architecture, chunk_size=80)
adapter.stream_text(text)              # texto pré-gerado (fallback)
adapter.stream_tokens(token_generator) # tokens do LLM; retorna o texto completo acumulado
```

---

## Métricas de Qualidade — resumo

**Arquivos:** `backend/core/quality_metrics.py` (determinísticas), `backend/core/ragas_metrics.py` (RAGAS)

| Eixo (chave no payload) | Métricas | Usa LLM? |
|-------------------------|----------|----------|
| Eficiência (`efficiency`) | E1 overhead de coordenação, E2 breakdown de latência por fase | Não |
| Qualidade (`quality`) | Q1 consistência determinística, Q3 completude, RAGAS (`faithfulness`, `answer_relevancy`, `context_relevance`) | Só o RAGAS (juiz) |
| Resiliência (`resilience`) | R1 cobertura de resultado parcial | Não |
| Custo (`cost`) | Tokens por topologia, intenção e RAGAS | Não (só contabiliza) |
| Comunicação (`communication`) | Invocações de agente, mensagens, registros transportados | Não |
| Outcome (`outcome`) | Sucesso agregado da análise (R1 + self-check + orçamento de tempo) | Não |

Métricas process-wide (fora do payload por análise): taxa de rejeição do guardrail (`core/guardrail_stats.py`) e taxa de acerto do cache de planejamento (inativa).

Fórmulas, código, justificativa acadêmica de cada métrica (D5–D26), configuração do juiz RAGAS (`RAGAS_PROVIDER`, `RAGAS_MODEL`, `RAGAS_EMBEDDING_MODEL`, `RAGAS_EMBEDDING_FALLBACKS`, diagnóstico com `python -m scripts.check_embeddings`) e ressalvas estatísticas: **[arquitetura/metricas-de-avaliacao.md](arquitetura/metricas-de-avaliacao.md)**.

---

## Relatório Comparativo

**Função:** `generate_comparative_report()` (`core/quality_metrics.py`)

Gerado depois das métricas determinísticas e do RAGAS. Consolida eficiência (tempo, overhead, breakdown), qualidade (Q1, Q3, RAGAS), resiliência e custo num texto lado a lado e termina numa **conclusão** com a topologia vencedora.

**Critério do vencedor** (`_decide_winner`), em ordem lexicográfica:
1. **Fidelidade (RAGAS)**: só quando as duas topologias têm score e a diferença é ≥ `FAITHFULNESS_TIE_THRESHOLD` (0,05). Diferenças menores são ruído do juiz.
2. **Completude (Q3)**: o relatório declara quando caiu para este critério (fidelidade não medida ou tecnicamente empatada).
3. **Eficiência (tempo total)**: desempate final.

Transmitido via WebSocket em chunks de 80 chars com `architecture: "both"`, e também acessível em `GET /api/analysis/{id}/report`.

---

## Tratamento de Erros

| Camada | Erro | Estratégia |
|--------|------|-----------|
| Agente de saúde/orçamento | Falha no Neo4j | Fallback registrado em `procedural_memory` grava lista vazia |
| Arbitragem de dimensão / priorização | LLM falhou ou resposta inválida | Maior score determinístico |
| `AgenteInterpretacaoIntencao` | LLM indisponível ou resposta inválida | `escopo = "indisponivel"`. O usuário é convidado a tentar de novo |
| `AgenteInterpretacaoIntencao` | Mensagem fora de escopo | Recusa escrita pelo LLM. Nenhuma topologia é instanciada |
| `TextSynthesizer` | LLM indisponível | Texto estruturado de fallback |
| Verificação pós-síntese | Qualquer falha | Mantém o texto original |
| `OrquestradorEstrela` | Falha de agente | Evento `error`, continua com resultados parciais |
| `CoordenadorGeral` | Falha de supervisor | Evento `error`, segue com retorno vazio daquele supervisor |
| Runner (thread) | Exceção na topologia | Envia `done` mesmo assim, para o WebSocket não esperar indefinidamente |
| RAGAS | Sem chave do juiz ou métrica falhou | `available: false` com motivo, ou erro registrado em `errors[]`. O relatório segue |
| REST | Análise inexistente / topologias não terminaram | HTTP 404 com mensagem |
| WS de resultados | Cliente desconectou | Limpa filas e threads, mantém `active_results` |
| WS de chat | Mensagem longa demais / rodada em andamento / sessão inválida | Evento `error` (ou close 4001) |
| Frontend | WebSocket perdido | Reconexão automática (até 3 tentativas: backoff linear no WS de resultados, exponencial 1s/2s/4s no WS de chat) |

### Persistência do resultado

Ao terminar cada topologia (mesmo com erros parciais), `_persist_topology_result()` atualiza o nó `Analise`:
- `starStatus` / `hierStatus` → `"completed"`
- `starTextAnalysis` / `hierTextAnalysis` → texto gerado
- `starCompletedAt` / `hierCompletedAt` → timestamp ISO 8601 UTC
