# Arquitetura Estrela — Ciclo Completo

> Este documento cobre o pipeline interno da topologia estrela, do momento em que `OrquestradorEstrela.run()` é chamado até o resultado final ser retornado. Para a base comum a qualquer agente (ciclo CoALA, chamada ao LLM), ver [agente-coala-e-llm.md](agente-coala-e-llm.md). Para o fluxo de ponta a ponta que antecede/sucede este pipeline (chat, disparo de análise, WebSocket de resultados), ver [ciclo-completo.md](ciclo-completo.md). Para o detalhamento de cada agente citado aqui, ver [../02-AGENTES.md](../02-AGENTES.md).

**Arquivo:** `backend/agents/star/orchestrator.py`

## Visão geral

Um único agente central — `OrquestradorEstrela` — coordena até 15 agentes periféricos (8 de saúde + 7 orçamentários). Nenhum agente periférico se comunica diretamente com outro: tudo passa pelo hub. `OrquestradorEstrela` herda de `AgenteCoALA` e executa pelo ciclo CoALA (`run_coala_cycle()`). A ordem das macro-ações é imposta por **dependência de dados**: o cruzamento só ocorre depois da coleta, e a síntese só depois de correlação, anomalias e contexto. Já a escolha de **quais agentes de dados acionar** é uma decisão do orquestrador, tomada com o LLM em `evaluate_and_select()` (ver [Planejamento por LLM](#planejamento-por-llm)). As outras decisões por LLM ficam nos agentes: a deliberação de dimensão de consulta em cada agente de saúde e de orçamento, e a escolha do ângulo em `AgentePriorizacaoAnalitica`.

```
                         OrquestradorEstrela
                              (Hub)
                               │
   ┌────┬────┬────┬────┬────┬────┬────┬────┬── até 7 ──┬───────┬───────┬──────┐
   ▼    ▼    ▼    ▼    ▼    ▼    ▼    ▼         orçamentários  ▼       ▼      ▼
 SINAN SIH  SIM SIPNI COVID SINASC SIA CNES   (122,301-306)  Contex. Analit. Prior. Sintet.
  (até 8, ativação condicional por health_params)             (CAGR) (stats) (LLM)  (LLM)
   │    │    │    │    │    │    │    │              │           │      │       │      │
   └────┴────┴────┴────┴────┴────┴────┴──────────────┘           │      │       │      │
               │                                                 │      │       │      │
          despesas + indicadores                                 │      │       │      │
               ├──── cross_domain_data() ────────────────────────┤      │       │      │
               │     (dados cruzados)                            │      │       │      │
               └──────────────────────────────────────────────────┴──────┘       │      │
                                                                                  │      │
                    correlações + anomalias + contexto ──────────────────────────┘      │
                                        (escolhe ângulo de ênfase) ──────────────────────┘
                                                                                         │
                                                                                  texto (streaming)
                                                                                         │
                                                                          self-check (opcional, Etapa 4)
```

## Ativação condicional dos agentes

`OrquestradorEstrela` usa o mapeamento `INDICADOR_TO_AGENT` para instanciar **só** os agentes de saúde relevantes aos `health_params` da análise (extraídos pelo `AgenteInterpretacaoIntencao` — o chat é hoje o único caminho de entrada), e `_SUBFUNCAO_TOKENS` para os agentes orçamentários:

```python
INDICADOR_TO_AGENT: dict[str, str] = {
    "dengue": "sinan", "chikungunya": "sinan", ... (9 subtipos SINAN),
    "covid": "covid",
    "internacoes": "sih",
    "vacinacao": "sipni",
    "mortalidade": "sim",
    "nascidos_vivos": "sinasc",
    "producao_ambulatorial": "sia",
    "leitos": "cnes", ... (12 subtipos CNES),
}
```

Se o usuário pergunta só sobre `dengue`, a **proposta** traz apenas `AgenteSINAN` (saúde) e `AgenteOrcamentoSubfuncao(303)`/`(305)` (orçamento, pela correspondência subfunção↔sistema). O plano final sai do planejamento descrito abaixo. Os agentes analíticos (`AgenteAnalitico`), de contexto (`AgenteContextoOrcamentario`), de priorização (`AgentePriorizacaoAnalitica`) e o sintetizador **sempre** rodam.

## Planejamento por LLM

**Arquivo:** `backend/agents/planejamento.py`

O orquestrador segue o mesmo padrão de `arbitrar_dimensao` e de `AgentePriorizacaoAnalitica`, isto é, uma proposta determinística, a seleção pelo LLM e a proposta como alternativa:

1. `propose_actions()` deriva a proposta a partir de `INDICADOR_TO_AGENT` e `_SUBFUNCAO_TOKENS`.
2. `evaluate_and_select()` chama `planejamento.planejar(...)` **uma única vez**. O catálogo tem todos os 8 agentes de saúde e as 7 subfunções, com ids `saude:<sistema>` e `orcamento:<código>`. Esse é o planejamento centralizado da estrela.
3. O LLM só pode **retirar** itens da proposta ou **acrescentar** itens do catálogo, sempre com uma justificativa curta para cada mudança. Um id fora do catálogo, uma mudança sem justificativa ou um plano vazio invalidam a resposta.
4. Com resposta inválida, falha na chamada ou `use_llm=False`, vale a proposta. A origem fica registrada como `llm`, `llm_failed_fallback` ou `proposta`.

A revisão das entregas entra depois da coleta sempre que algum agente de dados ficar no plano. O plano completo (objetivo, proposta, escolhidos, acréscimos, retiradas, justificativas e origem) vai para `result["plano"]["orquestrador"]` e para o payload da bateria. Assim, toda diferença de dados entre as topologias pode ser rastreada até a decisão que a causou.

## O pipeline, passo a passo

`propose_actions()` monta a lista completa de macro-ações, em ordem fixa de dependência:

```python
def propose_actions(self) -> list[dict]:
    actions = []
    # 1. Um "consultar_saude" por agente de saúde ativo (0 a 8)
    for key in _SAUDE_AGENT_KEY_ORDER:  # covid, sih, sipni, sim, sinan, sinasc, sia, cnes
        if _AGENT_KEY_TO_TYPE[key] in active_agent_types:
            actions.append({"goal": "consultar_saude", "agent_key": key})

    # 2. Um "consultar_orcamento" por subfunção ativa (0 a 7)
    for codigo, nome in _ORCAMENTO_SUBFUNCOES:
        if _subfuncao_ativa(codigo, hp_set):
            actions.append({"goal": "consultar_orcamento", ...})

    if actions:                                           # há agente de dados a consultar
        actions.append({"goal": "revisar_entregas"})      # ← chama LLM ×1, sempre
    actions.append({"goal": "cruzar_dados"})
    actions.append({"goal": "detectar_gaps"})
    actions.append({"goal": "analisar_contexto"})
    actions.append({"goal": "analisar"})                  # correlação + anomalias
    actions.append({"goal": "capturar_wallclock"})        # ← marca o fim do "pipeline puro"
    actions.append({"goal": "priorizar_achados"})          # ← chama LLM (Etapa 3)
    actions.append({"goal": "sintetizar_texto"})           # ← chama LLM
    if use_self_check and use_llm:
        actions.append({"goal": "verificar_afirmacoes"})   # ← chama LLM ×3 (Etapa 4, opcional)
    actions.append({"goal": "persistir_metricas"})
    return actions
```

`execute()` (herdado da classe base, ver [agente-coala-e-llm.md](agente-coala-e-llm.md)) percorre essa lista em ordem, chamando o método `_act_*` correspondente de cada goal.

### 1. Fase de Saúde — `consultar_saude` (0 a 8x)

Para cada agente de saúde ativo, `_act_consultar_saude` instancia a classe (`AgenteSINAN`, `AgenteCNES`, etc.), chama `agent.query(analysis_id, date_from, date_to, intent_summary=..., health_params=..., use_llm=...)`, e acumula o resultado em `working_memory["indicadores"]`. Cada chamada de `query()` roda o próprio ciclo CoALA do agente — para os 6 agentes com dimensões válidas (todos exceto COVID e SIA), isso inclui a deliberação real de dimensão (`propose_actions`/`evaluate_and_select` arbitrando entre "sem quebra" e cada dimensão do sistema, via `agents/domain/query_planning.py`).

Falha num agente de saúde: envia evento `error` via `ws_queue`, mas **não interrompe** o pipeline — segue com dados parciais (degradação graciosa).

### 2. Fase de Orçamento — `consultar_orcamento` (0 a 7x)

Para cada subfunção ativa, `_act_consultar_orcamento` instancia `AgenteOrcamentoSubfuncao(codigo, nome)` e chama `query()`, que retorna despesas (com deliberação de dimensão `POR_NATUREZA`/`POR_APLICACAO`/sem quebra), a tendência do período (`tendencia`, via CAGR) e a variação ano-a-ano pré-computada no ETL (`variacao_anual`).

### 2b. `revisar_entregas` — revisão das entregas pelo orquestrador

O orquestrador recebe as entregas de todos os agentes de dados. Por isso revisa saúde e orçamento juntos, numa única chamada ao LLM (`agents/revisao_entregas.py`). Cada indicador e cada subfunção (e cada fatia de dimensão) vira uma série, e o LLM aponta os valores incompatíveis com a própria série.

Os valores apontados são contestados por reenvio (`AgenteCoALA.reenviar()`, que devolve a última entrega do agente sem consultar o banco de novo):
- se o reenvio traz o mesmo valor, ele é confirmado;
- se traz um valor diferente, um segundo reenvio decide pela maioria;
- sem maioria, o registro é descartado.

O LLM só aponta suspeitos e nunca escreve valores.

A revisão roda sempre que há agentes de dados, independentemente de `use_llm`. Se a chamada falhar, os dados seguem como chegaram e o resumo registra `falhou=True`. A etapa fica antes de `capturar_wallclock`, então o seu tempo entra no relógio e no custo de coordenação. O resumo vai para `result["revisao"]`. A hierárquica segue a mesma regra, com a revisão feita por ramo nos supervisores de dados (ver [arquitetura-hierarquica.md](arquitetura-hierarquica.md)).

### 3. Cruzamento e transparência de dados

- `cruzar_dados` — deduplica despesas (salvaguarda estrutural — hoje só os agentes orçamentários produzem despesas) e cruza despesas × indicadores por `(ano, dimensao_valor)` via `cross_domain_data()`.
- `detectar_gaps` — identifica lacunas via `detect_data_gaps()`, para o sintetizador poder mencionar transparentemente o que falta.

### 4. Fase Analítica — cálculo determinístico

- `analisar_contexto` → delega a `AgenteContextoOrcamentario.analyze_trends()` (variação YoY + CAGR, classifica tendência por subfunção).
- `analisar` → delega a `AgenteAnalitico` — correlação de Spearman **e** detecção de anomalias, consolidadas num único agente CoALA (antes eram dois agentes separados; ver [../02-AGENTES.md](../02-AGENTES.md)).

Nenhuma dessas ações usa LLM — cálculo 100% determinístico, decisão intencional (não lacuna).

### 5. `capturar_wallclock` — a fronteira antes do LLM

```python
def _act_capturar_wallclock(self, action: dict) -> None:
    self.working_memory["_orch_end"] = time.time()
```

Marca o fim do "pipeline determinístico" — tudo que roda **depois** deste ponto (priorização, síntese, self-check) envolve LLM e fica **fora** do wall-clock usado para comparar a eficiência das duas topologias.

### 6. `priorizar_achados` — decide ênfase (Etapa 3)

Delega a `AgentePriorizacaoAnalitica.prioritize(correlacoes, anomalias, contexto_orcamentario, intent_summary, use_llm=...)`. Esse agente decide qual ângulo de ênfase usar (ineficiências, correlações fortes, tendências orçamentárias, intenção do usuário, etc.) e reordena — nunca filtra ou recalcula — as listas de correlações/anomalias. O resultado fica em `working_memory["achados_priorizados"]`. Roda **depois** de `capturar_wallclock`. Se falhar, é só logado como warning — o sintetizador segue com os dados brutos, sem ênfase.

### 7. `sintetizar_texto` — geração do texto final

Delega a `TextSynthesizer` (que **não** é um agente CoALA). Usa `achados_priorizados` se disponível; senão usa os dados brutos diretamente.

```
use_llm=True:
    tenta sintetizador.generate_stream(...) com streaming via StreamingAdapter
        ├── sucesso → texto do LLM
        └── falha/vazio → sintetizador.generate_fallback(...) (texto estruturado determinístico)
use_llm=False:
    sintetizador.generate_fallback(...) diretamente
```

### 8. `verificar_afirmacoes` — self-check pós-síntese (Etapa 4, opcional)

Proposta somente quando `use_self_check=True` **e** `use_llm=True` — não faz sentido verificar um texto do fallback determinístico contra os dados que já o geraram. Delega a `core.claim_verifier.self_check()` (Chain-of-Verification: extrai afirmações do texto, verifica cada uma contra correlações/anomalias/contexto brutos, revisa o texto numa única passada se houver afirmação não suportada — nunca mais de 1 correção). Se `revisado=True`, `working_memory["texto_analise"]` é substituído pelo texto corrigido. Falha aqui nunca propaga — o texto original é mantido.

### 9. `persistir_metricas`

Persiste no Neo4j o `executionTimeMs`/`cpuPercent` de cada agente que rodou (via `MetricsCollector`), e emite os eventos `metric` e `agent_data` no WebSocket.

**Regra de contabilização importante:** os agentes `"sintetizador"`, `"priorizacao"` e `"verificacao"` são **excluídos** da soma `workers_time_ms` (aparecem no breakdown para exibição, mas não entram no cálculo de overhead) — todos correm depois de `capturar_wallclock` e envolvem LLM, então somá-los distorceria a métrica de eficiência da arquitetura em si:

```python
for _, agent_type, mc in collectors:
    if agent_type in ("sintetizador", "priorizacao", "verificacao"):
        continue
    ...
    workers_time_ms += m["executionTimeMs"]

overhead_ms = round(max(0, wall_clock_ms - workers_time_ms), 2)
```

`overhead_ms` na estrela tende a ~0, porque o próprio orquestrador não aparece como uma entrada separada nas métricas de agente — ele *é* o pipeline.

## Retorno

```python
result = {
    "despesas": [...], "indicadores": [...], "dados_cruzados": [...],
    "contexto_orcamentario": {...}, "correlacoes": [...], "anomalias": [...],
    "texto_analise": "...", "data_coverage": {...}, "self_check": {...} | None,
    "agent_queries": [...],
    "revisao": {...},            # resumo da revisão das entregas
    "mensagens": [...],          # registro FIPA-ACL das trocas (request/inform/failure)
    "plano": {"orquestrador": {...}},
    "redacao": {"modo": "unica"},
    "tempo_total_ms": ...,       # do início do run() até o texto final, redação incluída
}
```

Importante: `result["correlacoes"]`/`result["anomalias"]` são sempre os dados **brutos** (não a versão reordenada por `AgentePriorizacaoAnalitica`) — isso é o que garante que a métrica Q1 (consistência determinística entre estrela e hierárquica) continue comparando exatamente os mesmos números, independente de qual ângulo de ênfase cada topologia escolheu para o texto.

## Características-chave desta topologia

- **Ponto único de falha** — se o próprio orquestrador falhar, toda a análise falha (diferente da hierárquica, que tem degradação por camada).
- **Comunicação simples** — cada delegação do orquestrador a um agente gera um `request` e um `inform` (ou `failure`), registrados em `result["mensagens"]`. Nunca há troca lateral (hub-and-spoke). Ferramentas (`AgenteAnalitico`, contexto orçamentário, SIA, COVID) são ações do orquestrador e não geram mensagens.
- **Planejamento centralizado** — uma única chamada ao LLM decide quais agentes de dados acionar, sobre o catálogo inteiro.
- **Redação única** — o orquestrador redige com todos os achados numa chamada só.
- **Sem overhead de coordenação estrutural** — não há camada de supervisores; o "custo de coordenar" está embutido no próprio orquestrador.
