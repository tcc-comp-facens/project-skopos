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
