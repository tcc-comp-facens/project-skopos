# Sistema Multiagente BDI

## Sumário

1. [O que é um Agente?](#o-que-é-um-agente)
2. [Modelo BDI](#modelo-bdi-belief-desire-intention)
3. [Classe Base AgenteBDI](#classe-base-agentebdi)
4. [Agentes de Domínio (4)](#agentes-de-domínio-4)
5. [Agentes Analíticos (3)](#agentes-analíticos-3)
6. [Agente de Contexto (1)](#agente-de-contexto-1)
7. [Arquitetura Estrela](#arquitetura-estrela)
8. [Arquitetura Hierárquica](#arquitetura-hierárquica)
9. [Regras de Negócio](#regras-de-negócio)

---

## O que é um Agente?

Um **agente** é um programa autônomo que percebe o ambiente, toma decisões e age para atingir objetivos. Diferente de uma função comum que recebe input e retorna output, um agente tem:

- **Autonomia** — decide sozinho o que fazer
- **Reatividade** — responde a mudanças no ambiente
- **Proatividade** — age para atingir objetivos, não só reage
- **Estado interno** — mantém crenças, desejos e planos

```
┌─────────────────────────────────────────────┐
│              AGENTE                          │
│                                             │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│   │ Crenças  │  │ Desejos  │  │ Intenções│ │
│   │ (o que   │  │ (o que   │  │ (como    │ │
│   │  sei)    │  │  quero)  │  │  farei)  │ │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘ │
│        │             │             │        │
│        └──────┬──────┘             │        │
│               ▼                    ▼        │
│         Deliberação           Execução      │
│                                             │
│   Percepção ◄──── Ambiente ────► Ação       │
└─────────────────────────────────────────────┘
```

Neste projeto, cada agente é uma classe Python que herda de `AgenteBDI` e se especializa em uma tarefa: consultar dados, calcular correlações, detectar anomalias, etc.

---

## Modelo BDI (Belief-Desire-Intention)

O modelo **BDI** é uma arquitetura cognitiva inspirada na filosofia da mente humana. Ele organiza o raciocínio do agente em três componentes:

### Os três componentes

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   BELIEFS (Crenças)          "O que eu sei sobre o mundo"   │
│   ─────────────────                                         │
│   • Dados do Neo4j (despesas, indicadores)                  │
│   • Parâmetros da análise (período, tipo)                   │
│   • Resultados parciais de outros agentes                   │
│                                                             │
│   DESIRES (Desejos)          "O que eu quero alcançar"      │
│   ─────────────────                                         │
│   • "Quero consultar despesas da subfunção 305"             │
│   • "Quero calcular correlações"                            │
│   • "Quero detectar anomalias"                              │
│                                                             │
│   INTENTIONS (Intenções)     "Como vou fazer"               │
│   ──────────────────────                                    │
│   • Plano: consultar Neo4j com query X                      │
│   • Plano: aplicar Spearman nos dados cruzados              │
│   • Plano: comparar com mediana                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### O Ciclo BDI

Todo agente executa o mesmo ciclo de raciocínio:

```
    ┌──────────┐
    │ PERCEBER │ ◄─── Observa o ambiente (lê crenças, consulta Neo4j)
    └────┬─────┘
         ▼
    ┌──────────┐
    │ ATUALIZAR│ ◄─── Incorpora novas informações às crenças
    │ CRENÇAS  │
    └────┬─────┘
         ▼
    ┌──────────┐
    │ DELIBERAR│ ◄─── Decide quais objetivos perseguir
    └────┬─────┘      (dado o que sei, o que devo fazer?)
         ▼
    ┌──────────┐
    │ PLANEJAR │ ◄─── Cria planos concretos para cada objetivo
    └────┬─────┘
         ▼
    ┌──────────┐
    │ EXECUTAR │ ◄─── Executa cada plano (consulta, calcula, gera texto)
    └──────────┘
```

**Exemplo concreto** — AgenteVigilanciaEpidemiologica:

```
1. PERCEBER:   "Tenho analysis_id=abc, período 2019-2021"
2. ATUALIZAR:  beliefs = {analysis_id: "abc", date_from: 2019, date_to: 2021}
3. DELIBERAR:  "Tenho os parâmetros → quero consultar despesas E indicadores"
               desires = [{goal: "consultar_despesas"}, {goal: "consultar_indicadores"}]
4. PLANEJAR:   intentions = [{desire: consultar_despesas, status: pending},
                             {desire: consultar_indicadores, status: pending}]
5. EXECUTAR:   → Query Neo4j: DespesaSIOPS subfunção 305
               → Query Neo4j: IndicadorDataSUS tipo IN [dengue, covid]
               → beliefs["despesas"] = [...]
               → beliefs["indicadores"] = [...]
```

### O Ciclo BDI no Código

O método `run_cycle()` da classe base executa o ciclo completo em 5 linhas:

```python
# backend/agents/base.py — AgenteBDI

def run_cycle(self) -> None:
    """Ciclo BDI completo."""
    perception = self.perceive()          # 1. Perceber
    self.update_beliefs(perception)       # 2. Atualizar crenças
    desires = self.deliberate()           # 3. Deliberar
    self.intentions = self.plan(desires)  # 4. Planejar
    self.execute(self.intentions)         # 5. Executar
```

Cada passo corresponde a um método que as subclasses sobrescrevem:

**1. Perceber** — o agente lê suas crenças e extrai o que é relevante:

```python
# Implementação padrão (retorna vazio)
def perceive(self) -> dict:
    return {}

# Implementação real (agente de domínio)
def perceive(self) -> dict:
    return {
        "analysis_id": self.beliefs.get("analysis_id"),
        "date_from": self.beliefs.get("date_from"),
        "date_to": self.beliefs.get("date_to"),
    }
```

**2. Atualizar crenças** — incorpora novas informações (mesmo para todos):

```python
def update_beliefs(self, perception: dict) -> None:
    self.beliefs.update(perception)
```

**3. Deliberar** — aqui está a **tomada de decisão**. O agente avalia suas crenças e decide quais objetivos perseguir. Cada agente tem sua própria lógica:

```python
# Agente de domínio — "se tenho parâmetros, quero consultar"
def deliberate(self):
    desires = []
    if self.beliefs.get("analysis_id") and self.beliefs.get("date_from") is not None:
        desires.append({"goal": "consultar_despesas"})
        desires.append({"goal": "consultar_indicadores"})
    # Se NÃO tem parâmetros → desires fica vazio → agente não faz nada
    return desires

# AgenteCorrelacao — "se tenho dados cruzados, quero calcular"
def deliberate(self):
    if self.beliefs.get("dados_cruzados"):
        desires.append({"goal": "calcular_correlacoes"})

# AgenteSintetizador — "se tenho analysis_id, quero sintetizar (mesmo sem dados)"
def deliberate(self):
    if self.beliefs.get("analysis_id") is not None:
        desires.append({"goal": "sintetizar_texto"})
```

**4. Planejar** — converte desejos em intenções executáveis (1 desejo = 1 intenção):

```python
def plan(self, desires: list[dict]) -> list[dict]:
    return [{"desire": d, "status": "pending"} for d in desires]
```

**5. Executar** — percorre cada intenção pendente, com recuperação de falha:

```python
def execute(self, intentions: list[dict]) -> None:
    for intention in intentions:
        try:
            self._execute_intention(intention)
        except IntentionFailure as e:
            intention["status"] = "failed"
            self._failed_intentions.append(intention)

            # Tenta alternativa
            alternative = self._recover_intention(intention)
            if alternative is not None:
                try:
                    self._execute_intention(alternative)
                except IntentionFailure:
                    alternative["status"] = "failed"
```

### A exceção IntentionFailure

Quando algo dá errado, o agente encapsula o erro com contexto da intenção:

```python
# backend/agents/base.py
class IntentionFailure(Exception):
    def __init__(self, intention: dict, reason: str):
        self.intention = intention
        self.reason = reason
        super().__init__(f"Intention failed: {reason}")

# Uso em um agente de domínio:
def _execute_intention(self, intention):
    try:
        despesas = self.neo4j_client.get_despesas(...)
    except Exception as e:
        raise IntentionFailure(intention, str(e)) from e
```

### Recuperação de Falhas

Quando algo dá errado durante a execução de um plano:

```
    Executando intenção "consultar_despesas"
              │
              ▼
    ┌─────────────────┐
    │  Neo4j offline!  │ ──► IntentionFailure levantada
    └────────┬────────┘
             ▼
    ┌─────────────────┐
    │ _recover_        │ ──► Tenta alternativa:
    │  intention()     │     "Retornar lista vazia em vez de falhar"
    └────────┬────────┘
             ▼
    ┌─────────────────┐
    │ Agente continua  │ ──► beliefs["despesas"] = []
    │ em estado válido │     (orquestrador recebe dados parciais)
    └─────────────────┘
```

No código, a recuperação é implementada por cada agente:

```python
# Agente de domínio — fallback: retorna lista vazia
def _recover_intention(self, failed_intention):
    goal = failed_intention["desire"]["goal"]
    if goal == "consultar_despesas":
        self.beliefs["despesas"] = []    # dados parciais em vez de falha total
        return {"desire": {"goal": "noop"}, "status": "completed"}
    elif goal == "consultar_indicadores":
        self.beliefs["indicadores"] = []
        return {"desire": {"goal": "noop"}, "status": "completed"}
    return None  # sem alternativa → erro propagado
```

O agente **nunca fica em estado indefinido**. Se a recuperação também falhar, o erro é propagado ao orquestrador/supervisor, que decide como continuar.

---

## Classe Base AgenteBDI

**Arquivo:** `backend/agents/base.py`

Todos os 8 agentes herdam desta classe. Ela fornece o esqueleto do ciclo BDI:

```python
class AgenteBDI:
    agent_id: str                    # ID único (ex: "star-vigilancia-a1b2c3d4")
    beliefs: dict[str, Any]          # Base de crenças
    desires: list[dict]              # Lista de objetivos
    intentions: list[dict]           # Planos selecionados
    _failed_intentions: list[dict]   # Intenções que falharam
```

### Métodos que cada agente sobrescreve

| Método | O que faz | Exemplo |
|--------|-----------|---------|
| `perceive()` | Observa o ambiente | Lê parâmetros das crenças |
| `deliberate()` | Decide objetivos | "Tenho dados → quero calcular correlações" |
| `plan(desires)` | Cria planos | Converte cada desejo em intenção pendente |
| `_execute_intention(intention)` | Executa um plano | Roda query no Neo4j |
| `_recover_intention(failed)` | Trata falha | Retorna lista vazia como fallback |

### Padrão de IDs

Formato: `{topologia}-{papel}-{uuid_hex[:8]}`

```
star-vigilancia-a1b2c3d4     ← agente de vigilância na topologia estrela
hier-sup-dominio-m3n4o5p6    ← supervisor de domínio na hierárquica
star-correlacao-e5f6g7h8     ← agente de correlação na estrela
```

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
              │  1. update_beliefs()  │
              │  beliefs = {          │
              │    analysis_id: "abc" │
              │    date_from: 2019    │
              │    date_to: 2021      │
              │  }                    │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  2. perceive()        │
              │  "Tenho analysis_id,  │
              │   date_from, date_to" │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  3. deliberate()      │
              │  "Quero:              │
              │   - consultar_despesas│
              │   - consultar_        │
              │     indicadores"      │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  4. plan()            │
              │  intentions = [       │
              │   {consultar_despesas,│
              │    status: pending},  │
              │   {consultar_         │
              │    indicadores,       │
              │    status: pending}   │
              │  ]                    │
              └──────────┬────────────┘
                         ▼
              ┌───────────────────────┐
              │  5. execute()         │
              │                       │
              │  Intenção 1:          │
              │  Neo4j ──► despesas   │
              │  WHERE subfuncao=305  │
              │  AND ano >= 2019      │
              │  AND ano <= 2021      │
              │                       │
              │  Intenção 2:          │
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
- Se o Neo4j falhar: `_recover_intention()` retorna listas vazias

---

## Agentes Analíticos (3)

Os agentes analíticos processam dados **em memória** — não acessam Neo4j. Recebem dados já coletados pelos agentes de domínio e produzem análises.

```
┌─────────────────────────────────────────────────────────────────┐
│                   AGENTES ANALÍTICOS                            │
│                                                                 │
│  Dados dos agentes     ┌──────────────┐                        │
│  de domínio ──────────►│ Correlação   │──► Pearson, Spearman,  │
│  (dados cruzados)      │              │    Kendall por par     │
│                        └──────────────┘                        │
│                                                                 │
│  Dados dos agentes     ┌──────────────┐                        │
│  de domínio ──────────►│ Anomalias    │──► alto_gasto +        │
│  (dados cruzados)      │              │    baixo_resultado     │
│                        └──────────────┘                        │
│                                                                 │
│  Correlações +         ┌──────────────┐                        │
│  Anomalias +  ────────►│ Sintetizador │──► Texto via LLM       │
│  Contexto              │              │    (streaming chunks)  │
│                        └──────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

### AgenteCorrelacao — Correlação Estatística

**Arquivo:** `agents/analytical/correlacao.py`

Calcula a força da relação entre gastos e indicadores de saúde usando três métodos estatísticos:

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
│  Calcula 3 coeficientes para cada par:               │
│                                                      │
│  Pearson  = -0.85  (relação linear)                  │
│  Spearman = -0.87  (relação monotônica, por ranks)   │
│  Kendall  = -0.82  (concordância entre pares)        │
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
  pearson: -0.85,
  spearman: -0.87,
  kendall: -0.82,
  classificacao: "alta",
  n_pontos: 3
}
```

**Classificação (baseada no Spearman):**

```
|r| ≥ 0.7  ──►  "alta"     (relação forte)
|r| ≥ 0.4  ──►  "média"    (relação moderada)
|r| < 0.4  ──►  "baixa"    (relação fraca)
```

**Regras especiais:**
- Pares com < 2 pontos de dados → retorna 0.0 para tudo (não é possível calcular correlação)
- Arrays constantes (todos os valores iguais) → retorna 0.0 (NaN tratado)
- Resultado sempre clamped a [-1, 1]

### AgenteAnomalias — Detecção de Ineficiências

**Arquivo:** `agents/analytical/anomalias.py`

Detecta anos onde o gasto e o resultado divergem da mediana, sugerindo ineficiência ou eficiência inesperada:

```
Entrada: dados cruzados (subfunção 301 × vacinação)
┌──────────────────────────────────────────────────────┐
│  2019: despesa=185M, indicador=485320                │
│  2020: despesa=199M, indicador=412100                │
│  2021: despesa=23M,  indicador=892450                │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Calcula medianas:                                   │
│  mediana_despesa    = 185M                           │
│  mediana_indicador  = 485320                         │
└──────────────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Compara cada ano com as medianas:                   │
│                                                      │
│  2019: despesa=185M (= mediana), indicador=485320    │
│        → Sem anomalia (na mediana)                   │
│                                                      │
│  2020: despesa=199M (> mediana), indicador=412100    │
│        indicador < mediana                           │
│        → ⚠ ALTO GASTO + BAIXO RESULTADO             │
│          (possível ineficiência)                     │
│                                                      │
│  2021: despesa=23M (< mediana), indicador=892450     │
│        indicador > mediana                           │
│        → ✓ BAIXO GASTO + ALTO RESULTADO             │
│          (possível eficiência)                       │
└──────────────────────────────────────────────────────┘
```

**Tipos de anomalia:**

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  alto_gasto_baixo_resultado                                 │
│  ─────────────────────────                                  │
│  Despesa ACIMA da mediana + Indicador ABAIXO da mediana     │
│  → "Gastou muito mas o resultado foi ruim"                  │
│  → Possível ineficiência na alocação de recursos            │
│                                                             │
│  baixo_gasto_alto_resultado                                 │
│  ──────────────────────────                                 │
│  Despesa ABAIXO da mediana + Indicador ACIMA da mediana     │
│  → "Gastou pouco mas o resultado foi bom"                   │
│  → Possível eficiência ou fatores externos positivos        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Regra:** Pares com < 2 pontos de dados são ignorados (não faz sentido calcular mediana com 1 valor).

### AgenteSintetizador — Geração de Texto

**Arquivo:** `agents/analytical/sintetizador.py`

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
       │  Tenta LLM:     │
       │  1º Groq        │──► Sucesso? → Texto do LLM
       │  2º Gemini      │
       │  (3 tentativas) │──► Falhou?  → Texto estruturado (fallback)
       └────────┬────────┘
                ▼
       ┌─────────────────┐
       │  Streaming:      │
       │  Divide texto em │
       │  chunks de ~80   │──► ws_queue ──► WebSocket ──► Frontend
       │  caracteres      │
       └─────────────────┘
```

**Seções do texto gerado (fallback):**
1. Resumo Executivo
2. Cobertura de Dados (gaps detectados)
3. Análise das Correlações (Pearson/Spearman/Kendall por par)
4. Discussão das Anomalias (com descrições em português)
5. Contexto Orçamentário (tendências por subfunção)

---

## Agente de Contexto (1)

### AgenteContextoOrcamentario — Tendências de Gasto

**Arquivo:** `agents/context/contexto_orcamentario.py`

Analisa como o gasto de cada subfunção evoluiu ao longo dos anos:

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

Na topologia estrela, um único agente central (OrquestradorEstrela) coordena todos os 8 agentes. Nenhum agente periférico se comunica diretamente com outro — tudo passa pelo hub.

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

### Pipeline (10 passos)

```
Passo 1:  Instancia 8 agentes com IDs únicos
Passo 2:  Cria MessageCounter
Passo 3:  Executa 4 agentes de domínio em SEQUÊNCIA
          Vigilância → Hospitalar → Primária → Mortalidade
Passo 4:  Deduplica despesas (mortalidade retorna todas as subfunções)
Passo 5:  Cruza dados: cross_domain_data(despesas, indicadores)
Passo 6:  Detecta lacunas: detect_data_gaps()
Passo 7:  AgenteContextoOrcamentario.analyze_trends(despesas)
Passo 8:  AgenteCorrelacao.compute(dados_cruzados)
Passo 9:  AgenteAnomalias.detect(dados_cruzados)
Passo 10: AgenteSintetizador.synthesize(correlações, anomalias, contexto)
```

**Características:**
- Comunicação simples: orquestrador ↔ agente (ida + volta = 2 mensagens)
- Ponto único de falha: se o orquestrador falhar, toda a análise falha
- Mensagens esperadas: ~16 (8 agentes × 2)
- Em falha de agente: envia evento `error` via WebSocket, continua com dados parciais

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
  Vigil. Hosp. Prim. Mort. CtxOrç.     Corr. Anom. Sint.
  (Nível 2)                (Nível 2)    (Nível 2)


  Comunicação lateral (sem passar pelo Coordenador):
  ─────────────────────────────────────────────────
  SupervisorDominio ───────► SupervisorAnalitico
                              (despesas + indicadores)
  SupervisorDominio ───────► SupervisorContexto
                              (despesas)
  SupervisorContexto ──────► SupervisorAnalitico
                              (contexto orçamentário)
```

### Pipeline

```
Passo 1:  Coordenador instancia 3 supervisores
Passo 2:  SupervisorDominio.run()
          → Executa 4 agentes de domínio em sequência
          → Agrega despesas + indicadores
Passo 3:  Comunicação lateral: Domínio → Analítico (despesas + indicadores)
Passo 4:  Comunicação lateral: Domínio → Contexto (despesas)
Passo 5:  SupervisorContexto.run()
          → Executa AgenteContextoOrcamentario
Passo 6:  Comunicação lateral: Contexto → Analítico (contexto orçamentário)
Passo 7:  SupervisorAnalitico.run()
          → Cruza dados, executa correlação, anomalias, sintetizador
Passo 8:  Persiste métricas para 8 agentes + 3 supervisores
```

**Características:**
- Degradação graciosa: se um supervisor falha, o coordenador continua com dados parciais
- Comunicação lateral via `receive_from_peer()` — supervisores trocam dados diretamente
- Mensagens esperadas: ~24+ (agentes + supervisores + comunicação lateral)
- Métricas coletadas para 11 entidades (8 agentes + 3 supervisores)

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

### Cruzamento de Dados

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

Identifica dados faltantes para transparência na análise:

```
Período solicitado: 2019-2023

Despesas subfunção 305:  2019 ✓  2020 ✓  2021 ✓  2022 ✗  2023 ✗
Indicador dengue:        2019 ✓  2020 ✓  2021 ✓  2022 ✓  2023 ✓

Gaps detectados:
  ⚠ Despesa subfunção 305: sem dados para 2022, 2023
  ⚠ Cruzamento Vigilância × dengue: despesa sem indicador em 2022, 2023

Cobertura: despesas 60%, indicadores 100%
```

O resultado é passado ao sintetizador para que o texto mencione explicitamente quais dados estão faltando.

### Contagem de Mensagens

**Arquivo:** `backend/core/message_counter.py`

Cada chamada entre agentes = 2 mensagens (ida + volta):

```
Estrela:                          Hierárquica:
Orquestrador → Agente (ida)       Coordenador → Supervisor (ida)
Agente → Orquestrador (volta)     Supervisor → Coordenador (volta)
= 2 mensagens por chamada         Supervisor → Agente (ida)
                                  Agente → Supervisor (volta)
8 agentes × 2 = ~16 mensagens    + comunicação lateral entre supervisores
                                  = ~24+ mensagens
```

A diferença na contagem de mensagens é uma das métricas comparativas entre as topologias.


---

## Apêndice: Código-Fonte dos Agentes

Esta seção mostra trechos reais do código de cada agente, demonstrando como o ciclo BDI é implementado na prática.

### A. Classe Base — AgenteBDI

O ciclo completo é executado por `run_cycle()`:

```python
# backend/agents/base.py

class AgenteBDI:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.beliefs: dict[str, Any] = {}
        self.desires: list[dict] = []
        self.intentions: list[dict] = []
        self._failed_intentions: list[dict] = []

    def run_cycle(self):
        """Executa o ciclo BDI completo: perceive → deliberate → plan → execute."""
        perception = self.perceive()
        self.update_beliefs(perception)
        desires = self.deliberate()
        self.intentions = self.plan(desires)
        self.execute()

    def execute(self):
        """Executa cada intenção pendente, com recuperação de falha."""
        for intention in self.intentions:
            if intention["status"] != "pending":
                continue
            try:
                self._execute_intention(intention)
            except IntentionFailure as e:
                intention["status"] = "failed"
                self._failed_intentions.append(intention)
                recovered = self._recover_intention(intention)
                if recovered:
                    self.intentions.append(recovered)
```

### B. Agente de Domínio — AgenteVigilanciaEpidemiologica

Cada agente de domínio segue o mesmo padrão. A diferença está na **configuração** (subfunção e tipos de indicador):

```python
# backend/agents/domain/vigilancia_epidemiologica.py

SUBFUNCAO = 305
TIPOS_INDICADOR = ["dengue", "covid"]

class AgenteVigilanciaEpidemiologica(AgenteBDI):
    def __init__(self, agent_id, neo4j_client):
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client

    # PERCEBER: "O que eu sei?"
    def perceive(self):
        return {
            "analysis_id": self.beliefs.get("analysis_id"),
            "date_from": self.beliefs.get("date_from"),
            "date_to": self.beliefs.get("date_to"),
        }

    # DELIBERAR: "O que eu quero fazer?"
    def deliberate(self):
        desires = []
        if self.beliefs.get("analysis_id") and self.beliefs.get("date_from") is not None:
            desires.append({"goal": "consultar_despesas"})
            desires.append({"goal": "consultar_indicadores"})
        #                    ↑ Se não tem parâmetros, desires fica vazio
        #                      e o agente não faz nada
        self.desires = desires
        return desires

    # EXECUTAR: "Fazendo o trabalho"
    def _execute_intention(self, intention):
        goal = intention["desire"]["goal"]
        if goal == "consultar_despesas":
            all_despesas = self.neo4j_client.get_despesas(...)
            # Filtra APENAS subfunção 305
            despesas = [d for d in all_despesas if d.get("subfuncao") == 305]
            self.beliefs["despesas"] = despesas

        elif goal == "consultar_indicadores":
            indicadores = self.neo4j_client.get_indicadores(
                ..., health_params=["dengue", "covid"]
            )
            self.beliefs["indicadores"] = indicadores

    # RECUPERAR: "Deu errado, o que faço?"
    def _recover_intention(self, failed_intention):
        goal = failed_intention["desire"]["goal"]
        if goal == "consultar_despesas":
            self.beliefs["despesas"] = []    # fallback: lista vazia
            return {"desire": {"goal": "noop"}, "status": "completed"}
```

### C. Diferença entre os 4 agentes de domínio

A única diferença real entre eles é a configuração:

```python
# vigilancia_epidemiologica.py     # saude_hospitalar.py
SUBFUNCAO = 305                    SUBFUNCAO = 302
TIPOS_INDICADOR = ["dengue",      TIPOS_INDICADOR = ["internacoes"]
                   "covid"]

# atencao_primaria.py              # mortalidade.py
SUBFUNCAO = 301                    SUBFUNCOES = [301, 302, 303, 305]  # TODAS!
TIPOS_INDICADOR = ["vacinacao"]    TIPOS_INDICADOR = ["mortalidade"]
```

O `AgenteMortalidade` é especial — não filtra por uma subfunção, retorna despesas de TODAS:

```python
# mortalidade.py — _execute_intention
despesas = [d for d in all_despesas if d.get("subfuncao") in [301, 302, 303, 305]]
#                                                             ↑ todas as subfunções
```

### D. AgenteCorrelacao — Cálculo estatístico

```python
# backend/agents/analytical/correlacao.py

class AgenteCorrelacao(AgenteBDI):
    # DELIBERAR: só calcula se tem dados cruzados
    def deliberate(self):
        desires = []
        if self.beliefs.get("dados_cruzados"):  # ← precisa de dados
            desires.append({"goal": "calcular_correlacoes"})
        return desires

    # EXECUTAR: calcula 3 coeficientes por par
    def _compute_correlations(self):
        crossed = self.beliefs["dados_cruzados"]

        # Agrupa por (subfunção, tipo_indicador)
        pairs = {}
        for item in crossed:
            key = (item["subfuncao"], item["tipo_indicador"])
            pairs.setdefault(key, []).append(item)

        for (subfuncao, tipo), items in pairs.items():
            xs = [it["valor_despesa"] for it in items]
            ys = [it["valor_indicador"] for it in items]

            if len(items) < 2:
                # Menos de 2 pontos → impossível calcular → retorna 0.0
                correlacoes.append({..., "pearson": 0.0, "spearman": 0.0, "kendall": 0.0})
            else:
                r_pearson  = _safe_correlation(stats.pearsonr, xs, ys)
                r_spearman = _safe_correlation(stats.spearmanr, xs, ys)
                r_kendall  = _safe_correlation(stats.kendalltau, xs, ys)

                correlacoes.append({
                    "subfuncao": subfuncao,
                    "tipo_indicador": tipo,
                    "pearson": round(r_pearson, 4),
                    "spearman": round(r_spearman, 4),
                    "kendall": round(r_kendall, 4),
                    "classificacao": _classify(r_spearman),  # "alta"/"média"/"baixa"
                })
```

### E. AgenteAnomalias — Detecção via mediana

```python
# backend/agents/analytical/anomalias.py

class AgenteAnomalias(AgenteBDI):
    def _detect_anomalies(self):
        crossed = self.beliefs["dados_cruzados"]

        # Agrupa por par e calcula medianas
        for (subfuncao, tipo), items in pairs.items():
            if len(items) < 2:
                continue  # ignora pares com poucos dados

            med_desp = _median([it["valor_despesa"] for it in items])
            med_ind  = _median([it["valor_indicador"] for it in items])

            for it in items:
                high_spend  = it["valor_despesa"] > med_desp
                low_outcome = it["valor_indicador"] < med_ind

                if high_spend and low_outcome:
                    anomalias.append({
                        "tipo_anomalia": "alto_gasto_baixo_resultado",
                        "descricao": f"Gasto acima da mediana com indicador abaixo..."
                    })

                low_spend    = it["valor_despesa"] < med_desp
                high_outcome = it["valor_indicador"] > med_ind

                if low_spend and high_outcome:
                    anomalias.append({
                        "tipo_anomalia": "baixo_gasto_alto_resultado",
                        "descricao": f"Gasto abaixo da mediana com indicador acima..."
                    })
```

### F. AgenteSintetizador — LLM com fallback

```python
# backend/agents/analytical/sintetizador.py

class AgenteSintetizador(AgenteBDI):
    # DELIBERAR: gera texto mesmo sem dados (fallback)
    def deliberate(self):
        if self.beliefs.get("analysis_id") is not None:
            desires.append({"goal": "sintetizar_texto"})
        return desires

    def _generate_analysis_text(self):
        try:
            return self._generate_via_llm()      # tenta Groq/Gemini
        except Exception:
            return self._generate_structured_text()  # fallback estruturado

    def _generate_via_llm(self):
        import core.llm_client as llm_client
        prompt = self._build_prompt(correlacoes, anomalias, contexto)
        text = llm_client.generate(prompt)   # Groq → Gemini → None
        if text:
            return text
        return self._generate_structured_text()  # LLM retornou None

    # Streaming: divide texto em chunks de ~80 chars
    def _stream_text(self, text, analysis_id, ws_queue, architecture):
        for i in range(0, len(text), 80):
            chunk = text[i: i + 80]
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": architecture,
                "type": "chunk",
                "payload": chunk,
            })
```

### G. AgenteContextoOrcamentario — Tendências YoY

```python
# backend/agents/context/contexto_orcamentario.py

STAGNATION_THRESHOLD = 5.0  # |variação| < 5% → estagnação

class AgenteContextoOrcamentario(AgenteBDI):
    def _analyze_trends(self):
        despesas = self.beliefs["despesas"]

        # Agrupa por subfunção, ordena por ano
        for subfuncao, items in by_subfuncao.items():
            sorted_years = sorted(year_values.keys())

            if len(sorted_years) < 2:
                tendencias[subfuncao] = {"tendencia": "insuficiente"}
                continue

            # Calcula variação ano a ano
            variations = []
            for i in range(1, len(sorted_years)):
                prev = year_values[sorted_years[i - 1]]
                curr = year_values[sorted_years[i]]
                variation = ((curr - prev) / prev) * 100  # variação percentual
                variations.append(variation)

            # Classifica
            tendencia = _classify_trend(variations)
            # "crescimento" se positivo consecutivo ≥ 2 anos
            # "corte" se negativo consecutivo ≥ 2 anos
            # "estagnacao" se todas |variação| < 5%
```

### H. OrquestradorEstrela — Coordenação central

```python
# backend/agents/star/orchestrator.py

class OrquestradorEstrela(AgenteBDI):
    def run(self, analysis_id, params, ws_queue):
        counter = MessageCounter()

        # Fase 1: Domínio (sequencial)
        for agent_id, agent_type, agent in domain_agents:
            mc = MetricsCollector(agent_id, agent_type)
            mc.start()
            try:
                result = agent.query(analysis_id, date_from, date_to)
                counter.increment(2)  # ida + volta
                all_despesas.extend(result["despesas"])
                all_indicadores.extend(result["indicadores"])
            except Exception as exc:
                ws_queue.put({..., "type": "error", "payload": str(exc)})
                # continua com dados parciais
            mc.stop()

        # Deduplica despesas (mortalidade retorna todas)
        # Cruza dados
        dados_cruzados = cross_domain_data(unique_despesas, all_indicadores)

        # Fase 2: Analítica
        contexto = agente_contexto.analyze_trends(unique_despesas)
        correlacoes = agente_correlacao.compute(dados_cruzados)
        anomalias = agente_anomalias.detect(dados_cruzados)
        texto = agente_sintetizador.synthesize(
            correlacoes, anomalias, contexto, analysis_id, ws_queue, "star"
        )
```

### I. CoordenadorGeral — Hierarquia com comunicação lateral

```python
# backend/agents/hierarchical/coordinator.py

class CoordenadorGeral(AgenteBDI):
    def run(self, analysis_id, params, ws_queue):
        # Instancia 3 supervisores
        sup_dominio = SupervisorDominio(...)
        sup_analitico = SupervisorAnalitico(...)
        sup_contexto = SupervisorContexto(...)

        # Supervisor de domínio executa 4 agentes
        dominio_data = sup_dominio.run(analysis_id, date_from, date_to, counter)

        # Comunicação LATERAL (sem passar pelo coordenador):
        sup_analitico.receive_from_peer({
            "despesas": dominio_data["despesas"],
            "indicadores": dominio_data["indicadores"],
        })
        sup_contexto.receive_from_peer({
            "despesas": dominio_data["despesas"],
        })

        # Supervisor de contexto executa AgenteContextoOrcamentario
        contexto_data = sup_contexto.run(counter=counter)

        # Mais comunicação lateral:
        sup_analitico.receive_from_peer({
            "contexto_orcamentario": contexto_data["contexto_orcamentario"],
        })

        # Supervisor analítico executa correlação, anomalias, sintetizador
        analitico_data = sup_analitico.run(analysis_id, ws_queue, counter)

        # Degradação graciosa: se um supervisor falha, continua com dados parciais
        # try/except em cada supervisor com ws_queue.put({"type": "error", ...})
```
