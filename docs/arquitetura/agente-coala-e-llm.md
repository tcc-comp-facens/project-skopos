# Arquitetura Base de um Agente CoALA (e como ele chama o LLM)

> Este documento explica a estrutura comum a **todo** agente do sistema — de onde vêm `working_memory`/`semantic_memory`/`episodic_memory`/`procedural_memory`, como o ciclo cognitivo roda, e o caminho exato de código que um agente percorre quando decide chamar o LLM (DeepSeek por default, OpenAI opcional — ver `LLM_PROVIDER`). Para o pipeline completo de cada topologia, ver [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-hierarquica.md](arquitetura-hierarquica.md).

## Sumário

1. [A classe base — `AgenteCoALA`](#a-classe-base--agentecoala)
2. [As quatro memórias](#as-quatro-memórias)
3. [O ciclo cognitivo](#o-ciclo-cognitivo)
4. [Ações e `procedural_memory`](#ações-e-procedural_memory)
5. [Recuperação de falha](#recuperação-de-falha)
6. [Como um agente chama o LLM](#como-um-agente-chama-o-llm)
7. [Exemplo completo de ponta a ponta](#exemplo-completo-de-ponta-a-ponta)
8. [Quem herda de `AgenteCoALA` hoje](#quem-herda-de-agentecoala-hoje)

---

## A classe base — `AgenteCoALA`

**Arquivo:** `backend/agents/base.py`

Todo agente do sistema — agentes de domínio, analíticos, de contexto, o agente de interpretação de intenção, e os dois orquestradores/coordenador/supervisores — herda desta classe. Ela implementa o framework **CoALA** (*Cognitive Architectures for Language Agents* — Sumers, Yao, Narasimhan & Griffiths, 2023, arXiv:2309.02427), que organiza um agente em três dimensões: como ele guarda informação (memória), qual seu espaço de ações (interno/externo), e como ele decide o que fazer (o ciclo cognitivo).

```python
class AgenteCoALA:
    agent_id: str                                              # ID único (ex: "star-vigilancia-a1b2c3d4")
    working_memory: dict[str, Any]                             # Buffer de curto prazo
    semantic_memory: dict[str, Any]                             # Fatos/regras de domínio
    episodic_memory: list[dict]                                 # Histórico de ações (sucesso/falha)
    procedural_memory: dict[str, list[Callable[[dict], None]]]  # Estratégias por goal
    _failed_actions: list[dict]                                 # Ações que falharam
```

Nenhum agente concreto reimplementa essas quatro estruturas do zero — todos herdam o `__init__` da base (que as inicializa vazias) e só populam `semantic_memory`/`procedural_memory` no próprio `__init__`.

---

## As quatro memórias

| Memória | O que guarda | Exemplo real no código |
|---|---|---|
| **`working_memory`** | Buffer ativo de curto prazo — parâmetros da análise em andamento, resultados parciais de outros agentes. Equivalente às antigas "beliefs" de um design BDI. | `AgenteVigilanciaEpidemiologica.working_memory["despesas"]` — despesas retornadas pela última consulta ao Neo4j |
| **`semantic_memory`** | Fatos/regras declarativas de domínio, lidos via *retrieval* explícito — nunca hardcoded direto do módulo no meio da lógica de decisão. | `AgenteCorrelacao.semantic_memory = {"limiar_alta": 0.7, "limiar_media": 0.4}` |
| **`episodic_memory`** | Histórico de ações executadas nesta instância (sucesso e falha), gravado automaticamente pela classe base a cada tentativa. Só em memória — descartado ao fim do processo. | Cada entrada: `{"action": goal, "status": "completed"/"failed", "detail": ..., "timestamp": ...}` |
| **`procedural_memory`** | "Como fazer" — lista ordenada de estratégias (métodos `_act_*`) por `goal`. A primeira é a estratégia primária; as demais são fallbacks tentados em ordem se a anterior falhar. | `{"consultar_despesas": [self._act_consultar_despesas, self._act_fallback_despesas]}` |

O espaço de ações de um agente é dividido em duas categorias (terminologia do próprio CoALA):

- **Internas** (*reasoning*/*retrieval*/*learning*) — processamento sobre a `working_memory`, leitura de `semantic_memory`, gravação em `episodic_memory`. Inclui chamadas ao LLM para raciocinar sobre o estado atual — o LLM é parte da procedural memory implícita do agente, não do ambiente (terminologia do próprio CoALA). Ex.: calcular Spearman, cruzar dados, classificar tendência, reordenar achados, `core.llm_client.generate(...)`.
- **Externas** (*grounding*) — interação com o ambiente fora da cognição do agente: Neo4j, o WebSocket, comunicação lateral entre agentes. Ex.: `neo4j_client.get_despesas(...)`.

---

## O ciclo cognitivo

```
    ┌──────────┐
    │ PERCEBER │ ◄─── perceive() — observa o ambiente (lê working memory,
    └────┬─────┘      às vezes consulta Neo4j)
         ▼
    ┌──────────────┐
    │  ATUALIZAR   │ ◄─── update_working_memory(perception) — incorpora
    │WORKING MEMORY│      a percepção
    └────┬─────────┘
         ▼
    ┌──────────────┐
    │   PROPOR     │ ◄─── propose_actions() — gera candidatos de ação
    │   AÇÕES      │      (goals), fazendo retrieval de semantic_memory
    └────┬─────────┘      onde relevante
         ▼
    ┌──────────────┐
    │  AVALIAR E   │ ◄─── evaluate_and_select(candidates) — escolhe quais
    │  SELECIONAR  │      candidatos executar
    └────┬─────────┘
         ▼
    ┌──────────┐
    │ EXECUTAR │ ◄─── execute(actions) — roda cada ação via
    └──────────┘      procedural_memory, registrando o episódio
```

No código, o método `run_coala_cycle()` da classe base executa esse ciclo inteiro em 5 linhas — nenhuma subclasse reimplementa esse método, só os 4 hooks que ele chama:

```python
# backend/agents/base.py
def run_coala_cycle(self) -> None:
    perception = self.perceive()
    self.update_working_memory(perception)
    candidates = self.propose_actions()
    actions = self.evaluate_and_select(candidates)
    self.execute(actions)
```

**Instrumentação de log (observabilidade):** `run_coala_cycle()` loga início/fim do ciclo com timing e a lista de goals propostos; `execute()` loga início/fim de cada ação individual, também com timing. Isso é herdado automaticamente por todo agente — nenhuma subclasse precisa adicionar logging próprio para ter essa visibilidade. Exemplo real de saída (nível `INFO`):

```
Agent star-vigilancia-a1b2c3: iniciando ciclo CoALA
Agent star-vigilancia-a1b2c3: 3 ação(ões) propostas: ['planejar_consulta', 'consultar_despesas', 'consultar_indicadores']
Agent star-vigilancia-a1b2c3: iniciando ação 'planejar_consulta' (estratégia 1/1: _act_planejar_consulta)
Agent star-vigilancia-a1b2c3: ação 'planejar_consulta' concluída em 0.1ms
...
Agent star-vigilancia-a1b2c3: ciclo CoALA concluído em 1.0ms (3 ações, 0 falha(s) nesta rodada)
```

### Os 4 hooks que uma subclasse sobrescreve

| Método | Implementação padrão (na base) | O que uma subclasse tipicamente faz |
|---|---|---|
| `perceive()` | Retorna `{}` | Lê parâmetros já colocados na `working_memory` pelo caller |
| `propose_actions()` | Retorna `[]` | Decide quais goals propor, com base no estado da `working_memory` — normalmente 1 candidato por goal aplicável; só `AgentePriorizacaoAnalitica` propõe **múltiplos** candidatos concorrentes para o mesmo goal |
| `evaluate_and_select(candidates)` | `[dict(c, status="pending") for c in candidates]` — passthrough | Na maioria dos agentes, permanece passthrough (não há candidatos concorrentes a arbitrar — decisão intencional documentada no código, não omissão). Só `AgentePriorizacaoAnalitica` faz arbitragem de verdade aqui |
| `execute(actions)` | Já implementado na base (ver abaixo) — normalmente **não** é sobrescrito | — |

---

## Ações e `procedural_memory`

`execute()` (implementado uma única vez na classe base, herdado por todos) percorre cada ação selecionada e tenta as estratégias registradas em `procedural_memory[goal]`, em ordem:

```python
# backend/agents/base.py (simplificado — ver arquivo para a versão com logging)
def execute(self, actions: list[dict]) -> None:
    for action in actions:
        goal = action.get("goal")
        strategies = self.procedural_memory.get(goal, [])
        for i, strategy in enumerate(strategies):
            try:
                strategy(action)
            except ActionFailure as exc:
                action["status"] = "failed"
                self._failed_actions.append(dict(action, reason=exc.reason))
                self._observe_and_learn(action, "failed", detail=exc.reason)
                continue  # tenta a próxima estratégia (fallback)
            else:
                action["status"] = "completed"
                self._observe_and_learn(action, "completed")
                break
```

Cada estratégia é um método `_act_*(self, action: dict) -> None` da própria subclasse, registrado explicitamente no `__init__`:

```python
# Exemplo — agente de domínio
self.procedural_memory = {
    "planejar_consulta": [self._act_planejar_consulta],
    "consultar_despesas": [
        self._act_consultar_despesas,   # estratégia primária
        self._act_fallback_despesas,     # fallback: lista vazia
    ],
    "consultar_indicadores": [
        self._act_consultar_indicadores,
        self._act_fallback_indicadores,
    ],
}
```

---

## Recuperação de falha

Quando uma estratégia primária levanta `ActionFailure`, `execute()` automaticamente tenta a próxima estratégia registrada para o mesmo goal (se houver). O agente **nunca fica em estado indefinido**:

```
Executando ação "consultar_despesas"
          │
          ▼
  Neo4j offline! ──► ActionFailure levantada
          │
          ▼
  Próxima estratégia registrada: _act_fallback_despesas
          │
          ▼
  working_memory["despesas"] = []   (dados parciais em vez de falha total)
```

```python
# backend/agents/base.py
class ActionFailure(Exception):
    def __init__(self, action: dict, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"Action failed: {reason}")
```

Se **todas** as estratégias registradas falharem, a ação é marcada `"failed"` e o laço segue para a próxima ação — o erro fica registrado em `_failed_actions` e `episodic_memory`, e é reportado ao orquestrador/supervisor, que decide como continuar (tipicamente: degradação graciosa, continuar com dados parciais).

Alguns agentes (ex.: `AgenteInterpretacaoIntencao`, os agentes de domínio na ação `planejar_consulta`) optam por **não** registrar uma segunda estratégia e em vez disso capturam a exceção internamente dentro da própria função — quando o objetivo é "nunca deixar essa etapa quebrar o pipeline, mesmo que o resultado seja só usar um valor padrão", sem precisar do aparato de `ActionFailure`/fallback registrado.

---

## Como um agente chama o LLM

**Arquivo:** `backend/core/llm_client.py`

Não existe um método `self.chamar_llm()` na classe base — cada agente que precisa de LLM importa `core.llm_client` **dentro** do método `_act_*` que faz a chamada (não no topo do módulo — evita import circular e facilita mock em teste) e chama `generate()` (modo batch) ou `generate_stream()` (modo streaming, usado só pelo sintetizador de texto).

```python
def generate(
    prompt: str, model: Optional[str] = None, *, caller: str = "desconhecido"
) -> Optional[str]:
    ...
```

### O provedor: DeepSeek (default) ou OpenAI

Qual provedor responde é decidido pela variável `LLM_PROVIDER` — lida a cada chamada, não no import, então basta trocar o `.env` e reiniciar o backend. Nenhum agente sabe qual provedor está ativo: todos chamam `generate()`/`generate_stream()`. O SDK é o `openai` nos dois casos (a API do DeepSeek é compatível), e o backend loga o provedor resolvido no startup e em cada chamada (`provider=`).

| Aspecto | `LLM_PROVIDER=deepseek` (default) | `LLM_PROVIDER=openai` |
|---|---|---|
| Endpoint | `base_url="https://api.deepseek.com"` | endpoint oficial do SDK |
| Modelo | `deepseek-v4-flash` | `gpt-5.6-luna` |
| Variável da chave | `DEEPSEEK_API_KEY` | `OPENAI_API_KEY` |
| Override de modelo | `DEEPSEEK_MODEL` | `OPENAI_MODEL` |
| `thinking` | Desabilitado na chamada (`extra_body={"thinking": {"type": "disabled"}}`) — resposta direta, sem chain-of-thought | Não enviado — é parâmetro proprietário do DeepSeek e a OpenAI responde 400 a parâmetro desconhecido |
| Limite de saída | `max_tokens` | `max_completion_tokens` (sem `temperature`) nos modelos de raciocínio (`gpt-5*`, série `o*`), que rejeitam a forma clássica; `max_tokens` nos demais |

Em ambos: um único modelo por vez, sem cadeia de fallback entre modelos; retry de até 2 tentativas com backoff linear (`10s × tentativa`), só para erros de rate limit (429). Não há lock global nem intervalo mínimo auto-imposto — chamadas de threads diferentes (estrela e hierárquica) correm em paralelo de verdade.

### O que acontece a cada chamada

1. **Antes da chamada** — log em `INFO` com um preview de uma linha do prompt (truncado a ~300 chars) e o tamanho total; o prompt **completo** só aparece em `DEBUG` (`LOG_LEVEL=DEBUG`).
2. **A chamada em si** — dispara direto, sem espera preventiva; em caso de 429, espera e tenta de novo (até 2x).
3. **Pós-processamento** — remove automaticamente tags `<think>...</think>` da resposta, caso apareçam (defensivo, já que `thinking` está desabilitado).
4. **Depois da chamada** — log com o tamanho da resposta recebida e a contagem de tokens (prompt/completion/total). O **conteúdo** da resposta não é logado (só o tamanho) — assimétrico com o prompt, que tem preview + versão completa em DEBUG.
5. **Falha** (indisponível, erro fatal, ou todas as tentativas de retry esgotadas) — retorna `None` em vez de levantar exceção. Quem chamou decide o que fazer (tipicamente: usar um valor padrão determinístico).

### O parâmetro `caller` — rastreabilidade

Todo call site passa `caller=` — normalmente o `agent_id` do agente, às vezes com um sufixo de propósito (`f"{self.agent_id}:algum_proposito"`) quando o mesmo agente pode chamar o LLM por mais de um motivo. Isso não afeta o comportamento da chamada — é usado só para os logs, para responder "qual agente disparou qual chamada e por quê" quando várias análises/threads rodam ao mesmo tempo:

```
LLM [star-priorizacao-9f8e7d:priorizar_achados]: enviando prompt (model=deepseek-v4-flash, 850 chars) — ...
```

---

## Exemplo completo de ponta a ponta

O `AgenteInterpretacaoIntencao` (`backend/agents/intent/agente_interpretacao_intencao.py`) é o exemplo mais direto de um agente CoALA que chama o LLM — sua única ação de fato faz isso:

```python
class AgenteInterpretacaoIntencao(AgenteCoALA):
    def __init__(self, agent_id, min_year=None, max_year=None):
        super().__init__(agent_id)
        self.semantic_memory = {"valid_health_params": VALID_HEALTH_PARAMS}
        self.procedural_memory = {
            "classificar_escopo": [
                self._act_classificar_escopo,
                self._act_fallback_classificar_escopo,
            ],
            "extrair_parametros": [self._act_extrair_parametros],
        }

    def propose_actions(self) -> list[dict]:
        if not self.working_memory.get("texto_usuario", "").strip():
            return []
        return [
            {"goal": "classificar_escopo"},
            {"goal": "extrair_parametros"},
        ]

    def _act_classificar_escopo(self, action: dict) -> None:
        texto = self.working_memory["texto_usuario"]
        try:
            import core.llm_client as llm_client

            prompt = self._build_prompt(texto, ...)
            raw = llm_client.generate(
                prompt, caller=f"{self.agent_id}:classificar_e_extrair"
            )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

        if not raw:
            raise ActionFailure(action, "LLM retornou resposta vazia")

        parsed = self._parse_llm_json(raw)  # parsing JSON estrito, chaves esperadas
        if parsed is None:
            raise ActionFailure(action, "resposta do LLM não é um JSON válido/esperado")

        self.working_memory["escopo"] = "dentro" if parsed.get("em_escopo") else "fora"
        self.working_memory["_llm_parsed"] = parsed

    def _act_fallback_classificar_escopo(self, action: dict) -> None:
        # 2ª estratégia registrada — roda se _act_classificar_escopo levantar ActionFailure
        self.working_memory["escopo"] = "indisponivel"
```

Percorrendo o ciclo com esse exemplo:

1. **`propose_actions()`** — como há texto do usuário, propõe **duas** ações juntas: `classificar_escopo` e `extrair_parametros`.
2. **`evaluate_and_select`** — passthrough (não há candidatos concorrentes aqui — as duas ações sempre rodam juntas, uma depois da outra).
3. **`execute()`** roda as duas ações **em sequência, na mesma passada**:
   - `classificar_escopo` chama o LLM (única chamada do ciclo — decisão de custo: classifica escopo E extrai parâmetros na mesma resposta JSON) e grava o resultado em `working_memory`.
   - `extrair_parametros` **não chama o LLM de novo** — só lê o que a ação anterior já deixou em `working_memory["_llm_parsed"]` e copia os campos relevantes. Existe como ação própria (não fundida com a primeira) para aparecer como um passo auditável e independente em `episodic_memory`.
4. Se o LLM falhar, `_act_fallback_classificar_escopo` (2ª estratégia registrada) assume, marcando `escopo = "indisponivel"` — o agente segue em estado válido, sem levantar exceção para o caller.

Esse mesmo padrão — uma ação chama o LLM via `core.llm_client.generate(..., caller=...)`, com uma estratégia de fallback registrada (ou um `try/except` interno) para quando o LLM falhar — se repete em todo outro ponto do sistema que usa LLM: `agents/domain/query_planning.py` (planejamento de consulta dos agentes de domínio) e `agents/analytical/priorizacao.py` (`AgentePriorizacaoAnalitica`, na escolha de ângulo de ênfase).

---

## Quem herda de `AgenteCoALA` hoje

| Agente | Chama o LLM? | Arquivo |
|---|---|---|
| `AgenteInterpretacaoIntencao` | **Sim** — sempre (guardrail + extração) | `agents/intent/agente_interpretacao_intencao.py` |
| `AgenteVigilanciaEpidemiologica`, `AgenteSaudeHospitalar`, `AgenteAtencaoPrimaria`, `AgenteMortalidade` | Condicional — só se `USE_LLM_QUERY_PLANNING` estiver ligada **e** o mapeamento estático não for mais trivial (fast-path por padrão) | `agents/domain/*.py` + `agents/domain/query_planning.py` |
| `AgenteCorrelacao`, `AgenteAnomalias`, `AgenteContextoOrcamentario` | **Não** — cálculo 100% determinístico, decisão intencional | `agents/analytical/correlacao.py`, `anomalias.py`, `agents/context/contexto_orcamentario.py` |
| `AgentePriorizacaoAnalitica` | Condicional — só se `use_llm=True` na análise | `agents/analytical/priorizacao.py` |
| `OrquestradorEstrela`, `CoordenadorGeral`, `SupervisorDominio`, `SupervisorAnalitico`, `SupervisorContexto` | Não diretamente — delegam a agentes/serviços que chamam | `agents/star/orchestrator.py`, `agents/hierarchical/*.py` |

`TextSynthesizer` (`agents/analytical/sintetizador.py`) também chama o LLM, mas **não** herda de `AgenteCoALA` — é modelado como serviço comum porque não percebe ambiente mutável, não propõe ações concorrentes, e não escolhe entre estratégias alternativas (quem decide isso é o caller). Ver [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-hierarquica.md](arquitetura-hierarquica.md) para onde ele entra no pipeline.
