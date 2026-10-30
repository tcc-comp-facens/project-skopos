# Arquitetura Hierárquica — Ciclo Completo

> Este documento cobre o pipeline interno da topologia hierárquica, do momento em que `CoordenadorGeral.run()` é chamado até o resultado final ser retornado. Para a base comum a qualquer agente (ciclo CoALA, chamada ao LLM), ver [agente-coala-e-llm.md](agente-coala-e-llm.md). Para o fluxo de ponta a ponta que antecede/sucede este pipeline (chat, disparo de análise, WebSocket de resultados), ver [ciclo-completo.md](ciclo-completo.md). Para o detalhamento de cada agente citado aqui, ver [../02-AGENTES.md](../02-AGENTES.md).

**Arquivos:** `backend/agents/hierarchical/coordinator.py`, `backend/agents/hierarchical/supervisors.py`

## Visão geral

Os agentes são organizados em **3 níveis**: um coordenador geral (nível 0), **4 supervisores** intermediários (nível 1) — `SupervisorOrcamento`, `SupervisorSaude`, `SupervisorContexto`, `SupervisorAnalitico` —, e os agentes-folha (nível 2) — os mesmos 8 agentes de saúde + 7 orçamentários + agentes analíticos usados na estrela, agrupados sob supervisores em vez de reportar direto a um hub único. `SupervisorOrcamento` e `SupervisorSaude` substituem o antigo `SupervisorDominio` único — decisão de separar orçamento e saúde em supervisores distintos.

**Árvore estrita**: nenhum supervisor troca dado diretamente com outro. Um design anterior tinha um canal `receive_from_peer`/`peer_data` (comunicação "lateral", mediada pela raiz mas ainda uma troca par-a-par, inclusive com resumos textuais via LLM) — removido por decisão explícita: o modelo de hierárquica deste projeto precisa ser uma árvore no sentido estrito, sem atalho lateral algum. Hoje o `CoordenadorGeral` colhe o **retorno** de cada supervisor e repassa o que for necessário como **parâmetro direto** na chamada do próximo — o mesmo padrão de qualquer árvore convencional (filho retorna ao pai; pai passa adiante como entrada do próximo filho).

```
                         CoordenadorGeral
                           (Nível 0)
                              │
      ┌───────────────┬───────────────┬───────────────┐
      ▼               ▼               ▼               ▼
SupervisorOrcamento SupervisorSaude SupervisorContexto SupervisorAnalitico
   (Nível 1)          (Nível 1)        (Nível 1)          (Nível 1)
      │                  │                │                  │
  ┌───┼───┐      ┌───────┼───────┐        │            ┌─────┼─────┐
  ▼   ▼   ▼      ▼   ▼   ▼   ▼   ▼        ▼            ▼     ▼     ▼
até 7 AgenteOrcamentoSubfuncao   até 8 agentes de saúde  CtxOrç.  Analitico Priorizacao TextSynth.
                                                        (Nível 2) (Nível 2) (Nível 2)  (serviço)

  Sem comunicação lateral entre supervisores. O coordenador colhe o
  retorno de cada um e repassa como parâmetro direto do próximo:
  ─────────────────────────────────────────────────────────────────
  despesas(Orçamento) + despesas(Saúde) → combinar_despesas → despesas_combinadas
  despesas_combinadas ──► SupervisorContexto.run(despesas=...)
  despesas_combinadas + indicadores(Saúde) + contexto_orcamentario(Contexto)
                       ──► SupervisorAnalitico.run(despesas=..., indicadores=..., contexto_orcamentario=...)
```

`CoordenadorGeral` e os 4 supervisores herdam de `AgenteCoALA`. A ordem das macro-ações é imposta por dependência de dados entre supervisores. Já **quais** subordinados acionar é decidido com o LLM, nível a nível (ver [Planejamento decomposto pela árvore](#planejamento-decomposto-pela-árvore)).

## Planejamento decomposto pela árvore

**Arquivo:** `backend/agents/planejamento.py`

O padrão é o mesmo da estrela: uma proposta determinística em `propose_actions()`, a seleção pelo LLM em `evaluate_and_select()` e a proposta como alternativa. O LLM só pode retirar itens da proposta ou acrescentar itens do catálogo do próprio nó, com justificativa. A diferença está em como o planejamento se reparte pelos níveis.

1. **`CoordenadorGeral`** decide quais ramos de dados acionar (`ramo:orcamento`, `ramo:saude`, `ramo:contexto`). O ramo analítico, que calcula e redige, é sempre acionado. Para os ramos de orçamento e de saúde, o coordenador também escreve um **subobjetivo** em linguagem natural. Esse subobjetivo vai no `request` ao supervisor como mensagem de natureza `texto`.
2. **`SupervisorSaude`** escolhe entre os 8 agentes de saúde (`saude:<sistema>`), a partir do subobjetivo que recebeu.
3. **`SupervisorOrcamento`** escolhe entre as 7 subfunções (`orcamento:<código>`), também a partir do próprio subobjetivo.

São três chamadas ao LLM, contra uma na estrela. Cada supervisor vê só o subobjetivo do seu ramo e o próprio catálogo, que é o contexto repartido das topologias em árvore (Qian et al., 2025). O `SupervisorContexto` tem um único subordinado e não planeja. Sem LLM, ou com resposta inválida, o subobjetivo cai num texto padrão e a escolha cai na proposta.

Os planos dos três nós vão para `result["plano"]` (`coordenador`, `supervisor_orcamento`, `supervisor_saude`) e para o payload da bateria.

## O pipeline do `CoordenadorGeral`

```python
def propose_actions(self) -> list[dict]:
    if not self.working_memory.get("analysis_id"):
        return []
    return [
        {"goal": "delegar_orcamento"},
        {"goal": "delegar_saude"},
        {"goal": "combinar_despesas"},
        {"goal": "delegar_contexto"},
        {"goal": "delegar_analitico"},
        {"goal": "persistir_metricas"},
    ]
```

### 1–2. `delegar_orcamento` / `delegar_saude`

`SupervisorOrcamento.run()` ativa condicionalmente até 7 `AgenteOrcamentoSubfuncao` (por correspondência subfunção↔sistema, `_SUBFUNCAO_TOKENS`); `SupervisorSaude.run()` ativa condicionalmente até 8 agentes de saúde (mesmo mapeamento `INDICADOR_TO_AGENT` da estrela). Cada supervisor agrega o resultado dos seus subordinados e devolve o agregado ao coordenador — nenhum dos dois fala com outro supervisor.

Antes de agregar, cada um dos dois supervisores executa `revisar_entregas`: uma chamada ao LLM sobre as entregas do seu ramo. O `SupervisorSaude` revisa os indicadores e o `SupervisorOrcamento` revisa as despesas. Os valores apontados são contestados por reenvio do agente de origem e terminam confirmados, corrigidos pela maioria entre os reenvios ou descartados (`agents/revisao_entregas.py`). É a mesma regra da estrela: revisa quem recebe as entregas dos agentes de dados. Na estrela, o orquestrador revisa tudo numa chamada. Aqui, cada supervisor revisa só o seu ramo, e por isso são até duas chamadas, cada uma com menos contexto. O `CoordenadorGeral` não revisa, porque recebe agregados de supervisores e não entregas de agentes de dados. Ele só soma os dois resumos em `result["revisao"]`. A revisão roda sempre, independentemente de `use_llm`. Uma falha na chamada deixa os dados como chegaram.

### 3. `combinar_despesas`

Desde a decomposição completa dos agentes de saúde (nenhum deles retorna despesas), esta etapa é uma **salvaguarda estrutural** — dedupe por `(subfuncao, ano)` entre o que `SupervisorOrcamento` e `SupervisorSaude` retornaram — não porque haja overlap real esperado hoje. O resultado (`_despesas_combinadas`) fica na `working_memory` do coordenador, pronto para ser repassado como parâmetro aos passos seguintes.

### 4. `delegar_contexto` → `SupervisorContexto.run(despesas=...)`

O coordenador chama `sup_contexto.run(despesas=self.working_memory["_despesas_combinadas"], use_llm=...)` — as despesas combinadas chegam como **argumento direto**, não por um canal lateral. `SupervisorContexto` delega a `AgenteContextoOrcamentario` (CAGR + classificação), cálculo 100% determinístico, sem LLM.

### 5. `delegar_analitico` → `SupervisorAnalitico.run(despesas=..., indicadores=..., contexto_orcamentario=...)`

O coordenador colhe `indicadores` do retorno de `SupervisorSaude` e `contexto_orcamentario` do retorno de `SupervisorContexto`, e repassa os três (mais despesas combinadas) como argumentos diretos de `SupervisorAnalitico.run()`. Ver seção dedicada abaixo — é aqui que corre o pipeline analítico completo, incluindo as três etapas que envolvem LLM (priorização, síntese, self-check opcional).

### 6. `persistir_metricas`

Persiste métricas dos agentes-folha (até 15) + 4 supervisores. Ver [seção de contabilização](#contabilização-de-métricas-e-overhead) abaixo.

## O pipeline do `SupervisorAnalitico`

```python
def propose_actions(self) -> list[dict]:
    if not (self.working_memory.get("analysis_id") and self.working_memory.get("_ws_queue")):
        return []
    actions = [
        {"goal": "cruzar_dados"},
        {"goal": "detectar_gaps"},
        {"goal": "analisar"},                 # correlação + anomalias (AgenteAnalitico)
        {"goal": "capturar_wallclock"},        # ← marca _coala_leaf_end_time
        {"goal": "priorizar_achados"},          # ← chama LLM (Etapa 3)
        {"goal": "redigir_ramos"},              # ← um redator por sistema (LLM)
        {"goal": "sintetizar_texto"},           # ← chama LLM (composição ou redação única)
    ]
    if use_self_check and use_llm:
        actions.append({"goal": "verificar_afirmacoes"})  # ← chama LLM ×3 (Etapa 4, opcional)
    return actions
```

`run()` recebe `despesas`, `indicadores` e `contexto_orcamentario` como parâmetros — gravados direto na `working_memory` (não num `peer_data` separado). Estrutura equivalente à fase analítica da estrela (`cruzar_dados` usa esses dados; `analisar` delega a `AgenteAnalitico`, sem LLM). Particularidade desta topologia:

- **`capturar_wallclock`** marca `self._coala_leaf_end_time` — usado pelo `CoordenadorGeral` para medir o supervisor analítico **sem** incluir o tempo do que roda depois (priorização + síntese + self-check, todos com LLM).
- **`verificar_afirmacoes`** (self-check, Etapa 4) segue a mesma regra da estrela — só proposta quando `use_self_check=True` e `use_llm=True`; falha nunca propaga.

### Redação por ramo

**Arquivos:** `backend/agents/analytical/redacao_ramos.py`, `backend/agents/analytical/sintetizador.py`

Na estrela, o orquestrador redige numa única chamada, com todos os achados. Aqui, o `SupervisorAnalitico` reparte a redação como a árvore reparte a coleta:

- `redacao_ramos.particionar(...)` agrupa correlações e anomalias por sistema de informação, a partir do `tipo_indicador`. Cada ramo recebe só o contexto orçamentário das suas subfunções e só as lacunas dos seus indicadores.
- Com **dois ou mais sistemas**, há um redator por sistema (`TextSynthesizer.generate_ramo`, com `_build_prompt_ramo`), e cada um vê só o seu ramo. Cada texto volta ao supervisor como um `inform` de natureza `texto`. O supervisor compõe a resposta final a partir desses textos (`generate_stream_composicao`, com `_build_prompt_composicao`).
- Com **um único sistema**, o supervisor escreve direto, como na estrela. As perguntas focais servem de controle.
- **Se um ramo falhar**, os achados crus dele vão direto para a composição. Se **todos** falharem, cai no redator único.

Os dois prompts novos têm o mesmo bloco FIDELIDADE AOS DADOS do redator único. O RAGAS continua julgando o texto final contra os achados crus (`build_contexts`), então um erro introduzido por um ramo aparece na fidelidade. O resultado traz `redacao` (`modo` `por_ramo` ou `unica`, `ramos`, `falhas`) e `textos_parciais`. Sem LLM, não há redação por ramo.

O restante — delega a `AgentePriorizacaoAnalitica` (com `intent_summary` recebido direto, sem mais concatenar resumos de outros supervisores), depois a `TextSynthesizer` usando `achados_priorizados` se disponível, depois opcionalmente `core.claim_verifier.self_check()` — é o mesmo mecanismo documentado em [arquitetura-estrela.md](arquitetura-estrela.md).

## Contabilização de métricas e overhead

Esta é a parte que mais difere da estrela — aqui existe uma camada real de 4 supervisores cujo custo precisa ser isolado do trabalho de fato.

```python
overhead = wall_clock - soma dos agentes FOLHA (nível 2)
```

- Supervisores aparecem no breakdown de métricas **para exibição**, mas **não** são somados em `workers_time_ms` — o tempo de um supervisor já engloba o de seus subordinados; somar ambos causaria dupla contagem.
- `overhead_ms` captura: tempo dos supervisores fora dos subordinados + instanciação (não há mais tempo de comunicação lateral a capturar — a árvore é estrita).
- O relógio do coordenador fecha no mesmo ponto em que o da estrela (`_orch_end`): o fim da parte determinística do `SupervisorAnalitico` (`capturar_wallclock`, exposto em `_coala_leaf_end_time`). Ficam de fora priorização, síntese e verificação (LLM) e a gravação das métricas no Neo4j. Os agentes-folha `"sintetizador"`, `"priorizacao"` e `"verificacao"` também não entram em `workers_time_ms`.

```python
# CoordenadorGeral._act_persistir_metricas
leaf_end = getattr(sup_analitico, "_coala_leaf_end_time", None)
coord_end = leaf_end if leaf_end valido else persist_entry   # fallback se o analítico não fechou o relógio
wall_clock_ms = (coord_end - coord_start) * 1000
overhead_ms = max(0, wall_clock_ms - workers_time_ms)
```

> Uma versão anterior calculava `coord_end = time.time()` depois de gravar as métricas e subtraía só o tempo do LLM. A gravação (24 a 37 s com o banco sem índice e cheio de nós órfãos) entrava no relógio só da hierárquica e aparecia como ~50% de "overhead".

## Comunicação — registro de mensagens

Toda delegação entre agentes vira uma mensagem no estilo FIPA-ACL (`Mensagem` em `agents/base.py`). Cada mensagem tem remetente, destinatário, performativa (`request`, `inform` ou `failure`), natureza (`dados` ou `texto`), conteúdo resumido e tamanho em caracteres. O `CoordenadorGeral` cria o registro em `run()` e o repassa aos quatro supervisores, e cada nó registra as trocas que faz com os seus subordinados (`_delegar`). O registro vai para `result["mensagens"]`.

Chamadas a ferramentas (`AgenteAnalitico`, contexto orçamentário, SIA, COVID) são ações do nó que as chama e não geram mensagens. `compute_communication_volume` conta as mensagens reais do registro e separa as de dados das de texto. Na hierárquica aparecem as trocas coordenador ↔ supervisores, supervisores ↔ agentes, os subobjetivos (texto), os textos dos ramos (texto) e as contestações da revisão.

## Degradação graciosa

Diferente da estrela (ponto único de falha no orquestrador), aqui cada supervisor pode falhar independentemente e o coordenador continua com dados parciais para os demais:

```
    SupervisorSaude falha!
              │
              ▼
    CoordenadorGeral:
    ├── Captura exceção, envia evento "error" via WebSocket
    ├── Define saude_data = {despesas: [], indicadores: []}
    └── CONTINUA com SupervisorOrcamento, SupervisorContexto e SupervisorAnalitico
              │
              ▼
    Resultado final: parcial mas válido
    (correlações e anomalias parciais, texto gerado com fallback se necessário)
```

## Retorno

```python
result = dict(analitico_data)  # correlacoes, anomalias, texto_analise, data_coverage, dados_cruzados, self_check
result["despesas"] = despesas_combinadas
result["indicadores"] = saude_data.get("indicadores", [])
result["contexto_orcamentario"] = contexto_data.get("contexto_orcamentario", {})
result["agent_queries"] = orcamento_data["agent_queries"] + saude_data["agent_queries"]
result["revisao"] = combinar_resumos(...)         # revisão dos dois ramos de dados
result["mensagens"] = [...]                       # registro FIPA-ACL da topologia inteira
result["plano"] = {"coordenador": ..., "supervisor_orcamento": ..., "supervisor_saude": ...}
result["redacao"], result["textos_parciais"]      # vindos do SupervisorAnalitico
result["tempo_total_ms"] = ...                    # do início do run() até o texto final
```

Mesma garantia da estrela: `correlacoes`/`anomalias` no retorno são sempre os dados brutos de `AgenteAnalitico`, nunca a versão reordenada pela priorização — Q1 compara números idênticos entre as duas topologias independente de qual ângulo de ênfase cada uma escolheu.

## Características-chave desta topologia

- **Árvore estrita** — nenhum supervisor troca dado com outro; toda comunicação passa pelo `CoordenadorGeral` (retorno do filho → argumento do próximo filho), nunca um canal par-a-par mesmo que mediado pela raiz.
- **Degradação graciosa por camada** — falha de um supervisor não derruba os demais (mais resiliente que a estrela nesse aspecto).
- **Overhead de coordenação mensurável** — ao contrário da estrela, aqui existe uma métrica dedicada (`overhead_ms`) para o custo real de ter a camada extra de supervisores.
- **Planejamento decomposto** — o coordenador escolhe os ramos e escreve subobjetivos, e cada supervisor de dados planeja o seu ramo.
- **Redação por ramo** — com mais de um sistema na pergunta, cada sistema tem o seu redator, e o supervisor analítico compõe a resposta.
- **Mais entidades monitoradas** — até 15 agentes-folha + 4 supervisores, contra até 15 agentes na estrela (sem camada de supervisor).
