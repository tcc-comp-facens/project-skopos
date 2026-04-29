# Backend, API e Integração LLM

## Sumário

1. [API REST](#api-rest)
2. [WebSocket](#websocket)
3. [Integração com LLM](#integração-com-llm)
4. [Métricas de Execução](#métricas-de-execução)
5. [Métricas de Qualidade e Eficiência — Detalhamento Completo](#métricas-de-qualidade-e-eficiência--detalhamento-completo)
   - [E. Eficiência dos Agentes](#e-eficiência-dos-agentes) (E1, E2, E3)
   - [Q. Qualidade da Resposta](#q-qualidade-da-resposta) (Q1, Q2, Q2+, Q3, Q4)
   - [R. Resiliência](#r-resiliência) (R1, R2)
   - [Métricas Complementares dos Agentes Analíticos](#métricas-complementares-agentes-analíticos)
   - [Resumo de Valores-Alvo](#resumo-de-valores-alvo)
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
  },
  "useLlm": true,
  "useLlmJudge": false
}
```

| Campo | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `dateFrom` | `int` | `2019` | Ano inicial do período |
| `dateTo` | `int` | `2021` | Ano final do período |
| `healthParams` | `object` | — | Parâmetros de saúde (pelo menos um `true`) |
| `useLlm` | `bool` | `true` | Se `true`, usa LLM para síntese textual; se `false`, gera texto estruturado (fallback) |
| `useLlmJudge` | `bool` | `false` | Se `true`, habilita avaliação Q2+ (LLM-as-Judge) durante o cálculo de métricas de qualidade. Consome 1 chamada LLM extra por topologia |

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

**Query parameters:**
- `use_llm_judge` (bool, default `false`) — habilita avaliação Q2+ via LLM-as-Judge (consome 1 chamada LLM extra por topologia)

**Cache:**
- Resultado cacheado em `active_results` para requests subsequentes sem `use_llm_judge`
- Requests com `use_llm_judge=true` sempre recomputam as métricas (ignora cache)

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
  "type": "chunk" | "done" | "error" | "metric" | "quality_metrics" | "llm_judge" | "llm_judge_done",
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
| `llm_judge` | `both` | `""` | Notificação de início do LLM Judge |
| `llm_judge` | `both` | `string` | Fragmento do resultado LLM-as-Judge (~80 chars) |
| `llm_judge_done` | `both` | `""` | Streaming do LLM Judge concluído |

### Ciclo de Vida do WebSocket

1. Cliente conecta em `/ws/{analysisId}`
2. Servidor aceita e busca a `Queue` ativa
3. Loop: consome eventos da queue com timeout de 1s
4. Captura métricas de agentes dos eventos `metric`
5. Envia cada evento como JSON ao cliente
6. Encerra quando recebe 2 eventos `done` (um por arquitetura)
7. Computa `quality_metrics` **sem LLM Judge** (rápido) e envia como evento
8. Gera relatório comparativo e faz streaming em chunks de 80 chars
9. Envia evento `done` final com `architecture: "both"`
10. Se `use_llm_judge=true` e `use_llm=true`: envia evento `llm_judge` inicial com payload vazio (notifica frontend que o LLM Judge está iniciando), executa `compute_faithfulness_llm` por topologia (apenas Q2+, sem recomputar todas as métricas), armazena resultado em `active_results`, e faz streaming do resultado formatado em chunks via eventos `llm_judge` seguido de `llm_judge_done`
11. Em desconexão: limpa `active_queues` e `active_threads` (mantém `active_results`)

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
| Max retries | 2 (por modelo, antes de avançar para o próximo da cadeia) |
| Base delay | 10 segundos |
| Backoff | `delay × (attempt + 1)` (linear) |
| Erros retryable | 429, `RESOURCE_EXHAUSTED`, `rate_limit` |
| Erros fatais | Qualquer outro erro → avança para o próximo modelo da cadeia |

### Modos de geração

O cliente LLM oferece dois modos de geração:

| Modo | Função | Descrição |
|------|--------|-----------|
| **Batch** | `generate(prompt, model?)` | Retorna o texto completo de uma vez. Usado pelo `AgenteSintetizador` (fallback) e pela avaliação Q2+ (LLM-as-Judge). |
| **Streaming** | `generate_stream(prompt, model?)` | Yield de tokens incrementalmente conforme chegam da API. Usado pelo `AgenteSintetizador` para streaming em tempo real via WebSocket. |

Ambos os modos compartilham o mesmo lock global, rate limiting, cadeia de fallback entre modelos e retry em caso de 429.

### Pós-processamento de respostas

Modelos de raciocínio (ex: Qwen3) incluem tags `<think>...</think>` com o processo de pensamento na resposta. O cliente LLM remove automaticamente essas tags em ambos os modos:
- **Batch** (`generate`): via `re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)` após receber a resposta completa
- **Streaming** (`generate_stream`): via buffer incremental que detecta `<think>` e suprime tokens até encontrar `</think>`, garantindo que apenas o conteúdo final seja enviado ao consumidor em tempo real

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

## Métricas de Qualidade e Eficiência — Detalhamento Completo

**Arquivo:** `backend/core/quality_metrics.py`

Calculadas automaticamente após ambas as topologias completarem, organizadas em 3 eixos com 9 métricas individuais. Cada métrica é descrita abaixo com sua fórmula, significado, valores-alvo e contribuição para a comparação entre as topologias Estrela e Hierárquica.

### Visão Geral

| Eixo | Métricas | Pergunta que responde |
|------|----------|-----------------------|
| **E. Eficiência** | E1, E2, E3 | Qual topologia usa melhor seus recursos computacionais e de comunicação? |
| **Q. Qualidade** | Q1, Q2, Q2+, Q3, Q4 | Os resultados são corretos, fiéis aos dados e bem estruturados? |
| **R. Resiliência** | R1, R2 | O sistema se comporta bem quando algo falha? |

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

#### E3 — Eficiência de Comunicação

**Função:** `compute_communication_efficiency(message_count, num_agents)`

**O que mede:** O overhead de comunicação por agente, indicando quão "tagarela" é a topologia.

**Como é calculado:**

```
messages_per_agent = total_messages / num_agents
```

Onde:
- `total_messages` = valor do `MessageCounter` (cada chamada de método entre agentes = 2 mensagens: ida + volta)
- `num_agents` = 8 (estrela) ou 11 (hierárquica: 8 agentes + 3 supervisores)

**Contagem esperada de mensagens:**
- **Estrela:** ~16 mensagens (8 agentes × 2 mensagens cada) → ~2.0 msg/agente
- **Hierárquica:** ~24+ mensagens (agentes + supervisores + comunicação lateral entre supervisores) → ~2.2+ msg/agente

**Valores-alvo:**
- **Estrela:** 2.0 msg/agente (ideal — comunicação mínima hub-spoke)
- **Hierárquica:** 2.0-3.0 msg/agente é bom, > 3.5 indica overhead de comunicação lateral excessivo

**Significado para o TCC:** Demonstra quantitativamente que a topologia hierárquica requer mais comunicação por agente devido à comunicação lateral entre supervisores (`receive_from_peer()`). Permite comparar o trade-off: mais mensagens na hierárquica compram degradação graciosa e escalabilidade.

---

### Q. Qualidade da Resposta

#### Q1 — Consistência Determinística

**Função:** `compute_deterministic_consistency(star_result, hier_result)`

**O que mede:** Se ambas as topologias produzem resultados numéricos idênticos quando alimentadas com os mesmos dados de entrada.

**Como é calculado:**

1. Extrai `correlacoes` e `anomalias` de cada resultado
2. Normaliza cada lista ordenando por chave natural:
   - Correlações: `(subfuncao, tipo_indicador, pearson, spearman, kendall)`
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

#### Q2 — Fidelidade (Faithfulness) — Checklist Automático

**Função:** `compute_faithfulness(correlacoes, anomalias, texto)`

**O que mede:** Se o texto gerado pelo `AgenteSintetizador` (via LLM) reflete fielmente os dados numéricos calculados pelos agentes analíticos. Verifica se o texto não "inventa" informações nem omite achados importantes.

**Como é calculado:**

Cria um checklist de "checkpoints" e verifica cada um no texto:

**Para cada correlação com classificação "alta" (|Spearman| ≥ 0.7):**
- Verifica se o texto menciona o número da subfunção (ex: "301"), OU o nome da subfunção (ex: "Atenção Básica"), OU o tipo de indicador (ex: "vacinacao")
- Se encontrou → hit; senão → miss

**Para cada anomalia detectada:**
- Verifica se o texto menciona o ano da anomalia E (o número da subfunção OU o nome OU o tipo de indicador)
- Ambas as condições devem ser verdadeiras → hit; senão → miss

```
score = hits / total_checkpoints
```

Se não há checkpoints (nenhuma correlação alta e nenhuma anomalia), o score é 1.0 (vacuamente verdadeiro).

**Valores retornados:**
- `score`: 0.0 a 1.0
- `total_checkpoints`: número de itens verificados
- `hits`: quantos foram encontrados no texto
- `misses`: quantos não foram encontrados
- `details`: lista detalhada de cada checkpoint com `found: true/false`

**Valores-alvo:**
- ≥ 0.80 (80%): bom — o texto menciona a maioria dos achados relevantes
- ≥ 0.60 (60%): aceitável — algumas omissões
- < 0.60 (60%): ruim — o texto omite muitos achados importantes

**Significado para o TCC:** Mede a qualidade do output do LLM. Se o sintetizador gera texto que não menciona uma correlação forte entre gasto em Atenção Básica e cobertura vacinal, o texto é infiel aos dados. Permite comparar se uma topologia produz textos mais fiéis que a outra (embora ambas usem o mesmo sintetizador, o contexto passado pode diferir em caso de falhas parciais).

---

#### Q2+ — Fidelidade via LLM-as-Judge (Opcional)

**Função:** `compute_faithfulness_llm(correlacoes, anomalias, contexto_orcamentario, texto)`

**O que mede:** Avaliação qualitativa da fidelidade do texto usando o próprio LLM como juiz, complementando o checklist automático (Q2).

**Como é calculado:**

1. Monta um prompt com todos os dados numéricos (correlações, anomalias, contexto orçamentário) e o texto gerado
2. Pede ao LLM que avalie a fidelidade numa escala de 1 a 5
3. Extrai o JSON da resposta via regex `\{[^}]+\}`

**Escala de avaliação:**

| Score | Significado |
|-------|-------------|
| 1 | Texto contradiz os dados |
| 2 | Texto omite a maioria dos achados |
| 3 | Texto parcialmente fiel, com omissões significativas |
| 4 | Texto majoritariamente fiel, com omissões menores |
| 5 | Texto completamente fiel aos dados |

**Valores retornados:**
- `method`: `"llm_as_judge"`
- `score`: 1 a 5 (ou 0 se LLM indisponível)
- `justificativa`: explicação do LLM
- `raw_response`: resposta bruta

**Valor-alvo:** ≥ 4

**Pré-condição:** Requer `use_llm=True` (análise com texto gerado por LLM). Quando `use_llm=False`, a avaliação Q2+ é desabilitada automaticamente, independente do valor de `use_llm_judge`, pois não faz sentido avaliar fidelidade semântica de texto gerado pelo fallback estruturado.

**Significado para o TCC:** Oferece uma avaliação mais nuançada que o checklist automático. O checklist verifica presença textual (mencionou ou não), enquanto o LLM-as-judge avalia se o texto é semanticamente correto e coerente. Desabilitado por padrão para economizar chamadas LLM (rate limits do Groq free tier).

---

#### Q3 — Completude (Completeness)

**Função:** `compute_completeness(correlacoes, anomalias, contexto_orcamentario, texto)`

**O que mede:** Se TODOS os achados relevantes aparecem no texto, não apenas os mais importantes. Diferente de Q2 (que verifica se o que está no texto é verdadeiro), Q3 verifica se tudo que deveria estar no texto está lá.

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

**Significado para o TCC:** Complementa Q2 medindo abrangência. Um texto pode ser fiel (Q2 alto) mas incompleto (Q3 baixo) se menciona corretamente apenas metade dos achados. Os pesos refletem a importância relativa: correlações e anomalias são o core da análise (40% cada), enquanto o contexto orçamentário é complementar (20%).

---

#### Q4 — Qualidade Estrutural

**Função:** `compute_structural_quality(texto)`

**O que mede:** Se o texto gerado segue a estrutura esperada com as 4 seções obrigatórias definidas no prompt do sintetizador.

**Como é calculado:**

Busca palavras-chave no texto (case-insensitive) para cada seção esperada:

| Seção | Palavras-chave buscadas |
|-------|------------------------|
| `resumo_executivo` | "resumo executivo", "resumo", "executive summary" |
| `correlacoes` | "correlações", "correlacoes", "análise das correlações", "correlação" |
| `anomalias` | "anomalias", "discussão das anomalias", "anomalia", "ineficiências" |
| `contexto_orcamentario` | "contexto orçamentário", "orcamentario", "tendência", "tendencia" |

```
score = seções_encontradas / 4
```

**Valores retornados:**
- `score`: 0.0, 0.25, 0.50, 0.75 ou 1.0
- `sections_found`: 0 a 4
- `sections_expected`: 4
- `sections`: dict com `true`/`false` por seção

**Valor-alvo:** 1.0 (100%) — todas as 4 seções presentes

**Significado para o TCC:** Garante que o LLM seguiu o prompt estruturado. Se o score é < 1.0, indica que o LLM ignorou parte da instrução. Também serve como validação do fallback: quando o LLM está indisponível, o texto estruturado gerado automaticamente deve sempre atingir 1.0.

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
| `texto_analise` | `AgenteSintetizador` |

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

#### R2 — Degradação Graciosa

**Função:** `compute_graceful_degradation(full_result, degraded_result)`

**O que mede:** Quanto da qualidade do resultado é preservada quando um agente falha, comparando o resultado completo (sem falhas) com um resultado degradado (com falha simulada).

**Como é calculado:**

1. Calcula R1 para o resultado completo e para o resultado degradado
2. Calcula a taxa de preservação:

```
preservation_score = score_degradado / score_completo
```

3. Adicionalmente, compara a preservação de correlações e anomalias:

```
corr_preserved = num_correlações_degradado / num_correlações_completo
anom_preserved = num_anomalias_degradado / num_anomalias_completo
```

**Valores retornados:**
- `preservation_score`: 0.0 a 1.0 (1.0 = sem degradação)
- `correlacoes_preserved`: 0.0 a 1.0
- `anomalias_preserved`: 0.0 a 1.0
- `full_coverage`: resultado de R1 para o resultado completo
- `degraded_coverage`: resultado de R1 para o resultado degradado

**Valores-alvo:**
- `preservation_score` ≥ 0.85: excelente — sistema mantém quase toda a funcionalidade
- `preservation_score` ≥ 0.70: bom — degradação controlada
- `preservation_score` < 0.50: ruim — falha cascata

**Significado para o TCC:** Esta é a métrica mais importante para diferenciar as topologias em cenários de falha. A hierárquica, com 3 supervisores independentes, pode continuar operando se um supervisor falha (ex: `SupervisorContexto` falha → perde apenas contexto orçamentário, mas correlações e anomalias permanecem). A estrela, com ponto único de falha, tende a perder mais componentes. Permite argumentar que a hierárquica é mais adequada para ambientes de produção onde falhas são esperadas.

---

### Métricas Complementares (Agentes Analíticos)

Além das métricas de qualidade do módulo `quality_metrics.py`, os agentes analíticos produzem métricas de domínio que alimentam as métricas de qualidade:

#### Correlações Estatísticas (`AgenteCorrelacao`)

**Arquivo:** `backend/agents/analytical/correlacao.py`

Três coeficientes calculados por par subfunção-indicador:

| Coeficiente | Fórmula (via scipy) | O que mede | Quando usar |
|-------------|---------------------|------------|-------------|
| **Pearson** | `scipy.stats.pearsonr(x, y)` | Relação linear entre duas variáveis | Dados normalmente distribuídos, relação linear |
| **Spearman** | `scipy.stats.spearmanr(x, y)` | Relação monotônica baseada em ranks | Dados não-normais, relação monotônica não-linear |
| **Kendall Tau-b** | `scipy.stats.kendalltau(x, y)` | Concordância entre pares ordenados | Amostras pequenas, dados ordinais |

**Classificação (baseada em |Spearman|):**

| Faixa | Classificação |
|-------|---------------|
| \|r\| ≥ 0.7 | `"alta"` |
| \|r\| ≥ 0.4 | `"média"` |
| \|r\| < 0.4 | `"baixa"` |

**Tratamento de edge cases:**
- < 2 pontos de dados → retorna 0.0 para todos os coeficientes, classificação `"baixa"`
- Arrays constantes (scipy retorna NaN) → retorna 0.0
- Resultado clamped a [-1.0, 1.0]

**Significado para o TCC:** Spearman é a referência principal porque é robusto a outliers e não assume linearidade — adequado para dados de gastos públicos que podem ter variações abruptas. Pearson complementa para relações lineares, e Kendall é ideal para as amostras pequenas típicas (3-5 anos de dados).

---

#### Detecção de Anomalias (`AgenteAnomalias`)

**Arquivo:** `backend/agents/analytical/anomalias.py`

**Método:** Comparação com mediana por par subfunção-indicador.

Para cada par (subfunção, tipo_indicador) com ≥ 2 pontos:
1. Calcula a mediana das despesas e a mediana dos indicadores
2. Para cada ano, classifica:

| Condição | Tipo de anomalia | Interpretação |
|----------|-----------------|---------------|
| despesa > mediana E indicador < mediana | `alto_gasto_baixo_resultado` | Possível ineficiência |
| despesa < mediana E indicador > mediana | `baixo_gasto_alto_resultado` | Possível eficiência |

**Significado para o TCC:** Identifica anos onde o gasto e o resultado divergem do padrão. Um ano com gasto acima da mediana mas indicador abaixo sugere ineficiência na aplicação dos recursos. Essas anomalias são o principal achado analítico do sistema e alimentam tanto o texto do sintetizador quanto as métricas de fidelidade (Q2) e completude (Q3).

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

`compute_all_quality_metrics()` calcula todas as métricas de uma vez e retorna dict organizado por eixo, pronto para envio via WebSocket. Parâmetros fixos: estrela = 8 agentes, hierárquica = 11 agentes (8 + 3 supervisores). Aceita `use_llm` (default `True`) que, quando `False`, desabilita a avaliação Q2+ (LLM-as-Judge) independente de `use_llm_judge`, pois não faz sentido avaliar fidelidade semântica de texto gerado pelo fallback estruturado.

### Resumo de Valores-Alvo

| Métrica | Valor-alvo | Interpretação |
|---------|-----------|---------------|
| E1 Overhead | Estrela ~0%, Hierárquica < 15% | Custo aceitável de coordenação |
| E2 Breakdown | Fase analítica 30-50% | LLM é o gargalo esperado |
| E3 Comunicação | ≤ 2.0 msg/agente (estrela), ≤ 3.0 (hierárquica) | Comunicação eficiente |
| Q1 Consistência | `true` (sempre) | Resultados numéricos idênticos |
| Q2 Fidelidade | ≥ 0.80 | Texto reflete os dados |
| Q2+ LLM Judge | ≥ 4 (de 5) | Avaliação semântica positiva |
| Q3 Completude | ≥ 0.75 | Texto abrangente |
| Q4 Estrutura | 1.0 (4/4 seções) | Texto bem organizado |
| R1 Cobertura | 1.0 (7/7 componentes) | Pipeline completo |
| R2 Degradação | ≥ 0.85 | Resiliência a falhas |

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
