# Ciclo Completo — da Mensagem do Usuário ao Relatório Comparativo

> Este documento cobre o fluxo de ponta a ponta do sistema, **sem** entrar nos detalhes internos de cada topologia — esses estão em [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-hierarquica.md](arquitetura-hierarquica.md). Para a base comum a qualquer agente (ciclo CoALA, chamada ao LLM), ver [agente-coala-e-llm.md](agente-coala-e-llm.md).

**Arquivos:** `backend/api/chat_websocket.py`, `backend/api/chat_runner.py`, `backend/api/dispatch.py`, `backend/api/runners.py`, `backend/api/websocket.py`, `backend/core/quality_metrics.py`

## Visão geral

```
 1. Usuário digita mensagem no chat
              │  WS /ws/chat/{session_id}
              ▼
 2. AgenteInterpretacaoIntencao.parse(texto)
    ├── fora do escopo → esclarecimento, encerra o turno
    └── dentro do escopo → AnalysisIntent (params + intent_summary)
              │
              ▼
 3. dispatch_analysis()
    ├── persiste Analise no Neo4j (relaciona despesas/indicadores do período)
    └── dispara 2 threads paralelas
              │
       ┌──────┴──────┐
       ▼             ▼
 4. run_star()   run_hierarchical()
    (thread A)    (thread B)
       │             │
       └──────┬──────┘
              │  ambas escrevem na mesma ws_queue
              ▼
 5. WS /ws/{analysisId} — streaming
    ├── eventos chunk/metric/done de cada arquitetura
    ├── quando as duas terminam: compute_all_quality_metrics()
    ├── generate_comparative_report() (streaming)
    └── opcional: LLM Judge (compute_faithfulness_llm)
```

Duas conexões WebSocket distintas cobrem o ciclo: `/ws/chat/{session_id}` (turno de conversa — 1 pergunta, 1 confirmação) e `/ws/{analysisId}` (streaming dos resultados da análise disparada). São desacopladas de propósito: o chat só inicia a análise e devolve o `analysisId`; quem efetivamente consome os eventos de progresso é o segundo WebSocket, aberto pelo frontend logo em seguida.

## 1–2. Interpretação de intenção (guardrail de escopo)

`chat_websocket.py` recebe `{"type": "user_message", "payload": {"text", "useLlm", "useLlmJudge"}}`, valida tamanho e uma rodada por vez por sessão, e delega a interpretação a `AgenteInterpretacaoIntencao.parse(texto)` — único ponto do sistema que decide se uma mensagem está dentro do escopo (dados de saúde/orçamento de Sorocaba) antes de qualquer outro processamento acontecer. Se fora do escopo, o usuário recebe uma mensagem de esclarecimento e nenhuma análise é disparada. O mecanismo de classificação, extração de parâmetros e chamada ao LLM está detalhado em [agente-coala-e-llm.md](agente-coala-e-llm.md#exemplo-completo-agenteinterpretacaointencao).

Quando o resultado é bem-sucedido (`result.success`), `chat_runner.run_chat_analysis()` chama `dispatch_analysis()` (mesma função usada pelo endpoint REST legado `POST /api/analysis`, garantindo que os dois caminhos de entrada produzam análises idênticas em estrutura).

## 3. `dispatch_analysis()` — ponto de entrada compartilhado

```python
# backend/api/dispatch.py
def dispatch_analysis(date_from, date_to, health_params, use_llm, use_llm_judge,
                       source_question=None, interpreted_via=None, intent_summary=None) -> str:
    analysis_id = str(uuid.uuid4())
    # persiste Analise + relaciona DespesaSIOPS/IndicadorDataSUS do período no Neo4j
    ws_queue = Queue()
    active_queues[analysis_id] = ws_queue
    params = {date_from, date_to, health_params, use_llm, use_llm_judge, intent_summary}
    threading.Thread(target=run_star, args=(analysis_id, params, ws_queue), daemon=True).start()
    threading.Thread(target=run_hierarchical, args=(analysis_id, params, ws_queue), daemon=True).start()
    return analysis_id
```

Ponto central: as duas topologias recebem exatamente o mesmo `params` (incluindo o mesmo `intent_summary`, quando a análise vem do chat) e escrevem na **mesma fila** (`ws_queue`) — é essa fila compartilhada que permite ao WebSocket de resultados multiplexar os eventos das duas execuções concorrentes num único stream, taggeados por `architecture`.

## 4. Execução paralela — `run_star()` / `run_hierarchical()`

Cada runner (`backend/api/runners.py`) roda em uma thread daemon dedicada, instancia o orquestrador/coordenador correspondente com um `agent_id` único por execução (`star-orch-{uuid}` / `hier-coord-{uuid}`), chama `.run(analysis_id, params, ws_queue)`, persiste o resultado em `active_results[analysis_id]` e no Neo4j, e por fim envia um evento `{"type": "done", "architecture": "star"|"hierarchical"}` na `ws_queue` — inclusive em caso de exceção (o `except` também envia `done`, para que o WebSocket de resultados nunca fique esperando indefinidamente por uma arquitetura que falhou).

O que acontece **dentro** de cada `.run()` é o pipeline específico de cada topologia — ver os documentos dedicados.

## 5. `/ws/{analysisId}` — streaming e pós-processamento

`api/websocket.py` consome a `ws_queue` compartilhada e repassa cada evento (`chunk`, `metric`, `done`, `error`) ao cliente em tempo real, contando quantas arquiteturas já enviaram `done` (encerra quando `done_count == 2`). Durante o streaming, captura de cada evento `metric` o `agentMetrics`/`totalExecutionTimeMs` de cada arquitetura — dados que alimentam a etapa seguinte.

### Cálculo de métricas de qualidade

Assim que ambas terminam, com os resultados completos (`star_result`/`hier_result`) disponíveis:

```python
quality = compute_all_quality_metrics(
    star_result, hier_result, star_agent_metrics, hier_agent_metrics,
    use_llm_judge=False,  # LLM Judge roda depois, separadamente
    use_llm=results.get("use_llm", True),
    star_wall_clock_ms=..., hier_wall_clock_ms=...,
)
```

Enviado ao cliente como um evento `quality_metrics`. Internamente calcula E1 (overhead de coordenação), E2 (breakdown de latência por fase), Q1 (consistência determinística — compara `correlacoes`/`anomalias` **brutas**, não reordenadas pela priorização), Q2/Q2+ (faithfulness heurístico), Q3 (completude), R1 (cobertura de resultado parcial). `use_llm_judge=False` aqui é proposital — a avaliação por LLM roda **depois** do relatório textual, não antes, para não atrasar a entrega do relatório principal por causa de uma chamada LLM potencialmente lenta.

### Relatório comparativo

`generate_comparative_report()` monta o texto final (estrela vs. hierárquica lado a lado) a partir das métricas de qualidade + métricas de agente + cobertura de dados, e é transmitido em chunks de 80 caracteres via eventos `chunk` com `architecture: "both"`, seguido de um `done` final.

### LLM Judge (opcional, pós-relatório)

Se `use_llm_judge=True` **e** `use_llm=True` (LLM Judge nunca roda se o modo geral está com LLM desligado), o servidor envia um evento `llm_judge` (sinaliza ao frontend que uma avaliação adicional está em andamento) e chama `compute_faithfulness_llm()` uma vez para cada arquitetura — essa é a única etapa do ciclo completo em que o próprio **avaliador** usa um LLM (papel de "juiz", não de agente do pipeline) para julgar se o texto gerado é fiel aos dados numéricos que o sustentam. O resultado (score 1–5 + justificativa) é anexado a `quality_metrics` e transmitido como texto adicional em chunks, do mesmo jeito que o relatório comparativo.

## Onde o LLM entra, em ordem cronológica de uma análise via chat

| # | Etapa | Componente | Obrigatório? |
|---|-------|------------|---------------|
| 1 | Classificação de escopo + extração de parâmetros | `AgenteInterpretacaoIntencao` | Sempre (é a única via de interpretação — sem fallback regex) |
| 2 | Planejamento de consulta (busca) | Agentes de domínio (`planejar_consulta`) | Só se `USE_LLM_QUERY_PLANNING=true` **e** a base tiver crescido além do mapeamento estático conhecido — fast-path determinístico por padrão |
| 3 | Escolha do ângulo de ênfase | `AgentePriorizacaoAnalitica` | Só se `use_llm=True` na análise |
| 4 | Geração do texto de análise | `TextSynthesizer` | Só se `use_llm=True`; senão usa `generate_fallback()` determinístico |
| 5 | Avaliação de fidelidade (juiz) | `compute_faithfulness_llm` | Só se `use_llm_judge=True` **e** `use_llm=True` |

Etapas 1 e 5 rodam uma única vez por análise (a interpretação de intenção acontece antes de qualquer topologia começar); etapas 2–4 rodam **duas vezes** — uma para a execução estrela, outra para a hierárquica — de forma independente e paralela, o que é justamente o que permite comparar as duas arquiteturas sobre a mesma entrada.
