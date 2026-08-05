# Sistema Multiagente CoALA

## Sumário

1. [O que é um Agente?](#o-que-é-um-agente)
2. [Modelo CoALA](#modelo-coala-cognitive-architectures-for-language-agents)
3. [Classe Base AgenteCoALA](#classe-base-agentecoala)
4. [Agente de Interpretação de Intenção (1)](#agente-de-interpretação-de-intenção-1)
5. [Agentes de Domínio (4)](#agentes-de-domínio-4)
6. [Agentes Analíticos (2 CoALA + 1 serviço)](#agentes-analíticos-2-coala--1-serviço)
7. [Agente de Contexto (1)](#agente-de-contexto-1)
8. [Arquitetura Estrela](#arquitetura-estrela)
9. [Arquitetura Hierárquica](#arquitetura-hierárquica)
10. [Regras de Negócio](#regras-de-negócio)

---

## O que é um Agente?

Um **agente** é um programa autônomo que percebe o ambiente, toma decisões e age para atingir objetivos. Diferente de uma função comum que recebe input e retorna output, um agente tem:

- **Autonomia** — decide sozinho o que fazer
- **Reatividade** — responde a mudanças no ambiente
- **Proatividade** — age para atingir objetivos, não só reage
- **Estado interno** — mantém memória e planos de ação

```
┌─────────────────────────────────────────────┐
│              AGENTE                          │
│                                             │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│   │ Memória  │  │Percepção │  │  Ação    │ │
│   │ (o que   │  │ (o que   │  │ (o que   │ │
│   │  sei)    │  │  observo)│  │  faço)   │ │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘ │
│        │             │             │        │
│        └──────┬──────┘             │        │
│               ▼                    ▼        │
│         Deliberação           Execução      │
│                                             │
│   Percepção ◄──── Ambiente ────► Ação       │
└─────────────────────────────────────────────┘
```

Neste projeto, cada agente é uma classe Python que herda de `AgenteCoALA` e se especializa em uma tarefa: interpretar a intenção do usuário, consultar dados, calcular correlações, detectar anomalias, etc.

---

## Modelo CoALA (Cognitive Architectures for Language Agents)

O modelo **CoALA** (Sumers, Yao, Narasimhan & Griffiths, 2023 — *"Cognitive Architectures for Language Agents"*, arXiv:2309.02427) é um framework para organizar agentes baseados em LLM segundo três dimensões: como armazenam informação (memória de curto e longo prazo), qual o espaço de ações disponível (internas e externas), e como decidem o que fazer (um loop de percepção-proposta-avaliação-execução).

### Memória — quatro sistemas

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   WORKING MEMORY (memória de trabalho)                      │
│   ─────────────────────────────────────                     │
│   Buffer ativo de curto prazo — equivalente às antigas       │
│   "beliefs". Parâmetros da análise, resultados parciais de   │
│   outros agentes, tudo que está "em uso" agora.               │
│                                                             │
│   SEMANTIC MEMORY (memória semântica)                        │
│   ─────────────────────────────────                          │
│   Fatos/regras declarativas de domínio — ex: mapeamento       │
│   subfunção→indicador, limiares de classificação. Lidos via   │
│   *retrieval* explícito dentro de `propose_actions`, nunca    │
│   hardcoded direto do módulo no meio da lógica de decisão.    │
│                                                             │
│   EPISODIC MEMORY (memória episódica)                        │
│   ────────────────────────────────                           │
│   Histórico de ações executadas nesta instância (sucesso e   │
│   falha), gravado via `_observe_and_learn` — só em memória,   │
│   descartada ao fim do processo.                              │
│                                                             │
│   PROCEDURAL MEMORY (memória procedural)                      │
│   ──────────────────────────────────                         │
│   "Como fazer" — lista ordenada de estratégias (callables)    │
│   por goal; a primeira é a estratégia primária, as demais são │
│   fallbacks tentados em ordem se a anterior falhar.            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Espaço de ações

- **Internas** (reasoning/retrieval/learning) — processamento sobre a working memory, leitura de semantic memory, gravação em episodic memory. Ex.: calcular Spearman, cruzar dados, classificar tendência.
- **Externas** (grounding) — qualquer interação com o ambiente fora do processo do agente: Neo4j, LLM, WebSocket, comunicação lateral entre agentes. Ex.: consultar despesas no Neo4j, chamar o LLM para classificar escopo/gerar texto.

### O Ciclo CoALA

Todo agente executa o mesmo ciclo de raciocínio:

```
    ┌──────────┐
    │ PERCEBER │ ◄─── Observa o ambiente (lê working memory, consulta Neo4j)
    └────┬─────┘
         ▼
    ┌──────────────┐
    │  ATUALIZAR   │ ◄─── Incorpora novas informações à working memory
    │WORKING MEMORY│
    └────┬─────────┘
         ▼
    ┌──────────────┐
    │   PROPOR     │ ◄─── Gera candidatos de ação (goals), fazendo
    │   AÇÕES      │      retrieval de semantic memory onde relevante
    └────┬─────────┘
         ▼
    ┌──────────────┐
    │  AVALIAR E   │ ◄─── Seleciona quais candidatos executar
    │  SELECIONAR  │
    └────┬─────────┘
         ▼
    ┌──────────┐
    │ EXECUTAR │ ◄─── Executa cada ação via procedural memory,
    └──────────┘      registrando o episódio (observe/learn)
```

**Exemplo concreto** — `AgenteVigilanciaEpidemiologica`:

```
1. PERCEBER:     working_memory já tem analysis_id="abc", período 2019-2021
2. ATUALIZAR:    working_memory = {analysis_id: "abc", date_from: 2019, date_to: 2021}
3. PROPOR AÇÕES: "Tenho os parâmetros → proponho consultar_despesas E
                  consultar_indicadores" (retrieval de semantic_memory
                  para saber subfunção=305, tipos=["dengue","covid"])
4. AVALIAR/SEL.: as duas ações propostas são selecionadas (não há
                  candidatos concorrentes a arbitrar neste agente)
5. EXECUTAR:     → Query Neo4j: DespesaSIOPS subfunção 305
                  → Query Neo4j: IndicadorDataSUS tipo IN [dengue, covid]
                  → working_memory["despesas"] = [...]
                  → working_memory["indicadores"] = [...]
```

### O Ciclo CoALA no Código

O método `run_coala_cycle()` da classe base executa o ciclo completo:

```python
# backend/agents/base.py — AgenteCoALA

def run_coala_cycle(self) -> None:
    """Ciclo CoALA completo.

    perceive → update_working_memory → propose_actions →
    evaluate_and_select → execute (que já embute observe/learn por ação).
    """
    perception = self.perceive()
    self.update_working_memory(perception)
    candidates = self.propose_actions()
    actions = self.evaluate_and_select(candidates)
    self.execute(actions)
```

Cada passo corresponde a um método que as subclasses sobrescrevem:

**1. Perceber** — o agente lê a working memory e extrai o que é relevante:

```python
# Implementação padrão (retorna vazio)
def perceive(self) -> dict:
    return {}

# Implementação real (agente de domínio)
def perceive(self) -> dict:
    return {
        "analysis_id": self.working_memory.get("analysis_id"),
        "date_from": self.working_memory.get("date_from"),
        "date_to": self.working_memory.get("date_to"),
    }
```

**2. Atualizar working memory** — incorpora novas informações (mesmo para todos):

```python
def update_working_memory(self, perception: dict) -> None:
    self.working_memory.update(perception)
```

**3. Propor ações** — aqui está a **tomada de decisão**. O agente avalia a working memory e propõe candidatos de ação. Cada agente tem sua própria lógica:

```python
# Agente de domínio — "se tenho parâmetros, proponho consultar"
def propose_actions(self) -> list[dict]:
    actions = []
    if self.working_memory.get("analysis_id") and self.working_memory.get("date_from") is not None:
        actions.append({"goal": "consultar_despesas"})
        actions.append({"goal": "consultar_indicadores"})
    # Se NÃO tem parâmetros → actions fica vazia → agente não faz nada
    return actions

# AgenteCorrelacao — "se tenho dados cruzados, proponho calcular"
def propose_actions(self) -> list[dict]:
    if self.working_memory.get("dados_cruzados"):
        return [{"goal": "calcular_correlacoes"}]
    return []

# TextSynthesizer — serviço (não é agente CoALA), não usa propose_actions()
# O caller invoca diretamente: sintetizador.generate(correlacoes, anomalias, contexto)
```

**4. Avaliar e selecionar** — decide quais candidatos executar (1 candidato = 1 ação selecionada, na maioria dos agentes):

```python
def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
    """Neste sistema não há candidatos concorrentes com score real a
    arbitrar — evaluate e select colapsam num único passo por decisão
    intencional (documentado no código, não por omissão)."""
    return [dict(c, status="pending") for c in candidates]
```

**5. Executar** — percorre cada ação pendente pela procedural memory, com recuperação de falha:

```python
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

### A exceção ActionFailure

Quando algo dá errado, o agente encapsula o erro com contexto da ação:

```python
# backend/agents/base.py
class ActionFailure(Exception):
    def __init__(self, action: dict, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"Action failed: {reason}")

# Uso em um agente de domínio:
def _act_consultar_despesas(self, action: dict) -> None:
    try:
        despesas = self.neo4j_client.get_despesas(...)
    except Exception as e:
        raise ActionFailure(action, str(e)) from e
```

### Recuperação de Falhas

Quando uma estratégia falha, `procedural_memory[goal]` pode ter uma estratégia de fallback registrada em seguida:

```
    Executando ação "consultar_despesas"
              │
              ▼
    ┌─────────────────┐
    │  Neo4j offline!  │ ──► ActionFailure levantada
    └────────┬────────┘
             ▼
    ┌─────────────────┐
    │ Próxima          │ ──► Tenta a estratégia de fallback registrada:
    │ estratégia       │     "_act_fallback_despesas" — retorna lista vazia
    └────────┬────────┘
             ▼
    ┌─────────────────┐
    │ Agente continua  │ ──► working_memory["despesas"] = []
    │ em estado válido │     (orquestrador recebe dados parciais)
    └─────────────────┘
```

No código, a recuperação é implementada como uma segunda estratégia registrada para o mesmo goal:

```python
# Agente de domínio — procedural_memory com fallback
self.procedural_memory = {
    "consultar_despesas": [
        self._act_consultar_despesas,      # estratégia primária
        self._act_fallback_despesas,        # fallback: lista vazia
    ],
    ...
}

def _act_fallback_despesas(self, action: dict) -> None:
    self.working_memory["despesas"] = []    # dados parciais em vez de falha total
```

O agente **nunca fica em estado indefinido**. Se todas as estratégias registradas falharem, a ação é marcada `"failed"` e o laço segue para a próxima ação — o erro é reportado ao orquestrador/supervisor via `_failed_actions`, que decide como continuar.

---

## Classe Base AgenteCoALA

**Arquivo:** `backend/agents/base.py`

Todos os agentes herdam desta classe (agentes de domínio, analíticos, contexto, o agente de interpretação de intenção, e os dois orquestradores/coordenador/supervisores). Ela fornece o esqueleto do ciclo CoALA:

```python
class AgenteCoALA:
    agent_id: str                                          # ID único (ex: "star-vigilancia-a1b2c3d4")
    working_memory: dict[str, Any]                         # Buffer de curto prazo
    semantic_memory: dict[str, Any]                        # Fatos/regras de domínio
    episodic_memory: list[dict]                            # Histórico de ações (sucesso/falha)
    procedural_memory: dict[str, list[Callable[[dict], None]]]  # Estratégias por goal
    _failed_actions: list[dict]                            # Ações que falharam
```

### Métodos que cada agente sobrescreve

| Método | O que faz | Exemplo |
|--------|-----------|---------|
| `perceive()` | Observa o ambiente | Lê parâmetros da working memory |
| `propose_actions()` | Decide candidatos de ação | "Tenho dados → proponho calcular correlações" |
| `evaluate_and_select(candidates)` | Seleciona o que executar | Passthrough na maioria dos agentes (sem candidatos concorrentes) |
| `_act_*(action)` | Executa uma ação | Roda query no Neo4j, chama o LLM |
| `_act_fallback_*(action)` | Trata falha (2ª estratégia) | Retorna lista vazia como fallback |

### Padrão de IDs

Formato: `{topologia}-{papel}-{uuid_hex[:8]}`

```
star-vigilancia-a1b2c3d4     ← agente de vigilância na topologia estrela
hier-sup-dominio-m3n4o5p6    ← supervisor de domínio na hierárquica
star-correlacao-e5f6g7h8     ← agente de correlação na estrela
intent-9f8e7d6c              ← agente de interpretação de intenção (uma instância por sessão de chat, não amarrado a uma topologia)
```

---

## Agente de Interpretação de Intenção (1)

### AgenteInterpretacaoIntencao — Guardrail de Escopo + Extração via LLM

**Arquivo:** `agents/intent/agente_interpretacao_intencao.py`

Camada de entrada compartilhada pelas duas arquiteturas quando a análise é disparada via chat (`WS /ws/chat/{sessionId}`). Substitui totalmente um design anterior baseado em regex-primário/LLM-fallback — hoje **não há regex neste módulo**: toda mensagem do usuário passa pelo LLM.

**Duas ações, uma única chamada LLM:**

```
┌─────────────────────────────────────────────────────────────────┐
│              AgenteInterpretacaoIntencao.parse(texto)             │
│                                                                   │
│  propose_actions() propõe as duas ações juntas — execute() as    │
│  roda em ordem na mesma passada do ciclo CoALA:                   │
│                                                                   │
│  1. classificar_escopo                                            │
│     ─────────────────                                            │
│     Única chamada LLM do ciclo. Prompt combinado pede ao LLM:     │
│     (a) classificar se a mensagem está DENTRO do escopo do        │
│         assistente (dados orçamentários/saúde pública de          │
│         Sorocaba-SP) e (b) — se estiver dentro — já extrair       │
│         date_from/date_to/health_params/intent_summary na         │
│         MESMA resposta JSON (decisão de custo: evita 2 chamadas   │
│         de rede por mensagem).                                    │
│     → grava working_memory["escopo"] = "dentro" | "fora"          │
│     → grava working_memory["_llm_parsed"] com a resposta bruta    │
│                                                                   │
│  2. extrair_parametros                                            │
│     ──────────────────                                           │
│     Ação puramente interna — NÃO chama o LLM de novo. Só copia    │
│     os campos já obtidos em (1) para working_memory quando        │
│     escopo == "dentro". Existe como ação própria (e não fundida   │
│     com a anterior) para que o guardrail apareça como um passo    │
│     auditável e independente em episodic_memory.                  │
│                                                                   │
│  Se escopo == "fora": nenhum parâmetro é extraído, e parse()      │
│  retorna uma recusa — nenhuma arquitetura (estrela/hierárquica)   │
│  chega a ser instanciada pelo caller.                             │
└─────────────────────────────────────────────────────────────────┘
```

**Fallback (LLM indisponível ou resposta inválida):** `procedural_memory["classificar_escopo"]` registra uma segunda estratégia (`_act_fallback_classificar_escopo`) que grava `working_memory["escopo"] = "indisponivel"` — distinto de `"fora"`: é uma falha técnica, não uma recusa de escopo, e gera uma mensagem diferente ao usuário ("não consegui interpretar agora" em vez de "isso está fora do meu escopo").

**Saída — `AnalysisIntent`:**

```python
@dataclass
class AnalysisIntent:
    date_from: int
    date_to: int
    health_params: list[str]
    intent_summary: str   # resumo de 1 frase da intenção do usuário
```

`intent_summary` é o campo que estende o antigo `AnalysisParams` — carregado no dict `params` repassado tanto ao `OrquestradorEstrela` quanto ao `CoordenadorGeral` (chave `intent_summary`), para eventual uso por etapas futuras de priorização de achados (ver `PLANO_REFATORACAO.md`).

**Segurança:** a mensagem do usuário é tratada como dado a ser classificado/interpretado, nunca como instrução — mitigação de prompt injection. A resposta do LLM é validada por parsing JSON estrito (só as chaves esperadas: `em_escopo`, `date_from`, `date_to`, `health_params`, `intent_summary`) antes de qualquer uso.

**Observabilidade:** a chamada ao LLM é rotulada `caller=f"{self.agent_id}:classificar_e_extrair"` — aparece nos logs de `core/llm_client.py` identificando exatamente qual instância do agente e com qual propósito disparou a chamada.

**Interface pública:**

```python
def parse(self, texto: str, reference_year: int | None = None) -> IntentResult:
    """Retorna IntentResult(success, params, missing, clarification_message,
    interpreted_via). Sem regex — toda mensagem não-vazia passa pelo
    guardrail de escopo antes de qualquer extração de parâmetro."""
```

Chamado por `api/chat_websocket.py` a cada turno do chat.

---

## Agentes de Domínio (4)

Os agentes de domínio são os "coletores de dados". Cada um consulta o Neo4j para buscar despesas de uma subfunção específica e indicadores de saúde correspondentes.

```
┌─────────────────────────────────────────────────────────────────┐
│                    AGENTES DE DOMÍNIO                           │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐                      │
│  │ Vigilância       │  │ Saúde           │                      │
│  │ Epidemiológica   │  │ Hospitalar      │                      │
│  │                  │  │                 │                      │
│  │ Subfunção: 305   │  │ Subfunção: 302  │                      │
│  │ Indicadores:     │  │ Indicadores:    │                      │
│  │ • dengue         │  │ • internações   │                      │
│  │ • covid          │  │                 │                      │
│  └─────────────────┘  └─────────────────┘                      │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐                      │
│  │ Atenção          │  │ Mortalidade     │                      │
│  │ Primária         │  │                 │                      │
│  │                  │  │ Subfunção: TODAS│                      │
│  │ Subfunção: 301   │  │ (transversal)   │                      │
│  │ Indicadores:     │  │ Indicadores:    │                      │
│  │ • vacinação      │  │ • mortalidade   │                      │
│  └─────────────────┘  └─────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

### Como funciona um agente de domínio

Todos seguem o mesmo padrão. Vamos usar o **AgenteVigilanciaEpidemiologica** como exemplo:

```
Orquestrador chama: agente.query(analysis_id="abc", date_from=2019, date_to=2021)
                          │
                          ▼
              ┌───────────────────────┐
              │  update_working_memory│
              │  working_memory = {   │
              │    analysis_id: "abc" │
              │    date_from: 2019    │
              │    date_to: 2021      │
              │  }                    │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  perceive()           │
              │  "Tenho analysis_id,  │
              │   date_from, date_to" │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  propose_actions()    │
              │  "Proponho:           │
              │   - consultar_despesas│
              │   - consultar_        │
              │     indicadores"      │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  evaluate_and_select()│
              │  actions = [          │
              │   {consultar_despesas,│
              │    status: pending},  │
              │   {consultar_         │
              │    indicadores,       │
              │    status: pending}   │
              │  ]                    │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  execute()            │
              │                       │
              │  Ação 1:              │
              │  Neo4j ──► despesas   │
              │  WHERE subfuncao=305  │
              │  AND ano >= 2019      │
              │  AND ano <= 2021      │
              │                       │
              │  Ação 2:              │
              │  Neo4j ──► indicadores│
              │  WHERE tipo IN        │
              │  ["dengue", "covid"]  │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  Retorna:             │
              │  {                    │
              │   despesas: [         │
              │    {subfuncao: 305,   │
              │     ano: 2019,        │
              │     valor: 28350000}  │
              │   ],                  │
              │   indicadores: [      │
              │    {tipo: "dengue",   │
              │     ano: 2019,        │
              │     valor: 12847}     │
              │   ]                   │
              │  }                    │
              └───────────────────────┘
```

### Tabela dos 4 agentes de domínio

| Agente | Arquivo | Subfunção | Indicadores | Particularidade |
|--------|---------|-----------|-------------|-----------------|
| `AgenteVigilanciaEpidemiologica` | `domain/vigilancia_epidemiologica.py` | 305 | dengue, covid | Filtra subfunção 305 das despesas |
| `AgenteSaudeHospitalar` | `domain/saude_hospitalar.py` | 302 | internacoes | Filtra subfunção 302 |
| `AgenteAtencaoPrimaria` | `domain/atencao_primaria.py` | 301 | vacinacao | Filtra subfunção 301 |
| `AgenteMortalidade` | `domain/mortalidade.py` | TODAS | mortalidade | **Transversal** — retorna despesas de todas as subfunções |

**Comportamento comum:**
- Recebem `(agent_id, neo4j_client)` no construtor
- Método público: `query(analysis_id, date_from, date_to)` → `{"despesas": [...], "indicadores": [...]}`
- Retornam listas vazias (sem exceção) quando não há dados
- Se o Neo4j falhar: a estratégia de fallback registrada retorna listas vazias
- Logam os parâmetros da consulta (subfunção/tipos, período) **antes** de cada query, e o resultado (contagem) depois — ver `core/llm_client.py` e `agents/base.py` para o resto da instrumentação de execução

**Decisão de design deliberada, não lacuna:** qual subfunção/indicador cada agente busca é hardcoded (constantes de módulo), sem LLM envolvido — isso é intencional; a decisão "o que buscar" já é conhecida estaticamente hoje. O `PLANO_REFATORACAO.md` (Etapa 2, ainda não implementada) propõe introduzir LLM aqui como preparação arquitetural para quando a base de dados crescer com fontes/indicadores mais ambíguos — não como necessidade atual.

---

## Agentes Analíticos (2 CoALA + 1 serviço)

Os agentes analíticos processam dados **em memória** — não acessam Neo4j. Recebem dados já coletados pelos agentes de domínio e produzem análises.

```
┌─────────────────────────────────────────────────────────────────┐
│                   AGENTES ANALÍTICOS                            │
│                                                                 │
│  Dados dos agentes     ┌──────────────┐                        │
│  de domínio ──────────►│ Correlação   │──► Spearman por par     │
│  (dados cruzados)      │              │    subfunção-indicador  │
│                        └──────────────┘                        │
│                                                                 │
│  Dados dos agentes     ┌──────────────┐                        │
│  de domínio ──────────►│ Anomalias    │──► alto_gasto +        │
│  (dados cruzados)      │              │    baixo_resultado     │
│                        └──────────────┘                        │
│                                                                 │
│  Correlações +         ┌──────────────┐                        │
│  Anomalias +  ────────►│ Sintetizador │──► Texto via LLM       │
│  Contexto              │ (serviço)    │    (streaming chunks)  │
│                        └──────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

### AgenteCorrelacao — Correlação Estatística (Spearman)

**Arquivo:** `agents/analytical/correlacao.py`

Calcula correlação de Spearman entre gastos e indicadores de saúde. Spearman é baseado em ranks — robusto a outliers, captura relações monotônicas não-lineares. Ideal para dados de saúde pública com amostras pequenas e possíveis anos atípicos. **100% determinístico (scipy) — sem LLM, decisão intencional**: não há embasamento para substituir cálculo estatístico por geração de linguagem.

```
Entrada: dados cruzados (despesa × indicador por subfunção e ano)
┌──────────────────────────────────────────────────────┐
│  subfunção 305 × dengue:                             │
│    2019: despesa=28.3M, indicador=12847              │
│    2020: despesa=45.6M, indicador=5231               │
│    2021: despesa=4.9M,  indicador=3412               │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  n=3 pontos (≥ 2) → calcula Spearman                 │
│                                                      │
│  Spearman = -0.87  (relação monotônica, por ranks)   │
│                                                      │
│  Classificação (baseada em |Spearman|):              │
│  |0.87| = 0.87 ≥ 0.7 → "alta"                       │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
Saída:
{
  subfuncao: 305,
  tipo_indicador: "dengue",
  spearman: -0.87,
  classificacao: "alta",
  n_pontos: 3
}
```

**Classificação (baseada no |Spearman|, limiares em `semantic_memory`):**

```
|r| ≥ 0.7  ──►  "alta"     (relação forte)
|r| ≥ 0.4  ──►  "média"    (relação moderada)
|r| < 0.4  ──►  "baixa"    (relação fraca)
```

**Regras especiais:**
- Pares com < 2 pontos de dados → retorna 0.0 (não é possível calcular correlação)
- Arrays constantes (todos os valores iguais) → retorna 0.0 (NaN tratado)
- Resultado sempre clamped a [-1, 1]

### AgenteAnomalias — Detecção de Ineficiências

**Arquivo:** `agents/analytical/anomalias.py`

Detecta anos onde o gasto e o resultado divergem da mediana, sugerindo ineficiência ou eficiência inesperada. **100% determinístico — sem LLM**, pelo mesmo racional de `AgenteCorrelacao`.

**Polaridade dos indicadores:**

A interpretação de "resultado bom" ou "resultado ruim" depende da natureza do indicador, obtida via *retrieval* de `semantic_memory` (não hardcoded direto do módulo no meio da lógica):

```
┌─────────────────────────────────────────────────────────────┐
│  INDICADORES NEGATIVOS (mais = pior):                       │
│  dengue, covid, internacoes, mortalidade                    │
│  → Valor ALTO = muitos casos/óbitos = resultado RUIM        │
│  → Valor BAIXO = poucos casos/óbitos = resultado BOM        │
│                                                             │
│  INDICADORES POSITIVOS (mais = melhor):                     │
│  vacinacao                                                  │
│  → Valor ALTO = boa cobertura = resultado BOM               │
│  → Valor BAIXO = baixa cobertura = resultado RUIM           │
└─────────────────────────────────────────────────────────────┘
```

**Tipos de anomalia:**

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  alto_gasto_baixo_resultado (ineficiência)                  │
│  ─────────────────────────                                  │
│  Despesa ACIMA da mediana + resultado RUIM                  │
│  • Indicador negativo: gastou muito E casos altos           │
│  • Indicador positivo: gastou muito E cobertura baixa       │
│  → "Gastou muito mas o resultado foi ruim"                  │
│                                                             │
│  baixo_gasto_alto_resultado (eficiência)                    │
│  ──────────────────────────                                 │
│  Despesa ABAIXO da mediana + resultado BOM                  │
│  • Indicador negativo: gastou pouco E casos baixos          │
│  • Indicador positivo: gastou pouco E cobertura alta        │
│  → "Gastou pouco mas o resultado foi bom"                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Constantes de polaridade:**
- `INDICADORES_NEGATIVOS: set[str]` = {"dengue", "covid", "internacoes", "mortalidade"}
- `INDICADORES_POSITIVOS: set[str]` = {"vacinacao"}

**Regra:** Pares com < 2 pontos de dados são ignorados (não faz sentido calcular mediana com 1 valor).

### TextSynthesizer — Geração de Texto (Serviço, não é agente CoALA)

**Arquivo:** `agents/analytical/sintetizador.py`

> **Nota arquitetural:** O sintetizador NÃO é um agente CoALA — não possui `working_memory`/`episodic_memory`/`semantic_memory`/`procedural_memory` própria nem participa do ciclo `propose_actions → evaluate_and_select → execute`. Ele é, em si, a implementação de duas ações do espaço de ações de quem o chama (orquestrador/supervisor): `_build_prompt`/`_generate_structured_text` são uma ação de *reasoning* interna (transforma dados já resolvidos em texto, sem tocar o ambiente); a chamada a `core.llm_client.generate_stream` é uma ação de *grounding* externa (invocação de uma ferramenta fora do processo — a API do LLM). A decisão de modelá-lo como classe normal reflete que ele não percebe ambiente mutável, não propõe ações concorrentes, e não escolhe entre estratégias alternativas — quem decide isso é o caller. O streaming é responsabilidade do caller via `StreamingAdapter`.

Recebe todos os resultados dos outros agentes e gera um texto de análise em português:

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Correlações  │  │  Anomalias   │  │  Contexto    │
│ (do agente   │  │ (do agente   │  │ Orçamentário │
│  correlação) │  │  anomalias)  │  │ (do agente   │
│              │  │              │  │  contexto)   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └────────┬────────┘                 │
                ▼                          │
       ┌─────────────────┐                 │
       │  Monta prompt   │◄────────────────┘
       │  em português   │
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │  Tenta LLM       │──► Sucesso? → Texto do LLM (DeepSeek,
       │  (deepseek-v4-   │              modelo único, com retry)
       │   flash)         │──► Falhou?  → Texto estruturado (fallback)
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │  StreamingAdapter│
       │  (core/streaming │
       │  _adapter.py)    │
       │  Divide texto em │
       │  chunks de ~80   │──► ws_queue ──► WebSocket ──► Frontend
       │  caracteres      │
       └─────────────────┘
```

O streaming é realizado pelo `StreamingAdapter` (`backend/core/streaming_adapter.py`), um componente de infraestrutura reutilizável que encapsula a lógica de chunking e envio para `ws_queue`. Oferece dois modos:
- `stream_text(text)` — para texto pré-gerado (fallback estruturado)
- `stream_tokens(generator)` — para tokens incrementais do LLM (streaming em tempo real)

A chamada ao LLM é rotulada `caller=self.synthesizer_id` para rastreabilidade nos logs.

**Seções do texto gerado (fallback):**
1. Resumo Executivo
2. Cobertura de Dados (gaps detectados)
3. Análise das Correlações (Spearman por par, com classificação)
4. Discussão das Anomalias (com descrições em português)
5. Contexto Orçamentário (tendências por subfunção)

---

## Agente de Contexto (1)

### AgenteContextoOrcamentario — Tendências de Gasto

**Arquivo:** `agents/context/contexto_orcamentario.py`

Analisa como o gasto de cada subfunção evoluiu ao longo dos anos. **100% determinístico — sem LLM.**

```
Entrada: despesas agregadas por subfunção
┌──────────────────────────────────────────────────────┐
│  Subfunção 305 (Vigilância Epidemiológica):          │
│    2019: R$ 28.350.000                               │
│    2020: R$ 45.600.000                               │
│    2021: R$ 4.886.620                                │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Calcula variação ano a ano (YoY):                   │
│                                                      │
│  2019→2020: ((45.6M - 28.3M) / 28.3M) × 100         │
│           = +60.9%  (crescimento)                    │
│                                                      │
│  2020→2021: ((4.9M - 45.6M) / 45.6M) × 100          │
│           = -89.3%  (corte drástico)                 │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Classifica tendência:                               │
│                                                      │
│  +60.9% seguido de -89.3%                            │
│  → Não há 2+ anos consecutivos na mesma direção      │
│  → Variação média: (60.9 + (-89.3)) / 2 = -14.2%    │
│  → Tendência: "corte" (média negativa > 5%)          │
└──────────────────────────────────────────────────────┘
```

**Classificação de tendências:**

```
┌────────────────┬──────────────────────────────────────────┐
│ Tendência      │ Condição                                 │
├────────────────┼──────────────────────────────────────────┤
│ "crescimento"  │ Variação positiva consecutiva ≥ 2 anos   │
│ "corte"        │ Variação negativa consecutiva ≥ 2 anos   │
│ "estagnacao"   │ Todas as variações com |valor| < 5%      │
│ "insuficiente" │ Menos de 2 anos de dados                 │
└────────────────┴──────────────────────────────────────────┘
```

---

## Arquitetura Estrela

**Arquivo:** `backend/agents/star/orchestrator.py`

Na topologia estrela, um único agente central (OrquestradorEstrela) coordena todos os agentes. Nenhum agente periférico se comunica diretamente com outro — tudo passa pelo hub.

**Ativação condicional de agentes de domínio:** O orquestrador usa o mapeamento `INDICADOR_TO_AGENT` para instanciar apenas os agentes de domínio relevantes aos `health_params` selecionados pelo usuário (ou extraídos pelo `AgenteInterpretacaoIntencao`, se a análise veio do chat):

```
INDICADOR_TO_AGENT:
  dengue      → vigilancia_epidemiologica
  covid       → vigilancia_epidemiologica
  internacoes → saude_hospitalar
  vacinacao   → atencao_primaria
  mortalidade → mortalidade
```

Se o usuário seleciona apenas `dengue` e `vacinacao`, somente `AgenteVigilanciaEpidemiologica` e `AgenteAtencaoPrimaria` são instanciados. Agentes analíticos e de contexto são sempre executados.

```
                         OrquestradorEstrela
                              (Hub)
                               │
        ┌──────┬───────┬───────┼───────┬──────┬──────┬──────┐
        ▼      ▼       ▼       ▼       ▼      ▼      ▼      ▼
      Vigil. Hospit. Primár. Mortal. Contex. Correl. Anomal. Sintet.
      (305)  (302)   (301)   (todas)  (YoY)  (stats) (median) (LLM)
        │      │       │       │       │      │      │      │
        └──────┴───────┴───────┘       │      │      │      │
               │                       │      │      │      │
          despesas +                   │      │      │      │
          indicadores                  │      │      │      │
               │                       │      │      │      │
               ├───────────────────────┘      │      │      │
               │  (despesas)                  │      │      │
               │                              │      │      │
               ├──── cross_domain_data() ─────┤      │      │
               │     (dados cruzados)         │      │      │
               │                              │      │      │
               │                              │      │      │
               └──────────────────────────────┴──────┘      │
                                                            │
                    correlações + anomalias + contexto ──────┘
                                                            │
                                                     texto (streaming)
```

### Pipeline

O método `run()` executa o pipeline completo de forma linear (delega inteiramente ao ciclo `run_coala_cycle()` da base). O orquestrador herda de `AgenteCoALA` por uniformidade de interface e para permitir extensão futura, mas opera como agente de coordenação com pipeline determinístico. A autonomia deliberativa reside nos agentes de nível folha (domínio, analíticos, contexto), que efetivamente exercem o ciclo CoALA para decidir quais dados consultar e como processar resultados. Os métodos `perceive`/`propose_actions` são mantidos por conformidade de interface, mas `evaluate_and_select` colapsa num passthrough — não há candidatos concorrentes a arbitrar neste nível (a ordem das macro-ações é imposta por dependência de dados, não por arbitragem):

```
Passo 1:  Instancia agentes de domínio condicionalmente conforme
          health_params (via mapeamento INDICADOR_TO_AGENT)
Passo 2:  Executa apenas os agentes de domínio relevantes em SEQUÊNCIA
          Vigilância → Hospitalar → Primária → Mortalidade
Passo 3:  Deduplica despesas (mortalidade retorna todas as subfunções)
Passo 4:  Cruza dados: cross_domain_data(despesas, indicadores)
Passo 5:  Detecta lacunas: detect_data_gaps()
Passo 6:  AgenteContextoOrcamentario.analyze_trends(despesas)
Passo 7:  AgenteCorrelacao.compute(dados_cruzados)
Passo 8:  AgenteAnomalias.detect(dados_cruzados)
Passo 9:  *** Captura wall-clock (exclui sintetizador) ***
Passo 10: TextSynthesizer.generate(correlações, anomalias, contexto)
Passo 11: Persiste métricas de execução por agente
```

**Características:**
- Pipeline via `run_coala_cycle()` — cada macro-etapa é uma ação (`_act_*`) registrada em `procedural_memory`, proposta em ordem fixa por `propose_actions()`
- Comunicação simples: orquestrador ↔ agente (ida + volta = 2 mensagens)
- Ponto único de falha: se o orquestrador falhar, toda a análise falha
- Ativação condicional: apenas agentes de domínio relevantes aos `health_params` são instanciados
- Métricas de benchmark excluem o sintetizador do breakdown por agente (é serviço LLM, não agente CoALA) — o overhead do orquestrador é calculado como `wall_clock - soma_workers`
- Em falha de agente: envia evento `error` via WebSocket, continua com dados parciais
- Recebe opcionalmente `intent_summary` (quando a análise vem do chat) no dict `params` — armazenado na working memory para uso futuro por etapas de priorização de achados (ainda não implementadas)

---

## Arquitetura Hierárquica

**Arquivos:** `backend/agents/hierarchical/coordinator.py`, `supervisors.py`

Na topologia hierárquica, os agentes são organizados em 3 níveis com supervisores intermediários que podem se comunicar lateralmente:

```
                    CoordenadorGeral
                      (Nível 0)
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    SupervisorDominio  SupervisorContexto  SupervisorAnalitico
      (Nível 1)         (Nível 1)           (Nível 1)
          │                │                    │
    ┌─────┼─────┐         │              ┌─────┼─────┐
    ▼     ▼     ▼    ▼    ▼              ▼     ▼     ▼
  Vigil. Hosp. Prim. Mort. CtxOrç.     Corr. Anom. TextSynth.
  (Nível 2)                (Nível 2)    (Nível 2)   (serviço)


  Comunicação lateral (sem passar pelo Coordenador):
  ─────────────────────────────────────────────────
  SupervisorDominio ───────► SupervisorAnalitico
                              (despesas + indicadores + intent_summary)
  SupervisorDominio ───────► SupervisorContexto
                              (despesas)
  SupervisorContexto ──────► SupervisorAnalitico
                              (contexto orçamentário)
```

### Pipeline

```
Passo 1:  Coordenador instancia 3 supervisores
Passo 2:  SupervisorDominio.run()
          → Executa agentes de domínio relevantes em sequência
          → Agrega despesas + indicadores
Passo 3:  Comunicação lateral: Domínio → Analítico (despesas + indicadores)
Passo 4:  Comunicação lateral: Domínio → Contexto (despesas)
Passo 5:  SupervisorContexto.run()
          → Executa AgenteContextoOrcamentario
Passo 6:  Comunicação lateral: Contexto → Analítico (contexto orçamentário)
Passo 7:  SupervisorAnalitico.run()
          → Cruza dados, executa correlação, anomalias
          → Marca fim da parte determinística (antes do sintetizador)
          → TextSynthesizer (streaming LLM)
Passo 8:  Coordenador usa esse marco para medir o supervisor analítico
          sem incluir tempo do sintetizador/LLM
Passo 9:  *** Captura wall-clock (subtrai tempo do sintetizador) ***
Passo 10: Persiste métricas para agentes + 3 supervisores
          (exclui sintetizador do breakdown — é serviço LLM, não agente CoALA)
          workers_time_ms soma apenas agentes folha (nível 2);
          supervisores aparecem no breakdown para exibição mas NÃO
          são somados (evita dupla contagem — seu tempo engloba subordinados)
```

**Características:**
- Degradação graciosa: se um supervisor falha, o coordenador continua com dados parciais
- Comunicação lateral via `receive_from_peer()` — supervisores trocam dados diretamente (e agora logam explicitamente o que repassam, de ambos os lados do hop)
- Mensagens esperadas: ~24+ (agentes + supervisores + comunicação lateral)
- Métricas coletadas para 11 entidades (8 agentes + 3 supervisores)
- Supervisores implementam o ciclo CoALA por uniformidade de interface, mas `_execute_intention()`-equivalente é essencialmente determinístico. A deliberação é determinística e a execução real ocorre no método `run()` chamado pelo coordenador.
- Cálculo de overhead: `overhead = wall_clock - soma dos agentes folha (nível 2)`. Supervisores aparecem no breakdown de métricas para exibição, mas NÃO são somados em `workers_time_ms` — seu tempo já engloba o dos subordinados, somá-los causaria dupla contagem. O overhead captura: tempo dos supervisores fora dos subordinados + comunicação lateral + instanciação.

### Degradação Graciosa

```
    SupervisorDominio falha!
              │
              ▼
    CoordenadorGeral:
    ├── Captura exceção
    ├── Envia evento "error" via WebSocket
    ├── Define dominio_data = {despesas: [], indicadores: []}
    └── CONTINUA com SupervisorContexto e SupervisorAnalitico
              │
              ▼
    Resultado final: parcial mas válido
    (correlações e anomalias vazias, mas texto gerado com fallback)
```

---

## Regras de Negócio

### Mapeamento Subfunção → Indicador

```
┌──────────┬──────────────────────────┬─────────────────┬──────────┐
│ Código   │ Nome                     │ Indicador       │ DataSUS  │
├──────────┼──────────────────────────┼─────────────────┼──────────┤
│ 301      │ Atenção Básica           │ Vacinação       │ SI-PNI   │
│ 302      │ Assistência Hospitalar   │ Internações     │ SIH      │
│ 303      │ Suporte Profilático      │ — (sem par)     │ —        │
│ 305      │ Vigilância Epidemiológica│ Dengue, COVID   │ SINAN    │
│ (todas)  │ Mortalidade (transversal)│ Mortalidade     │ SIM      │
└──────────┴──────────────────────────┴─────────────────┴──────────┘
```

### Deduplicação de Despesas (`deduplicate_despesas`)

**Arquivo:** `backend/agents/data_crossing.py`

O `AgenteMortalidade` é transversal — retorna despesas de **todas** as subfunções (301, 302, 303, 305). Quando executado junto com outros agentes de domínio, as despesas se sobrepõem. A função `deduplicate_despesas()` resolve isso:

```
Antes (com duplicatas):
┌──────────────────────────────────────────────────────┐
│  AgenteAtencaoPrimaria  → subfunção=301, ano=2020    │
│  AgenteMortalidade      → subfunção=301, ano=2020  ← │ duplicata!
│  AgenteMortalidade      → subfunção=302, ano=2020    │
│  AgenteSaudeHospitalar  → subfunção=302, ano=2020  ← │ duplicata!
└──────────────────────────────────────────────────────┘
                    │
                    ▼  deduplicate_despesas()
                    │  (chave: subfuncao + ano)
                    ▼
Depois (sem duplicatas):
┌──────────────────────────────────────────────────────┐
│  subfunção=301, ano=2020  (primeira ocorrência)      │
│  subfunção=302, ano=2020  (primeira ocorrência)      │
└──────────────────────────────────────────────────────┘
```

Preserva a ordem de inserção — a primeira ocorrência de cada par `(subfuncao, ano)` é mantida.

### Cruzamento de Dados (`cross_domain_data`)

**Arquivo:** `backend/agents/data_crossing.py`

```
Despesas (por subfunção e ano)     Indicadores (por tipo e ano)
┌────────────────────────┐         ┌────────────────────────┐
│ subfunção=305, ano=2020│         │ tipo=dengue, ano=2020  │
│ valor=45.600.000       │         │ valor=5231             │
└───────────┬────────────┘         └───────────┬────────────┘
            │                                  │
            └──────────┬───────────────────────┘
                       ▼
              CrossedDataPoint:
              {
                subfuncao: 305,
                tipo_indicador: "dengue",
                ano: 2020,
                valor_despesa: 45600000,
                valor_indicador: 5231
              }
```

### Detecção de Lacunas (`detect_data_gaps`)

**Arquivo:** `backend/agents/data_crossing.py`

Identifica dados faltantes para transparência na análise.

**Assinatura:**
```python
detect_data_gaps(despesas, indicadores, date_from, date_to, health_params=None)
```

**Parâmetro `health_params`** (opcional):
- Quando fornecido (ex: `["dengue", "vacinacao"]`), verifica apenas as subfunções e indicadores relevantes à seleção do usuário
- Quando `None`, verifica todos os tipos e subfunções (comportamento legado)
- Mortalidade é transversal — se incluída em `health_params`, todas as subfunções são verificadas

```
Período solicitado: 2019-2023, health_params=["dengue", "vacinacao"]

Verifica apenas:
  Subfunções: 305 (dengue→vigilância), 301 (vacinacao→atenção primária)
  Indicadores: dengue, vacinacao

Despesas subfunção 305:  2019 ✓  2020 ✓  2021 ✓  2022 ✗  2023 ✗
Indicador dengue:        2019 ✓  2020 ✓  2021 ✓  2022 ✓  2023 ✓

Gaps detectados:
  ⚠ Despesa subfunção 305: sem dados para 2022, 2023
  ⚠ Cruzamento Vigilância × dengue: despesa sem indicador em 2022, 2023

Cobertura: despesas 60%, indicadores 100%
```

O resultado é passado ao sintetizador para que o texto mencione explicitamente quais dados estão faltando.

---

## Uso de LLM — resumo

| Agente/serviço | Usa LLM? | Observação |
|---|---|---|
| `AgenteInterpretacaoIntencao` | **Sim** — única chamada, combinando guardrail de escopo + extração | Único ponto de entrada de linguagem natural do sistema |
| Agentes de domínio (4) | Não | Filtro de subfunção/indicador é hardcoded (decisão de escopo atual, não lacuna definitiva — ver `PLANO_REFATORACAO.md`) |
| `AgenteCorrelacao` | Não | Estatística determinística (scipy), decisão intencional |
| `AgenteAnomalias` | Não | Comparação com mediana, decisão intencional |
| `AgenteContextoOrcamentario` | Não | Variação percentual, decisão intencional |
| `TextSynthesizer` | **Sim** | Síntese textual final, com fallback estruturado determinístico |
| `OrquestradorEstrela` / `CoordenadorGeral` / supervisores | Não | Coordenação — ordem de macro-ações fixa por dependência de dados, sem arbitragem real |

Ver `PLANO_REFATORACAO.md` na raiz do repositório para o plano (em andamento, Etapa 1 de 6 concluída) de introduzir uso adicional de LLM nos agentes de domínio (construção de consultas), priorização de achados, verificação pós-síntese e comunicação lateral semântica.
