# Arquitetura Base de um Agente CoALA (e como ele chama o LLM)

> Este documento explica a estrutura comum a **todo** agente do sistema — de onde vêm `working_memory`/`semantic_memory`/`episodic_memory`/`procedural_memory`, como o ciclo cognitivo roda, e o caminho exato de código que um agente percorre quando decide chamar o LLM (DeepSeek por default, OpenAI opcional — ver `LLM_PROVIDER`). Para o pipeline completo de cada topologia, ver [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-arvore.md](arquitetura-arvore.md).

## Sumário

1. [A classe base — `AgenteCoALA`](#a-classe-base--agentecoala)
2. [As quatro memórias](#as-quatro-memórias)
3. [Os três papéis da memória semântica](#os-três-papéis-da-memória-semântica)
4. [O ciclo cognitivo](#o-ciclo-cognitivo)
5. [Ações e `procedural_memory`](#ações-e-procedural_memory)
6. [Recuperação de falha](#recuperação-de-falha)
7. [Como um agente chama o LLM](#como-um-agente-chama-o-llm)
8. [Exemplo completo de ponta a ponta](#exemplo-completo-de-ponta-a-ponta)
9. [Quem herda de `AgenteCoALA` hoje](#quem-herda-de-agentecoala-hoje)
10. [Aderência ao CoALA original](#aderência-ao-coala-original)

---

## A classe base — `AgenteCoALA`

**Arquivo:** `backend/agents/base.py`

Todo agente do sistema — agentes de domínio, analíticos, de contexto, o agente de interpretação de intenção, e os dois orquestradores/coordenador/supervisores — herda desta classe. Ela implementa o framework **CoALA** (*Cognitive Architectures for Language Agents* — Sumers, Yao, Narasimhan & Griffiths, 2023, arXiv:2309.02427), que organiza um agente em três dimensões: como ele guarda informação (memória), qual seu espaço de ações (interno/externo), e como ele decide o que fazer (o ciclo cognitivo).

```python
class AgenteCoALA:
    agent_id: str                                              # ID único (ex: "star-sinan-a1b2c3d4")
    working_memory: dict[str, Any]                             # Buffer de curto prazo
    semantic_memory: dict[str, Any]                             # Fatos/regras de domínio
    episodic_memory: list[dict]                                 # Histórico de ações (sucesso/falha)
    procedural_memory: dict[str, list[Callable[[dict], None]]]  # Estratégias por goal
    _failed_actions: list[dict]                                 # Ações que falharam
```

Nenhum agente concreto reimplementa essas quatro estruturas do zero — todos herdam o `__init__` da base (que as inicializa vazias) e só populam `semantic_memory`/`procedural_memory` no próprio `__init__`.

---

## As quatro memórias

O CoALA separa a informação de um agente por **tempo de vida**: uma memória de curto prazo (`working`) e três de longo prazo (`semantic`, `episodic`, `procedural`), lidas por ações internas de *retrieval* e escritas por ações internas de *learning*. As subseções abaixo seguem a mesma forma — o que o framework propõe, o que este projeto implementa, e por quê.

### `working_memory` — o estado do ciclo atual

**No CoALA:** é o hub. Tudo que vem de uma memória de longo prazo passa por ela antes de ser usado, e tudo que uma ação produz é depositado nela — é o equivalente ao contexto que o LLM enxerga.

**Neste projeto:** `dict` vazio no `__init__`, com um protocolo fixo — o método público do agente (`query()`, `analyze_trends()`, `parse()`) deposita os parâmetros via `update_working_memory(...)`, chama `run_coala_cycle()` e lê o resultado de volta. Cumpre três papéis:

- **Insumo do ciclo.** O `perceive()` dos agentes de domínio lê da própria working memory: a "percepção do ambiente" ali é a percepção do que o caller depositou. O grounding de verdade (Neo4j) só acontece depois, dentro das estratégias.
- **Canal entre ações do mesmo ciclo.** Em `AgenteInterpretacaoIntencao`, `classificar_escopo` grava `working_memory["escopo"]`; `extrair_parametros`, proposta junto e executada depois, já enxerga esse valor. Nenhuma das duas conhece a outra.
- **Canal entre agentes.** Os agentes periféricos não conversam entre si. Cada um escreve na sua working memory, o orquestrador/supervisor recolhe e deposita na dele, e o agente seguinte recebe isso já como estado inicial. A "circulação pela working memory" do CoALA atravessa aqui também a hierarquia de agentes.

### `semantic_memory` — os fatos de domínio

**No CoALA:** o conhecimento do agente sobre o mundo e sobre si mesmo — fatos, não experiências. Tipicamente inicializada a partir de um corpus e consultada sob demanda (o caso canônico é uma base vetorial); o framework também prevê escrita, para o agente aprender fatos novos.

**Neste projeto:** `dict` preenchido **no `__init__` e nunca mais alterado**, montado a partir de constantes de módulo. Não é vetorial, não tem busca por similaridade, não cresce em runtime.

A disciplina que dá sentido à estrutura é a regra de que **nenhuma lógica de decisão lê a constante direto**:

```python
# Evitado — a decisão consome a constante do módulo
if abs(r) >= 0.7: ...

# Adotado — AgenteAnalitico._act_calcular_correlacao
limiar_alta = self.semantic_memory["limiar_alta"]
limiar_media = self.semantic_memory["limiar_media"]
```

Funcionalmente é idêntico; arquiteturalmente não. A constante no módulo é o corpus; o agente só a acessa como conteúdo de memória. O efeito prático é que o conhecimento de domínio fica inspecionável de fora — é exatamente isso que os testes de todos os agentes de domínio verificam (`assert agente.semantic_memory["dimensoes_validas"] == [...]`) — e substituível por instância sem tocar na lógica.

**Onde o retrieval acontece:** em `propose_actions` (dimensões válidas, ângulos de ênfase, mapa indicador → agente), em `evaluate_and_select` (a arbitragem de dimensão opera sobre os candidatos montados a partir dela) e dentro das próprias ações `_act_*` (limiares e polaridade no agente analítico, `subfuncao_codigo` na consulta de despesas, vocabulário válido na extração de parâmetros).

### `episodic_memory` — o histórico de ações

**No CoALA:** guarda experiências de ciclos de decisão anteriores — o que foi tentado, o que deu certo, o que falhou. É a base do aprendizado: o agente recupera episódios para fazer *few-shot* em si mesmo, ou para refletir sobre um erro e não repeti-lo.

**Neste projeto:** a escrita é automática e completa; a leitura é quase inexistente. `_observe_and_learn` grava uma entrada por tentativa de ação, sem que a subclasse precise fazer nada. Mas a lista é **por instância e volátil** — não é persistida — e **nenhum agente lê a própria episodic memory para alterar uma decisão**. Na prática, ela é trilha de auditoria do ciclo, não realimentação.

A exceção, e o único retrieval episódico do sistema, é `AgenteInterpretacaoIntencao._act_buscar_memoria_episodica`: recupera as 3 análises anteriores já registradas no grafo (nós `Analise`, via `neo4j_client.get_past_analises`), anexa o conteúdo recuperado como episódio próprio e o injeta como contexto opcional no prompt que interpreta a pergunta atual. É retrieval **entre sessões** — mais ambicioso que a memória em processo —, mas melhor esforço: se o banco não responder, o ciclo segue sem esse contexto.

### `procedural_memory` — as estratégias

**No CoALA:** o "como fazer", em duas partes — a **implícita**, nos pesos do LLM (a capacidade de raciocinar, que ninguém inspeciona), e a **explícita**, que é o próprio código do agente. O framework observa que alterar essa memória é a modificação mais arriscada que um agente pode fazer em si mesmo, e recomenda cautela.

**Neste projeto:** em vez de deixar o "como fazer" implícito na sequência de chamadas do código, a procedural memory é **materializada como dado inspecionável** — um mapa `goal → lista ordenada de estratégias` que `execute()` percorre em runtime. É mais explícito do que o framework exige, e é o que sustenta duas propriedades: o fallback em cadeia (a primeira estratégia é a primária, as seguintes são planos B) e a degradação graciosa em todos os níveis — inclusive na coordenação, já que orquestrador e supervisores também têm o pipeline inteiro registrado como goals. Não há auto-modificação: as estratégias são registradas no `__init__` e não mudam, alinhado à cautela do próprio CoALA.

### Espaço de ações: internas e externas

O espaço de ações de um agente é dividido em duas categorias (terminologia do próprio CoALA):

- **Internas** (*reasoning*/*retrieval*/*learning*) — processamento sobre a `working_memory`, leitura de `semantic_memory`, gravação em `episodic_memory`. Inclui chamadas ao LLM para raciocinar sobre o estado atual — o LLM é parte da procedural memory implícita do agente, não do ambiente (terminologia do próprio CoALA). Ex.: calcular Spearman, cruzar dados, classificar tendência, reordenar achados, `core.llm_client.generate(...)`.
- **Externas** (*grounding*) — interação com o ambiente fora da cognição do agente: Neo4j, o WebSocket, a delegação a outro agente. Ex.: `neo4j_client.get_indicadores_por_sistema(...)`.

---

## Os três papéis da memória semântica

Descrever a `semantic_memory` como "fatos de domínio" numa única linha esconde que ela faz três trabalhos diferentes no sistema:

| Papel | O que carrega | Efeito |
|---|---|---|
| **Identidade e competência** | `sistema`, `subtipos`, `subfuncao_codigo`/`subfuncao_nome` | Define o que o agente é e até onde vai o seu domínio |
| **Menu fechado de opções** | `dimensoes_validas`, `angulos_possiveis`, `valid_health_params`, `indicador_to_agent` | Converte o LLM de gerador livre em selecionador de item enumerado |
| **Regras de interpretação** | `limiar_alta`/`limiar_media`, `indicadores_negativos`, `stagnation_threshold`, `min_consecutive_years` | Diz o que o número significa — o que a estatística não responde |

**1. Identidade e competência.** O `AgenteSINAN` sabe que é o especialista do sistema `sinan` e que seu domínio são 9 agravos; o `AgenteCNES`, que cuida de 12 subtipos da rede assistencial. Não é configuração, é o que o agente é. O caso mais eloquente é o `AgenteOrcamentoSubfuncao`: existe **uma única classe** para as 7 subfunções orçamentárias, e o que diferencia a instância da Atenção Básica da instância da Vigilância Epidemiológica é só o conteúdo da semantic memory, injetado no nascimento pelo caller. A identidade do agente mora inteiramente ali.

**2. Menu fechado de opções.** O sistema usa LLM para decidir, **nunca para gerar livremente**, e a semantic memory é o mecanismo que fecha a escolha. Na deliberação de dimensão, o agente não pergunta ao LLM "como eu deveria consultar isso?": lê as dimensões válidas do próprio sistema, transforma cada uma num candidato explícito (mais o candidato "sem quebra"), entrega a lista fechada e descarta qualquer resposta fora dela. É por isso que o LLM **nunca escreve Cypher** — ele escolhe um item de um menu que a memória montou.

E a mesma lista cumpre um segundo papel: é a allowlist contra a qual `db/query_builder.py` valida o nome do relacionamento antes de interpolá-lo na query — o único trecho do sistema que entra em Cypher como texto, porque o Neo4j não parametriza reltype. Um fato de domínio e uma fronteira de segurança são, aqui, a mesma estrutura. O padrão se repete nos ângulos de ênfase do `AgentePriorizacaoAnalitica`, no vocabulário do agente de intenção (filtra o que o LLM extraiu da pergunta) e no mapa indicador → agente do orquestrador, que decide quais agentes nem chegam a ser instanciados.

**3. Regras de interpretação.** A estatística produz um número; ela não diz se o número é boa ou má notícia. `indicadores_negativos` responde a isso: é a lista de indicadores em que valor alto é resultado ruim. O fato existe por causa de um erro observado em produção — Spearman devolveu r = +0,77 entre gasto e mortalidade (mais gasto acompanhando mais mortes) e o LLM sintetizador narrou isso como "mais investimento = menos mortes", o oposto do dado. A correção foi usar a polaridade para anexar uma leitura explícita a cada correlação antes de o texto chegar ao LLM. Em vez de confiar que o modelo inferiria o sinal, o agente entrega a interpretação já resolvida — o papel mais característico de uma memória semântica: carregar o conhecimento que o LLM não tem como derivar do dado bruto.

### Inventário — o que cada agente sabe

| Agente | Conteúdo da `semantic_memory` |
|---|---|
| 8 agentes de saúde | `sistema`, `subtipos`, `dimensoes_validas` |
| `AgenteOrcamentoSubfuncao` (7 instâncias) | `subfuncao_codigo`, `subfuncao_nome`, `dimensoes_validas` (`POR_NATUREZA`, `POR_APLICACAO`) |
| `AgenteAnalitico` | `limiar_alta` (0.7), `limiar_media` (0.4), `indicadores_negativos`, `indicadores_positivos`, `subfuncao_nomes` |
| `AgenteContextoOrcamentario` | `stagnation_threshold` (5.0), `min_consecutive_years` (2) |
| `AgentePriorizacaoAnalitica` | `angulos_possiveis` (5 ângulos, nome → descrição) |
| `AgenteInterpretacaoIntencao` | `valid_health_params` (27 tokens) |
| `OrquestradorEstrela`, `SupervisorSaude` | `indicador_to_agent` |
| `CoordenadorGeral`, `SupervisorOrcamento`, `SupervisorAnalitico`, `SupervisorContexto` | **vazia** — recebem tudo pronto e só encadeiam |

> `indicadores_positivos` está declarado mas não é lido: a decisão de polaridade é `tipo in indicadores_negativos`, tratando como positivo tudo que não esteja na lista de negativos.

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
Agent star-sinan-a1b2c3d4 (AgenteSINAN): iniciando ciclo CoALA
Agent star-sinan-a1b2c3d4 (AgenteSINAN): 3 ação(ões) propostas: ['consultar_indicadores', 'consultar_indicadores', 'consultar_indicadores']
Agent star-sinan-a1b2c3d4: dimensão de consulta escolhida via llm: POR_FAIXA_ETARIA
Agent star-sinan-a1b2c3d4 (AgenteSINAN): iniciando ação 'consultar_indicadores' (estratégia 1/2: _act_consultar_indicadores)
Agent star-sinan-a1b2c3d4 (AgenteSINAN): ação 'consultar_indicadores' concluída em 42.3ms
Agent star-sinan-a1b2c3d4 (AgenteSINAN): ciclo CoALA concluído em 900.5ms (1 ações, 0 falha(s) nesta rodada)
```

### Os 4 hooks que uma subclasse sobrescreve

| Método | Implementação padrão (na base) | O que uma subclasse tipicamente faz |
|---|---|---|
| `perceive()` | Retorna `{}` | Lê parâmetros já colocados na `working_memory` pelo caller |
| `propose_actions()` | Retorna `[]` | Decide quais goals propor, com base no estado da `working_memory`. Propõem **múltiplos** candidatos concorrentes para o mesmo goal: os agentes de saúde com dimensões válidas e o `AgenteOrcamentoSubfuncao` (um candidato por dimensão de consulta) e o `AgentePriorizacaoAnalitica` (um por ângulo de ênfase) |
| `evaluate_and_select(candidates)` | `[dict(c, status="pending") for c in candidates]` — passthrough | Os mesmos agentes acima fazem arbitragem de verdade: score determinístico + LLM opcional, com fallback ao maior score (`query_planning.arbitrar_dimensao` / `AgentePriorizacaoAnalitica`). Nos demais (analítico, contexto, intenção, orquestrador, coordenador, supervisores) permanece passthrough, por decisão intencional documentada no código |
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
# Exemplo — AgenteOrcamentoSubfuncao
self.procedural_memory = {
    "consultar_despesas": [
        self._act_consultar_despesas,   # estratégia primária
        self._act_fallback_despesas,     # fallback: lista vazia
    ],
    "analisar_tendencia": [self._act_analisar_tendencia],
    "consultar_variacao_anual": [
        self._act_consultar_variacao_anual,
        self._act_fallback_variacao_anual,
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

Alguns pontos (ex.: a arbitragem de dimensão em `query_planning.arbitrar_dimensao`, a priorização de achados, a verificação pós-síntese) optam por **não** registrar uma segunda estratégia e em vez disso capturam a exceção internamente dentro da própria função — quando o objetivo é "nunca deixar essa etapa quebrar o pipeline, mesmo que o resultado seja só usar um valor padrão", sem precisar do aparato de `ActionFailure`/fallback registrado.

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
| Esforço de raciocínio | — | `OPENAI_REASONING_EFFORT` (default `low`) e `OPENAI_MAX_COMPLETION_TOKENS` (default 16384) — com esforço alto e teto apertado, o modelo gasta o orçamento pensando e devolve resposta vazia |
| Opcionais | — | `OPENAI_BASE_URL` (proxy/gateway compatível), `OPENAI_STORE_LOGS=true` (retém as chamadas na aba Logs da OpenAI, filtráveis por `caller`) |

Em ambos: um único modelo por vez, sem cadeia de fallback entre modelos; retry de até 2 tentativas com backoff linear (`10s × tentativa`), só para erros de rate limit (429). Não há lock global nem intervalo mínimo auto-imposto — chamadas de threads diferentes (estrela e árvore) correm em paralelo de verdade.

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
    def __init__(self, agent_id, min_year=None, max_year=None, neo4j_client=None):
        super().__init__(agent_id)
        self.semantic_memory = {"valid_health_params": VALID_HEALTH_PARAMS}
        self.procedural_memory = {
            "buscar_memoria_episodica": [self._act_buscar_memoria_episodica],
            "classificar_escopo": [
                self._act_classificar_escopo,
                self._act_fallback_classificar_escopo,
            ],
            "extrair_parametros": [self._act_extrair_parametros],
        }

    def propose_actions(self) -> list[dict]:
        if not self.working_memory.get("texto_usuario", "").strip():
            return []
        actions = []
        if self.neo4j_client is not None:
            actions.append({"goal": "buscar_memoria_episodica"})   # análises passadas
        actions.append({"goal": "classificar_escopo"})
        actions.append({"goal": "extrair_parametros"})
        return actions

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

1. **`propose_actions()`** — como há texto do usuário, propõe `classificar_escopo` e `extrair_parametros` (precedidas de `buscar_memoria_episodica` quando há cliente Neo4j, que recupera as últimas análises como contexto).
2. **`evaluate_and_select`** — passthrough (não há candidatos concorrentes aqui — as ações sempre rodam juntas, em sequência).
3. **`execute()`** roda as duas ações **em sequência, na mesma passada**:
   - `classificar_escopo` chama o LLM (única chamada do ciclo — decisão de custo: classifica escopo E extrai parâmetros na mesma resposta JSON) e grava o resultado em `working_memory`.
   - `extrair_parametros` **não chama o LLM de novo** — só lê o que a ação anterior já deixou em `working_memory["_llm_parsed"]` e copia os campos relevantes. Existe como ação própria (não fundida com a primeira) para aparecer como um passo auditável e independente em `episodic_memory`.
4. Se o LLM falhar, `_act_fallback_classificar_escopo` (2ª estratégia registrada) assume, marcando `escopo = "indisponivel"` — o agente segue em estado válido, sem levantar exceção para o caller.

Esse mesmo padrão — uma ação chama o LLM via `core.llm_client.generate(..., caller=...)`, com uma estratégia de fallback registrada (ou um `try/except` interno) para quando o LLM falhar — se repete em todo outro ponto do sistema que usa LLM: `agents/domain/query_planning.py::arbitrar_dimensao` (escolha de dimensão dos agentes de saúde e de orçamento), `agents/analytical/priorizacao.py` (`AgentePriorizacaoAnalitica`, na escolha de ângulo de ênfase) e `core/claim_verifier.py` (verificação pós-síntese).

---

## Quem herda de `AgenteCoALA` hoje

| Agente | Chama o LLM? | Arquivo |
|---|---|---|
| `AgenteInterpretacaoIntencao` | **Sim** — sempre (guardrail + extração) | `agents/intent/agente_interpretacao_intencao.py` |
| `AgenteSINAN`, `AgenteSIM`, `AgenteSIH`, `AgenteSIPNI`, `AgenteSINASC`, `AgenteCNES` | Condicional — só se `use_llm=True`, para arbitrar a dimensão de consulta (sem LLM, maior score determinístico) | `agents/domain/agente_*.py` + `agents/domain/query_planning.py` |
| `AgenteSIA`, `AgenteCOVID` | **Não** — sem dimensões válidas, consulta única | `agents/domain/agente_sia.py`, `agente_covid.py` |
| `AgenteOrcamentoSubfuncao` | Condicional — só se `use_llm=True`, para arbitrar a dimensão das despesas | `agents/domain/agente_orcamento.py` |
| `AgenteAnalitico`, `AgenteContextoOrcamentario` | **Não** — cálculo 100% determinístico, decisão intencional | `agents/analytical/analitico.py`, `agents/context/contexto_orcamentario.py` |
| `AgentePriorizacaoAnalitica` | Condicional — só se `use_llm=True` na análise | `agents/analytical/priorizacao.py` |
| `OrquestradorEstrela`, `CoordenadorGeral`, `SupervisorOrcamento`, `SupervisorSaude`, `SupervisorAnalitico`, `SupervisorContexto` | Não diretamente — delegam a agentes/serviços que chamam | `agents/star/orchestrator.py`, `agents/hierarchical/*.py` |

`TextSynthesizer` (`agents/analytical/sintetizador.py`) também chama o LLM, mas **não** herda de `AgenteCoALA` — é modelado como serviço comum porque não percebe ambiente mutável, não propõe ações concorrentes, e não escolhe entre estratégias alternativas (quem decide isso é o caller). Ver [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-arvore.md](arquitetura-arvore.md) para onde ele entra no pipeline.

---

## Aderência ao CoALA original

| Memória | Previsto no framework | Implementado aqui | Lacuna assumida |
|---|---|---|---|
| **`working_memory`** | Hub de tudo que circula no ciclo atual | Idem, inclusive como canal entre agentes | — |
| **`semantic_memory`** | Leitura e escrita (aprender fatos), tipicamente com retrieval por similaridade | Leitura por chave, em todas as decisões | Sem escrita e sem busca vetorial: o conhecimento é curado por humano, em código |
| **`episodic_memory`** | Experiências passadas realimentam decisões futuras | Escrita automática e completa; leitura num único agente, entre sessões | Ciclo de aprendizado truncado: nenhum agente usa o próprio histórico |
| **`procedural_memory`** | Pesos implícitos do LLM + o código do agente | Materializada como dado inspecionável, com fallback ordenado | — vai além do exigido; sem auto-modificação, por decisão |

As duas lacunas são deliberadas e têm a mesma raiz.

- **Semantic memory sem escrita.** Nenhum fato que sustenta uma conclusão sobre dinheiro público ou indicador de saúde é inventado em runtime: todos são curados em código e versionados com ele. Um agente que aprendesse fatos novos sozinho tornaria a origem de cada afirmação do texto final não rastreável.
- **Episodic memory não realimentada.** Uma decisão que mudasse em função do histórico da instância faria duas execuções da mesma pergunta depender da ordem em que as análises aconteceram. A comparação entre topologias exige que as duas recebam exatamente os mesmos números — é o que a métrica Q1 verifica (ver [metricas-de-avaliacao.md](metricas-de-avaliacao.md)).

Em uma frase: o CoALA é usado aqui como arquitetura de **contenção**, não de autonomia. As quatro memórias estão implementadas, mas o papel que cumprem é restringir o espaço de decisão do LLM a escolhas enumeradas e auditáveis — não deixá-lo aprender e se reconfigurar sozinho. Para um sistema que responde sobre orçamento público e indicadores de saúde, é a troca pretendida; o que esta seção faz é nomeá-la, em vez de deixá-la implícita.
