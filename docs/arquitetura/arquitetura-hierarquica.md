# Arquitetura Hierárquica — Ciclo Completo

> Este documento cobre o pipeline interno da topologia hierárquica, do momento em que `CoordenadorGeral.run()` é chamado até o resultado final ser retornado. Para a base comum a qualquer agente (ciclo CoALA, chamada ao LLM), ver [agente-coala-e-llm.md](agente-coala-e-llm.md). Para o fluxo de ponta a ponta que antecede/sucede este pipeline (chat, disparo de análise, WebSocket de resultados), ver [ciclo-completo.md](ciclo-completo.md).

**Arquivos:** `backend/agents/hierarchical/coordinator.py`, `backend/agents/hierarchical/supervisors.py`

## Visão geral

Os agentes são organizados em **3 níveis**: um coordenador geral (nível 0), três supervisores intermediários (nível 1), e os agentes-folha (nível 2) — os mesmos agentes de domínio/analíticos/contexto usados na estrela, só que agrupados sob supervisores em vez de reportar direto a um hub único. A diferença estrutural central em relação à estrela: os supervisores podem trocar dados **lateralmente** entre si, sem passar pelo coordenador.

```
                    CoordenadorGeral
                      (Nível 0)
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    SupervisorDominio  SupervisorContexto  SupervisorAnalitico
      (Nível 1)         (Nível 1)           (Nível 1)
          │                │                    │
    ┌─────┼─────┐         │              ┌─────┼──────┐
    ▼     ▼     ▼    ▼    ▼              ▼     ▼      ▼
  Vigil. Hosp. Prim. Mort. CtxOrç.     Corr. Anom.  Prior.+Sintet.
  (Nível 2)                (Nível 2)    (Nível 2)    (serviços)


  Comunicação lateral (sem passar pelo Coordenador):
  ─────────────────────────────────────────────────
  SupervisorDominio ───────► SupervisorAnalitico
                              (despesas + indicadores + intent_summary + health_params)
  SupervisorDominio ───────► SupervisorContexto
                              (despesas)
  SupervisorContexto ──────► SupervisorAnalitico
                              (contexto orçamentário)
```

`CoordenadorGeral` e os 3 supervisores herdam de `AgenteCoALA`. Assim como na estrela, `evaluate_and_select()` é passthrough em todos eles — a ordem das macro-ações é imposta por dependência de dados entre supervisores, não por arbitragem.

## O pipeline do `CoordenadorGeral`

```python
def propose_actions(self) -> list[dict]:
    if not self.working_memory.get("analysis_id"):
        return []
    return [
        {"goal": "delegar_dominio"},
        {"goal": "comunicar_dominio_analitico"},   # comunicação lateral
        {"goal": "comunicar_dominio_contexto"},    # comunicação lateral
        {"goal": "delegar_contexto"},
        {"goal": "comunicar_contexto_analitico"},  # comunicação lateral
        {"goal": "delegar_analitico"},
        {"goal": "persistir_metricas"},
    ]
```

### 1. `delegar_dominio` → `SupervisorDominio.run()`

O supervisor de domínio roda seu próprio ciclo CoALA: ativa condicionalmente os agentes de domínio relevantes aos `health_params` (mesmo mapeamento `INDICADOR_TO_AGENT` da estrela), chama `agent.query(..., intent_summary=..., health_params=...)` para cada um — cada consulta passa pela ação `planejar_consulta` de cada agente de domínio (ver `agents/domain/query_planning.py`, fast-path por padrão) — e agrega despesas/indicadores (deduplicando, já que `AgenteMortalidade` é transversal).

### 2–3. Comunicação lateral: Domínio → Analítico / Domínio → Contexto

```python
def _act_comunicar_dominio_analitico(self, action: dict) -> None:
    sup_analitico.receive_from_peer({
        "despesas": dominio_data.get("despesas", []),
        "indicadores": dominio_data.get("indicadores", []),
        "date_from": ..., "date_to": ..., "health_params": ...,
        "intent_summary": self.working_memory.get("intent_summary"),
    })
```

`receive_from_peer()` é uma ação externa de comunicação — escreve numa região nomeada da `working_memory`/`peer_data` de quem recebe, sem passar pelo `propose_actions`/`execute` do receptor (é o par que empurra o dado, não o receptor que o busca). O coordenador loga ambos os lados do hop (o que está enviando, e cada supervisor loga o que recebeu).

### 4. `delegar_contexto` → `SupervisorContexto.run()`

Delega a `AgenteContextoOrcamentario` usando as despesas recebidas lateralmente do `SupervisorDominio`. Cálculo determinístico, sem LLM.

### 5. Comunicação lateral: Contexto → Analítico

Repassa o `contexto_orcamentario` calculado para o `SupervisorAnalitico`.

### 6. `delegar_analitico` → `SupervisorAnalitico.run()`

Ver seção dedicada abaixo — é aqui que corre o pipeline analítico completo, incluindo as duas etapas que envolvem LLM (priorização e síntese).

### 7. `persistir_metricas`

Persiste métricas de 8 agentes-folha + 3 supervisores (11 entidades). Ver [seção de contabilização](#contabilização-de-métricas-e-overhead) abaixo.

## O pipeline do `SupervisorAnalitico`

```python
def propose_actions(self) -> list[dict]:
    if not (self.working_memory.get("analysis_id") and self.working_memory.get("_ws_queue")):
        return []
    return [
        {"goal": "cruzar_dados"},
        {"goal": "detectar_gaps"},
        {"goal": "calcular_correlacoes"},
        {"goal": "detectar_anomalias"},
        {"goal": "capturar_wallclock"},      # ← marca _coala_leaf_end_time
        {"goal": "priorizar_achados"},        # ← chama LLM (Etapa 3)
        {"goal": "sintetizar_texto"},         # ← chama LLM
    ]
```

Estrutura idêntica à fase analítica da estrela (`cruzar_dados` usa os dados recebidos lateralmente via `peer_data`; `calcular_correlacoes`/`detectar_anomalias` delegam a `AgenteCorrelacao`/`AgenteAnomalias`, sem LLM). As duas particularidades desta topologia:

- **`capturar_wallclock`** marca `self._coala_leaf_end_time` — usado pelo `CoordenadorGeral` para medir o supervisor analítico **sem** incluir o tempo do que roda depois (priorização + síntese, ambos com LLM).
- **`priorizar_achados`** lê `intent_summary` de `self.peer_data` (repassado lateralmente pelo `SupervisorDominio`, não da própria `working_memory` como na estrela) — reflexo direto da estrutura em camadas: o dado precisa atravessar coordenador → supervisor → comunicação lateral antes de chegar aqui.

O restante — delega a `AgentePriorizacaoAnalitica`, depois a `TextSynthesizer` usando `achados_priorizados` se disponível — é o mesmo mecanismo documentado em [arquitetura-estrela.md](arquitetura-estrela.md#5-priorizar_achados--decide-ênfase-etapa-3).

## Contabilização de métricas e overhead

Esta é a parte que mais difere da estrela — aqui existe uma camada real de supervisores cujo custo precisa ser isolado do trabalho de fato.

```python
overhead = wall_clock - soma dos agentes FOLHA (nível 2)
```

- Supervisores aparecem no breakdown de métricas **para exibição**, mas **não** são somados em `workers_time_ms` — o tempo de um supervisor já engloba o de seus subordinados; somar ambos causaria dupla contagem.
- `overhead_ms` captura: tempo dos supervisores fora dos subordinados + comunicação lateral (`receive_from_peer`) + instanciação — é a métrica que quantifica o "custo real" de ter uma camada extra de coordenação, em contraste com a estrela (onde esse overhead é ~0 por design).
- Os agentes-folha `"sintetizador"` e `"priorizacao"` são excluídos tanto de `workers_time_ms` quanto do wall-clock do coordenador (mesma lógica da estrela, mas aqui a subtração é explícita porque o coordenador captura seu próprio `time.time()` **depois** de tudo já ter rodado):

```python
# CoordenadorGeral._act_persistir_metricas
sint_time_ms = 0.0
prior_time_ms = 0.0
for agent_mc in sup_analitico._collectors:
    m = agent_mc.collect()
    if m["agentType"] == "sintetizador":
        sint_time_ms = m["executionTimeMs"]
    elif m["agentType"] == "priorizacao":
        prior_time_ms = m["executionTimeMs"]

wall_clock_ms = max(0, wall_clock_ms_raw - sint_time_ms - prior_time_ms)
overhead_ms = max(0, wall_clock_ms - workers_time_ms)
```

## Degradação graciosa

Diferente da estrela (ponto único de falha no orquestrador), aqui cada supervisor pode falhar independentemente e o coordenador continua com dados parciais para os demais:

```
    SupervisorDominio falha!
              │
              ▼
    CoordenadorGeral:
    ├── Captura exceção, envia evento "error" via WebSocket
    ├── Define dominio_data = {despesas: [], indicadores: []}
    └── CONTINUA com SupervisorContexto e SupervisorAnalitico
              │
              ▼
    Resultado final: parcial mas válido
    (correlações e anomalias vazias, texto gerado com fallback)
```

## Retorno

```python
result = dict(analitico_data)  # correlacoes, anomalias, texto_analise, data_coverage, dados_cruzados
result["despesas"] = dominio_data.get("despesas", [])
result["indicadores"] = dominio_data.get("indicadores", [])
result["contexto_orcamentario"] = contexto_data.get("contexto_orcamentario", {})
```

Mesma garantia da estrela: `correlacoes`/`anomalias` no retorno são sempre os dados brutos do `AgenteCorrelacao`/`AgenteAnomalias`, nunca a versão reordenada pela priorização — Q1 compara números idênticos entre as duas topologias independente de qual ângulo de ênfase cada uma escolheu.

## Características-chave desta topologia

- **Degradação graciosa por camada** — falha de um supervisor não derruba os demais (mais resiliente que a estrela nesse aspecto).
- **Comunicação lateral real** — supervisores trocam dados diretamente entre si, sem intermediação do coordenador.
- **Overhead de coordenação mensurável** — ao contrário da estrela, aqui existe uma métrica dedicada (`overhead_ms`) para o custo real de ter a camada extra de supervisores.
- **Mais mensagens** — ~24+ trocas (agentes + supervisores + comunicação lateral), contra o modelo simples "ida+volta" da estrela.
- **Mais entidades monitoradas** — 11 (8 agentes-folha + 3 supervisores) contra 8 na estrela.
