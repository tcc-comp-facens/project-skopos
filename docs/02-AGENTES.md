# Sistema Multiagente CoALA

## Sumário

1. [O que é um Agente?](#o-que-é-um-agente)
2. [Modelo CoALA](#modelo-coala-cognitive-architectures-for-language-agents)
3. [Classe Base AgenteCoALA](#classe-base-agentecoala)
4. [Visão Geral dos 20 Agentes CoALA](#visão-geral-dos-20-agentes-coala)
5. [Agente de Interpretação de Intenção (1)](#agente-de-interpretação-de-intenção-1)
6. [Agentes de Saúde (8 — 1 por Sistema de Informação)](#agentes-de-saúde-8--1-por-sistema-de-informação)
7. [Agentes Orçamentários (7 — 1 por Subfunção)](#agentes-orçamentários-7--1-por-subfunção)
8. [Agente de Contexto Orçamentário (1)](#agente-de-contexto-orçamentário-1)
9. [Agentes Analíticos (2 CoALA + 1 serviço)](#agentes-analíticos-2-coala--1-serviço)
10. [Verificação Pós-Síntese — Self-Check (Etapa 4)](#verificação-pós-síntese--self-check-etapa-4)
11. [Arquitetura Estrela](#arquitetura-estrela)
12. [Arquitetura Hierárquica](#arquitetura-hierárquica)
13. [Regras de Negócio](#regras-de-negócio)

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

Neste projeto, cada agente é uma classe Python que herda de `AgenteCoALA` e se especializa em uma tarefa: interpretar a intenção do usuário, consultar dados de um Sistema de Informação de saúde ou de uma subfunção orçamentária, calcular correlações, detectar anomalias, decidir ênfase narrativa, verificar o texto gerado, etc.

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
│   Fatos/regras declarativas de domínio — ex: subfunção do    │
│   agente orçamentário, subtipos/dimensões válidas de um       │
│   Sistema de Informação, limiares de classificação. Lidos    │
│   via *retrieval* explícito dentro de `propose_actions`,      │
│   nunca hardcoded direto do módulo no meio da lógica de       │
│   decisão.                                                    │
│                                                             │
│   EPISODIC MEMORY (memória episódica)                        │
│   ────────────────────────────────                           │
│   Histórico de ações executadas nesta instância (sucesso e   │
│   falha), gravado via `_observe_and_learn` — só em memória,   │
│   descartada ao fim do processo. `AgenteInterpretacaoIntencao`│
│   também grava aqui, com conteúdo real, a memória episódica   │
│   recuperada do Neo4j (análises anteriores) — ver seção        │
│   dedicada abaixo.                                            │
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

- **Internas** (reasoning/retrieval/learning) — processamento sobre a working memory, leitura de semantic memory, gravação em episodic memory. Inclui chamadas ao LLM para raciocinar sobre o estado atual — o LLM é parte da procedural memory implícita do agente, não do ambiente. Ex.: calcular Spearman, cruzar dados, classificar tendência (CAGR), escolher dimensão/ângulo de ênfase, verificar afirmações pós-síntese.
- **Externas** (grounding) — interação com o ambiente fora da cognição do agente: Neo4j, WebSocket, comunicação lateral entre agentes/supervisores. Ex.: consultar indicadores ou despesas no Neo4j.

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
    │  AVALIAR E   │ ◄─── Seleciona quais candidatos executar — em vários
    │  SELECIONAR  │      agentes hoje isso é arbitragem REAL (ver abaixo)
    └────┬─────────┘
         ▼
    ┌──────────┐
    │ EXECUTAR │ ◄─── Executa cada ação via procedural memory,
    └──────────┘      registrando o episódio (observe/learn)
```

**Deliberação real, não só passthrough.** Diferente de uma versão anterior do sistema, `evaluate_and_select` não é mais um passthrough universal. Hoje há quatro famílias de deliberação genuína, todas seguindo o mesmo padrão de dois estágios (score determinístico + arbitragem LLM opcional, com fallback ao maior score se o LLM falhar/estiver desligado):

1. **Dimensão de consulta** — cada agente de saúde com dimensões válidas (ver tabela mais abaixo) e `AgenteOrcamentoSubfuncao` propõem um candidato por dimensão + "sem quebra", e arbitram entre eles (`agents/domain/query_planning.py::arbitrar_dimensao`).
2. **Ângulo de ênfase narrativa** — `AgentePriorizacaoAnalitica` propõe um candidato por ângulo possível (ineficiências, eficiência, correlações fortes, tendências orçamentárias, intenção do usuário) e escolhe um.
3. **Escopo/extração da intenção do chat** — `AgenteInterpretacaoIntencao` classifica escopo em 3 vias (não um binário) numa única chamada LLM.
4. **Planejamento dos coordenadores** — `OrquestradorEstrela`, `CoordenadorGeral`, `SupervisorSaude` e `SupervisorOrcamento` propõem os subordinados pelo mapeamento fixo entre indicadores e agentes, e o LLM pode retirar ou acrescentar subordinados do catálogo, com justificativa (`agents/planejamento.py`). Na estrela é uma chamada só. Na hierárquica, o coordenador escolhe os ramos e escreve subobjetivos, e cada supervisor de dados planeja o seu ramo.

Nos coordenadores, a **ordem** das macro-ações continua imposta por dependência de dados (cruzamento só depois da coleta, síntese só depois de correlação, anomalias e contexto). O que o LLM decide é quais subordinados acionar.

**Agentes e ferramentas.** Seguindo CoALA, `core.quality_metrics.FERRAMENTAS` classifica como ferramentas as classes que não decidem nada com o LLM: `AgenteAnalitico`, `AgenteContextoOrcamentario`, `AgenteSIA` e `AgenteCOVID`. Elas continuam existindo como classes, mas são ações de grounding de quem as chama. Não geram mensagens e contam em `ferramentas_acionadas`, não em `agent_invocations`.

**Exemplo concreto** — `AgenteSINAN`:

```
1. PERCEBER:     working_memory já tem analysis_id="abc", período 2019-2021
2. ATUALIZAR:    working_memory = {analysis_id: "abc", date_from: 2019, date_to: 2021}
3. PROPOR AÇÕES: um candidato por dimensão válida do SINAN
                 ("sem quebra", POR_FAIXA_ETARIA, POR_SEXO)
4. AVALIAR/SEL.: arbitra entre os 3 candidatos (score determinístico +
                 LLM opcional) e seleciona só 1 — evita N consultas ao Neo4j
5. EXECUTAR:     → Query Neo4j: IndicadorSaude(sistema="sinan", subtipos=[...])
                   com a dimensão escolhida
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

| Método | O que faz | Exemplo |
|--------|-----------|---------|
| `perceive()` | Observa o ambiente | Lê parâmetros da working memory |
| `propose_actions()` | Decide candidatos de ação | 1 candidato fixo (maioria dos agentes) ou N candidatos concorrentes (dimensão/ângulo/escopo) |
| `evaluate_and_select(candidates)` | Seleciona o que executar | Passthrough (orquestradores/supervisores) ou arbitragem real (agentes de saúde/orçamento/priorização/intenção) |
| `_act_*(action)` | Executa uma ação | Roda query no Neo4j, chama o LLM |
| `_act_fallback_*(action)` | Trata falha (2ª estratégia) | Retorna lista vazia como fallback |

### A exceção ActionFailure

Quando algo dá errado, o agente encapsula o erro com contexto da ação:

```python
# backend/agents/base.py
class ActionFailure(Exception):
    def __init__(self, action: dict, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"Action failed: {reason}")
```

### Recuperação de Falhas

Quando uma estratégia falha, `procedural_memory[goal]` pode ter uma estratégia de fallback registrada em seguida — a próxima tentativa roda, o erro é registrado em `episodic_memory` e em `_failed_actions`, e o agente **nunca fica em estado indefinido**. Se todas as estratégias registradas para um goal falharem, a ação é marcada `"failed"` e o laço segue para a próxima ação.

---

## Classe Base AgenteCoALA

**Arquivo:** `backend/agents/base.py`

Todos os 20 agentes CoALA do sistema herdam desta classe. Ela fornece o esqueleto do ciclo CoALA:

```python
class AgenteCoALA:
    agent_id: str                                          # ID único (ex: "star-sinan-a1b2c3d4")
    working_memory: dict[str, Any]                         # Buffer de curto prazo
    semantic_memory: dict[str, Any]                        # Fatos/regras de domínio
    episodic_memory: list[dict]                            # Histórico de ações (sucesso/falha)
    procedural_memory: dict[str, list[Callable[[dict], None]]]  # Estratégias por goal
    _failed_actions: list[dict]                            # Ações que falharam
```

### Padrão de IDs

Formato: `{topologia}-{papel}-{uuid_hex[:8]}`

```
star-sinan-a1b2c3d4          ← AgenteSINAN na topologia estrela
star-orcamento-305-b2c3d4e5  ← AgenteOrcamentoSubfuncao(305) na estrela
hier-sup-saude-m3n4o5p6      ← SupervisorSaude na hierárquica
hier-cnes-e5f6g7h8           ← AgenteCNES na hierárquica
intent-9f8e7d6c              ← AgenteInterpretacaoIntencao (uma instância por sessão de chat, não amarrado a uma topologia)
```

---

## Visão Geral dos 20 Agentes CoALA

Todas as 20 classes abaixo herdam `AgenteCoALA`. O número reflete o resultado de uma migração completa do modelo de dados/agentes (commit `f4838d2` em diante): os 4 agentes de domínio genéricos por região anatômica (`AgenteVigilanciaEpidemiologica`, `AgenteSaudeHospitalar`, `AgenteAtencaoPrimaria`, `AgenteMortalidade`) e os agentes analíticos separados (`AgenteCorrelacao`, `AgenteAnomalias`) **não existem mais** — foram substituídos pelo modelo abaixo, particionado por Sistema de Informação DATASUS de um lado e por subfunção orçamentária do outro.

| Categoria | Quantidade | Classes |
|---|---|---|
| Interpretação de intenção | 1 | `AgenteInterpretacaoIntencao` |
| Saúde (1 por Sistema de Informação) | 8 | `AgenteSINAN`, `AgenteSIH`, `AgenteSIM`, `AgenteSIPNI`, `AgenteCOVID`, `AgenteSINASC`, `AgenteSIA`, `AgenteCNES` |
| Orçamento (1 classe parametrizada, 7 instâncias — 1 por subfunção) | 7 | `AgenteOrcamentoSubfuncao(122)`, `(301)`, `(302)`, `(303)`, `(304)`, `(305)`, `(306)` |
| Contexto orçamentário | 1 | `AgenteContextoOrcamentario` |
| Analíticos | 2 | `AgenteAnalitico` (correlação + anomalias), `AgentePriorizacaoAnalitica` |
| Orquestração (estrela) | 1 | `OrquestradorEstrela` |
| Orquestração (hierárquica) | 5 | `CoordenadorGeral` + `SupervisorOrcamento` + `SupervisorSaude` + `SupervisorAnalitico` + `SupervisorContexto` |

Fora dessa contagem: `TextSynthesizer` (síntese textual final) é deliberadamente **um serviço, não um agente CoALA** — não tem `working_memory`/`propose_actions` própria (ver seção dedicada). O self-check pós-síntese (`core/claim_verifier.py`) também não é uma classe de agente — é um módulo funcional de 3 chamadas LLM em sequência, invocado pelos orquestradores/supervisores.

---

## Agente de Interpretação de Intenção (1)

### AgenteInterpretacaoIntencao — Guardrail de Escopo em 3 Vias + Extração via LLM

**Arquivo:** `agents/intent/agente_interpretacao_intencao.py`

Camada de entrada única do sistema — hoje o **único** ponto de disparo de análise é o chat (`WS /ws/chat/{sessionId}`); o antigo formulário estruturado (`POST /api/analysis`) foi removido (ver [Arquitetura Estrela](#arquitetura-estrela) e `docs/04-BACKEND-API.md`). Não há regex neste módulo: toda mensagem do usuário passa pelo LLM.

**Classificação em 3 vias** (não um guardrail binário aceita/rejeita) — o objetivo é soar natural numa conversa:

```
┌─────────────────────────────────────────────────────────────────┐
│  em_escopo=false                                                  │
│  → assunto genuinamente não relacionado. Recusado com gentileza.  │
│                                                                     │
│  em_escopo=true, precisa_analise=false                             │
│  → dentro do espírito da conversa mas não precisa rodar o          │
│    pipeline (saudação, "o que você faz?", agradecimento).           │
│    Responde direto, nenhuma arquitetura é instanciada.              │
│                                                                     │
│  em_escopo=true, precisa_analise=true                               │
│  → pergunta de dados de verdade. Segue o fluxo normal (extrai       │
│    parâmetros, dispara análise).                                    │
└─────────────────────────────────────────────────────────────────┘
```

Nos dois primeiros casos a própria LLM já escreve a resposta natural (`resposta_direta`) **na mesma chamada** — nunca uma segunda chamada LLM nem uma string estática genérica (essas só existem como fallback se a LLM não escrever `resposta_direta`).

**Ações do ciclo CoALA:**

```
0. buscar_memoria_episodica (só se neo4j_client foi injetado)
   ─────────────────────────
   Ação externa (grounding): consulta análises anteriores (nó Analise,
   via neo4j_client.get_past_analises) e grava o conteúdo recuperado em
   episodic_memory como um episódio real (sourceQuestion + createdAt),
   não só um dado de passagem em working_memory. Usado como contexto
   opcional no prompt para desambiguar referências vagas ("a mesma
   coisa de antes"). Falha aqui não bloqueia as ações seguintes.

1. classificar_escopo
   ──────────────────
   Única chamada LLM do ciclo. Classifica em_escopo + precisa_analise
   e, se precisa_analise=true, já extrai date_from/date_to/
   health_params/intent_summary na MESMA resposta JSON (decisão de
   custo: evita chamadas de rede extras por mensagem).
   Fallback (LLM indisponível/resposta inválida):
   working_memory["escopo"] = "indisponivel" — falha técnica, distinta
   de "fora" (recusa de escopo).

2. extrair_parametros
   ──────────────────
   Ação puramente interna — NÃO chama o LLM de novo. Só copia os
   campos já obtidos em (1) para working_memory quando escopo=="dentro"
   E precisa_analise=true.

   Guardrail "natural": em vez de recusar quando o usuário não
   especifica tema ou período, completa com um default sensato — todo
   o período disponível (min_year/max_year) e/ou todos os indicadores
   válidos — e marca a inferência via date_range_inferred/
   health_params_inferred (consumida por pretty_print, que avisa o
   usuário da suposição). Só recusa (pede para reformular) no caso
   residual em que nem o LLM extraiu um período nem min_year/max_year
   estão disponíveis (sem dados carregados).
└─────────────────────────────────────────────────────────────────┘
```

**Saída — `AnalysisIntent`:**

```python
@dataclass
class AnalysisIntent:
    date_from: int
    date_to: int
    health_params: list[str] = field(default_factory=list)
    intent_summary: str = ""            # resumo de 1 frase da intenção
    date_range_inferred: bool = False   # True se o período foi completado automaticamente
    health_params_inferred: bool = False # True se o tema foi completado automaticamente
```

`health_params` cobre hoje **26 tokens**: os 9 subtipos SINAN, `covid`, `internacoes`, `vacinacao`, `mortalidade`, `nascidos_vivos`, `producao_ambulatorial` e os 12 subtipos do CNES.

**Segurança:** a mensagem do usuário (e as perguntas anteriores da memória episódica) são tratadas como dado a ser classificado, nunca como instrução — mitigação de prompt injection. A resposta do LLM é validada por parsing JSON estrito (só as chaves esperadas) antes de qualquer uso, e o prompt instrui explicitamente o LLM a classificar `em_escopo=false` se a mensagem tentar instruí-lo a ignorar as regras.

Cada decisão de escopo classificada com sucesso é registrada em `core/guardrail_stats.py` (`record_guardrail_decision`), alimentando a métrica process-wide `GET /api/metrics/guardrail`.

Chamado por `api/chat_websocket.py` a cada turno do chat.

---

## Agentes de Saúde (8 — 1 por Sistema de Informação)

Os agentes de saúde são os "coletores de dados" do lado saúde. Cada um consulta `IndicadorSaude` no Neo4j para os subtipos do seu Sistema de Informação, e — quando o sistema tem dimensões válidas — delibera de fato por qual dimensão quebrar a consulta.

**Diferente do modelo anterior** (4 agentes por área anatômica, cada um também buscando despesas), estes 8 agentes **não consultam despesas** — orçamento é responsabilidade exclusiva dos 7 `AgenteOrcamentoSubfuncao` (seção seguinte), consultados separadamente pelo orquestrador/supervisor.

### Tabela dos 8 agentes de saúde

| Agente | Arquivo | Sistema | Subtipos | Dimensões válidas |
|---|---|---|---|---|
| `AgenteSINAN` | `domain/agente_sinan.py` | sinan | 9 agravos: dengue, chikungunya, sífilis adquirida/gestante/congênita, coqueluche, hepatites virais, tuberculose, hanseníase | `POR_FAIXA_ETARIA`, `POR_SEXO` |
| `AgenteSIH` | `domain/agente_sih.py` | sih | internacoes | `POR_CAPITULO_CID` |
| `AgenteSIM` | `domain/agente_sim.py` | sim | mortalidade | `POR_FAIXA_ETARIA`, `POR_CAPITULO_CID` |
| `AgenteSIPNI` | `domain/agente_sipni.py` | sipni | cobertura_vacinal, doses_aplicadas | `COBERTURA_VACINAL`, `DOSES_APLICADAS` |
| `AgenteCOVID` | `domain/agente_covid.py` | covid | casos, obitos | — (nenhuma) |
| `AgenteSINASC` | `domain/agente_sinasc.py` | sinasc | nascidos_vivos | `POR_FAIXA_ETARIA`, `POR_FAIXA_PESO` |
| `AgenteSIA` | `domain/agente_sia.py` | sia | producao_ambulatorial | — (nenhuma) |
| `AgenteCNES` | `domain/agente_cnes.py` | cnes | 12 subtipos: leitos, profissionais, estabelecimentos_por_tipo, ocupacoes, equipes_saude, tipo_atendimento, leitos_consultorios, equipamentos, estabelecimentos_nivel_atencao, estabelecimentos_servico_classificacao, estabelecimentos_habilitacao, estabelecimentos_vigilancia_epidemiologica | `POR_TIPO_ESTABELECIMENTO`, `POR_OCUPACAO`, `POR_TIPO_EQUIPE`, `POR_TIPO_ATENDIMENTO`, `POR_TIPO_LEITO_CONSULTORIO`, `POR_TIPO_EQUIPAMENTO` |

A allowlist de dimensões por sistema vive em `db/query_builder.py::SISTEMA_DIMENSOES` — é a única fonte da verdade; um nome fora dela é rejeitado (`DimensaoInvalida`) antes de virar texto interpolado numa query Cypher.

### Como funciona um agente de saúde com dimensões — `AgenteSINAN` como referência

`agents/domain/agente_sinan.py` é o padrão de referência que os outros agentes de saúde com dimensão replicam (todos exceto COVID e SIA, que não têm dimensões válidas):

```
Orquestrador/supervisor chama:
  agente.query(analysis_id, date_from, date_to, intent_summary, health_params, use_llm)
                          │
                          ▼
              propose_actions()
              "Proponho um candidato por dimensão válida:
               sem quebra / POR_FAIXA_ETARIA / POR_SEXO"
                          │
                          ▼
              evaluate_and_select(candidates)
              arbitra via agents.domain.query_planning.arbitrar_dimensao:
                1. score determinístico por candidato (a dimensão foi
                   mencionada, por nome legível, no intent_summary?)
                2. se use_llm=True, um LLM arbitra entre os candidatos
                   (com o score como referência, não vinculante)
                3. qualquer falha (LLM indisponível, resposta inválida,
                   dimensão não reconhecida) cai no maior score
              → seleciona 1 único candidato (nunca N — evita N
                consultas ao Neo4j por análise)
                          │
                          ▼
              execute()
              → Neo4j: IndicadorSaude(sistema="sinan", subtipos=[9 agravos])
                        com a dimensão escolhida (ou nenhuma)
              → working_memory["indicadores"] = [...]
```

`agents/domain/query_planning.py` centraliza essa deliberação (`propose_dimensao_candidatos`, `score_dimensao_candidato`, `arbitrar_dimensao`) — reaproveitada por todos os agentes de saúde com dimensão e por `AgenteOrcamentoSubfuncao`.

O mesmo módulo também expõe `plan_query`, um mecanismo **separado e mais simples** (fast-path/cache/LLM single-shot, sem candidatos concorrentes) preparado para quando o mapeamento sistema→subtipo deixar de ser trivial — hoje inativo por padrão (`USE_LLM_QUERY_PLANNING` desligada), alimenta a métrica `GET /api/metrics/query-planning-cache`.

**Comportamento comum aos 8 agentes:**
- Recebem `(agent_id, neo4j_client)` no construtor
- Método público: `query(analysis_id, date_from, date_to, intent_summary=None, health_params=None, use_llm=True)` → `{"indicadores": [...]}`
- Retornam lista vazia (sem exceção) quando não há dados ou a consulta falha (estratégia de fallback registrada)
- `health_params` **filtra** os subtipos do agente (`data_crossing.subtipos_pedidos`): numa pergunta sobre dengue, o `AgenteSINAN` consulta só `dengue`, e não os 9 agravos. Os tokens são traduzidos antes, por exemplo `covid` vira casos e óbitos. Sem `health_params`, ou quando nenhum subtipo do agente foi pedido (o planejamento o acrescentou por outro motivo), o agente cobre o domínio inteiro. Antes, todo agente ativado trazia o domínio inteiro, e uma pergunta sobre dengue gerava achados de chikungunya, sífilis etc.

---

## Agentes Orçamentários (7 — 1 por Subfunção)

### AgenteOrcamentoSubfuncao — Classe Parametrizada

**Arquivo:** `agents/domain/agente_orcamento.py`

Uma única classe, parametrizada por `(subfuncao_codigo, subfuncao_nome)`, instanciada **7 vezes** — uma por subfunção orçamentária de saúde:

| Código | Nome |
|---|---|
| 122 | Administração Geral |
| 301 | Atenção Básica |
| 302 | Assistência Hospitalar e Ambulatorial |
| 303 | Suporte Profilático e Terapêutico |
| 304 | Vigilância Sanitária |
| 305 | Vigilância Epidemiológica |
| 306 | Alimentação e Nutrição |

Consulta `DespesaAnual(subfuncaoCodigo=<código>)` no Neo4j via `neo4j_client.get_despesas_por_subfuncao`. Delibera dimensão (`POR_NATUREZA` — o que foi comprado: medicamentos, equipamentos, salários — ou `POR_APLICACAO` — bloco de financiamento SUS — ou "sem quebra") pelo mesmo mecanismo de dois estágios de `AgenteSINAN`, via `agents.domain.query_planning`.

**Três goals na mesma lista de candidatos de `propose_actions`** (diferente de `AgenteSINAN`, que só propõe um goal):

```
consultar_despesas       ← com deliberação de dimensão (POR_NATUREZA/POR_APLICACAO/sem quebra)
analisar_tendencia       ← ação única, sempre incluída, sem deliberação
consultar_variacao_anual ← ação única, sempre incluída, sem deliberação
```

`evaluate_and_select` particiona por goal: só o grupo `consultar_despesas` é candidato a arbitragem real; os outros dois passam direto.

**`analisar_tendencia`** — reaproveita `AgenteContextoOrcamentario.analyze_trends` (não duplica a lógica de classificação) para calcular a tendência (CAGR + classificação crescimento/corte/estagnação/insuficiente) do próprio orçamento da subfunção, resumindo o período pedido inteiro (`date_from`-`date_to`) num único veredito.

**`consultar_variacao_anual`** — lê `VARIACAO_ANUAL`, a variação percentual ano-a-ano pré-computada **uma única vez no ETL** (`etl/orcamento_loader.py`, reaproveitando literalmente `agents.context.contexto_orcamentario.compute_yoy_variation`/`classify_trend`). Informativo, coexiste com `tendencia` porque têm semânticas diferentes: `tendencia` resume a janela pedida; `variacao_anual` é a lista crua de transições ano-a-ano pré-computadas globalmente (insensível à janela pedida além do filtro final por ano).

**Interface pública:**

```python
def query(self, analysis_id, date_from, date_to, intent_summary=None,
          health_params=None, use_llm=True) -> dict[str, Any]:
    """Retorna {"despesas": [...], "tendencia": {...}, "variacao_anual": [...]}."""
```

---

## Agente de Contexto Orçamentário (1)

### AgenteContextoOrcamentario — Tendências de Gasto (CAGR)

**Arquivo:** `agents/context/contexto_orcamentario.py`

Analisa como o gasto de cada subfunção evoluiu ao longo dos anos. **100% determinístico — sem LLM.** Usado tanto como agente independente na fase analítica dos orquestradores (`analyze_trends(despesas)` sobre todas as subfunções presentes) quanto internamente por cada `AgenteOrcamentoSubfuncao._act_analisar_tendencia` (sobre a própria subfunção).

```
Entrada: despesas agregadas por subfunção
┌──────────────────────────────────────────────────────┐
│  Subfunção 305 (Vigilância Epidemiológica):          │
│    2019: R$ 28.350.000                               │
│    2020: R$ 45.600.000                               │
│    2021: R$  4.886.620                               │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Calcula variação ano a ano (YoY) para classificar    │
│  a tendência: cada par consecutivo, "crescimento"      │
│  (positiva ≥2 anos consecutivos), "corte" (negativa    │
│  ≥2 anos), "estagnacao" (|variação| < 5%) ou           │
│  "insuficiente" (<2 anos de dados).                     │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Resume a "variação média ao ano" via CAGR             │
│  (taxa de crescimento anual composta), NÃO média       │
│  aritmética simples das variações ano-a-ano:           │
│                                                        │
│  CAGR = ((valor_final/valor_inicial)^(1/anos) - 1)×100│
│  (ponta a ponta: primeiro→último ano com dado)         │
└──────────────────────────────────────────────────────┘
```

**Por que CAGR e não a média simples:** a média aritmética das variações YoY é dominada por um único ano de salto de base baixa/zero — achado real do ETL (a subfunção 122 não existe nos empenhos antes de 2020, e parte da folha de pagamento migra de 301→122 nessa transição), o que inflava a "média anual" muito acima do que qualquer ano isolado da série sustenta. CAGR olha só ponta a ponta e não é afetado pela volatilidade dos anos intermediários. `anos` é o intervalo em anos corridos entre o primeiro e o último ano **com dado disponível** (não o número de pontos da série), para lacunas no meio não inflarem a taxa.

**Classificação de tendências** (limiares em `semantic_memory`, retrieval dentro do reasoning):

| Tendência | Condição |
|---|---|
| `"crescimento"` | Variação positiva consecutiva ≥ 2 anos |
| `"corte"` | Variação negativa consecutiva ≥ 2 anos |
| `"estagnacao"` | Todas as variações com \|valor\| < 5% |
| `"insuficiente"` | Menos de 2 anos de dados |

`compute_yoy_variation`/`classify_trend` (funções-módulo, sem estado) são reaproveitadas literalmente por `etl/orcamento_loader.py` para pré-computar `VARIACAO_ANUAL` — mesma fórmula/limiares, para não haver duas lógicas de classificação divergentes no sistema.

---

## Agentes Analíticos (2 CoALA + 1 serviço)

Os agentes analíticos processam dados **em memória** — não acessam Neo4j. Recebem dados já coletados pelos agentes de saúde/orçamento e produzem análises.

```
┌─────────────────────────────────────────────────────────────────┐
│                   AGENTES ANALÍTICOS                            │
│                                                                 │
│  Dados dos agentes     ┌──────────────┐                        │
│  de saúde/orçamento──►│AgenteAnalitico│──► Spearman (correlação) │
│  (dados cruzados)      │(consolidado) │    + anomalias (mediana)│
│                        └──────┬───────┘                        │
│                               ▼                                │
│                        ┌──────────────┐                        │
│                        │Priorizacao   │──► escolhe ângulo de    │
│                        │Analitica     │    ênfase, reordena     │
│                        └──────┬───────┘                        │
│                               ▼                                │
│                        ┌──────────────┐                        │
│                        │ Sintetizador │──► Texto via LLM       │
│                        │ (serviço)    │    (streaming chunks)  │
│                        └──────┬───────┘                        │
│                               ▼                                │
│                        ┌──────────────┐                        │
│                        │ Self-Check   │──► verifica/corrige     │
│                        │(claim_verifier, opcional)              │
│                        └──────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

### AgenteAnalitico — Correlação (Spearman) + Anomalias, consolidados

**Arquivo:** `agents/analytical/analitico.py`

Consolida os antigos `AgenteCorrelacao` e `AgenteAnomalias` num único agente CoALA com 2 ações de reasoning (`calcular_correlacao`, `detectar_anomalia`) — os dois cálculos sempre operaram sobre o mesmo `dados_cruzados`, com `propose_actions`/`evaluate_and_select` idênticos (passthrough), então mantê-los como dois agentes só duplicava overhead de coordenação. Nenhum dos dois cálculos muda de comportamento. **100% determinístico (scipy) — sem LLM.**

**Correlação de Spearman** — robusta a outliers, capta relações monotônicas não-lineares:

```
|r| ≥ 0.7  ──►  "alta"     (relação forte)
|r| ≥ 0.4  ──►  "média"    (relação moderada)
|r| < 0.4  ──►  "baixa"    (relação fraca)
```

Cada correlação também carrega uma `leitura` — tradução explícita do sinal em "desejável"/"indesejável" dado a polaridade do indicador (`INDICADORES_NEGATIVOS`: dengue, casos, obitos, internacoes, mortalidade, chikungunya, sífilis (3 subtipos), coqueluche, hepatites_virais, tuberculose, hanseniase; `INDICADORES_POSITIVOS`: cobertura_vacinal, doses_aplicadas). Sem isso o sintetizador (via LLM) tinha que adivinhar sozinho se uma correlação positiva com "mortalidade" é boa ou má notícia — bug real observado em produção onde o LLM inverteu essa leitura.

**Detecção de anomalias por mediana:**

```
alto_gasto_baixo_resultado (ineficiência) — despesa ACIMA da mediana + resultado RUIM
baixo_gasto_alto_resultado (eficiência)   — despesa ABAIXO da mediana + resultado BOM
```

**Interface pública:** `analisar(dados_cruzados)` → `{"correlacoes": [...], "anomalias": [...]}`.

### AgentePriorizacaoAnalitica — Ênfase Narrativa (Etapa 3 do plano de refatoração)

**Arquivo:** `agents/analytical/priorizacao.py`

Recebe correlações, anomalias e contexto orçamentário **já calculados** (nunca recalcula nem filtra) e decide qual ângulo de ênfase usar e em que ordem apresentá-los ao `TextSynthesizer`. É o primeiro lugar do sistema onde `propose_actions`/`evaluate_and_select` fazem deliberação real com mais de um candidato genuíno:

```
propose_actions(): 1 candidato por ângulo possível:
  "ineficiencias"            — foco em alto gasto × baixo resultado
  "eficiencia"                — foco em baixo gasto × bom resultado
  "correlacoes_fortes"        — foco nas correlações mais fortes
  "tendencias_orcamentarias"  — foco em cortes/crescimento ao longo do tempo
  "intencao_usuario"          — foco direto no que foi perguntado

evaluate_and_select(): score determinístico (magnitude/contagem dos
  achados brutos) + LLM opcional (informado por intent_summary) →
  escolhe 1. Respeita use_llm=False (nem tenta a chamada).
```

A ação de execução (`_act_priorizar_achados`) reordena (nunca filtra) `correlacoes`/`anomalias` conforme o ângulo escolhido. **Interface pública:** `prioritize(correlacoes, anomalias, contexto_orcamentario, intent_summary, use_llm)` → dict com `angulo`, `descricao_angulo`, `correlacoes`/`anomalias` reordenadas.

### TextSynthesizer — Geração de Texto (Serviço, não é agente CoALA)

**Arquivo:** `agents/analytical/sintetizador.py`

> **Nota arquitetural:** o sintetizador **não** é um agente CoALA — não possui `working_memory`/`propose_actions` própria. Recebe correlações/anomalias (já priorizadas, se a priorização rodou), contexto orçamentário, cobertura de dados, e opcionalmente `enfase` (descrição do ângulo escolhido) e `pergunta_usuario` (`intent_summary`), e gera um texto de análise em português.

```
Correlações + Anomalias (priorizadas) + Contexto + Ênfase
                    │
                    ▼
           Monta prompt em português
                    │
                    ▼
           Tenta LLM (gpt-5.6-luna)
             │ Sucesso → texto do LLM, streaming
             │ Falhou  → texto estruturado determinístico (fallback)
                    │
                    ▼
           StreamingAdapter (core/streaming_adapter.py)
           Divide em chunks ──► ws_queue ──► WebSocket ──► Frontend
```

---

## Verificação Pós-Síntese — Self-Check (Etapa 4)

**Arquivo:** `backend/core/claim_verifier.py`

Não é um agente CoALA — é um módulo funcional que implementa o padrão **Chain-of-Verification (CoVe)** em 3 chamadas LLM em sequência:

```
1. extract_claims(texto)               — extrai afirmações factuais discretas
2. verify_claims(claims, dados)        — verifica cada uma contra correlações/
                                          anomalias/contexto orçamentário brutos
3. revise_text(texto, não_suportadas)  — se houver alguma não suportada, pede
                                          UMA revisão focada (sem loop, no
                                          máximo 1 correção por análise)
```

`self_check()` encadeia os 3 passos e nunca lança exceção — qualquer falha preserva o texto original. Controlado pela flag `use_self_check` (default `False`, roteada por `models.py`/`routes.py`/`dispatch.py`/`chat_runner.py`/`chat_websocket.py`), e só é proposta pelo pipeline quando `use_self_check=True` **e** `use_llm=True` — não faz sentido verificar um texto do fallback determinístico contra os próprios dados que o geraram. Invocado como ação `verificar_afirmacoes` por `OrquestradorEstrela` e `SupervisorAnalitico`, sempre **depois** de `capturar_wallclock` (seu tempo, como o do sintetizador e da priorização, fica fora do tempo "oficial" comparado entre as topologias).

Este módulo **corrige** o texto, não o mede — a medição de fidelidade é separada, feita pela biblioteca RAGAS (ver `docs/arquitetura/metricas-de-avaliacao.md`).

---

## Ativação Condicional de Agentes

O orquestrador estrela e o `SupervisorSaude`/`SupervisorOrcamento` da hierárquica usam o mesmo mapeamento `INDICADOR_TO_AGENT` (definido em ambos `agents/star/orchestrator.py` e `agents/hierarchical/supervisors.py`) para instanciar apenas os agentes relevantes aos `health_params` selecionados pelo usuário (ou extraídos pelo `AgenteInterpretacaoIntencao`, no caso de chat):

```
dengue, chikungunya, sifilis_* (3), coqueluche, hepatites_virais,
tuberculose, hanseniase          → sinan
covid                            → covid
internacoes                      → sih
vacinacao                        → sipni
mortalidade                      → sim
nascidos_vivos                   → sinasc
producao_ambulatorial            → sia
leitos, profissionais, ... (12)  → cnes
```

E os 7 agentes orçamentários são ativados por correspondência subfunção↔sistema (`_SUBFUNCAO_TOKENS`, decidida explicitamente com o usuário):

| Subfunção | Ativada por |
|---|---|
| 301 Atenção Básica | SI-PNI (vacinação) + SINASC (nascidos vivos) |
| 302 Assist. Hospitalar/Ambul. | SIH (internações) + SIA (produção ambulatorial) |
| 303 Suporte Profilático/Terap. | Qualquer agravo do SINAN |
| 304 Vigilância Sanitária | Qualquer agravo do SINAN + só o subtipo CNES `estabelecimentos_vigilancia_epidemiologica` |
| 305 Vigilância Epidemiológica | SINAN + COVID |
| 122 Administração Geral | Qualquer subtipo do CNES (sem candidato mais específico) |
| 306 Alimentação e Nutrição | Qualquer subtipo do CNES (idem 122) |

Mortalidade (SIM) é transversal para fins de cruzamento de dados: ativa as despesas de 301/302/303/305 mesmo sem seus próprios tokens de saúde presentes.

Se o usuário pergunta só sobre `dengue`, apenas `AgenteSINAN` e o `AgenteOrcamentoSubfuncao(303)`/`(305)` são instanciados. Os agentes analíticos e de contexto rodam sempre.

---

## Arquitetura Estrela

**Arquivo:** `backend/agents/star/orchestrator.py`

Na topologia estrela, um único agente central (`OrquestradorEstrela`) coordena todos os agentes. Nenhum agente periférico se comunica diretamente com outro — tudo passa pelo hub. Herda de `AgenteCoALA`. A ordem das macro-ações é imposta por dependência de dados, e `evaluate_and_select` decide com o LLM quais agentes de dados acionar (`agents/planejamento.py`).

### Pipeline (`propose_actions`, executado por `run_coala_cycle`)

```
1.  consultar_saude       — até 8 agentes de saúde, ativação condicional, em sequência fixa
2.  consultar_orcamento   — até 7 agentes orçamentários, ativação condicional
3.  cruzar_dados          — deduplica despesas + cross_domain_data()
4.  detectar_gaps         — detect_data_gaps()
5.  analisar_contexto     — AgenteContextoOrcamentario.analyze_trends(despesas)
6.  analisar              — AgenteAnalitico (correlação + anomalias)
7.  capturar_wallclock    — *** marca o fim do pipeline determinístico ***
8.  priorizar_achados     — AgentePriorizacaoAnalitica (Etapa 3, LLM opcional)
9.  sintetizar_texto      — TextSynthesizer (LLM, streaming)
10. verificar_afirmacoes  — self-check (Etapa 4), só se use_self_check=True e use_llm=True
11. persistir_metricas    — persiste MetricaExecucao por agente, emite eventos WS
```

**Características:**
- Comunicação simples: orquestrador ↔ agente (ida + volta = 2 mensagens); estrela nunca tem comunicação lateral (hub-and-spoke)
- Ponto único de falha: se o orquestrador falhar, toda a análise falha; falha de um agente individual é reportada via evento `error` no WebSocket e o pipeline segue com dados parciais
- Wall-clock (para comparação de eficiência entre topologias) exclui sintetizador, priorização e verificação — todos rodam depois de `capturar_wallclock` e envolvem chamada LLM, cuja duração depende da disponibilidade externa, não da arquitetura em si
- Overhead do orquestrador = `wall_clock - soma_workers` (agentes folha)
- Recebe `intent_summary` (quando a análise vem do chat) — insumo da deliberação de dimensão (agentes de saúde/orçamento) e do ângulo de ênfase (priorização)

---

## Arquitetura Hierárquica

**Arquivos:** `backend/agents/hierarchical/coordinator.py`, `supervisors.py`

Na topologia hierárquica, os agentes são organizados em 3 níveis com **4 supervisores** intermediários (não mais o `SupervisorDominio` único de versões anteriores — decisão tomada com o usuário de separar orçamento e saúde em supervisores distintos). É uma **árvore estrita**: nenhum supervisor troca dado diretamente com outro — o `CoordenadorGeral` colhe o retorno de cada um e repassa o que for necessário como parâmetro direto na chamada seguinte (um design anterior tinha os supervisores trocando dados entre si via um canal `receive_from_peer`, mediado pela raiz mas ainda par-a-par, inclusive com resumos textuais via LLM — removido por decisão explícita de tornar a hierárquica uma árvore no sentido estrito):

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
até 7 AgenteOrcamentoSubfuncao   até 8 agentes de saúde  CtxOrç.   Analitico Priorizacao TextSynth.
                                                        (Nível 2)  (Nível 2) (Nível 2)  (serviço)

  Sem comunicação lateral: o coordenador colhe o retorno de cada supervisor
  e repassa como parâmetro direto do próximo:
  ─────────────────────────────────────────────────────────────────
  despesas(Orçamento) + despesas(Saúde) → combinar_despesas → despesas_combinadas
  despesas_combinadas ──► SupervisorContexto.run(despesas=...)
  despesas_combinadas + indicadores(Saúde) + contexto_orcamentario(Contexto)
                       ──► SupervisorAnalitico.run(despesas=..., indicadores=..., contexto_orcamentario=...)
```

### Pipeline (`CoordenadorGeral.propose_actions`)

```
1. delegar_orcamento    — SupervisorOrcamento.run()
2. delegar_saude        — SupervisorSaude.run()
3. combinar_despesas    — dedupe (salvaguarda estrutural — desde a
                           decomposição completa, só SupervisorOrcamento
                           produz despesas)
4. delegar_contexto     — SupervisorContexto.run(despesas=despesas_combinadas)
5. delegar_analitico    — SupervisorAnalitico.run(despesas=..., indicadores=...,
                           contexto_orcamentario=...) — cruza dados,
                           correlação/anomalias, prioriza, sintetiza,
                           self-check opcional
6. persistir_metricas   — persiste métricas de 4 supervisores + agentes
                           folha, emite eventos WS
```

**Características:**
- Degradação graciosa: se um supervisor falha, o coordenador continua com dados parciais
- Árvore estrita: nenhum supervisor conhece ou escreve no estado de outro — toda troca de dado passa pelo coordenador (retorno do filho → argumento do próximo filho)
- Overhead = `wall_clock - soma dos agentes folha (nível 2)`. Supervisores aparecem no breakdown de métricas para exibição, mas não são somados em `workers_time_ms` (seu tempo já engloba o dos subordinados — somar causaria dupla contagem)
- Wall-clock exclui sintetizador, priorização e self-check (mesmo motivo da estrela)

---

## Regras de Negócio

### Deduplicação de Despesas (`deduplicate_despesas`)

**Arquivo:** `backend/agents/data_crossing.py`

Salvaguarda estrutural contra despesas duplicadas por `(subfuncao, ano)` — preserva a ordem de inserção (primeira ocorrência mantida). Hoje, com a decomposição completa (agentes de saúde nunca retornam despesas), o overlap real que motivou essa função na Fase 1 não existe mais, mas ela permanece como proteção contra regressão futura.

### Cruzamento de Dados (`cross_domain_data`)

**Arquivo:** `backend/agents/data_crossing.py`

Junta despesas e indicadores por `(ano, dimensao_valor)` — não só por ano — para que múltiplas linhas de uma mesma quebra dimensional não se sobrescrevam silenciosamente num único ponto cruzado.

```
Despesas (por subfunção/dimensão e ano)   Indicadores (por tipo/dimensão e ano)
┌────────────────────────┐               ┌────────────────────────┐
│ subfunção=305, ano=2020│               │ tipo=dengue, ano=2020  │
│ valor=45.600.000       │               │ valor=5231             │
└───────────┬────────────┘               └───────────┬────────────┘
            └──────────┬────────────────────────────┘
                       ▼
              CrossedDataPoint:
              {subfuncao, tipo_indicador, ano, valor_despesa, valor_indicador}
```

### Detecção de Lacunas (`detect_data_gaps`)

**Arquivo:** `backend/agents/data_crossing.py`

```python
detect_data_gaps(despesas, indicadores, date_from, date_to, health_params=None)
```

Quando `health_params` é fornecido, verifica apenas as subfunções/indicadores relevantes à seleção do usuário; `None` verifica tudo. O resultado é passado ao sintetizador para que o texto mencione explicitamente quais dados estão faltando.

Anos anteriores ao início da série de um indicador (`INICIO_DA_SERIE`, hoje só COVID-19, a partir de 2020) ficam **fora de vigência**. Eles não são lacuna, não entram na completude nem nos descasamentos de cruzamento e são devolvidos à parte em `fora_de_vigencia`. Assim, uma pergunta de 2016 a 2025 sobre covid não gera "faltam dados de 2016 a 2019".

---

## Uso de LLM — resumo

| Agente/serviço | Usa LLM? | Observação |
|---|---|---|
| `AgenteInterpretacaoIntencao` | **Sim** — 1 chamada, guardrail de escopo (3 vias) + extração combinados | Único ponto de entrada de linguagem natural do sistema |
| 8 agentes de saúde (com dimensão) | **Opcional** — arbitragem de dimensão | Fast-path/score determinístico se `use_llm=False`; COVID e SIA nunca chamam (sem dimensões válidas) |
| 7 `AgenteOrcamentoSubfuncao` | **Opcional** — arbitragem de dimensão de despesa | Mesma lógica acima |
| `AgenteAnalitico` | Não | Estatística determinística (scipy) |
| `AgenteContextoOrcamentario` | Não | CAGR + classificação, determinístico |
| `AgentePriorizacaoAnalitica` | **Opcional** — escolha de ângulo | Score determinístico se `use_llm=False` |
| `TextSynthesizer` | **Sim** | Síntese textual final, com fallback estruturado determinístico |
| Self-check (`claim_verifier`) | **Sim** (3 chamadas) | Só quando `use_self_check=True` e `use_llm=True` |
| `OrquestradorEstrela` / `CoordenadorGeral` / supervisores de saúde e orçamento | **Sim** — planejamento (quais subordinados acionar) e revisão das entregas | Ordem de macro-ações fixa por dependência de dados; a proposta determinística vale se o LLM falhar (`agents/planejamento.py`, `agents/revisao_entregas.py`) |
| `SupervisorAnalitico` (hierárquica) | **Sim** — redação por ramo e composição | Só com 2 ou mais sistemas na pergunta; com 1, redige direto |

Ver [docs/arquitetura/agente-coala-e-llm.md](arquitetura/agente-coala-e-llm.md) e [docs/arquitetura/metricas-de-avaliacao.md](arquitetura/metricas-de-avaliacao.md) para os detalhes de custo/observabilidade de cada chamada LLM.
