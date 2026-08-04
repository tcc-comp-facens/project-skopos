# Arquitetura Estrela — Ciclo Completo

> Este documento cobre o pipeline interno da topologia estrela, do momento em que `OrquestradorEstrela.run()` é chamado até o resultado final ser retornado. Para a base comum a qualquer agente (ciclo CoALA, chamada ao LLM), ver [agente-coala-e-llm.md](agente-coala-e-llm.md). Para o fluxo de ponta a ponta que antecede/sucede este pipeline (chat, disparo de análise, WebSocket de resultados), ver [ciclo-completo.md](ciclo-completo.md).

**Arquivo:** `backend/agents/star/orchestrator.py`

## Visão geral

Um único agente central — `OrquestradorEstrela` — coordena todos os demais. Nenhum agente periférico se comunica diretamente com outro: tudo passa pelo hub. `OrquestradorEstrela` herda de `AgenteCoALA` e executa de fato pelo ciclo CoALA (`run_coala_cycle()`), mas com uma particularidade documentada explicitamente no código: `evaluate_and_select()` é um passthrough aqui, porque a ordem das macro-ações é imposta por **dependência de dados** (cruzamento só pode ocorrer depois do domínio; síntese só depois de correlação/anomalias/contexto), não por arbitragem entre candidatos concorrentes. A autonomia deliberativa de verdade mora nos agentes de nível folha.

```
                         OrquestradorEstrela
                              (Hub)
                               │
        ┌──────┬───────┬───────┼───────┬──────┬──────┬───────┬──────┐
        ▼      ▼       ▼       ▼       ▼      ▼      ▼       ▼      ▼
      Vigil. Hospit. Primár. Mortal. Contex. Correl. Anomal. Prior. Sintet.
      (305)  (302)   (301)   (todas)  (YoY)  (stats) (median) (LLM)  (LLM)
        │      │       │       │       │      │      │       │      │
        └──────┴───────┴───────┘       │      │      │       │      │
               │                       │      │      │       │      │
          despesas +                   │      │      │       │      │
          indicadores                  │      │      │       │      │
               │                       │      │      │       │      │
               ├───────────────────────┘      │      │       │      │
               │  (despesas)                  │      │       │      │
               ├──── cross_domain_data() ─────┤      │       │      │
               │     (dados cruzados)         │      │       │      │
               └──────────────────────────────┴──────┘       │      │
                                                              │      │
                    correlações + anomalias + contexto ───────┘      │
                                        (escolhe ângulo de ênfase) ───┘
                                                                     │
                                                              texto (streaming)
```

## Ativação condicional dos agentes de domínio

`OrquestradorEstrela` usa o mapeamento `INDICADOR_TO_AGENT` para instanciar **só** os agentes de domínio relevantes aos `health_params` da análise (vindos do formulário REST, ou extraídos pelo `AgenteInterpretacaoIntencao` quando a análise vem do chat):

```python
INDICADOR_TO_AGENT: dict[str, str] = {
    "dengue": "vigilancia_epidemiologica",
    "covid": "vigilancia_epidemiologica",
    "internacoes": "saude_hospitalar",
    "vacinacao": "atencao_primaria",
    "mortalidade": "mortalidade",
}
```

Se o usuário seleciona só `dengue` e `vacinacao`, só `AgenteVigilanciaEpidemiologica` e `AgenteAtencaoPrimaria` são instanciados — os demais nem entram na lista de ações propostas. Os agentes analíticos, o de contexto, o de priorização e o sintetizador **sempre** rodam.

## O pipeline, passo a passo

`propose_actions()` monta a lista completa de macro-ações, em ordem fixa de dependência:

```python
def propose_actions(self) -> list[dict]:
    actions = []
    # 1. Um "consultar_dominio" por agente de domínio ativo (0 a 4)
    for key in _DOMAIN_AGENT_KEY_ORDER:  # vigilancia, hospitalar, primaria, mortalidade
        if _AGENT_KEY_TO_TYPE[key] in active_agent_types:
            actions.append({"goal": "consultar_dominio", "agent_key": key})

    actions.append({"goal": "cruzar_dados"})
    actions.append({"goal": "detectar_gaps"})
    actions.append({"goal": "analisar_contexto"})
    actions.append({"goal": "calcular_correlacoes"})
    actions.append({"goal": "detectar_anomalias"})
    actions.append({"goal": "capturar_wallclock"})       # ← marca o fim do "pipeline puro"
    actions.append({"goal": "priorizar_achados"})         # ← chama LLM (Etapa 3)
    actions.append({"goal": "sintetizar_texto"})          # ← chama LLM
    actions.append({"goal": "persistir_metricas"})
    return actions
```

`execute()` (herdado da classe base, ver [agente-coala-e-llm.md](agente-coala-e-llm.md)) percorre essa lista em ordem, chamando o método `_act_*` correspondente de cada goal.

### 1. Fase de Domínio — `consultar_dominio` (0 a 4x)

Para cada agente de domínio ativo, `_act_consultar_dominio` instancia a classe (`AgenteVigilanciaEpidemiologica`, etc.), chama `agent.query(analysis_id, date_from, date_to, intent_summary=..., health_params=...)`, e acumula o resultado em `working_memory["despesas"]`/`["indicadores"]`. Cada chamada de `query()` roda o próprio ciclo CoALA do agente de domínio — incluindo a ação `planejar_consulta` (ver `agents/domain/query_planning.py`), que por padrão é um fast-path determinístico e só chama o LLM se a base de dados tiver crescido além do mapeamento estático conhecido.

Falha num agente de domínio: envia evento `error` via `ws_queue`, mas **não interrompe** o pipeline — segue com dados parciais (degradação graciosa).

### 2. Cruzamento e transparência de dados

- `cruzar_dados` — deduplica despesas (o `AgenteMortalidade` é transversal, retorna despesas de todas as subfunções, então se sobrepõe aos demais) e cruza despesas × indicadores por subfunção/ano via `cross_domain_data()`.
- `detectar_gaps` — identifica lacunas (anos sem despesa ou sem indicador) via `detect_data_gaps()`, para o sintetizador poder mencionar transparentemente o que falta.

### 3. Fase Analítica — cálculo determinístico

- `analisar_contexto` → delega a `AgenteContextoOrcamentario` (variação YoY, classifica tendência).
- `calcular_correlacoes` → delega a `AgenteCorrelacao` (Spearman por par subfunção-indicador).
- `detectar_anomalias` → delega a `AgenteAnomalias` (comparação com mediana, considerando polaridade do indicador).

Nenhuma dessas três ações usa LLM — cálculo 100% determinístico, decisão intencional (não lacuna).

### 4. `capturar_wallclock` — a fronteira antes do LLM

```python
def _act_capturar_wallclock(self, action: dict) -> None:
    self.working_memory["_orch_end"] = time.time()
```

Marca o fim do "pipeline determinístico" — tudo que roda **depois** deste ponto (priorização e síntese) envolve LLM e fica **fora** do wall-clock usado para comparar a eficiência das duas topologias (a latência da API não deve contaminar a métrica de eficiência da arquitetura em si).

### 5. `priorizar_achados` — decide ênfase (Etapa 3)

Delega a `AgentePriorizacaoAnalitica.prioritize(correlacoes, anomalias, contexto_orcamentario, intent_summary, use_llm=...)`. Esse agente decide qual "ângulo" de ênfase usar (ineficiências, correlações fortes, tendências orçamentárias, etc.) e reordena — nunca filtra ou recalcula — as listas de correlações/anomalias. O resultado fica em `working_memory["achados_priorizados"]`.

Roda **depois** de `capturar_wallclock` porque envolve 1 chamada LLM. Se falhar, é só logado como warning — o sintetizador segue com os dados brutos, sem ênfase.

### 6. `sintetizar_texto` — geração do texto final

Delega a `TextSynthesizer` (que **não** é um agente CoALA — ver [agente-coala-e-llm.md](agente-coala-e-llm.md)). Usa `achados_priorizados` se disponível (correlações/anomalias reordenadas + descrição do ângulo escolhido, passada como `enfase` para o prompt); senão usa os dados brutos diretamente — mesmo comportamento de antes da Etapa 3 existir.

```
use_llm=True:
    tenta sintetizador.generate_stream(...) com streaming via StreamingAdapter
        ├── sucesso → texto do LLM
        └── falha/vazio → sintetizador.generate_fallback(...) (texto estruturado determinístico)
use_llm=False:
    sintetizador.generate_fallback(...) diretamente
```

### 7. `persistir_metricas`

Persiste no Neo4j o `executionTimeMs`/`cpuPercent` de cada agente que rodou (via `MetricsCollector`), e emite o evento `metric` no WebSocket com o breakdown agregado.

**Regra de contabilização importante:** os agentes `"sintetizador"` e `"priorizacao"` são **excluídos** da soma `workers_time_ms` (aparecem no breakdown para exibição, mas não entram no cálculo de overhead) — ambos correm depois de `capturar_wallclock` e envolvem LLM, então somá-los distorceria a métrica de eficiência da arquitetura em si:

```python
for _, agent_type, mc in collectors:
    if agent_type in ("sintetizador", "priorizacao"):
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
    "texto_analise": "...", "data_coverage": {...},
}
```

Importante: `result["correlacoes"]`/`result["anomalias"]` são sempre os dados **brutos** (não a versão reordenada por `AgentePriorizacaoAnalitica`) — isso é o que garante que a métrica Q1 (consistência determinística entre estrela e hierárquica) continue comparando exatamente os mesmos números, independente de qual ângulo de ênfase cada topologia escolheu para o texto.

## Características-chave desta topologia

- **Ponto único de falha** — se o próprio orquestrador falhar, toda a análise falha (diferente da hierárquica, que tem degradação por camada).
- **Comunicação simples** — orquestrador ↔ agente é sempre ida+volta (2 mensagens), nunca lateral.
- **Ativação condicional** — só instancia os agentes de domínio necessários.
- **Sem overhead de coordenação estrutural** — não há camada de supervisores; o "custo de coordenar" está embutido no próprio orquestrador.
