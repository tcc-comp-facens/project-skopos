# Backend, API e Integração LLM

## Sumário

1. [API REST](#api-rest)
2. [WebSocket](#websocket)
3. [Chat e Interpretação de Intenção](#chat-e-interpretação-de-intenção)
4. [Integração com LLM](#integração-com-llm)
5. [Métricas de Execução](#métricas-de-execução)
6. [Métricas de Qualidade e Eficiência — Detalhamento Completo](#métricas-de-qualidade-e-eficiência--detalhamento-completo)
   - [E. Eficiência dos Agentes](#e-eficiência-dos-agentes) (E1, E2)
   - [Q. Qualidade da Resposta](#q-qualidade-da-resposta) (Q1, Q3, RAGAS)
   - [R. Resiliência](#r-resiliência) (R1)
   - [Métricas Complementares dos Agentes Analíticos](#métricas-complementares-agentes-analíticos)
   - [Resumo de Valores-Alvo](#resumo-de-valores-alvo)
7. [Relatório Comparativo](#relatório-comparativo)
8. [Tratamento de Erros](#tratamento-de-erros)

---

## API REST

**Arquivo:** `backend/api/routes.py`, `backend/api/websocket.py`, `backend/api/chat_websocket.py`

O `main.py` é o entry point que cria o app FastAPI, configura CORS, logging (nível via `LOG_LEVEL`) e registra os routers de `api/routes.py` (REST), `api/websocket.py` (WebSocket de resultados) e `api/chat_websocket.py` (WebSocket de chat). A lógica de endpoints, modelos, estado compartilhado e thread runners está organizada em `backend/api/`. `api/dispatch.py` concentra o disparo de análise (persistência + threads) compartilhado entre o formulário REST (`routes.py`) e o chat (`chat_runner.py`).

### Endpoints

| Método | Rota | Descrição | Retorno |
|--------|------|-----------|---------|
| `POST` | `/api/analysis` | Inicia análise comparativa (parâmetros já estruturados) | `{ "analysisId": "uuid" }` |
| `GET` | `/api/analysis/{id}` | Recupera resultado da análise do Neo4j | Nó Analise completo |
| `GET` | `/api/analysis/{id}/quality` | Métricas de qualidade (3 eixos) | Dict com efficiency, quality, resilience |
| `GET` | `/api/analysis/{id}/report` | Relatório comparativo textual | `{ "report": "texto..." }` |
| `GET` | `/api/benchmarks` | Métricas de todas as análises | Lista de MetricaExecucao |
| `GET` | `/api/data-range` | Intervalo de anos com dados carregados no Neo4j | `{ "minYear": int\|null, "maxYear": int\|null }` |
| `WS` | `/ws/chat/{sessionId}` | Turno de intenção do chat — ver [Chat e Interpretação de Intenção](#chat-e-interpretação-de-intenção) | eventos JSON |

### POST /api/analysis

**Request body:**
```json
{
  "dateFrom": 2018,
  "dateTo": 2023,
  "healthParams": {
    "dengue": true,
    "covid": true,
    "vaccination": true,
    "internacoes": true,
    "mortalidade": true
  },
  "useLlm": true,
}
```

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `dateFrom` | `int` | `2018` | Ano inicial do período |
| `dateTo` | `int` | `2021` | Ano final do período |
| `healthParams` | `object` | — | Parâmetros de saúde (pelo menos um `true`) |
| `useLlm` | `bool` | `true` | Se `true`, usa LLM para síntese textual; se `false`, gera texto estruturado (fallback) |

**Validações (retorna 400):**
- `dateFrom` deve ser < `dateTo` (intervalo mínimo de 2 anos)
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

**Query parameters:**
- (sem parâmetros — devolve o resultado já calculado)

**Comportamento:**
- Usa o tempo real (wall-clock) de cada topologia (`star_wall_clock_ms`, `hier_wall_clock_ms`) armazenado em `active_results` para o cálculo do latency breakdown, evitando dupla contagem de supervisores na soma dos tempos individuais dos agentes — consistente com o cálculo feito via WebSocket.

**Cache:**
- Resultado cacheado em `active_results`; o endpoint sempre devolve o cache quando ele existe
- A avaliação RAGAS (quando solicitada) roda uma única vez no WebSocket e já chega encaixada no cache, em `quality.{arch}.ragas` — o endpoint nunca a recomputa

### GET /api/analysis/{id}/report

Retorna relatório comparativo textual. Requer que `/quality` tenha sido computado primeiro.

### Estado Compartilhado (`backend/api/state.py`)

```python
active_queues: dict[str, Queue]                    # analysisId → fila WS
active_threads: dict[str, list[Thread]]            # analysisId → [thread_star, thread_hier]
active_results: dict[str, dict[str, Any]]          # analysisId → {"star": result, "hierarchical": result, ...}
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
  "type": "chunk" | "done" | "error" | "metric" | "quality_metrics" | "ragas" | "ragas_done",
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
| `ragas` | `both` | `""` | Notificação de início da avaliação RAGAS |
| `ragas` | `both` | `string` | Fragmento do resultado RAGAS (~80 chars) |
| `ragas_done` | `both` | `""` | Streaming da avaliação RAGAS concluído |

### Ciclo de Vida do WebSocket

1. Cliente conecta em `/ws/{analysisId}`
2. Servidor aceita e busca a `Queue` ativa
3. Loop: consome eventos da queue com timeout de 1s
4. Captura métricas de agentes dos eventos `metric`
5. Envia cada evento como JSON ao cliente
6. Encerra quando recebe 2 eventos `done` (um por arquitetura)
7. Computa `quality_metrics` (determinístico, sem nenhuma chamada LLM) e envia como evento
8. Gera relatório comparativo e faz streaming em chunks de 80 chars
9. Envia evento `done` final com `architecture: "both"`
10. Envia evento `ragas` inicial com payload vazio (notifica o frontend que a avaliação começou), executa `core.ragas_metrics.evaluate_architecture` por topologia (cada uma sob seu próprio `TokenBucket`, sequencialmente), encaixa o resultado em `quality.{arch}.ragas` e o custo em `cost.ragas` dentro de `active_results`, e faz streaming do texto formatado em chunks via eventos `ragas` seguido de `ragas_done`. **Só então** gera e transmite o relatório comparativo — cuja seção "Conclusão" decide a topologia vencedora pela fidelidade do RAGAS
11. Em desconexão: limpa `active_queues` e `active_threads` (mantém `active_results`)

---

## Chat e Interpretação de Intenção

**Arquivos:** `backend/api/chat_websocket.py`, `backend/api/chat_runner.py`, `backend/agents/intent/agente_interpretacao_intencao.py`

### Endpoint: `WS /ws/chat/{sessionId}`

Cuida apenas do turno de intenção: interpreta a mensagem do usuário, dispara a análise (reaproveitando `dispatch_analysis`, o mesmo usado pelo `POST /api/analysis`) e confirma em texto. O streaming dos resultados da análise em si continua exclusivamente pelo `/ws/{analysisId}` já existente — o WebSocket de chat não lê a mesma `ws_queue` (é um consumidor de fila único, removida ao terminar), então não pode competir por ela.

### Protocolo

```
cliente → servidor:
  {"type": "user_message", "payload": {"text": str}}

servidor → cliente:
  {"type": "user_ack", "payload": ""}
  {"type": "system_chunk", "payload": str}      # texto em ~80 chars
  {"type": "system_done", "payload": ""}
  {"type": "analysis_started", "payload": analysisId}
  {"type": "error", "payload": str}
```

### Validações no servidor

- `session_id` deve ser um UUID válido (senão a conexão é fechada com código 4001)
- Mensagens acima de `MAX_MESSAGE_LENGTH` (1000 chars) são rejeitadas
- Apenas uma rodada por vez por sessão é aceita (`active_chat_sessions`) — não confia só no frontend desabilitar o input

### Fluxo por mensagem

1. Servidor responde `user_ack` imediatamente
2. `AgenteInterpretacaoIntencao.parse(text)` roda o ciclo CoALA (1 chamada LLM que classifica escopo e extrai parâmetros na mesma resposta — ver `docs/02-AGENTES.md`)
3. **Fora de escopo, incompleto ou LLM indisponível** → `system_chunk`/`system_done` com a mensagem de esclarecimento/recusa; nenhuma análise é criada
4. **Sucesso** → `run_chat_analysis()` chama `dispatch_analysis()` (persiste `Analise`, dispara as duas threads star/hierarchical); servidor envia `analysis_started` com o `analysisId` e o texto de confirmação (`pretty_print` da intenção interpretada)
5. Cliente troca de socket para `/ws/{analysisId}` para acompanhar o processamento da análise

### Sem regex

Diferente de um design anterior (regex-primário, LLM como fallback), hoje **toda** mensagem passa pelo LLM — não há atalho determinístico. Isso inclui o guardrail de escopo: mensagens fora do domínio orçamentário/saúde pública de Sorocaba são recusadas sem instanciar `OrquestradorEstrela` nem `CoordenadorGeral`. Ver `PLANO_REFATORACAO.md` (Etapa 1) para a justificativa acadêmica dessa decisão.

---

## Integração com LLM

**Arquivo:** `backend/core/llm_client.py`

### Provider

| Provider | Modelo | Variável de ambiente |
|----------|--------|---------------------|
| **DeepSeek** (API compatível OpenAI) — default | `deepseek-v4-flash` | `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` (opcional) |
| **OpenAI** | `gpt-5.6-luna` | `OPENAI_API_KEY`, `OPENAI_MODEL` (opcional) |

O provedor ativo é escolhido por `LLM_PROVIDER` (`deepseek` ou `openai`; default `deepseek`). Nenhum call site muda — todos chamam `generate()`/`generate_stream()` de `core/llm_client.py`, que resolve o provedor a cada chamada. O SDK é o `openai` nos dois casos; só a `base_url` difere.

Um único modelo por provedor — sem cadeia de fallback entre modelos (diferente de um design anterior com múltiplos modelos Groq em cascata). No DeepSeek, `thinking` é desabilitado na chamada (`extra_body={"thinking": {"type": "disabled"}}`) para resposta direta, sem chain-of-thought; esse parâmetro é proprietário e não é enviado à OpenAI. Modelos de raciocínio da OpenAI (`gpt-5*`, série `o*`) recebem `max_completion_tokens` em vez de `max_tokens` e sem `temperature`, exigência da API deles.

`OPENAI_STORE_LOGS=true` (opt-in, só OpenAI) envia `store=True` e `metadata={"app": "skopos", "caller": ...}` em cada chamada, tornando-as visíveis e filtráveis por agente na aba Logs do dashboard — a API não retém nada por default (`store=False`), então sem isso só o consumo aparece no billing. Numa organização com Zero Data Retention o parâmetro é ignorado.

### Rate Limiting

| Mecanismo | Valor |
|-----------|-------|
| Lock global | `threading.Lock()` — serializa todas as chamadas |
| Intervalo mínimo | 2.0 segundos entre chamadas |

### Retry

| Parâmetro | Valor |
|-----------|-------|
| Max retries | 2 (no mesmo modelo) |
| Base delay | 10 segundos |
| Backoff | `delay × (attempt + 1)` (linear) |
| Erros retryable | 429, `RESOURCE_EXHAUSTED`, `rate_limit` |
| Erros fatais | Qualquer outro erro → `generate`/`generate_stream` retornam `None`/param imediatamente |

### Modos de geração

O cliente LLM oferece dois modos de geração:

| Modo | Função | Descrição |
|------|--------|-----------|
| **Batch** | `generate(prompt, model=None, *, caller="desconhecido", provider=None, temperature=None)` | Retorna o texto completo de uma vez. Usado pelo `AgenteInterpretacaoIntencao`, pelo `TextSynthesizer` (fallback) e pela avaliação RAGAS — que passa `provider` explícito para fixar o juiz num provedor independente de `LLM_PROVIDER`. Para modelos de raciocínio da OpenAI (`gpt-5*`, `o*`) a chamada leva `max_completion_tokens` (`OPENAI_MAX_COMPLETION_TOKENS`, default 16384) e `reasoning_effort` (`OPENAI_REASONING_EFFORT`, default `low`) em vez de `max_tokens`/`temperature` — esse teto é compartilhado com os tokens de raciocínio, e sem limitar o esforço um prompt grande volta vazio. |
| **Streaming** | `generate_stream(prompt, model=None, *, caller="desconhecido")` | Yield de tokens incrementalmente conforme chegam da API. Usado pelo `TextSynthesizer` para streaming em tempo real via WebSocket. |

Ambos os modos compartilham o mesmo lock global, rate limiting e retry em caso de 429.

### Observabilidade das chamadas

`caller` identifica quem disparou a chamada (tipicamente `agent_id`/`synthesizer_id`, às vezes com um sufixo de propósito, ex.: `"test-intent:classificar_e_extrair"`) — usado só para logging, não afeta o comportamento. Antes de cada chamada real à API:
- **INFO**: preview de uma linha do prompt (truncado a ~300 chars) e tamanho total
- **DEBUG** (`LOG_LEVEL=DEBUG`): prompt completo

Depois da resposta: tamanho da resposta recebida (não o conteúdo — ver observação abaixo). Todos os logs (tokens consumidos, retry por rate limit, erro, resposta vazia) incluem a tag `caller`.

> A resposta da API em si (o texto gerado) não é logada, só seu tamanho — assimétrico com o prompt, que tem preview + versão completa em DEBUG. É uma escolha deliberada (menos verboso); pode ser adicionado de forma simétrica se necessário.

### Pós-processamento de respostas

Modelos de raciocínio incluem tags `<think>...</think>` com o processo de pensamento na resposta. O cliente LLM remove automaticamente essas tags em ambos os modos, mesmo com `thinking` desabilitado (defensivamente):
- **Batch** (`generate`): via `re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)` após receber a resposta completa
- **Streaming** (`generate_stream`): via buffer incremental que detecta `<think>` e suprime tokens até encontrar `</think>`, garantindo que apenas o conteúdo final seja enviado ao consumidor em tempo real

### Fallback

Se o LLM retorna `None` (indisponível após as tentativas de retry, ou erro fatal), o `TextSynthesizer` gera texto estruturado com:
- Resumo Executivo
- Cobertura de Dados (gaps detectados)
- Análise das Correlações (Spearman por par, com estratégia e confiança)
- Discussão das Anomalias (com descrições em português)
- Contexto Orçamentário (tendências por subfunção)

O `AgenteInterpretacaoIntencao`, quando o LLM falha, cai na estratégia de fallback registrada (`escopo = "indisponivel"`) e pede ao usuário para tentar novamente — não há fallback determinístico para interpretação de linguagem natural (ver `docs/02-AGENTES.md`).

### Consumo por análise

Cada comparação estrela vs. hierárquica consome **2 chamadas LLM** de síntese (1 sintetizador estrela + 1 sintetizador hierárquica) — mais **1 chamada adicional** de interpretação de intenção quando a análise se origina do chat (não conta quando vem do formulário REST direto). A avaliação RAGAS (sempre executada) acrescenta, por topologia, um custo **fixo**: 2 chamadas para faithfulness, 3 para answer relevancy e 2 para context relevance, mais 1 requisição de embeddings por pergunta gerada. Nenhuma métrica escala com o número de achados.

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

### Wall-clock e exclusão do sintetizador

Ambas as topologias excluem o tempo do `TextSynthesizer` (chamada LLM) do `totalExecutionTimeMs` reportado no evento `metric`, pois esse tempo depende da disponibilidade e latência da API do provedor de LLM — não reflete a eficiência da arquitetura multiagente em si.

| Topologia | Estratégia |
|-----------|-----------|
| **Estrela** | Captura `_orch_end` *antes* de iniciar o sintetizador. O wall-clock mede apenas o pipeline CoALA (domínio → cruzamento → contexto → correlação → anomalias). |
| **Hierárquica** | Captura `_coord_end` após todo o pipeline (incluindo sintetizador), e subtrai `sint_time_ms` extraído dos collectors do `SupervisorAnalitico` (busca `agentType == "sintetizador"`). |

Isso garante que a comparação de eficiência entre topologias reflita apenas a orquestração e processamento de dados, não a variabilidade da API LLM.

### StreamingAdapter (`backend/core/streaming_adapter.py`)

Adaptador de streaming para WebSocket. Encapsula a lógica de chunking (~80 chars) e envio de eventos para a fila compartilhada. Não é um agente CoALA — é infraestrutura de transporte reutilizável pelo orquestrador estrela, coordenador hierárquico e relatório comparativo. Loga início/fim de cada streaming (chars, nº de chunks, duração), sem logar por chunk individual.

**Construtor:**
```python
adapter = StreamingAdapter(ws_queue, analysis_id, architecture, chunk_size=80)
```

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `ws_queue` | `Queue` | Fila compartilhada para eventos WebSocket |
| `analysis_id` | `str` | UUID da análise corrente |
| `architecture` | `str` | Identificador da topologia (`"star"`, `"hierarchical"`, `"both"`) |
| `chunk_size` | `int` | Tamanho aproximado de cada chunk (default: 80) |

**Métodos:**

| Método | Descrição |
|--------|-----------|
| `stream_text(text)` | Envia texto pré-gerado em chunks para `ws_queue` |
| `stream_tokens(token_generator)` | Consome generator de tokens LLM, faz buffering e streaming; retorna texto completo acumulado |

Cada chunk é enviado como evento `WSEvent` com `type: "chunk"` e o `architecture` configurado no construtor.

---

## Métricas de Qualidade e Eficiência — Detalhamento Completo

**Arquivo:** `backend/core/quality_metrics.py`

Calculadas automaticamente após ambas as topologias completarem, organizadas em 3 eixos com 9 métricas individuais. Cada métrica é descrita abaixo com sua fórmula, significado, valores-alvo e contribuição para a comparação entre as topologias Estrela e Hierárquica.

### Visão Geral

| Eixo | Métricas | Pergunta que responde |
|------|----------|-----------------------|
| **E. Eficiência** | E1, E2 | Qual topologia usa melhor seus recursos computacionais e de comunicação? |
| **Q. Qualidade** | Q1, Q3 (determinísticas) + RAGAS (opcional) | Os resultados são corretos, fiéis aos dados e respondem à pergunta feita? |
| **R. Resiliência** | R1 | O sistema se comporta bem quando algo falha? |

---

### E. Eficiência dos Agentes

#### E1 — Overhead de Coordenação

**Função:** `compute_coordination_overhead(agent_metrics)`

**O que mede:** A fração do tempo total de execução gasta em supervisores e coordenadores (camada de gerenciamento) em relação ao tempo gasto em agentes de trabalho efetivo.

**Como é calculado:**

```
overhead_ratio = tempo_supervisores / (tempo_supervisores + tempo_workers)
overhead_percent = overhead_ratio × 100
```

Onde:
- `tempo_supervisores` = soma de `executionTimeMs` dos agentes cujo nome pertence ao conjunto `{supervisor_dominio, supervisor_analitico, supervisor_contexto, orquestrador_estrela, coordenador_geral}`
- `tempo_workers` = soma de `executionTimeMs` de todos os demais agentes (domínio, analíticos, contexto)

**Valores retornados:**
- `supervisor_time_ms`: tempo total dos supervisores (ms)
- `worker_time_ms`: tempo total dos workers (ms)
- `total_time_ms`: soma dos dois
- `overhead_ratio`: 0.0 a 1.0
- `overhead_percent`: 0% a 100%

**Valores-alvo:**
- **Estrela:** ~0% (o orquestrador não aparece nas métricas individuais de agentes, pois ele é o próprio pipeline)
- **Hierárquica:** < 15% é bom, 15-30% é aceitável, > 30% indica overhead excessivo de coordenação

**Significado para o TCC:** Quantifica o "custo" da hierarquia de supervisores. Se a topologia hierárquica gasta 25% do tempo apenas coordenando, isso é evidência empírica de que a camada extra de supervisores tem um custo real. Permite argumentar se a escalabilidade e a degradação graciosa da hierárquica compensam esse overhead.

---

#### E2 — Latency Breakdown por Fase

**Função:** `compute_latency_breakdown(agent_metrics)`

**O que mede:** Como o tempo total de execução se distribui entre as 4 fases do pipeline de análise.

**Como é calculado:**

Para cada agente, classifica-o em uma das 4 fases com base no `agentName`:

| Fase | Agentes incluídos |
|------|-------------------|
| `dominio` | vigilancia_epidemiologica, saude_hospitalar, atencao_primaria, mortalidade |
| `analitico` | correlacao, anomalias, sintetizador |
| `contexto` | contexto_orcamentario |
| `supervisores` | supervisor_dominio, supervisor_analitico, supervisor_contexto, orquestrador_estrela, coordenador_geral |

```
percentual_fase = (tempo_fase / tempo_total) × 100
```

**Valores retornados (por fase):**
- `time_ms`: tempo absoluto em milissegundos
- `percent`: percentual do tempo total
- `total_ms`: tempo total de todas as fases

**Valores-alvo:**
- **Fase domínio:** 20-40% (consultas Neo4j são I/O-bound)
- **Fase analítica:** 30-50% (inclui o sintetizador que chama LLM — geralmente o gargalo)
- **Fase contexto:** 5-15% (cálculos simples de variação percentual)
- **Fase supervisores:** 0% (estrela) / < 15% (hierárquica)

**Significado para o TCC:** Identifica gargalos no pipeline. Se a fase analítica domina (por causa da chamada LLM), ambas as topologias terão perfis semelhantes nessa fase, e a diferença real estará nas fases de domínio e supervisores. Permite argumentar sobre onde otimizações teriam maior impacto.

---

### Q. Qualidade da Resposta

#### Q1 — Consistência Determinística

**Função:** `compute_deterministic_consistency(star_result, hier_result)`

**O que mede:** Se ambas as topologias produzem resultados numéricos idênticos quando alimentadas com os mesmos dados de entrada.

**Como é calculado:**

1. Extrai `correlacoes` e `anomalias` de cada resultado
2. Normaliza cada lista ordenando por chave natural:
   - Correlações: `(subfuncao, tipo_indicador, spearman, estrategia, confianca)`
   - Anomalias: `(subfuncao, tipo_indicador, ano, tipo_anomalia)`
3. Compara as listas normalizadas com `==`

```
corr_identical = sorted(star_correlacoes) == sorted(hier_correlacoes)
anom_identical = sorted(star_anomalias) == sorted(hier_anomalias)
all_identical = corr_identical AND anom_identical
```

**Valores retornados:**
- `all_identical`: `true` / `false`
- `correlacoes_identical`: `true` / `false`
- `anomalias_identical`: `true` / `false`
- Contagens por topologia e lista de divergências (se houver)

**Valor-alvo:** `all_identical = true` (sempre)

**Significado para o TCC:** Esta é uma métrica de validação fundamental. Como ambas as topologias usam os mesmos agentes analíticos (`AgenteCorrelacao`, `AgenteAnomalias`) com os mesmos dados de entrada, os resultados numéricos **devem** ser idênticos. Se não forem, há um bug no sistema. Isso garante que a comparação entre topologias é justa — a diferença está apenas na orquestração, não nos resultados.

---

#### Fidelidade e relevância — biblioteca RAGAS (opcional)

**Módulo:** `core/ragas_metrics.py` — `evaluate_architecture(result, user_input, *, caller)` (assíncrona)

Substituiu três implementações caseiras que existiam aqui (`compute_faithfulness` por substring, `_compute_faithfulness_claims` "estilo RAGAS" e `compute_faithfulness_llm` com nota 1-5). A última era o baseline "GPT Score" que o paper do RAGAS mede como inferior à própria metodologia — 0.72 contra 0.95 de concordância com anotadores humanos (Es et al., 2024, Tabela 4).

**O que mede:** três aspectos reference-free (não exigem resposta-padrão anotada), calculados pela biblioteca [`ragas`](https://github.com/explodinggradients/ragas) 0.4.x:

| Métrica | Pergunta que responde |
|---------|-----------------------|
| `faithfulness` | O texto é sustentado pelos dados? Decompõe a resposta em afirmações e verifica cada uma contra o contexto: `score = suportadas / total` |
| `answer_relevancy` | O texto responde à pergunta feita? Gera N perguntas a partir da resposta e mede a similaridade de cosseno com a pergunta original (exige embeddings) |
| `context_relevance` | Os achados entregues ao sintetizador eram relevantes à pergunta? Dois juízes avaliam o conjunto numa escala 0/1/2 |

**Como a tripla do RAGAS é montada:** o sistema não é um RAG clássico (não há retriever nem corpus). O mapeamento é `user_input` = pergunta original do chat, `response` = `texto_analise`, `retrieved_contexts` = **tudo** que o sintetizador recebeu: um chunk por achado (com a leitura do sinal, o n e a descrição com valores em R$), mais o período, a cobertura de dados e as afirmações que o próprio prompt injeta (pandemia, traduções de subfunção) — que o modelo é instruído a repetir e sem as quais o texto seria reprovado por obedecer à instrução. Justificativa completa em [metricas-de-avaliacao.md](arquitetura/metricas-de-avaliacao.md).

**Juiz:** provedor fixo via `RAGAS_PROVIDER` (default `openai`), **independente de `LLM_PROVIDER`** — trocar o provedor do pipeline não pode trocar o instrumento de medida. As chamadas passam por `core.llm_client.generate(provider=...)`, então continuam contabilizadas no `TokenBucket` e cobertas pelo retry de 429.

**Configuração (toda via `.env`):** `RAGAS_PROVIDER`, `RAGAS_MODEL`, `RAGAS_EMBEDDING_MODEL` (default `text-embedding-3-large` — melhor qualidade multilíngue, que é o que importa para comparar textos curtos em português), e `RAGAS_EMBEDDING_FALLBACKS` (cadeia separada por vírgula; vazio desliga o fallback). Não há teto de contextos: todas as métricas recebem o conjunto completo, porque nenhuma escala em número de chamadas com a quantidade de achados.

**Embeddings sem acesso (403 `model_not_found`):** pode ocorrer com a mesma chave que funciona no `chat.completions`. O código repete a chamada sem o cabeçalho `OpenAI-Project` (que dispara uma checagem que falha em algumas configurações) e, persistindo, desce a cadeia de fallback, reportando o modelo efetivo em `judge.embedding_model_used`. Para separar "fora da allowlist" de "na allowlist mas negado" — mesmo erro, soluções diferentes: `cd backend && python -m scripts.check_embeddings`.

**Valores retornados:**
```json
{
  "framework": "ragas", "version": "0.4.3",
  "judge": {"provider": "openai", "model": "...", "embedding_model": "..."},
  "metrics": {"faithfulness": {"score": 0.86}, "answer_relevancy": {"score": 0.91},
              "context_relevance": {"score": 0.62}},
  "sample": {"n_contexts_total": 67, "response_chars": 1840},
  "available": true, "unavailable_reason": null, "errors": []
}
```

Todos os scores estão em `[0, 1]` ou são `null` (métrica não calculável) — nunca `NaN`, que quebraria o `JSON.parse` do cliente.

**Degradação graciosa:** sem a API key do juiz, vem `available: false` com `unavailable_reason` legível — não um score 0, que seria indistinguível de um score 0 legítimo. A falha de uma métrica não impede as outras (registrada em `errors[]`).

**Significado para o TCC:** é a única métrica de qualidade textual do sistema com validação publicada. Permite comparar se uma topologia produz textos mais fiéis, mais relevantes ou apoiados em contexto mais enxuto que a outra.

---

#### Q3 — Completude (Completeness)

**Função:** `compute_completeness(correlacoes, anomalias, contexto_orcamentario, texto)`

**O que mede:** Se TODOS os achados relevantes aparecem no texto, não apenas os mais importantes. Diferente da fidelidade medida pelo RAGAS (que verifica se o que está no texto é sustentado pelos dados), Q3 verifica se tudo que deveria estar no texto está lá — e faz isso sem chamar o LLM.

**Como é calculado:**

Avalia cobertura em 3 categorias com pesos diferentes:

**1. Cobertura de correlações (peso 40%):**
Para cada correlação (todas, não só as fortes), verifica se o texto menciona o número da subfunção, o nome da subfunção ou o tipo de indicador.
```
corr_coverage = correlações_encontradas / total_correlações
```

**2. Cobertura de anomalias (peso 40%):**
Para cada anomalia, verifica se o texto menciona palavras-chave do tipo:
- `alto_gasto_baixo_resultado` → busca: "alto gasto", "gasto acima", "ineficiência", "ineficiente"
- `baixo_gasto_alto_resultado` → busca: "baixo gasto", "gasto abaixo", "eficiência", "eficiente"
```
anom_coverage = anomalias_encontradas / total_anomalias
```

**3. Cobertura de contexto orçamentário (peso 20%):**
Para cada subfunção no contexto, verifica se o texto menciona o número ou nome da subfunção.
```
ctx_coverage = subfunções_encontradas / total_subfunções
```

**Score final ponderado:**
```
score = corr_coverage × 0.4 + anom_coverage × 0.4 + ctx_coverage × 0.2
```

**Valores retornados:**
- `score`: 0.0 a 1.0
- `correlacoes_coverage`: 0.0 a 1.0
- `anomalias_coverage`: 0.0 a 1.0
- `contexto_coverage`: 0.0 a 1.0
- `details`: contagens (found/total) por categoria

**Valores-alvo:**
- ≥ 0.75 (75%): bom — texto abrangente
- ≥ 0.50 (50%): aceitável — cobre os principais achados
- < 0.50 (50%): ruim — texto incompleto

**Significado para o TCC:** Complementa a fidelidade medindo abrangência. Um texto pode ser fiel (faithfulness alto) mas incompleto (Q3 baixo) se menciona corretamente apenas metade dos achados. Os pesos refletem a importância relativa: correlações e anomalias são o core da análise (40% cada), enquanto o contexto orçamentário é complementar (20%).

---

### R. Resiliência

#### R1 — Cobertura de Resultados Parciais

**Função:** `compute_partial_result_coverage(result)`

**O que mede:** Quantos componentes do resultado final estão presentes e não-vazios, indicando quantos agentes completaram com sucesso.

**Como é calculado:**

Verifica a presença (valor truthy) de 7 componentes no resultado:

| Componente | Agente responsável |
|------------|-------------------|
| `despesas` | Agentes de domínio (4) |
| `indicadores` | Agentes de domínio (4) |
| `dados_cruzados` | `cross_domain_data()` |
| `correlacoes` | `AgenteCorrelacao` |
| `anomalias` | `AgenteAnomalias` |
| `contexto_orcamentario` | `AgenteContextoOrcamentario` |
| `texto_analise` | `TextSynthesizer` |

```
score = componentes_presentes / 7
```

**Valores retornados:**
- `score`: 0.0 a 1.0 (em incrementos de ~0.143)
- `completed`: 0 a 7
- `total`: 7
- `components`: dict com `true`/`false` por componente

**Valor-alvo:** 1.0 (7/7) — todos os componentes presentes

**Significado para o TCC:** Mede a robustez do pipeline. Na execução normal, ambas as topologias devem atingir 1.0. A diferença aparece quando há falhas: a topologia hierárquica, com degradação graciosa nos supervisores, tende a manter um score mais alto que a estrela (que tem ponto único de falha no orquestrador). Permite argumentar sobre a resiliência relativa das topologias.

---

### Métricas Complementares (Agentes Analíticos)

Além das métricas de qualidade do módulo `quality_metrics.py`, os agentes analíticos produzem métricas de domínio que alimentam as métricas de qualidade:

#### Correlações Estatísticas (`AgenteCorrelacao`)

**Arquivo:** `backend/agents/analytical/correlacao.py`

Coeficiente Spearman calculado por par subfunção-indicador. Spearman é baseado em ranks — robusto a outliers e captura relações monotônicas não-lineares. Ideal para dados de saúde pública com amostras pequenas e possíveis anos atípicos.

**Comportamento:**
- n < 2 pontos → retorna 0.0, classificação `"baixa"`
- n ≥ 2 pontos → calcula Spearman normalmente

**Classificação (baseada em |Spearman|):**

| Faixa | Classificação |
|-------|---------------|
| \|r\| ≥ 0.7 | `"alta"` |
| \|r\| ≥ 0.4 | `"média"` |
| \|r\| < 0.4 | `"baixa"` |

**Tratamento de edge cases:**
- < 2 pontos de dados → retorna 0.0, classificação `"baixa"`
- Arrays constantes (scipy retorna NaN) → retorna 0.0
- Resultado clamped a [-1.0, 1.0]

**Output por par:** `subfuncao`, `tipo_indicador`, `spearman`, `classificacao`, `n_pontos`.

**Significado para o TCC:** Spearman é o método único porque é robusto a outliers e não assume linearidade — adequado para dados de gastos públicos que podem ter variações abruptas entre anos (ex: pandemia).

---

#### Detecção de Anomalias (`AgenteAnomalias`)

**Arquivo:** `backend/agents/analytical/anomalias.py`

**Método:** Comparação com mediana por par subfunção-indicador, considerando a polaridade do indicador.

**Polaridade dos indicadores:**
- Indicadores NEGATIVOS (mais = pior): dengue, covid, internacoes, mortalidade
- Indicadores POSITIVOS (mais = melhor): vacinacao

Constantes: `INDICADORES_NEGATIVOS`, `INDICADORES_POSITIVOS`

Para cada par (subfunção, tipo_indicador) com ≥ 2 pontos:
1. Calcula a mediana das despesas e a mediana dos indicadores
2. Para cada ano, classifica conforme a polaridade:

| Condição | Tipo de anomalia | Interpretação |
|----------|-----------------|---------------|
| despesa > mediana E resultado ruim* | `alto_gasto_baixo_resultado` | Possível ineficiência |
| despesa < mediana E resultado bom* | `baixo_gasto_alto_resultado` | Possível eficiência |

\* "Resultado ruim" = indicador negativo acima da mediana OU indicador positivo abaixo da mediana.
\* "Resultado bom" = indicador negativo abaixo da mediana OU indicador positivo acima da mediana.

**Significado para o TCC:** Identifica anos onde o gasto e o resultado divergem do padrão. Um ano com gasto acima da mediana mas indicador abaixo sugere ineficiência na aplicação dos recursos. A consideração da polaridade garante interpretação correta — para vacinação (positivo), cobertura baixa é resultado ruim; para dengue (negativo), casos altos é resultado ruim. Essas anomalias são o principal achado analítico do sistema e alimentam tanto o texto do sintetizador quanto as métricas de completude (Q3) e a avaliação RAGAS (onde cada anomalia vira um chunk de contexto).

---

#### Tendências Orçamentárias (`AgenteContextoOrcamentario`)

**Arquivo:** `backend/agents/context/contexto_orcamentario.py`

**Fórmula de variação ano a ano:**
```
variação = ((valor_ano_n - valor_ano_n-1) / valor_ano_n-1) × 100
```

**Classificação de tendência:**

| Condição | Classificação |
|----------|---------------|
| Variação positiva consecutiva ≥ 2 anos | `"crescimento"` |
| Variação negativa consecutiva ≥ 2 anos | `"corte"` |
| Todas as \|variações\| < 5% | `"estagnacao"` |
| < 2 anos de dados | `"insuficiente"` |

**Significado para o TCC:** Contextualiza as correlações e anomalias. Uma correlação negativa entre gasto e resultado pode ser explicada por um corte orçamentário recente. Sem esse contexto, a análise seria incompleta.

---

#### Detecção de Lacunas de Dados (`detect_data_gaps`)

**Arquivo:** `backend/agents/data_crossing.py`

**O que detecta:**
1. **Anos faltantes por subfunção** — ex: subfunção 303 sem dados em 2021
2. **Anos faltantes por tipo de indicador** — ex: indicador de dengue sem dados em 2020
3. **Cruzamentos impossíveis** — despesa existe mas indicador não (ou vice-versa) para um dado ano

**Métricas de cobertura:**
```
despesas_completeness = células_presentes / (num_subfunções × num_anos_esperados)
indicadores_completeness = células_presentes / (num_tipos × num_anos_esperados)
```

**Significado para o TCC:** Transparência sobre limitações dos dados. Se o sistema reporta correlação "baixa" entre subfunção 303 e algum indicador, pode ser porque simplesmente não há dados suficientes, não porque a correlação é fraca. O sintetizador recebe essa informação para incluir ressalvas no texto gerado.

---

### Função Agregadora

`compute_all_quality_metrics()` calcula todas as métricas determinísticas de uma vez e retorna dict organizado por eixo, pronto para envio via WebSocket. **Nenhuma delas chama o LLM** — a avaliação RAGAS roda à parte, de forma assíncrona, em `api/websocket.py`. Parâmetros fixos: estrela = 8 agentes, hierárquica = 11 agentes (8 + 3 supervisores). Aceita `star_wall_clock_ms` e `hier_wall_clock_ms` (default `0`) para usar o tempo real (wall-clock) percebido pelo usuário no cálculo do latency breakdown, evitando dupla contagem de supervisores na soma dos tempos individuais dos agentes.

### Resumo de Valores-Alvo

| Métrica | Valor-alvo | Interpretação |
|---------|-----------|---------------|
| E1 Overhead | Estrela ~0%, Hierárquica < 15% | Custo aceitável de coordenação |
| E2 Breakdown | Fase analítica 30-50% | LLM é o gargalo esperado |
| Q1 Consistência | `true` (sempre) | Resultados numéricos idênticos |
| RAGAS faithfulness | ≥ 0.80 | Texto sustentado pelos dados |
| RAGAS faithfulness / answer relevancy | ≥ 0.80 | Texto fiel aos dados e aderente à pergunta |
| Q3 Completude | ≥ 0.75 | Texto abrangente |
| R1 Cobertura | 1.0 (7/7 componentes) | Pipeline completo |

---

## Relatório Comparativo

**Função:** `generate_comparative_report()`

Gerado após ambas as topologias completarem, consolida todas as métricas em texto legível:

### Seções do relatório

1. **Eficiência Operacional** — tempo total, overhead de coordenação, latency breakdown por fase
2. **Qualidade da Resposta** — consistência determinística, fidelidade, completude e qualidade estrutural por topologia
3. **Resiliência** — cobertura de resultados parciais por topologia
4. **Conclusão** — vencedor por eixo (qualidade, eficiência, consistência) e veredicto geral. Prioridade: qualidade (faithfulness) > eficiência. A topologia com melhor fidelidade vence; em caso de empate, eficiência é usada como desempate.

O relatório é transmitido via WebSocket em chunks de 80 chars com `architecture: "both"`.

---

## Tratamento de Erros

### Por camada

| Camada | Tipo de erro | Estratégia |
|--------|-------------|-----------|
| Agente de domínio | Falha Neo4j | Estratégia de fallback registrada em `procedural_memory` retorna listas vazias |
| `AgenteInterpretacaoIntencao` | LLM indisponível/resposta inválida | Fallback registrado grava `escopo = "indisponivel"`; usuário é convidado a tentar de novo (sem fallback determinístico de interpretação) |
| `AgenteInterpretacaoIntencao` | Mensagem fora de escopo | Recusa educada; nenhuma arquitetura é instanciada |
| Agente sintetizador | LLM indisponível | Fallback para texto estruturado |
| OrquestradorEstrela | Falha de agente | Envia evento `error` via ws_queue, continua com resultados parciais |
| CoordenadorGeral | Falha de supervisor | Degradação graciosa, continua com dados vazios para aquele supervisor |
| Backend (validação) | Parâmetros inválidos | HTTP 400 com descrição |
| Backend (análise não encontrada) | ID inexistente | HTTP 404 |
| Backend (quality) | Topologias não completaram | HTTP 404 com mensagem explicativa |
| WebSocket | Cliente desconectou | Limpa queues e threads, mantém results |
| Chat WebSocket | Mensagem muito longa / rodada em andamento | Evento `error`, mensagem não é processada |
| Frontend | WebSocket perdido (resultados ou chat) | Reconexão automática (até 3 tentativas, backoff — linear no WS de resultados, exponencial 1s/2s/4s no WS de chat) |

### Persistência de resultado em falha

Ao completar cada topologia (mesmo com erros parciais), `_persist_topology_result()` atualiza o nó `Analise`:
- `starStatus` / `hierStatus` → `"completed"`
- `starTextAnalysis` / `hierTextAnalysis` → texto gerado
- `starCompletedAt` / `hierCompletedAt` → timestamp ISO 8601 UTC
