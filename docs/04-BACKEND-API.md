# Backend, API e Integração LLM

## Sumário

1. [API REST](#api-rest)
2. [WebSocket](#websocket)
3. [Integração com LLM](#integração-com-llm)
4. [Métricas de Execução](#métricas-de-execução)
5. [Métricas de Qualidade](#métricas-de-qualidade)
6. [Relatório Comparativo](#relatório-comparativo)
7. [Tratamento de Erros](#tratamento-de-erros)

---

## API REST

**Arquivo:** `backend/api/routes.py`, `backend/api/websocket.py`

O `main.py` é o entry point que cria o app FastAPI, configura CORS e registra os routers de `api/routes.py` (REST) e `api/websocket.py` (WebSocket). A lógica de endpoints, modelos, estado compartilhado e thread runners está organizada em `backend/api/`.

### Endpoints

| Método | Rota | Descrição | Retorno |
|--------|------|-----------|---------|
| `POST` | `/api/analysis` | Inicia análise comparativa | `{ "analysisId": "uuid" }` |
| `GET` | `/api/analysis/{id}` | Recupera resultado da análise do Neo4j | Nó Analise completo |
| `GET` | `/api/analysis/{id}/quality` | Métricas de qualidade (3 eixos) | Dict com efficiency, quality, resilience |
| `GET` | `/api/analysis/{id}/report` | Relatório comparativo textual | `{ "report": "texto..." }` |
| `GET` | `/api/benchmarks` | Métricas de todas as análises | Lista de MetricaExecucao |

### POST /api/analysis

**Request body:**
```json
{
  "dateFrom": 2019,
  "dateTo": 2023,
  "healthParams": {
    "dengue": true,
    "covid": true,
    "vaccination": true,
    "internacoes": true,
    "mortalidade": true
  }
}
```

**Validações (retorna 400):**
- `dateFrom` deve ser ≤ `dateTo`
- Pelo menos um `healthParam` deve ser `true`

**Conversão de healthParams:**
- `dengue: true` → `"dengue"`
- `covid: true` → `"covid"`
- `vaccination: true` → `"vacinacao"` (com 'c', sem 'ç')
- `internacoes: true` → `"internacoes"`
- `mortalidade: true` → `"mortalidade"`

**Fluxo interno:**
1. Valida parâmetros
2. Gera UUID para a análise
3. Persiste nó `Analise` no Neo4j (status "pending")
4. Vincula nós `DespesaSIOPS` e `IndicadorDataSUS` existentes à análise via MERGE
5. Cria `Queue` compartilhada para WebSocket
6. Lança duas threads daemon (star + hierarchical)
7. Retorna `analysisId` imediatamente

### GET /api/analysis/{id}/quality

Computa métricas de qualidade em três eixos após ambas as topologias completarem.
- Resultado cacheado em `active_results` para requests subsequentes
- Parâmetro `use_llm_judge` desabilitado por padrão

### GET /api/analysis/{id}/report

Retorna relatório comparativo textual. Requer que `/quality` tenha sido computado primeiro.

### Estado Compartilhado (`backend/api/state.py`)

```python
active_queues: dict[str, Queue]                    # analysisId → fila WS
active_threads: dict[str, list[Thread]]            # analysisId → [thread_star, thread_hier]
active_results: dict[str, dict[str, Any]]          # analysisId → {"star": result, "hierarchical": result, ...}
active_agent_metrics: dict[str, dict[str, list]]   # analysisId → {"star": [...], "hierarchical": [...]}
```

### CORS

Configurado via variável `CORS_ORIGINS` (default `*`), aceita múltiplas origens separadas por vírgula.

---

## WebSocket

### Endpoint: `WS /ws/{analysisId}`

Streaming de eventos em tempo real das duas arquiteturas.

### Formato de evento (WSEvent)

```json
{
  "analysisId": "uuid",
  "architecture": "star" | "hierarchical" | "both",
  "type": "chunk" | "done" | "error" | "metric" | "quality_metrics",
  "payload": "string ou objeto"
}
```

### Tipos de evento

| Tipo | Architecture | Payload | Descrição |
|------|-------------|---------|-----------|
| `chunk` | `star` / `hierarchical` | `string` | Fragmento de texto (~80 chars) |
| `done` | `star` / `hierarchical` | `""` | Topologia completou |
| `error` | `star` / `hierarchical` | `string` | Mensagem de erro |
| `metric` | `star` / `hierarchical` | `BenchmarkMetrics` | Métricas de performance |
| `quality_metrics` | `both` | `QualityMetrics` | Métricas de qualidade (3 eixos) |
| `chunk` | `both` | `string` | Fragmento do relatório comparativo |
| `done` | `both` | `""` | Relatório comparativo concluído |

### Ciclo de Vida do WebSocket

1. Cliente conecta em `/ws/{analysisId}`
2. Servidor aceita e busca a `Queue` ativa
3. Loop: consome eventos da queue com timeout de 1s
4. Captura métricas de agentes dos eventos `metric`
5. Envia cada evento como JSON ao cliente
6. Encerra quando recebe 2 eventos `done` (um por arquitetura)
7. Computa `quality_metrics` e envia como evento
8. Gera relatório comparativo e faz streaming em chunks de 80 chars
9. Envia evento `done` final com `architecture: "both"`
10. Em desconexão: limpa `active_queues` e `active_threads` (mantém `active_results`)

---

## Integração com LLM

**Arquivo:** `backend/core/llm_client.py`

### Providers

| Provider | Modelo | Prioridade | Variável de ambiente |
|----------|--------|------------|---------------------|
| **Groq** | `llama-3.3-70b-versatile` | Primário | `GROQ_API_KEY` |
| **Google Gemini** | `gemini-2.0-flash` | Fallback | `GEMINI_API_KEY` |

A detecção é automática: se `GROQ_API_KEY` está definida, usa Groq; senão tenta Gemini.

### Rate Limiting

| Mecanismo | Valor |
|-----------|-------|
| Lock global | `threading.Lock()` — serializa todas as chamadas |
| Intervalo mínimo | 2.0 segundos entre chamadas |
| Groq free tier | 30 RPM, **1.000 RPD**, 12K TPM, **100K TPD** |
| Gargalo real | Limites diários (RPD/TPD), não os por minuto |

### Retry

| Parâmetro | Valor |
|-----------|-------|
| Max retries | 3 |
| Base delay | 10 segundos |
| Backoff | `delay × (attempt + 1)` (exponencial) |
| Erros retryable | 429, `RESOURCE_EXHAUSTED`, `rate_limit` |
| Erros fatais | Qualquer outro erro → retorna `None` imediatamente |

### Fallback

Se o LLM retorna `None` (indisponível após 3 tentativas), o `AgenteSintetizador` gera texto estruturado com:
- Resumo Executivo
- Cobertura de Dados (gaps detectados)
- Análise das Correlações (Pearson/Spearman/Kendall por par)
- Discussão das Anomalias (com descrições em português)
- Contexto Orçamentário (tendências por subfunção)

### Consumo por análise

Cada análise consome **2 chamadas LLM** (1 sintetizador estrela + 1 sintetizador hierárquica).

---

## Métricas de Execução

### MetricsCollector (`backend/core/metrics.py`)

Coleta por agente:

| Métrica | Fonte | Descrição |
|---------|-------|-----------|
| `executionTimeMs` | `time.time()` | Tempo de execução em milissegundos |
| `cpuPercent` | `psutil.Process.cpu_percent()` | Uso de CPU do processo |

**Uso:**
```python
mc = MetricsCollector(agent_id, "correlacao")
mc.start()
# ... trabalho do agente ...
mc.stop()
mc.persist(neo4j_client, analysis_id, "star")
```

Também suporta context manager: `with MetricsCollector(...) as mc:`

### MessageCounter (`backend/core/message_counter.py`)

Contador atômico thread-safe (`threading.Lock`):
- `increment(n=2)` — cada chamada entre agentes = 2 mensagens (ida + volta)
- `count` — property thread-safe para leitura
- Persistido no Neo4j como `starMessageCount` / `hierMessageCount`
- Enviado via WebSocket como parte do evento `metric`

---

## Métricas de Qualidade

**Arquivo:** `backend/core/quality_metrics.py`

Calculadas após ambas as topologias completarem, organizadas em 3 eixos:

### A. Eficiência dos Agentes

| Métrica | Função | Descrição |
|---------|--------|-----------|
| A6 | `compute_coordination_overhead()` | Tempo em supervisores / tempo total (0% na estrela, >0% na hierárquica) |
| A7 | `compute_latency_breakdown()` | Distribuição de tempo por fase: domínio, analítico, contexto, supervisores |
| A8 | `compute_communication_efficiency()` | Mensagens por agente (menor = mais eficiente) |

### B. Qualidade da Resposta

| Métrica | Função | Descrição |
|---------|--------|-----------|
| B1 | `compute_deterministic_consistency()` | Correlações e anomalias idênticas entre topologias? |
| B2 | `compute_faithfulness()` | Texto menciona correlações fortes e anomalias? (checklist automático) |
| B2+ | `compute_faithfulness_llm()` | Avaliação LLM-as-judge (1-5) — opcional |
| B3 | `compute_completeness()` | Cobertura: correlações (40%) + anomalias (40%) + contexto (20%) |
| B4 | `compute_structural_quality()` | Texto contém 4 seções esperadas? (resumo, correlações, anomalias, contexto) |

### C. Resiliência

| Métrica | Função | Descrição |
|---------|--------|-----------|
| C1 | `compute_partial_result_coverage()` | Quantos dos 7 componentes estão presentes e não-vazios |
| C2 | `compute_graceful_degradation()` | Quanto da qualidade é preservada sob falha simulada |

### Função Agregadora

`compute_all_quality_metrics()` calcula todas as métricas de uma vez e retorna dict organizado por eixo, pronto para envio via WebSocket.

---

## Relatório Comparativo

**Função:** `generate_comparative_report()`

Gerado após ambas as topologias completarem, consolida todas as métricas em texto legível:

### Seções do relatório

1. **Eficiência Operacional** — tempo total, overhead de coordenação, comunicação (mensagens por agente)
2. **Qualidade da Resposta** — consistência determinística, fidelidade, completude e qualidade estrutural por topologia
3. **Resiliência** — cobertura de resultados parciais por topologia
4. **Conclusão** — vencedor por eixo (eficiência, qualidade, consistência) e veredicto geral

O relatório é transmitido via WebSocket em chunks de 80 chars com `architecture: "both"`.

---

## Tratamento de Erros

### Por camada

| Camada | Tipo de erro | Estratégia |
|--------|-------------|-----------|
| Agente de domínio | Falha Neo4j | `_recover_intention()` retorna listas vazias |
| Agente sintetizador | LLM indisponível | Fallback para texto estruturado |
| OrquestradorEstrela | Falha de agente | Envia evento `error` via ws_queue, continua com resultados parciais |
| CoordenadorGeral | Falha de supervisor | Degradação graciosa, continua com dados vazios para aquele supervisor |
| Backend (validação) | Parâmetros inválidos | HTTP 400 com descrição |
| Backend (análise não encontrada) | ID inexistente | HTTP 404 |
| Backend (quality) | Topologias não completaram | HTTP 404 com mensagem explicativa |
| WebSocket | Cliente desconectou | Limpa queues e threads, mantém results |
| Frontend | WebSocket perdido | Reconexão automática (até 3 tentativas, delay incremental) |

### Persistência de resultado em falha

Ao completar cada topologia (mesmo com erros parciais), `_persist_topology_result()` atualiza o nó `Analise`:
- `starStatus` / `hierStatus` → `"completed"`
- `starMessageCount` / `hierMessageCount` → total de mensagens
- `starTextAnalysis` / `hierTextAnalysis` → texto gerado
- `starCompletedAt` / `hierCompletedAt` → timestamp ISO 8601 UTC
