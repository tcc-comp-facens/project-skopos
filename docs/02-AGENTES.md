# Sistema Multiagente BDI

## Sumário

1. [Modelo BDI](#modelo-bdi-belief-desire-intention)
2. [Classe Base AgenteBDI](#classe-base-agentebdi)
3. [Agentes Especializados](#agentes-especializados-8-por-topologia)
4. [Arquitetura Estrela](#arquitetura-estrela)
5. [Arquitetura Hierárquica](#arquitetura-hierárquica)
6. [Regras de Negócio](#regras-de-negócio)

---

## Modelo BDI (Belief-Desire-Intention)

O modelo BDI é uma arquitetura cognitiva para agentes racionais baseada em três componentes mentais:

| Conceito | Descrição | Implementação no sistema |
|----------|-----------|--------------------------|
| **Beliefs** (Crenças) | Estado do ambiente percebido pelo agente | `dict[str, Any]` com dados do Neo4j, parâmetros da análise, resultados parciais |
| **Desires** (Desejos) | Objetivos que o agente quer alcançar | `list[dict]` de goals (ex: `"consultar_despesas"`, `"calcular_correlacoes"`) |
| **Intentions** (Intenções) | Planos de ação selecionados para execução | `list[dict]` com status (`pending` / `completed` / `failed`) |

### Ciclo BDI

Todos os agentes executam o mesmo ciclo:

```
perceive() → update_beliefs() → deliberate() → plan() → execute()
```

O método de conveniência `run_cycle()` executa o ciclo completo em sequência.

### Recuperação de Falhas

Quando uma intenção falha durante a execução:

1. A exceção `IntentionFailure` é levantada com a intenção e o motivo
2. A intenção é marcada como `failed` e movida para `_failed_intentions`
3. O agente tenta `_recover_intention()` para encontrar uma alternativa
4. Se a recuperação falhar, o erro é propagado ao orquestrador/supervisor
5. O agente nunca fica em estado indefinido

---

## Classe Base AgenteBDI

**Arquivo:** `backend/agents/base.py`

```python
class AgenteBDI:
    agent_id: str              # Identificador único
    beliefs: dict[str, Any]    # Base de crenças
    desires: list[dict]        # Objetivos
    intentions: list[dict]     # Planos selecionados
    _failed_intentions: list[dict]  # Intenções que falharam
```

### Métodos que subclasses sobrescrevem

| Método | Responsabilidade |
|--------|------------------|
| `perceive()` | Perceber o ambiente (consultar Neo4j ou ler crenças) |
| `deliberate()` | Selecionar desejos alcançáveis dadas as crenças |
| `plan(desires)` | Gerar intenções (planos) para cada desejo |
| `_execute_intention(intention)` | Executar uma intenção específica |
| `_recover_intention(failed)` | Encontrar alternativa para intenção falha |

### Padrão de IDs de Agentes

Formato: `{topologia}-{papel}-{uuid_hex[:8]}`

Exemplos:
- `star-vigilancia-a1b2c3d4`
- `star-correlacao-e5f6g7h8`
- `hier-coord-i9j0k1l2`
- `hier-sup-dominio-m3n4o5p6`

---

## Agentes Especializados (8 por topologia)

O sistema usa 8 agentes especializados organizados em 3 categorias:

### Agentes de Domínio (4) — `backend/agents/domain/`

Consultam o Neo4j para sua subfunção e tipo de indicador específicos.

| Agente | Arquivo | Subfunção | Indicadores | `__init__` | Método público |
|--------|---------|-----------|-------------|------------|----------------|
| `AgenteVigilanciaEpidemiologica` | `vigilancia_epidemiologica.py` | 305 | dengue, covid | `(agent_id, neo4j_client)` | `query(analysis_id, date_from, date_to)` |
| `AgenteSaudeHospitalar` | `saude_hospitalar.py` | 302 | internacoes | `(agent_id, neo4j_client)` | `query(analysis_id, date_from, date_to)` |
| `AgenteAtencaoPrimaria` | `atencao_primaria.py` | 301 | vacinacao | `(agent_id, neo4j_client)` | `query(analysis_id, date_from, date_to)` |
| `AgenteMortalidade` | `mortalidade.py` | todas (301,302,303,305) | mortalidade | `(agent_id, neo4j_client)` | `query(analysis_id, date_from, date_to)` |

**Comportamento comum:**
- Retornam `{"despesas": [...], "indicadores": [...]}`
- Retornam listas vazias sem exceção quando não há dados (degradação graciosa)
- `AgenteMortalidade` é transversal — cruza mortalidade com todas as subfunções
- Cada agente filtra apenas sua subfunção das despesas retornadas pelo Neo4j

### Agentes Analíticos (3) — `backend/agents/analytical/`

Operam sobre dados em memória — sem dependência de Neo4j.

| Agente | Arquivo | `__init__` | Método público | Entrada | Saída |
|--------|---------|------------|----------------|---------|-------|
| `AgenteCorrelacao` | `correlacao.py` | `(agent_id)` | `compute(dados_cruzados)` | CrossedDataPoint[] | CorrelacaoResult[] |
| `AgenteAnomalias` | `anomalias.py` | `(agent_id)` | `detect(dados_cruzados)` | CrossedDataPoint[] | AnomaliaResult[] |
| `AgenteSintetizador` | `sintetizador.py` | `(agent_id)` | `synthesize(correlacoes, anomalias, contexto, analysis_id, ws_queue, architecture, data_coverage)` | Resultados dos demais | texto (str) |

### Agente de Contexto (1) — `backend/agents/context/`

| Agente | Arquivo | `__init__` | Método público | Entrada | Saída |
|--------|---------|------------|----------------|---------|-------|
| `AgenteContextoOrcamentario` | `contexto_orcamentario.py` | `(agent_id)` | `analyze_trends(despesas)` | DespesaRecord[] | dict[int, TendenciaResult] |

---

## Arquitetura Estrela

**Arquivo:** `backend/agents/star/orchestrator.py`

```
                    OrquestradorEstrela (Hub)
                           │
        ┌──────┬───────┬───┴───┬───────┬──────┬──────┬──────┐
        ▼      ▼       ▼       ▼       ▼      ▼      ▼      ▼
   Vigilância Hospitalar Primária Mortalidade Contexto Correlação Anomalias Sintetizador
```

**Características:**
- Hub central intermedia TODA comunicação — nenhum agente periférico chama outro diretamente
- Ponto único de falha (se o orquestrador falhar, toda a análise falha)
- Comunicação simples: orquestrador → agente → orquestrador
- Contagem de mensagens esperada: ~16 (8 agentes × 2 mensagens ida+volta)

### Pipeline Estrela (10 passos)

1. Instancia 8 agentes com IDs únicos (`star-{papel}-{uuid_hex[:8]}`)
2. Cria `MessageCounter` para contagem de mensagens
3. Executa 4 agentes de domínio em sequência → agrega despesas + indicadores
4. Deduplica despesas (mortalidade retorna todas as subfunções, pode sobrepor)
5. Cruza dados via `cross_domain_data()` → produz CrossedDataPoints
6. Detecta lacunas de dados via `detect_data_gaps()`
7. Passa despesas ao `AgenteContextoOrcamentario.analyze_trends()`
8. Passa dados cruzados ao `AgenteCorrelacao.compute()`
9. Passa dados cruzados ao `AgenteAnomalias.detect()`
10. Passa correlações, anomalias, contexto e data_coverage ao `AgenteSintetizador.synthesize()`

Em cada passo:
- `MetricsCollector` registra tempo, CPU e memória do agente
- `MessageCounter` incrementa 2 por chamada (ida + volta)
- Em falha: envia evento `error` via `ws_queue` e continua com resultados parciais

---

## Arquitetura Hierárquica

**Arquivos:** `backend/agents/hierarchical/coordinator.py`, `supervisors.py`

```
            CoordenadorGeral (Nível 0)
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
  SupervisorDominio  SupervisorContexto  SupervisorAnalitico
   (Nível 1)         (Nível 1)           (Nível 1)
        │                │                    │
   ┌────┼────┐           │              ┌─────┼─────┐
   ▼    ▼    ▼    ▼      ▼              ▼     ▼     ▼
  Vig  Hosp Prim Mort  CtxOrç         Corr  Anom  Sint
  (Nível 2)            (Nível 2)       (Nível 2)

Comunicação lateral (sem intermediação do Coordenador):
  SupervisorDominio ──→ SupervisorAnalitico (despesas + indicadores)
  SupervisorDominio ──→ SupervisorContexto  (despesas)
  SupervisorContexto ──→ SupervisorAnalitico (contexto orçamentário)
```

**Características:**
- Hierarquia de 3 níveis com comunicação lateral entre supervisores
- Degradação graciosa: se um supervisor falha, o coordenador continua com resultados parciais
- Supervisores expõem `_collectors: list[MetricsCollector]` para persistência de métricas dos subordinados
- Contagem de mensagens esperada: ~24+ (agentes + supervisores + comunicação lateral)

### Pipeline Hierárquico

1. `CoordenadorGeral` instancia 3 supervisores com IDs únicos
2. Delega para `SupervisorDominio.run()` → coordena 4 agentes de domínio
3. Comunicação lateral: `SupervisorDominio` → `SupervisorAnalitico` (despesas + indicadores)
4. Comunicação lateral: `SupervisorDominio` → `SupervisorContexto` (despesas)
5. Delega para `SupervisorContexto.run()` → coordena `AgenteContextoOrcamentario`
6. Comunicação lateral: `SupervisorContexto` → `SupervisorAnalitico` (contexto orçamentário)
7. Delega para `SupervisorAnalitico.run()` → coordena correlação, anomalias, sintetizador
8. Persiste métricas para 8 agentes + 3 supervisores
9. Em falha de supervisor: envia evento `error`, continua com dados parciais

### Comunicação Lateral (`receive_from_peer`)

Os supervisores trocam dados diretamente via `receive_from_peer(data: dict)` sem intermediação do coordenador. Isso reduz o gargalo no nível 0 mas aumenta o acoplamento entre supervisores.

### Degradação Graciosa

Se um supervisor falha:
- O coordenador captura a exceção
- Envia evento `error` via `ws_queue`
- Continua com dados vazios para aquele supervisor
- O resultado final é parcial mas válido

---

## Regras de Negócio

### Mapeamento Subfunção → Indicador

| Subfunção | Código | Nome | Indicador correlacionado | Sistema DataSUS |
|-----------|--------|------|--------------------------|-----------------|
| 301 | Atenção Básica | Cobertura vacinal | SI-PNI |
| 302 | Assistência Hospitalar | Internações | SIH |
| 303 | Suporte Profilático | — (sem indicador direto) | — |
| 305 | Vigilância Epidemiológica | Dengue, COVID-19 | SINAN |
| — | Mortalidade (transversal) | Mortalidade | SIM |

### Cruzamento de Dados (`cross_domain_data`)

**Arquivo:** `backend/agents/data_crossing.py`

Para cada subfunção no mapeamento, encontra indicadores do tipo correspondente no mesmo ano:
- 301 → vacinacao (mesmo ano)
- 302 → internacoes (mesmo ano)
- 305 → dengue, covid (mesmo ano)
- Mortalidade → transversal, cruza com TODAS as subfunções (301, 302, 303, 305)

Produz `CrossedDataPoint`:
```python
{
    "subfuncao": 305,
    "subfuncao_nome": "Vigilância Epidemiológica",
    "tipo_indicador": "dengue",
    "ano": 2020,
    "valor_despesa": 45600000.0,
    "valor_indicador": 5231.0,
}
```

### Correlações Estatísticas (`AgenteCorrelacao`)

Três métodos implementados para cada par subfunção-indicador:

| Método | Tipo | Uso |
|--------|------|-----|
| **Pearson** | Relação linear | Detecta relações proporcionais diretas |
| **Spearman** | Baseado em ranks | Robusto a outliers, monotônico não-linear |
| **Kendall Tau-b** | Concordância entre pares | Ideal para amostras pequenas |

**Classificação** (baseada no coeficiente de Spearman):
- `|r| >= 0.7` → **"alta"**
- `|r| >= 0.4` → **"média"**
- `|r| < 0.4` → **"baixa"**

**Regras:**
- Pares com < 2 pontos de dados retornam 0.0 para todas as métricas
- Valores NaN (arrays constantes) são tratados como 0.0
- Resultado clamped a [-1, 1]

### Detecção de Anomalias (`AgenteAnomalias`)

Compara despesas e indicadores com suas medianas por par subfunção-indicador:

| Tipo de anomalia | Condição | Interpretação |
|------------------|----------|---------------|
| `alto_gasto_baixo_resultado` | despesa > mediana E indicador < mediana | Possível ineficiência |
| `baixo_gasto_alto_resultado` | despesa < mediana E indicador > mediana | Possível eficiência ou fator externo |

**Regras:**
- Pares com < 2 pontos de dados são ignorados
- Mediana calculada separadamente para despesas e indicadores de cada par
- Descrição textual gerada em português com valores formatados

### Tendências Orçamentárias (`AgenteContextoOrcamentario`)

Calcula variação percentual ano a ano: `((valor_n - valor_n-1) / valor_n-1) × 100`

**Classificação de tendências:**

| Tendência | Condição |
|-----------|----------|
| `crescimento` | Variação positiva consecutiva por ≥ 2 anos |
| `corte` | Variação negativa consecutiva por ≥ 2 anos |
| `estagnacao` | Todas as variações com `|variação| < 5%` |
| `insuficiente` | Menos de 2 anos de dados |

**Tratamento de edge cases:**
- Divisão por zero (valor anterior = 0): retorna ±∞ conforme sinal do valor atual
- Valores infinitos: classificados pela direção (positivo = crescimento, negativo = corte)

### Detecção de Lacunas de Dados (`detect_data_gaps`)

**Arquivo:** `backend/agents/data_crossing.py`

Identifica para o período solicitado:
- Anos faltantes por subfunção (despesas)
- Anos faltantes por tipo (indicadores)
- Cruzamentos impossíveis (despesa sem indicador correspondente ou vice-versa)
- Cobertura percentual por dimensão

O resultado é passado ao sintetizador para transparência no texto gerado — o LLM é instruído a mencionar explicitamente quais dados estão faltando e como isso limita as conclusões.

### Contagem de Mensagens (`MessageCounter`)

**Arquivo:** `backend/message_counter.py`

Cada chamada de método entre agentes conta como 2 mensagens (ida + volta):
- **Estrela**: ~16 mensagens (8 agentes × 2)
- **Hierárquica**: ~24+ mensagens (8 agentes × 2 + 3 supervisores × 2 + 4 comunicações laterais × 2)

O total é persistido no Neo4j e enviado via WebSocket para comparação quantitativa.
