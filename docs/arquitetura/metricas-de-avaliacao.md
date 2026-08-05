# Métricas de Avaliação — Arquitetura e Justificativa Acadêmica

> Este documento cobre o módulo de métricas comparativas entre as topologias estrela e hierárquica: o que cada métrica mede, como é calculada (com o código real) e — seguindo o mesmo padrão da Seção 3 ("Justificativa Acadêmica por Decisão") do [`PLANO_REFATORACAO.md`](../../PLANO_REFATORACAO.md) — qual é o embasamento acadêmico de cada uma, com citação direta da fonte ou a marcação explícita **"decisão de engenharia, sem embasamento acadêmico direto"** quando não há fonte (nunca uma citação inventada). Para o pipeline que produz os dados que essas métricas consomem, ver [arquitetura-estrela.md](arquitetura-estrela.md), [arquitetura-hierarquica.md](arquitetura-hierarquica.md) e [ciclo-completo.md](ciclo-completo.md).

**Arquivos principais:** `backend/core/quality_metrics.py`, `backend/core/llm_client.py` (`TokenBucket`), `backend/core/guardrail_stats.py`, `backend/agents/domain/query_planning.py` (contadores de cache), `backend/core/claim_verifier.py` (reaproveitado por Q2 claim-based).

## Visão geral — seis eixos

O módulo evoluiu em duas fases. Os três eixos originais (E/Q/R) existiam antes do plano de refatoração; os três eixos novos foram adicionados na Etapa 6 do `PLANO_REFATORACAO.md`, depois que as Etapas 1–5 já tinham dado aos agentes os pontos de decisão via LLM que essas métricas passaram a medir.

```
core/quality_metrics.py
├── A. Eficiência dos Agentes         E1  compute_coordination_overhead
│                                     E2  compute_latency_breakdown
├── B. Qualidade da Resposta          Q1  compute_deterministic_consistency
│                                     Q2  compute_faithfulness (substring | claim-based)
│                                     Q2* compute_faithfulness_llm (LLM-as-judge, 1-5)
│                                     Q3  compute_completeness
├── C. Resiliência                    R1  compute_partial_result_coverage
├── D. Custo e Comunicação (Etapa 6)  compute_token_cost
│                                     compute_communication_volume
└── E. Outcome agregado (Etapa 6)     compute_analysis_success

core/guardrail_stats.py               compute_guardrail_rejection_rate   (process-wide)
agents/domain/query_planning.py       compute_cache_hit_rate             (process-wide)
```

A distinção entre **por-análise** (as seis primeiras — calculadas a cada comparação estrela-vs-hierárquica, no payload de `/api/analysis/{id}/quality`) e **process-wide** (as duas últimas — contadores acumulados ao longo de várias análises, expostas em `GET /api/metrics/guardrail` e `GET /api/metrics/query-planning-cache`) é proposital: guardrail e cache não descrevem uma análise isolada, descrevem uma tendência de comportamento do sistema ao longo do tempo.

## Caminho rápido vs. caminho opcional (onde o custo de cada métrica entra)

Nem toda métrica custa a mesma coisa. `api/websocket.py` calcula as métricas em dois momentos distintos:

```python
# 1. Caminho rápido — sempre roda, sem LLM extra
quality = compute_all_quality_metrics(
    star_result=star_result, hier_result=hier_result,
    star_agent_metrics=..., hier_agent_metrics=...,
    use_llm_judge=False,  # Q2 fica no modo substring (gratuito)
    ...
)
# → enviado ao cliente como evento "quality_metrics", ANTES do relatório

# 2. Caminho opcional — só quando use_llm_judge=True (mesma flag do usuário)
if use_llm_judge and use_llm:
    judge_result = compute_faithfulness_llm(...)       # score 1-5
    claims_result = compute_faithfulness(..., use_llm=True)  # claim-based
```

Isso significa que E1, E2, Q1, Q3, R1, custo de tokens (D), comunicação (D) e outcome (E) **sempre** são calculados e não envolvem LLM — são determinísticos e gratuitos. Só Q2 tem dois modos: o modo padrão (substring, sempre ativo) e um modo opcional mais caro (claim-based + LLM-as-judge, só quando o usuário pede fidelidade avaliada por LLM). Essa divisão é ela mesma uma decisão de engenharia, não uma prescrição acadêmica — ver D8 abaixo.

---

## Eixo A — Eficiência dos Agentes

### E1 — Overhead de coordenação

```python
def compute_coordination_overhead(agent_metrics: list[dict]) -> dict[str, Any]:
    supervisor_time = 0.0
    worker_time = 0.0
    for m in agent_metrics:
        if m.get("agentName", "") in FASE_SUPERVISORES:
            supervisor_time += m.get("executionTimeMs", 0)
        else:
            worker_time += m.get("executionTimeMs", 0)
    total = supervisor_time + worker_time
    ratio = supervisor_time / total if total > 0 else 0.0
    return {"supervisor_time_ms": ..., "worker_time_ms": ..., "overhead_ratio": ratio, "overhead_percent": ratio * 100}
```

Separa o tempo gasto em agentes de coordenação (`FASE_SUPERVISORES = {supervisor_dominio, supervisor_analitico, supervisor_contexto, orquestrador_estrela, coordenador_geral}`) do tempo gasto em agentes de trabalho. Na estrela, o overhead tende a ~0% (o orquestrador não aparece como entrada separada de `agent_metrics` — ver [arquitetura-estrela.md](arquitetura-estrela.md#7-persistir_metricas)); na hierárquica, mede o custo real da camada de supervisores (ver [arquitetura-hierarquica.md](arquitetura-hierarquica.md#contabilização-de-métricas-e-overhead)).

### E2 — Breakdown de latência por fase

Divide o tempo total em 4 fases (`dominio`, `analitico`, `contexto`, `supervisores`), usando os mesmos conjuntos `FASE_DOMINIO`/`FASE_ANALITICO`/`FASE_CONTEXTO`/`FASE_SUPERVISORES` — note que `FASE_ANALITICO` inclui `"priorizacao"` e `"verificacao"` (Etapas 3 e 4), acompanhando a evolução do pipeline analítico. Retorna tempo absoluto e percentual de cada fase, usado no relatório comparativo para mostrar onde cada topologia gasta seu tempo.

**Nenhuma das duas métricas usa limiar fixo** (ex.: "overhead > 30% é ruim") — a comparação é sempre relativa entre as duas topologias na mesma análise, nunca contra um corte absoluto. Essa é uma decisão deliberada (D12, ver tabela).

---

## Eixo B — Qualidade da Resposta

### Q1 — Consistência determinística

```python
def compute_deterministic_consistency(star_result, hier_result) -> dict[str, Any]:
    corr_identical = _sort_corr(star_result["correlacoes"]) == _sort_corr(hier_result["correlacoes"])
    anom_identical = _sort_anom(star_result["anomalias"]) == _sort_anom(hier_result["anomalias"])
    return {"all_identical": corr_identical and anom_identical, "divergences": [...]}
```

As duas topologias processam os mesmos dados brutos com os mesmos algoritmos determinísticos (Spearman, mediana) — correlações e anomalias devem ser **numericamente idênticas**, independente de qual ângulo de ênfase a Etapa 3 escolheu ou qual texto a Etapa 4 corrigiu (por isso `result["correlacoes"]`/`result["anomalias"]` retornados pelos orquestradores são sempre os dados brutos, nunca a versão reordenada — ver nota em ambos os documentos de arquitetura). Uma divergência aqui indica bug ou não-determinismo real, não uma diferença de interpretação — é a métrica mais "dura" do sistema: não há ambiguidade sobre o que conta como sucesso.

### Q2 — Fidelidade (dois modos + LLM-as-judge)

Três implementações coexistem, todas medindo a mesma pergunta ("o texto reflete os dados?") por métodos diferentes:

**Modo substring (padrão, `use_llm=False`)** — checklist determinístico: para cada correlação "alta" e cada anomalia, verifica se a subfunção/indicador (e, para anomalias, o ano) aparece no texto gerado. `score = hits / total`. Método pré-existente, mantido como caminho rápido.

**Modo claim-based (`use_llm=True`, Etapa 6/D8)** — reaproveita `core/claim_verifier.py` (Etapa 4):

```python
def _compute_faithfulness_claims(correlacoes, anomalias, contexto_orcamentario, texto, caller):
    claims = extract_claims(texto, caller=caller)          # LLM: extrai afirmações discretas
    verificacoes = verify_claims(claims, dados, caller=caller)  # LLM: cada uma é suportada?
    hits = sum(1 for v in verificacoes if v.get("suportado"))
    score = hits / total if total > 0 else 1.0
    return {"score": score, "method": "claim_based", "details": verificacoes}
```

`score = claims_suportados / total_claims` — estilo RAGAS (D8). Ao contrário do modo substring (que só verifica se as *palavras certas* aparecem, sem checar se a afirmação ao redor delas é verdadeira), o modo claim-based extrai a afirmação inteira e pede para o LLM julgá-la contra os dados numéricos brutos — o mesmo mecanismo que a Etapa 4 usa para *corrigir* o texto, aqui usado só para *medir*, sem a passada de revisão.

**LLM-as-judge (`compute_faithfulness_llm`)** — terceiro método, complementar aos dois acima: pede ao LLM uma nota holística de 1 a 5 (não uma fração de claims) cobrindo acurácia, cobertura e coerência numa única chamada. Roda sempre junto do modo claim-based, sob o mesmo gate `use_llm_judge`.

### Q3 — Completude

```python
score = corr_cov * 0.4 + anom_cov * 0.4 + ctx_cov * 0.2
```

Enquanto Q2 pergunta "o que está no texto é verdade?", Q3 pergunta "tudo que deveria estar no texto está lá?" — cobertura de correlações, anomalias (por palavra-chave de categoria, não claim-based) e contexto orçamentário, com pesos fixos (40/40/20). Os pesos são uma escolha de engenharia sem citação — não há fonte que prescreva essa ponderação específica.

---

## Eixo C — Resiliência

### R1 — Cobertura de resultado parcial

```python
components = {"despesas": bool(...), "indicadores": bool(...), "dados_cruzados": bool(...),
              "correlacoes": bool(...), "anomalias": bool(...), "contexto_orcamentario": bool(...),
              "texto_analise": bool(...)}
score = completed / total  # total = 7
```

Mede quantos dos 7 componentes do resultado estão presentes e não-vazios — reflete diretamente a degradação graciosa que ambas as arquiteturas implementam (falha de um agente não derruba a análise inteira, ver seções de degradação graciosa em [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-hierarquica.md](arquitetura-hierarquica.md)). Base de `compute_analysis_success` (Eixo E).

---

## Eixo D — Custo e Comunicação (Etapa 6, novo)

### Custo de tokens

```python
def compute_token_cost(token_usage: dict[str, int] | None) -> dict[str, Any]:
    usage = token_usage or {}
    return {"prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": ...,
            "total_tokens": ..., "call_count": ...}
```

Função quase trivial — o trabalho real está em capturar o `token_usage` corretamente por segmento, o que exigiu resolver um problema de concorrência real antes de implementar a métrica (ver caixa "Pré-requisito" abaixo).

**Pré-requisito técnico: `TokenBucket`.** Estrela e hierárquica rodam em threads concorrentes (`api/runners.py`); um contador global simples não distingue qual topologia gastou quais tokens. A solução implementada:

```python
# core/llm_client.py
_current_bucket: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(..., default=None)

class TokenBucket:
    def __enter__(self):
        self._token = _current_bucket.set(self.usage)
        return self
    def __exit__(self, *exc):
        _current_bucket.reset(self._token)
    def snapshot(self):
        return dict(self.usage)
```

Cada chamador ativa seu próprio "balde" (`with TokenBucket() as bucket: orchestrator.run(...)`) e qualquer chamada a `generate`/`generate_stream` feita dentro desse escopo — mesmo em profundidade, através de vários agentes subordinados — acumula ali. Um `ContextVar` (não thread-local puro) foi escolhido porque thread-local atribuiria tudo que roda na `MainThread` (interpretação de intenção **e** LLM Judge) ao mesmo balde; `ContextVar` permite múltiplos buckets sequenciais na mesma thread. Uso real, em `api/runners.py`:

```python
token_bucket = TokenBucket()
with token_bucket:
    result = orchestrator.run(analysis_id, params, ws_queue)
active_results[analysis_id]["star_token_usage"] = token_bucket.snapshot()
```

O resultado final no payload separa 4 segmentos: `cost.star`, `cost.hierarchical`, `cost.intent_interpretation` (1x por mensagem de chat, anterior à bifurcação das topologias) e `cost.llm_judge` (só quando `use_llm_judge=True`).

### Volume de comunicação

```python
def compute_communication_volume(architecture, agent_metrics, despesas_count=0, indicadores_count=0):
    n_agents = len(agent_metrics)
    lateral_hops = 3 if architecture == "hierarchical" else 0
    lateral_summaries = 2 if architecture == "hierarchical" else 0
    message_count = n_agents * 2 + lateral_hops
    return {"agent_invocations": n_agents, "lateral_hops": lateral_hops,
            "lateral_summaries": lateral_summaries, "message_count": message_count,
            "payload_records": despesas_count + indicadores_count}
```

O sistema não mantém um log de mensagens brutas (sockets/filas reais) — o proxy é determinístico e real, nunca estimado: cada entrada em `agent_metrics` é 1 agente efetivamente invocado (contado como 1 chamada + 1 retorno = 2 mensagens). A hierárquica soma os 3 hops de comunicação lateral fixos entre supervisores (Etapa 5 — `Dominio→Analitico`, `Dominio→Contexto`, `Contexto→Analitico`, sempre propostos por `CoordenadorGeral.propose_actions()` independente de falha upstream) e os 2 resumos textuais semânticos que os acompanham. A estrela nunca tem comunicação lateral (hub-and-spoke por definição), então ambos ficam em 0 — o harness de escalabilidade (ver seção própria abaixo) confirma que esse overhead lateral é **fixo**, não cresce com o volume de dados.

---

## Eixo E — Outcome agregado (Etapa 6, novo, opcional)

```python
def compute_analysis_success(result, wall_clock_ms=0, time_budget_ms=DEFAULT_TIME_BUDGET_MS):
    r1 = compute_partial_result_coverage(result)
    r1_complete = r1["completed"] == r1["total"]

    self_check = result.get("self_check")
    if self_check and self_check.get("verificado"):
        claims_nao_suportadas = sum(1 for c in self_check.get("claims", []) if not c.get("suportado", True))
        self_check_ok = self_check.get("revisado", False) or claims_nao_suportadas == 0
    else:
        self_check_ok = True  # self-check não rodou (opcional) — não penaliza

    within_budget = wall_clock_ms <= time_budget_ms if wall_clock_ms > 0 else True
    success = r1_complete and self_check_ok and within_budget
    return {"success": success, "r1_complete": ..., "self_check_ok": ..., "within_time_budget": ...}
```

Métrica binária composta: **sucesso = R1 completo E nenhuma claim não-suportada remanescente no self-check (Etapa 4) E dentro do orçamento de tempo**. `DEFAULT_TIME_BUDGET_MS = 60_000.0` não é prescrito por nenhuma fonte — é um parâmetro explícito, sobrescrevível pelo caller, não uma constante escondida (mesma lógica de "sem limiares mágicos" já aplicada a E1/E2, D12). Inspiração conceitual em métricas de sucesso estruturadas em marcos (D11), fórmula própria.

---

## Métricas process-wide (fora do payload por-análise)

### Taxa de rejeição do guardrail

```python
# core/guardrail_stats.py — contador global, protegido por lock, não reseta entre análises
def compute_guardrail_rejection_rate() -> dict[str, float | int]:
    with _lock:
        total, rejected = _counts["total"], _counts["rejected_out_of_scope"]
    rate = rejected / total if total > 0 else 0.0
    return {"total_messages": total, "rejected": rejected, "rejection_rate": round(rate, 4)}
```

`chat_websocket.py` registra cada decisão do guardrail da Etapa 1 (dentro/fora de escopo) via `record_guardrail_decision()`, excluindo mensagens vazias e falhas técnicas do LLM (que não são decisões de escopo de verdade). Pensada para calibrar o prompt de classificação ao longo do tempo — o risco de falso positivo/negativo do guardrail estava registrado como não resolvido na Etapa 1; esta métrica é a instrumentação que permite monitorá-lo, não a solução em si.

### Taxa de acerto do cache de planejamento de consulta

```python
# agents/domain/query_planning.py
def compute_cache_hit_rate() -> dict[str, Any]:
    total = sum(_stats.values())
    llm_calls = _stats["llm"] + _stats["llm_failed_fallback"]
    rate = (total - llm_calls) / total if total > 0 else 1.0
    return {"total": total, "fast_path": ..., "cache": ..., "llm": ...,
            "llm_failed_fallback": ..., "cache_or_fastpath_rate": round(rate, 4)}
```

`plan_query()` (Etapa 2) já retorna a origem de cada plano (`"fast_path" | "cache" | "llm" | "llm_failed_fallback"`); esta métrica só acumula essas origens num contador. Evidencia diretamente o trade-off da Etapa 2: enquanto a base tiver mapeamento trivial e/ou a flag `USE_LLM_QUERY_PLANNING` estiver desligada, a taxa fica em 100% — nenhuma chamada LLM acontece, exatamente o comportamento pretendido hoje.

---

## Harness de escalabilidade

`backend/tests/test_scalability_benchmark.py` roda as duas topologias com dados sintéticos em dois volumes (N=3 e N=10 anos, todos os 4 subfunções/tipos de indicador ativos), `use_llm=False` (determinístico, sem custo real de API — a variação do custo de tokens com N exigiria chamadas reais, fora do escopo de um harness automatizado de CI). Produz os 2 pontos de comparação exigidos pelo critério de aceite da Etapa 6 e valida invariantes estruturais em vez de comparar contra um limiar fixo (mesma filosofia de D12):

- Q1 continua consistente em qualquer N (nenhuma divergência introduzida pelo crescimento da base).
- O overhead lateral da hierárquica (`lateral_hops`, `lateral_summaries`) é fixo em qualquer N — só o `payload_records` cresce.
- Todos os despesas/indicadores sintéticos são processados sem perda silenciosa, em ambas as escalas.

---

## Justificativa Acadêmica por Métrica

Tabela no mesmo formato da Seção 3 do `PLANO_REFATORACAO.md`, filtrada às decisões que dizem respeito diretamente às métricas (os IDs D1–D5, D13–D15 dizem respeito a decisões de arquitetura de agentes, não de medição, e por isso não estão repetidos aqui — ver o plano para o conjunto completo).

| ID | Métrica/Decisão | Fonte | Trecho/resumo do argumento |
|---|---|---|---|
| D6 | Custo de tokens por topologia (`compute_token_cost`) | Achados de busca sobre frameworks de avaliação 2025 (REALM-Bench, CLEAR) e o catálogo AGENT 2026 | "New 2025 frameworks like REALM-Bench and CLEAR prioritize real-world complexity, adding cost, latency, efficiency, assurance, and reliability metrics to production evaluation"; AGENT 2026 classifica custo na categoria "Framework". **Ressalva:** acesso só a resumos de busca, não ao texto completo. O mecanismo de captura (`TokenBucket`/`ContextVar`) é decisão de engenharia própria — a fonte motiva medir custo, não como implementar a contabilização sob concorrência. |
| D7 | Volume de comunicação (`compute_communication_volume`) | Li et al. (2024), Seção 3.4.1 ("Message delivery"); complementar: *"Beyond Self-Talk: A Communication-Centric Survey of LLM-Based Multi-Agent Systems"* (arXiv:2502.14321) | "message delivery must account for supplementary overhead, including transmission efficiency, bandwidth, and the timeliness of message delivery"; "current leaderboards remain agent-centric and rarely capture system-level properties, including coordination efficiency, communication bandwidth and latency". **Ressalva:** o proxy usado (contagem de invocações + hops laterais fixos, não um log de mensagens brutas) é decisão de engenharia própria. |
| D8 | Fidelidade claim-based substituindo/complementando checklist por substring (`compute_faithfulness(use_llm=True)`) | Metodologia RAGAS (Es et al.) e TruLens "RAG Triad" | "faithfulness = number of claims supported by the retrieved context / total claims in the answer"; groundedness como fração de sentenças com afirmação verificável contra a fonte, avaliada por LLM-juiz, não por correspondência textual literal. |
| D9 | Eixo de escalabilidade (harness de N variável) | Li et al. (2024), Seção 5.1 ("Scaling Up the Multi-Agent System") | "Scaling up multi-agent systems involves increasing the number of agents... introduces challenges related to computational resources, communication efficiency, and system coordination... static adjustment and dynamic scaling methods are widely applied." **Ressalva:** a fonte discute a necessidade de medir escalabilidade em termos gerais, não prescreve a métrica exata nem o desenho do benchmark sintético usado aqui. |
| D10 | Taxonomia E/Q/R vs. reestruturação Outcome/Process/Product/Framework (não adotada) | *"A Catalogue of Evaluation Metrics for LLM-Based Multi-Agent Frameworks in Software Engineering"*, AGENT 2026 workshop @ ICSE 2026 | Propõe 37 métricas nessas 4 categorias contra "frameworks often relying on self-defined or inconsistent metrics, hindering reproducibility". Registrada como referência; não adotada nesta versão do sistema (custo/risco de quebrar a API atual do frontend maior que o ganho para o prazo do TCC). |
| D11 | Sucesso agregado da análise (`compute_analysis_success`) | Zhu et al., *"MultiAgentBench: Evaluating the Collaboration and Competition of LLM agents"*, ACL 2025, arXiv:2503.01935 | "measures not only task completion but also the quality of collaboration and competition using novel, milestone-based key performance indicators" — inspiração conceitual (sucesso estruturado, não binário simples). **Ressalva:** a fórmula (R1 + self-check + orçamento de tempo) é decisão de engenharia própria, não replica o benchmark original (domínio de agentes de pesquisa/coding, diferente deste projeto). |
| D12 | Ausência de limiares fixos em E1/E2/outcome (comparação sempre relativa entre topologias, nunca contra um corte absoluto) | — | **Decisão de engenharia, sem embasamento acadêmico direto.** Nenhuma fonte consultada define limiar absoluto universal para overhead de coordenação ou orçamento de tempo "aceitável"; os benchmarks citados (MultiAgentBench, AGENT 2026) comparam configurações entre si. A ausência de fonte é, em si, o argumento para não fixar cortes "porque parecem razoáveis" — por isso `time_budget_ms` em `compute_analysis_success` é parâmetro explícito, não constante escondida. |
| D16 | Remoção do rate limiting próprio em `core/llm_client.py`, mantendo só retry reativo a 429 real | — | **Decisão de engenharia, sem embasamento acadêmico direto — pedido explícito do usuário.** Motivada por evidência empírica direta (log de execução real mostrando uma chamada presa 177s atrás do lock global de outra topologia em streaming), relevante para este documento porque afeta diretamente a precisão da métrica de custo de tokens sob concorrência real. |
| — | Q1 (consistência determinística), Q3 (completude), R1 (cobertura parcial), pesos de Q3 (40/40/20) | — | **Decisões de engenharia pré-existentes, sem embasamento acadêmico direto** — não fizeram parte da Etapa 6 nem de nenhuma decisão citada no plano; documentadas aqui por completude do eixo de métricas, não por terem justificativa acadêmica a reportar. |

---

## Payload final — onde cada métrica aparece

```
GET /api/analysis/{id}/quality  (e evento WebSocket "quality_metrics")
├── efficiency.{star,hierarchical}.{coordination_overhead, latency_breakdown}   E1, E2
├── quality
│   ├── deterministic_consistency                                              Q1
│   └── {star,hierarchical}.{faithfulness, completeness,                       Q2, Q3
│                             faithfulness_llm?, faithfulness_claims?}         Q2 (LLM, opcional)
├── resilience.{star,hierarchical}                                             R1
├── cost.{star,hierarchical,intent_interpretation,llm_judge?}                  D (tokens)
├── communication.{star,hierarchical}                                         D (mensagens)
└── outcome.{star,hierarchical}                                               E (sucesso)

GET /api/metrics/guardrail                                                    process-wide
GET /api/metrics/query-planning-cache                                        process-wide
```
