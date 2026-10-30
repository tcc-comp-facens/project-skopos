# Métricas de Avaliação — Arquitetura e Justificativa Acadêmica

> Este documento cobre o módulo de métricas comparativas entre as topologias estrela e hierárquica: o que cada métrica mede, como é calculada (com o código real) e — seguindo o mesmo padrão da Seção 3 ("Justificativa Acadêmica por Decisão") do [`PLANO_REFATORACAO.md`](../../PLANO_REFATORACAO.md) — qual é o embasamento acadêmico de cada uma, com citação direta da fonte ou a marcação explícita **"decisão de engenharia, sem embasamento acadêmico direto"** quando não há fonte (nunca uma citação inventada). Para o pipeline que produz os dados que essas métricas consomem, ver [arquitetura-estrela.md](arquitetura-estrela.md), [arquitetura-hierarquica.md](arquitetura-hierarquica.md) e [ciclo-completo.md](ciclo-completo.md).

**Arquivos principais:** `backend/core/quality_metrics.py` (métricas determinísticas), `backend/core/ragas_metrics.py` (avaliação da resposta via biblioteca RAGAS), `backend/core/llm_client.py` (`TokenBucket`), `backend/core/guardrail_stats.py`, `backend/agents/domain/query_planning.py` (contadores de cache).

## Visão geral — seis eixos

O módulo evoluiu em três fases. Os três eixos originais (E/Q/R) existiam antes do plano de refatoração; os três eixos novos foram adicionados na Etapa 6 do `PLANO_REFATORACAO.md`, depois que as Etapas 1–5 já tinham dado aos agentes os pontos de decisão via LLM que essas métricas passaram a medir. A terceira fase substituiu o eixo B por uma biblioteca de avaliação de verdade (ver "Eixo B" abaixo).

```
core/quality_metrics.py               (100% determinístico — nenhuma chamada LLM)
├── A. Eficiência dos Agentes         E1  compute_coordination_overhead
│                                     E2  compute_latency_breakdown
├── B. Qualidade da Resposta          Q1  compute_deterministic_consistency
│                                     Q3  compute_completeness
├── C. Resiliência                    R1  compute_partial_result_coverage
├── D. Custo e Comunicação (Etapa 6)  compute_token_cost
│                                     compute_communication_volume
└── E. Outcome agregado (Etapa 6)     compute_analysis_success

core/ragas_metrics.py                 (sempre executado, custa LLM; ver Eixo B)
└── B. Qualidade da Resposta          faithfulness
                                      answer_relevancy
                                      context_relevance

core/guardrail_stats.py               compute_guardrail_rejection_rate   (process-wide)
agents/domain/query_planning.py       compute_cache_hit_rate             (process-wide)
```

A distinção entre **por-análise** (as seis primeiras — calculadas a cada comparação estrela-vs-hierárquica, no payload de `/api/analysis/{id}/quality`) e **process-wide** (as duas últimas — contadores acumulados ao longo de várias análises, expostas em `GET /api/metrics/guardrail` e `GET /api/metrics/query-planning-cache`) é proposital: guardrail e cache não descrevem uma análise isolada, descrevem uma tendência de comportamento do sistema ao longo do tempo.

## Ordem de execução (medir → avaliar → concluir)

Nem toda métrica custa a mesma coisa, mas **todas rodam sempre** — não há toggle. `api/websocket.py` executa três etapas em ordem, e a ordem é significativa:

```python
# 1. Determinístico e gratuito — nenhuma chamada LLM
quality = compute_all_quality_metrics(
    star_result=star_result, hier_result=hier_result,
    star_agent_metrics=..., hier_agent_metrics=..., ...
)
# → evento "quality_metrics", imediato

# 2. Avaliação RAGAS — custa LLM; as duas arquiteturas rodam concorrentes
async def _avaliar(arch_key, arch_result):
    with TokenBucket() as bucket:          # bucket por Task, isolado
        payload = await evaluate_architecture(arch_result, user_input, ...)
    return arch_key, payload, compute_token_cost(bucket.snapshot())

resultados = await asyncio.gather(_avaliar("star", ...), _avaliar("hierarchical", ...))
# → eventos "ragas" / "ragas_done"

# 3. Relatório comparativo — só agora, porque o veredito depende do RAGAS
report = generate_comparative_report(..., ragas=ragas_payloads)
# → eventos "chunk" / "done"
```

**`compute_all_quality_metrics` não chama o LLM em nenhum caminho** — é sempre determinística, gratuita e reproduzível (há teste garantindo isso, `test_never_calls_the_llm`). Toda a avaliação que custa LLM está num módulo só e é assíncrona.

O relatório vir **por último** não é detalhe de implementação: ele contém a seção "Conclusão", e o vencedor é decidido primeiro pela fidelidade (ver D20). Enquanto o RAGAS rodava depois do relatório, o sistema anunciava uma topologia vencedora escolhida sem a métrica que mais pesa no critério.

O custo da avaliação é **fixo**, independente do volume de achados: 2 chamadas para `faithfulness`, 3 para `answer_relevancy` e 2 para `context_relevance`, vezes duas arquiteturas — 14 chamadas por análise, mais os embeddings.

Nem sempre foi assim. Enquanto o terceiro pilar era medido por `ContextPrecisionWithoutReference`, o custo era de **1 chamada por achado**, em laço sequencial dentro da biblioteca (`for context in retrieved_contexts: await ...`, sem `asyncio.gather`): numa análise de 4 anos passava de 140 chamadas e a métrica dominava o relógio, na casa dos minutos. A troca por `ContextRelevance` (D25) eliminou esse crescimento.

As duas arquiteturas continuam sendo avaliadas **concorrentemente** (`asyncio.gather` em `_run_ragas_evaluation`). A contabilização de custo permanece correta porque cada corrotina abre seu próprio `TokenBucket` *dentro* da Task: `gather` dá a cada Task uma cópia do contexto, então os `ContextVar` não vazam entre elas.

---

## Eixo A — Eficiência dos Agentes

### E1 — Overhead de coordenação

```python
NOS_RAIZ = {"orquestrador_estrela", "coordenador_geral"}

def compute_coordination_overhead(agent_metrics: list[dict]) -> dict[str, Any]:
    coord_time = worker_time = 0.0
    for m in agent_metrics:
        nome, ms = m.get("agentName", ""), m.get("executionTimeMs", 0)
        if nome in NOS_RAIZ:
            coord_time += ms           # já é o overhead (relógio determinístico - Σ folhas)
        elif nome.startswith("supervisor_"):
            continue                   # engloba os subordinados: só exibição
        else:
            worker_time += ms
    ratio = coord_time / (coord_time + worker_time) if (coord_time + worker_time) > 0 else 0.0
    return {"supervisor_time_ms": ..., "worker_time_ms": ..., "overhead_ratio": ratio, "overhead_percent": ratio * 100}
```

Overhead = relógio determinístico da topologia menos a soma dos agentes-folha, **com a mesma definição na estrela e na hierárquica**. Esse valor já vem calculado pelo orquestrador/coordenador na entrada `orquestrador_estrela`/`coordenador_geral`. As entradas `supervisor_*` da hierárquica são ignoradas: o tempo de cada supervisor já contém o dos subordinados, e somá-los dobraria a contagem (uma versão anterior fazia isso e reportava ~50% de "overhead" que não existia). O relógio fecha antes da gravação das métricas no Neo4j e da redação (priorização, síntese, verificação), nas duas topologias. As decisões dos coordenadores que vêm antes da coleta e logo depois dela (planejamento e revisão das entregas) ficam dentro do relógio e entram no overhead, porque são o custo de coordenar. Na hierárquica essas decisões são repartidas entre o coordenador e os supervisores — ver [arquitetura-hierarquica.md](arquitetura-hierarquica.md#contabilização-de-métricas-e-overhead).

**Deliberação e estrutura.** Com o planejamento e a revisão por LLM, quase todo o overhead passou a ser espera pelo modelo. Numa pergunta sobre covid, foram 4,59 s de 4,59 s na estrela e 10,51 s de 10,51 s na hierárquica, com cerca de 5 ms de custo estrutural nas duas. Por isso o overhead é dividido em duas partes:

- `deliberation_time_ms` é o tempo das decisões por LLM dos coordenadores dentro do relógio. É medido por `AgenteCoALA._deliberar` em volta de `planejamento.planejar` e de `revisao_entregas.revisar_memoria_do_no`. Na estrela é o orquestrador. Na hierárquica é o coordenador somado aos supervisores de orçamento e de saúde.
- `structural_time_ms` é o restante, isto é, instanciar, repassar, agregar e registrar mensagens.

`deliberation_share` é a fração do overhead gasta em deliberação. A entrada da raiz em `agentMetrics` traz `deliberacaoMs`, e a bateria grava `overhead_deliberacao_ms` e `overhead_estrutural_ms`. Assim a diferença entre topologias fica separada entre o número de decisões encadeadas, que depende da latência do provedor, e o custo da estrutura em si.

### E2 — Breakdown de latência por fase

Divide o tempo total em 4 fases (`dominio`, `analitico`, `contexto`, `supervisores`), usando os mesmos conjuntos `FASE_DOMINIO`/`FASE_ANALITICO`/`FASE_CONTEXTO`/`NOS_RAIZ` (a fase `supervisores` conta só o overhead da raiz, não os `supervisor_*`) — note que `FASE_ANALITICO` inclui `"priorizacao"` e `"verificacao"` (Etapas 3 e 4), acompanhando a evolução do pipeline analítico. Retorna tempo absoluto e percentual de cada fase, usado no relatório comparativo para mostrar onde cada topologia gasta seu tempo.

### E3 — Tempo até o texto (`tempo_total_ms`)

O relógio de E1 fecha antes da parte com LLM, para isolar a coordenação. Mas é justamente na parte com LLM que as topologias passaram a diferir (planejamento, redação por ramo, composição). Por isso cada resultado traz também `tempo_total_ms`, medido do início do `run()` da raiz até o texto final, redação incluída. A bateria grava as duas medidas lado a lado.

**Nenhuma das métricas de tempo usa limiar fixo** (ex.: "overhead > 30% é ruim") — a comparação é sempre relativa entre as duas topologias na mesma análise, nunca contra um corte absoluto. Essa é uma decisão deliberada (D12, ver tabela).

---

## Eixo B — Qualidade da Resposta

### Q1 — Consistência determinística

```python
def compute_deterministic_consistency(star_result, hier_result) -> dict[str, Any]:
    corr_identical = _sort_corr(star_result["correlacoes"]) == _sort_corr(hier_result["correlacoes"])
    anom_identical = _sort_anom(star_result["anomalias"]) == _sort_anom(hier_result["anomalias"])
    return {"all_identical": corr_identical and anom_identical, "divergences": [...]}
```

As duas topologias processam os mesmos dados brutos com os mesmos algoritmos determinísticos (Spearman, mediana) — correlações e anomalias devem ser **numericamente idênticas**, independente de qual ângulo de ênfase a Etapa 3 escolheu ou qual texto a Etapa 4 corrigiu (por isso `result["correlacoes"]`/`result["anomalias"]` retornados pelos orquestradores são sempre os dados brutos, nunca a versão reordenada — ver nota em ambos os documentos de arquitetura). Uma divergência aqui indica bug ou não-determinismo real, não uma diferença de interpretação — é a métrica mais "dura" do sistema: não há ambiguidade sobre o que conta como sucesso.

### Fidelidade e relevância — a biblioteca RAGAS

Esta parte do eixo **não é implementada aqui**. Ela é a biblioteca [`ragas`](https://github.com/explodinggradients/ragas), chamada em `core/ragas_metrics.py`.

Antes existiam três implementações caseiras da mesma pergunta ("o texto reflete os dados?"), todas removidas:

| Removida | O que era | Por que saiu |
|---|---|---|
| `compute_faithfulness` (substring) | checklist: a subfunção/indicador/ano aparece no texto? | verifica se as *palavras certas* aparecem, não se a afirmação ao redor delas é verdadeira — um texto que inverte o sinal de uma correlação pontuava 100% |
| `_compute_faithfulness_claims` | claim-based "estilo RAGAS", reaproveitando `claim_verifier` | reimplementação manual e aproximada da métrica do paper, sem a validação do original; além disso media o texto com o mesmo mecanismo que o *corrige* (Etapa 4) |
| `compute_faithfulness_llm` | LLM-as-judge, nota holística de 1 a 5 | é literalmente o baseline **"GPT Score"** que o paper do RAGAS mede como inferior — ver D17 |

#### As três métricas adotadas

Todas são *reference-free*: não exigem resposta-padrão anotada, que é exatamente o cenário para o qual o RAGAS foi desenhado ("we focus on metrics that are fully self-contained and reference-free", Seção 3) e exatamente o cenário deste projeto.

| Métrica | Aspecto do paper | Como o paper define |
|---|---|---|
| `faithfulness` | Faithfulness (§3) | um LLM decompõe a resposta num conjunto de afirmações `S`, verifica cada uma contra o contexto, e `F = \|V\| / \|S\|`, com `V` as afirmações suportadas |
| `answer_relevancy` | Answer Relevance (§3) | um LLM gera `n` perguntas a partir da resposta; `AR` é a média das similaridades de cosseno entre os embeddings dessas perguntas e o da pergunta original |
| `context_relevance` | Context Relevance (§3) | dois juízes avaliam, numa escala 0/1/2, o quanto o contexto entregue é relevante para responder à pergunta |

As três são importadas de `ragas.metrics.collections`, **não** de `ragas.metrics`: o namespace antigo emite `DeprecationWarning` e a própria biblioteca anuncia sua remoção na v1.0.

#### O mapeamento de um pipeline que não é RAG

Esta é a ressalva metodológica mais importante desta seção, e precisa estar explícita: **este sistema não é um RAG clássico.** Não há retriever, não há corpus de documentos, não há busca vetorial. O RAGAS pressupõe a tripla `(pergunta, resposta, contexto recuperado)`.

O que justifica o mapeamento é que a posição estrutural do "contexto recuperado" existe aqui: os agentes de domínio consultam o Neo4j e calculam achados determinísticos (correlações de Spearman, anomalias, tendências orçamentárias), e é esse conjunto — e **só** ele — que o `TextSynthesizer` recebe para escrever o texto. Ele é, funcionalmente, o contexto sobre o qual a geração acontece.

| Campo do RAGAS | Origem no projeto |
|---|---|
| `user_input` | a pergunta original digitada no chat (`source_question`) |
| `response` | `result["texto_analise"]` — o texto do sintetizador |
| `retrieved_contexts` | **tudo** que o sintetizador recebeu (ver abaixo) |

**O contexto do juiz tem que ser idêntico ao do gerador.** Uma versão anterior mandava ao juiz uma forma empobrecida dos achados — só subfunção, indicador, coeficiente e classificação — e omitia campos que o gerador tinha: a `leitura` da correlação (que o prompt manda usar como *fonte da verdade* sobre o resultado ser desejável ou indesejável), o `n_pontos`, e a `descricao` da anomalia, que carrega **o valor gasto em R$ e o valor do indicador**. O texto citava esses dados corretamente e era reprovado, porque a evidência não estava no contexto do juiz. A fidelidade media a lacuna entre os dois contextos, não a fidelidade do texto.

**Séries anuais.** Redator e juiz recebem também a série anual de cada indicador pedido e o gasto anual de cada subfunção, uma linha por série, montadas pela mesma função (`core/contextos_avaliacao.linhas_series`). Antes, o redator só via os anos que viravam anomalia. Numa pergunta sobre dengue de 2020 a 2023, ele escreveu que "não há o número de 2020 e 2022", dados que existiam, e não conseguia responder "quantos casos". Na hierárquica, cada redator de ramo recebe só as séries do seu sistema, e a composição recebe todas.

Pelo mesmo motivo entram no contexto as afirmações que o prompt **injeta** no gerador e manda repetir: o período analisado, a nota sobre a pandemia, a tradução das subfunções para linguagem comum e as lacunas de dados. Em termos de RAG, uma afirmação fornecida ao gerador *é* parte do contexto sobre o qual a geração se apoia — o prompt diz literalmente "Sempre mencione esse fator". Excluí-la garantiria infidelidade artificial: o texto seria punido por obedecer à instrução.

Essas strings vivem como constantes em `agents/analytical/sintetizador.py` (`CONTEXTO_PANDEMIA`, `TRADUCAO_SUBFUNCOES`) e são **importadas** por `build_contexts`, não reescritas. Duplicá-las faria o contexto do juiz divergir do prompt na primeira edição, reintroduzindo o mesmo problema de forma silenciosa.

```python
# core/ragas_metrics.py — build_contexts()
"Correlação: subfunção 305 (Vigilância Epidemiológica) × dengue — coeficiente de Spearman 0.82, classificação alta."
"Anomalia em 2021: subfunção 305 (Vigilância Epidemiológica) × dengue — tipo alto_gasto_baixo_resultado."
"Contexto orçamentário da subfunção 305 (Vigilância Epidemiológica): tendência crescente, variação média 12.4%."
```

Um chunk por achado (em vez de um bloco único de texto) é o que torna a *context precision* interpretável: ela julga achado a achado se aquele item foi útil para a resposta — e é justamente aí que a comparação arquitetural fica visível, porque uma topologia pode entregar ao sintetizador mais achados irrelevantes que a outra.

**Não há teto de contextos.** Todas as métricas recebem o conjunto completo de achados, porque nenhuma escala em número de chamadas com a quantidade de chunks. Houve um teto (`RAGAS_MAX_CONTEXTS`) enquanto o terceiro pilar era medido por `ContextPrecisionWithoutReference`, que gasta 1 chamada LLM por achado; a troca por `ContextRelevance` (D25) tornou o teto — e o viés de corte que ele introduzia — desnecessários.

#### O juiz é fixo, de propósito

O juiz é o `gpt-5-mini`, fixo no código (`ragas_metrics.MODELO_JUIZ`), e as análises usam o `gpt-5.6-luna`. Há duas razões:
- **Juiz diferente do gerador.** Um modelo avaliando o próprio texto tende a favorecê-lo, o viés de autopreferência documentado na literatura de LLM como juiz (Zheng et al., 2023; Panickssery et al., 2024). Com modelos distintos, quem escreve nunca é quem julga.
- **Instrumento fixo.** Sem variável para trocá-lo, os scores de execuções diferentes continuam comparáveis (D18).

O `gpt-5-mini` foi escolhido por custo-benefício. As tarefas do RAGAS (extrair afirmações, checá-las contra os achados, gerar perguntas) são simples, e um juiz menor e diferente basta para o objetivo, que é eliminar a autopreferência. O desenho pareado ainda protege a comparação, porque as duas topologias são julgadas pelo mesmo juiz.

As chamadas passam por `core.llm_client.generate(model=MODELO_JUIZ)` em vez de a ragas abrir seu próprio cliente. Isso preserva três coisas que um `LangchainLLMWrapper(ChatOpenAI(...))` perderia: a contabilização de tokens por `TokenBucket` (sem ela o eixo D não enxergaria o custo da avaliação), o retry em 429, e o tratamento dos modelos de raciocínio da OpenAI — que exigem `max_completion_tokens` e rejeitam temperatura customizada, enquanto a ragas pede temperatura baixa por padrão (o que daria **400** num `gpt-5*`).

#### O juiz é um modelo de raciocínio — e isso exige cuidado

Nos modelos de raciocínio da OpenAI (`gpt-5*`, série `o*`), `max_completion_tokens` limita **tokens de raciocínio + resposta visível**, não só a resposta. Com o esforço no default da API (`medium`) e o teto de 4096 que o cliente usava para todos os modelos, um prompt grande — a verificação de afirmações do RAGAS sobre dezenas de achados — gastava o orçamento inteiro pensando e devolvia `content` vazio. O sintoma que chegava ao usuário era `faithfulness: RuntimeError: LLM indisponível`, indistinguível de uma queda de API.

A correção está em `core/llm_client.py::_completion_params`: esforço de raciocínio baixo (`OPENAI_REASONING_EFFORT`, default `low`) e um teto próprio e folgado (`OPENAI_MAX_COMPLETION_TOKENS`, default 16384). `_generate` também passou a ler `finish_reason`, para que truncagem por orçamento apareça no log como truncagem, não como "resposta vazia".

O modelo em si nunca foi o problema: era o orçamento de saída. O juiz atual (`gpt-5-mini`) também é de raciocínio e passa pela mesma regra.

#### Degradação graciosa

Nenhuma falha da avaliação derruba a análise. Sem a API key do juiz, o payload vem `available: false` com `unavailable_reason` legível — e não um score 0, que seria indistinguível de um score 0 legítimo (era exatamente a ambiguidade do `compute_faithfulness_llm` antigo, que devolvia `score: 0` tanto para "texto péssimo" quanto para "LLM indisponível"). A falha de uma métrica não impede as outras, e vai registrada em `errors[]`.

Chaves de projeto da OpenAI podem restringir acesso **por modelo**, e o sintoma é um `403 model_not_found` — não um erro de credencial. Observado em execução real: a mesma chave chamava `chat.completions` sem problema e recebia 403 em todos os modelos de embeddings.

Há duas causas possíveis, indistinguíveis pela mensagem:

1. **A allowlist do projeto não inclui embeddings** — corrige-se em platform.openai.com > Project > Limits (permissões de modelo).
2. **O cabeçalho `OpenAI-Project`**, que o SDK injeta a partir do projeto da chave, dispara uma checagem de acesso que falha em algumas configurações *mesmo com o modelo liberado*. Mandar o cabeçalho vazio pula a checagem; a chave por si só já identifica organização e projeto.

`SkoposRagasEmbeddings` cobre as duas: ao receber 403, repete a mesma chamada sem o cabeçalho antes de desistir do modelo, e só então desce a cadeia de alternativas. Memoiza o modelo e o modo que funcionaram e reporta em `judge.embedding_model_used` — o payload nunca afirma ter usado um modelo que apenas pediu. O fallback dispara só em erro de acesso a modelo; 429 e falhas de rede propagam para o retry existente.

Para distinguir as duas causas sem adivinhar: `python -m scripts.check_embeddings` testa cada modelo quanto à presença na allowlist **e** à chamada real, nas duas condições de cabeçalho, e imprime a matriz. A distinção importa porque "fora da allowlist" e "na allowlist mas negado" produzem o mesmo 403 e têm soluções diferentes — foi exatamente o que separou o diagnóstico quando a liberação dos modelos registrou no plano de controle antes de o caminho de inferência aplicá-la.

#### Qual modelo de embeddings, e por quê

O modelo e as alternativas são constantes do módulo (`DEFAULT_EMBEDDING_MODEL` e `DEFAULT_EMBEDDING_FALLBACKS`), sem configuração por ambiente, como o juiz.

O `answer_relevancy` compara a pergunta do usuário com perguntas geradas a partir da resposta: **similaridade semântica entre textos curtos em português**. É nesse eixo que os modelos se separam:

| Modelo | MIRACL (multilíngue) | MTEB (inglês) | MTEB-BR (22 tarefas pt-BR) | Dims | Preço /1M |
|---|---|---|---|---|---|
| `text-embedding-ada-002` | 31,4% | 61,0% | não avaliado | 1536 | US$ 0,10 |
| `text-embedding-3-small` | 44,0% | 62,3% | — | 1536 | US$ 0,02 |
| **`text-embedding-3-large`** (default) | **54,9%** | **64,6%** | **0,645** | 3072 | US$ 0,13 |

O `ada-002` está estritamente dominado: pior em todos os benchmarks *e* 5× mais caro que o `3-small`. Ele permanece na cadeia de fallback apenas por ser o mais amplamente liberado nos projetos (a OpenAI declarou que não vai depreciá-lo).

Entre `3-small` e `3-large` o custo não é critério: são 4 textos curtos por arquitetura, 8 por análise — algumas centenas de tokens, ou **~US$ 0,00005 por análise**. A diferença de preço (6,5×) é irrelevante em valor absoluto; a de qualidade multilíngue (54,9% contra 44,0%) não é.

Fontes: [OpenAI, *New embedding models and API updates*](https://openai.com/index/new-embedding-models-and-api-updates/); Stekel, T. R. C., *MTEB-BR: A Text Embedding Benchmark for Brazilian Portuguese*, IFSP ([arXiv:2607.04581](https://arxiv.org/abs/2607.04581)).

Detalhe de implementação com consequência real: quando uma métrica não pode ser calculada, a ragas devolve `float("nan")`. `json.dumps(nan)` emite o literal `NaN`, que é **JSON inválido** e quebra o `JSON.parse` do browser — levando junto o evento WebSocket inteiro, não só a métrica. `_clean_score` converte `NaN`/infinito para `null` e normaliza escalares numpy.

### Q4 — Fidelidade numérica

```python
# core/quality_metrics.py
def compute_numeric_fidelity(texto, contextos, anos=None) -> dict:
    # score = números do texto encontrados no material entregue / números do texto
```

Complemento determinístico da fidelidade do RAGAS. Extrai os números citados no texto (formato pt-BR, com escalas como "mil", "milhões" e "bilhões") e confere cada um contra os números do material entregue ao redator, que é o mesmo `build_contexts` usado pelo RAGAS (movido para `core/contextos_avaliacao.py`). A tolerância relativa é de 2%, para cobrir arredondamentos. Anos entre 1900 e 2100, escritos sem separador de milhar, contam como verificados quando estão dentro do período analisado. Com separador ("2.000 casos"), o número é uma contagem e é conferido como os demais, e inteiros de um dígito são ignorados (enumerações, ordinais). O retorno traz `score` (`null` quando o texto não cita números), `numeros_no_texto`, `verificados` e `nao_verificados` (os literais).

O motivo é a estabilidade. O juiz do RAGAS é um LLM e varia de uma execução para outra, enquanto esta conferência dá sempre o mesmo resultado para o mesmo texto. Ela não julga afirmações qualitativas, só números. O valor vai para `quality.{star,hierarchical}.numeric_fidelity`, para a bateria (`fidelidade_numerica`) e para a consolidação.

### Q3 — Completude

```python
score = corr_cov * 0.4 + anom_cov * 0.4 + ctx_cov * 0.2
```

Enquanto a fidelidade pergunta "o que está no texto é verdade?", Q3 pergunta "tudo que deveria estar no texto está lá?" — cobertura de correlações, anomalias e contexto orçamentário, com pesos fixos (40/40/20). Os pesos são uma escolha de engenharia sem citação — não há fonte que prescreva essa ponderação específica. É a única métrica de qualidade textual sem custo de LLM, e serve de desempate no veredito quando a fidelidade empata ou não pôde ser medida (D20).

**Correção na cobertura de anomalias (D19).** A implementação anterior fazia uma busca *global* por palavra-chave de categoria (`"ineficiência"`, `"alto gasto"`, …): uma única ocorrência em qualquer lugar do texto marcava **todas** as anomalias daquele tipo como cobertas. Um texto que mencionava 1 de 20 anomalias pontuava igual a um que mencionava as 20. Agora cada anomalia é procurada pela sua própria identidade (ano + subfunção/indicador), o mesmo critério usado no resto do módulo. **Isso muda os valores de Q3** — números medidos antes desta correção não são comparáveis com os de agora.

---

## Veredito — como a topologia vencedora é escolhida

```python
# core/quality_metrics.py::_decide_winner
1. fidelidade (RAGAS)   — maior vence, mas SÓ se ambas foram medidas
2. completude (Q3)      — desempate
3. tempo total          — desempate final
```

Ordem lexicográfica, não média ponderada: cada nível só é consultado se o anterior empatou — e "empate" na fidelidade inclui diferenças menores que `FAITHFULNESS_TIE_THRESHOLD` (0,05), porque o juiz é um LLM e 0,01 é ruído (D26). A fidelidade vem primeiro por ser a única métrica de qualidade textual com validação publicada (Es et al., 2024); o tempo vem por último porque uma resposta errada mais rápida não é melhor. Os pesos que uma média exigiria seriam arbitrários — a ordem, ao menos, é explicável em uma frase (D20).

**Uma fidelidade não medida não vale zero.** Se a métrica falhou numa arquitetura e não na outra, tratar o `None` como 0 entregaria a vitória por falha de instrumentação do adversário, não por qualidade própria. Por isso a fidelidade só decide quando **ambas** têm score; caso contrário o critério cai para completude, e o relatório diz qual critério decidiu:

```
  → Topologia Hierárquica apresentou melhor desempenho geral
    (critério: completude (fidelidade não medida)).
```

---

## Eixo C — Resiliência

### R1 — Cobertura de resultado parcial

```python
components = {"despesas": bool(...), "indicadores": bool(...), "dados_cruzados": bool(...),
              "correlacoes": bool(...), "anomalias": bool(...), "contexto_orcamentario": bool(...),
              "texto_analise": bool(...)}
score = completed / total  # total = 7
```

Mede quantos dos 7 componentes do resultado estão presentes e não-vazios — reflete diretamente a degradação graciosa que ambas as arquiteturas implementam (falha de um agente não derruba a análise inteira, ver seções de degradação graciosa em [arquitetura-estrela.md](arquitetura-estrela.md) e [arquitetura-hierarquica.md](arquitetura-hierarquica.md)). Base de `compute_analysis_success` (Eixo E).

### R2 — Resiliência a dados corrompidos (revisão das entregas)

As falhas de R1 fazem um componente parar com uma exceção. Huang et al. (2025, ICML) testam outro modo de falha: um agente que continua respondendo, mas com erros no conteúdo. O AutoInject do artigo corrompe cerca de 20% das linhas da mensagem. A vantagem que o artigo atribui à hierarquia vem de um agente de nível superior que julga o que recebe.

No Skopos, essa capacidade existe nas duas topologias, pela mesma regra: o nó que recebe as entregas dos agentes de dados as revisa numa chamada ao LLM e contesta os suspeitos por reenvio (`agents/revisao_entregas.py`).
- Na estrela, o orquestrador revisa tudo numa chamada.
- Na hierárquica, cada supervisor de dados revisa o seu ramo.

O resumo fica em `result["revisao"]` com os campos `chamadas`, `registros_revisados`, `sinalizados`, `confirmados`, `corrigidos`, `descartados`, `nao_verificados`, `reenvios` e `falhou`.

**O teste equivalente** fica em `scripts/falhas_semanticas.py` e é acionado por `batch_eval --experimento-revisao`. Um agente de saúde por vez (o alvo) entrega cerca de 20% dos registros não nulos com o valor multiplicado por 0,1, 0,5, 2 ou 10. A corrupção acontece entre o agente e o nó que recebe a entrega, então a memória do agente fica limpa:
- no modo `dados_corrompidos`, só a primeira entrega é corrompida e o reenvio chega limpo;
- no modo `dados_corrompidos_persistente`, toda entrega é corrompida.

A semente é `(analysis_id, agente, entrega)`, então as duas topologias recebem a mesma corrupção. O gabarito permite medir, por topologia:
- `revisao_vp` e `revisao_fp`: sinais em registros corrompidos e em registros limpos;
- `contaminados_finais`: registros que chegaram ao resultado ainda corrompidos;
- `perdidos`: corrompidos descartados;
- `achados_iguais_limpos`: se as correlações e anomalias coincidem com as que os dados limpos produziriam, recalculadas com o mesmo `AgenteAnalitico`.

`scripts/analise_revisao.py` consolida as rodadas por topologia e por amplitude de escopo, com teste exato de Fisher para os achados limpos e Wilcoxon pareado por análise para sensibilidade, tokens e tempo.

**Custo:** a revisão acrescenta uma chamada ao LLM na estrela e até duas na hierárquica em toda análise. Execuções feitas antes da revisão existir não têm essas chamadas.

---

## Eixo D — Custo e Comunicação (Etapa 6, novo)

### Custo de tokens

```python
def compute_token_cost(token_usage: dict[str, int] | None) -> dict[str, Any]:
    usage = token_usage or {}
    return {"prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": ...,
            "total_tokens": ..., "call_count": ...}
```

Função quase trivial — o trabalho real está em capturar o `token_usage` corretamente por segmento, o que exigiu resolver um problema de concorrência real antes de implementar a métrica (ver caixa "Pré-requisito" abaixo).

**Pré-requisito técnico: `TokenBucket`.** Estrela e hierárquica rodam em threads concorrentes (`api/runners.py`); um contador global simples não distingue qual topologia gastou quais tokens. A solução implementada:

```python
# core/llm_client.py
_current_bucket: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(..., default=None)

class TokenBucket:
    def __enter__(self):
        self._token = _current_bucket.set(self.usage)
        return self
    def __exit__(self, *exc):
        _current_bucket.reset(self._token)
    def snapshot(self):
        return dict(self.usage)
```

Cada chamador ativa seu próprio "balde" (`with TokenBucket() as bucket: orchestrator.run(...)`) e qualquer chamada a `generate`/`generate_stream` feita dentro desse escopo — mesmo em profundidade, através de vários agentes subordinados — acumula ali. Um `ContextVar` (não thread-local puro) foi escolhido porque thread-local atribuiria tudo que roda na `MainThread` (interpretação de intenção **e** avaliação RAGAS) ao mesmo balde; `ContextVar` permite múltiplos buckets sequenciais na mesma thread. Uso real, em `api/runners.py`:

```python
token_bucket = TokenBucket()
with token_bucket:
    result = orchestrator.run(analysis_id, params, ws_queue)
active_results[analysis_id]["star_token_usage"] = token_bucket.snapshot()
```

O resultado final no payload separa 4 segmentos: `cost.star`, `cost.hierarchical`, `cost.intent_interpretation` (1x por mensagem de chat, anterior à bifurcação das topologias) e `cost.ragas` (só quando o usuário liga a avaliação RAGAS — um bucket por arquitetura, sequenciais, incluindo o consumo dos embeddings).

### Volume de comunicação

```python
def compute_communication_volume(architecture, agent_metrics, despesas_count=0,
                                 indicadores_count=0, mensagens=None) -> dict:
    # agent_invocations   — só agentes de linguagem (papel "agente")
    # ferramentas_acionadas — ferramentas (papel "ferramenta")
    # message_count, mensagens_dados, mensagens_texto, caracteres_texto
    # fonte_mensagens: "registro" (log real) ou "estimativa" (2 × agentes)
```

**Registro real das mensagens.** Cada delegação entre agentes gera mensagens no estilo FIPA-ACL (Wooldridge, 2009), com o envelope `Mensagem` de `agents/base.py`: remetente, destinatário, performativa (`request`, `inform` ou `failure`), natureza (`dados` ou `texto`), conteúdo resumido e tamanho em caracteres. A raiz de cada topologia cria o registro, e na hierárquica o coordenador o repassa aos supervisores. O registro vai para `result["mensagens"]`, e a métrica conta as mensagens dele. O proxy antigo de 2 mensagens por agente só é usado quando um resultado não traz registro (`fonte_mensagens = "estimativa"`).

O conteúdo continua estruturado quando é dado e passa a ser linguagem natural quando é produção do LLM. É o caso dos subobjetivos do coordenador, dos textos dos ramos e da resposta do supervisor analítico. Por isso a métrica separa `mensagens_dados` de `mensagens_texto` e soma `caracteres_texto`.

**Agentes e ferramentas (CoALA).** Em CoALA (Sumers et al., 2024), um agente de linguagem decide com o LLM, e as consultas e os cálculos sem decisão são ações de grounding no espaço de ações de quem os chama. `papel_de` (`core/quality_metrics.py`) classifica como ferramentas o `AgenteAnalitico`, o contexto orçamentário e as consultas sem deliberação de dimensão (SIA e COVID), conforme `FERRAMENTAS`. Chamar uma ferramenta não gera mensagem, e ela conta em `ferramentas_acionadas`, não em `agent_invocations`. As entradas de `agentMetrics` ganham o campo `papel`, e as ferramentas continuam ali porque o tempo delas entra na decomposição por fase.

> **Nota histórica:** uma versão anterior somava `lateral_hops`/`lateral_summaries` fixos para a hierárquica — a comunicação lateral entre supervisores (Etapa 5, ver nota abaixo) foi removida por decisão explícita de tornar a hierárquica uma árvore estrita, e esses dois campos deixaram de existir no payload.

---

## Eixo E — Outcome agregado (Etapa 6, novo, opcional)

```python
def compute_analysis_success(result, wall_clock_ms=0, time_budget_ms=DEFAULT_TIME_BUDGET_MS):
    r1 = compute_partial_result_coverage(result)
    r1_complete = r1["completed"] == r1["total"]

    self_check = result.get("self_check")
    if self_check and self_check.get("verificado"):
        claims_nao_suportadas = sum(1 for c in self_check.get("claims", []) if not c.get("suportado", True))
        self_check_ok = self_check.get("revisado", False) or claims_nao_suportadas == 0
    else:
        self_check_ok = True  # self-check não rodou (opcional) — não penaliza

    within_budget = wall_clock_ms <= time_budget_ms if wall_clock_ms > 0 else True
    success = r1_complete and self_check_ok and within_budget
    return {"success": success, "r1_complete": ..., "self_check_ok": ..., "within_time_budget": ...}
```

Métrica binária composta: **sucesso = R1 completo E nenhuma claim não-suportada remanescente no self-check (Etapa 4) E dentro do orçamento de tempo**. `DEFAULT_TIME_BUDGET_MS = 60_000.0` não é prescrito por nenhuma fonte — é um parâmetro explícito, sobrescrevível pelo caller, não uma constante escondida (mesma lógica de "sem limiares mágicos" já aplicada a E1/E2, D12). Inspiração conceitual em métricas de sucesso estruturadas em marcos (D11), fórmula própria.

---

## Métricas process-wide (fora do payload por-análise)

### Taxa de rejeição do guardrail

```python
# core/guardrail_stats.py — contador global, protegido por lock, não reseta entre análises
def compute_guardrail_rejection_rate() -> dict[str, float | int]:
    with _lock:
        total, rejected = _counts["total"], _counts["rejected_out_of_scope"]
    rate = rejected / total if total > 0 else 0.0
    return {"total_messages": total, "rejected": rejected, "rejection_rate": round(rate, 4)}
```

`chat_websocket.py` registra cada decisão do guardrail da Etapa 1 (dentro/fora de escopo) via `record_guardrail_decision()`, excluindo mensagens vazias e falhas técnicas do LLM (que não são decisões de escopo de verdade). Pensada para calibrar o prompt de classificação ao longo do tempo — o risco de falso positivo/negativo do guardrail estava registrado como não resolvido na Etapa 1; esta métrica é a instrumentação que permite monitorá-lo, não a solução em si.

### Taxa de acerto do cache de planejamento de consulta

```python
# agents/domain/query_planning.py
def compute_cache_hit_rate() -> dict[str, Any]:
    total = sum(_stats.values())
    llm_calls = _stats["llm"] + _stats["llm_failed_fallback"]
    rate = (total - llm_calls) / total if total > 0 else 1.0
    return {"total": total, "fast_path": ..., "cache": ..., "llm": ...,
            "llm_failed_fallback": ..., "cache_or_fastpath_rate": round(rate, 4)}
```

`plan_query()` (Etapa 2) já retorna a origem de cada plano (`"fast_path" | "cache" | "llm" | "llm_failed_fallback"`); esta métrica só acumula essas origens num contador. Evidencia diretamente o trade-off da Etapa 2: enquanto a base tiver mapeamento trivial e/ou a flag `USE_LLM_QUERY_PLANNING` estiver desligada, a taxa fica em 100% — nenhuma chamada LLM acontece, exatamente o comportamento pretendido hoje.

---

## Harness de escalabilidade

`backend/tests/test_scalability_benchmark.py` roda as duas topologias com dados sintéticos em dois volumes (N=3 e N=10 anos, todos os 4 subfunções/tipos de indicador ativos), `use_llm=False` (determinístico, sem custo real de API — a variação do custo de tokens com N exigiria chamadas reais, fora do escopo de um harness automatizado de CI). Produz os 2 pontos de comparação exigidos pelo critério de aceite da Etapa 6 e valida invariantes estruturais em vez de comparar contra um limiar fixo (mesma filosofia de D12):

- Q1 continua consistente em qualquer N (nenhuma divergência introduzida pelo crescimento da base).
- `message_count` cresce só com `agent_invocations` (× 2) em qualquer N, em ambas as topologias — a árvore estrita da hierárquica não introduz overhead de comunicação que escale com o volume de dados.
- Todos os despesas/indicadores sintéticos são processados sem perda silenciosa, em ambas as escalas.

---

## Justificativa Acadêmica por Métrica

Tabela no mesmo formato da Seção 3 do `PLANO_REFATORACAO.md`, filtrada às decisões que dizem respeito diretamente às métricas (os IDs D1–D5, D13–D15 dizem respeito a decisões de arquitetura de agentes, não de medição, e por isso não estão repetidos aqui — ver o plano para o conjunto completo).

| ID | Métrica/Decisão | Fonte | Trecho/resumo do argumento |
|---|---|---|---|
| D6 | Custo de tokens por topologia (`compute_token_cost`) | Achados de busca sobre frameworks de avaliação 2025 (REALM-Bench, CLEAR) e o catálogo AGENT 2026 | "New 2025 frameworks like REALM-Bench and CLEAR prioritize real-world complexity, adding cost, latency, efficiency, assurance, and reliability metrics to production evaluation"; AGENT 2026 classifica custo na categoria "Framework". **Ressalva:** acesso só a resumos de busca, não ao texto completo. O mecanismo de captura (`TokenBucket`/`ContextVar`) é decisão de engenharia própria — a fonte motiva medir custo, não como implementar a contabilização sob concorrência. |
| D7 | Volume de comunicação (`compute_communication_volume`) | Li et al. (2024), Seção 3.4.1 ("Message delivery"); complementar: *"Beyond Self-Talk: A Communication-Centric Survey of LLM-Based Multi-Agent Systems"* (arXiv:2502.14321) | "message delivery must account for supplementary overhead, including transmission efficiency, bandwidth, and the timeliness of message delivery"; "current leaderboards remain agent-centric and rarely capture system-level properties, including coordination efficiency, communication bandwidth and latency". **Ressalva:** o proxy usado (contagem de invocações + hops laterais fixos, não um log de mensagens brutas) é decisão de engenharia própria. |
| D8 | Adoção da **biblioteca** RAGAS (`core/ragas_metrics.py`) no lugar de toda métrica caseira de fidelidade | Es, S., James, J., Espinosa-Anke, L., Schockaert, S. (2024). *"RAGAS: Automated Evaluation of Retrieval Augmented Generation"*. Proceedings of the 18th Conference of the EACL: System Demonstrations, pp. 150–158. Biblioteca: `ragas` 0.4.x | O paper define três aspectos reference-free: faithfulness (`F = \|V\|/\|S\|`, afirmações suportadas sobre total de afirmações), answer relevance (média das similaridades de cosseno entre `n` perguntas geradas a partir da resposta e a pergunta original) e context relevance. A versão anterior desta linha citava a *metodologia* e implementava uma aproximação própria; agora a implementação **é** a da referência. **Ressalva:** o pipeline não é um RAG clássico — o mapeamento da tripla `(user_input, response, retrieved_contexts)` para achados determinísticos está documentado na seção do Eixo B e é decisão de engenharia própria. |
| D17 | Remoção do LLM-as-judge de nota 1–5 (`compute_faithfulness_llm`) | Es et al. (2024), Tabela 4 e Seção 5 | O paper mede exatamente essa abordagem como baseline ("we ask ChatGPT to assign a score between 0 and 10 for the three quality dimensions") e reporta a concordância com anotadores humanos em faithfulness: **RAGAS 0.95, GPT Score 0.72, GPT Ranking 0.54**. Manter a nota holística ao lado da métrica do RAGAS seria manter, ao lado do instrumento validado, o instrumento que a mesma fonte demonstra ser pior — sem nenhuma pergunta que só ele responda. Citação direta, não decisão de engenharia. |
| D18 | Juiz fixo no código (`gpt-5-mini`) e diferente do modelo das análises (`gpt-5.6-luna`) | Es et al. (2024), Seção 5.1 ("Reproducibility"); Zheng et al. (2023) e Panickssery et al. (2024) sobre autopreferência | "Obtaining reproducible results with (large) language models is challenging... several runs of the same experiment under the same configuration might yield different results". A fonte motiva controlar as fontes de variação da medida; fixar o juiz no código elimina essa variação entre execuções. Ser um modelo diferente do gerador evita que o juiz favoreça o próprio texto. **Ressalva:** a escolha do `gpt-5-mini` é decisão de engenharia por custo-benefício. |
| D19 | Correção da cobertura de anomalias em `compute_completeness` (Q3) | — | **Decisão de engenharia, sem embasamento acadêmico direto — correção de bug de medição.** A busca global por palavra-chave de categoria marcava todas as anomalias de um tipo como cobertas a partir de uma única ocorrência no texto, tornando o score insensível à quantidade de achados efetivamente mencionados. Consequência a registrar: valores de Q3 anteriores à correção não são comparáveis com os atuais. |
| D9 | Eixo de escalabilidade (harness de N variável) | Li et al. (2024), Seção 5.1 ("Scaling Up the Multi-Agent System") | "Scaling up multi-agent systems involves increasing the number of agents... introduces challenges related to computational resources, communication efficiency, and system coordination... static adjustment and dynamic scaling methods are widely applied." **Ressalva:** a fonte discute a necessidade de medir escalabilidade em termos gerais, não prescreve a métrica exata nem o desenho do benchmark sintético usado aqui. |
| D10 | Taxonomia E/Q/R vs. reestruturação Outcome/Process/Product/Framework (não adotada) | *"A Catalogue of Evaluation Metrics for LLM-Based Multi-Agent Frameworks in Software Engineering"*, AGENT 2026 workshop @ ICSE 2026 | Propõe 37 métricas nessas 4 categorias contra "frameworks often relying on self-defined or inconsistent metrics, hindering reproducibility". Registrada como referência; não adotada nesta versão do sistema (custo/risco de quebrar a API atual do frontend maior que o ganho para o prazo do TCC). |
| D11 | Sucesso agregado da análise (`compute_analysis_success`) | Zhu et al., *"MultiAgentBench: Evaluating the Collaboration and Competition of LLM agents"*, ACL 2025, arXiv:2503.01935 | "measures not only task completion but also the quality of collaboration and competition using novel, milestone-based key performance indicators" — inspiração conceitual (sucesso estruturado, não binário simples). **Ressalva:** a fórmula (R1 + self-check + orçamento de tempo) é decisão de engenharia própria, não replica o benchmark original (domínio de agentes de pesquisa/coding, diferente deste projeto). |
| D12 | Ausência de limiares fixos em E1/E2/outcome (comparação sempre relativa entre topologias, nunca contra um corte absoluto) | — | **Decisão de engenharia, sem embasamento acadêmico direto.** Nenhuma fonte consultada define limiar absoluto universal para overhead de coordenação ou orçamento de tempo "aceitável"; os benchmarks citados (MultiAgentBench, AGENT 2026) comparam configurações entre si. A ausência de fonte é, em si, o argumento para não fixar cortes "porque parecem razoáveis" — por isso `time_budget_ms` em `compute_analysis_success` é parâmetro explícito, não constante escondida. |
| D16 | Remoção do rate limiting próprio em `core/llm_client.py`, mantendo só retry reativo a 429 real | — | **Decisão de engenharia, sem embasamento acadêmico direto — pedido explícito do usuário.** Motivada por evidência empírica direta (log de execução real mostrando uma chamada presa 177s atrás do lock global de outra topologia em streaming), relevante para este documento porque afeta diretamente a precisão da métrica de custo de tokens sob concorrência real. |
| D20 | Critério lexicográfico do veredito (fidelidade > completude > eficiência) e a regra de não tratar fidelidade ausente como zero | — | **Decisão de engenharia, sem embasamento acadêmico direto.** Nenhuma fonte consultada prescreve como combinar métricas de eixos diferentes num veredito único. A ordem lexicográfica foi escolhida sobre a média ponderada justamente por não exigir pesos arbitrários — a prioridade da fidelidade se apoia em ela ser a única métrica de qualidade textual com validação publicada (D8), não numa fórmula. A regra do `None` é correção de viés: sem ela, a arquitetura cuja medição falhou perderia por falha de instrumento. |
| D21 | ~~Teto de contextos (`RAGAS_MAX_CONTEXTS`) aplicado só à precisão de contexto~~ — **superada por D25**, que removeu a métrica que exigia o teto | — | **Decisão de engenharia, sem embasamento acadêmico direto — correção de viés de medição.** O teto existe pelo custo (a precisão de contexto gasta 1 chamada LLM por achado); a fidelidade gasta 2 chamadas independente do volume. Truncar o contexto dela não economizava nada e fazia as afirmações sobre os achados removidos serem julgadas "não suportadas", deprimindo o score artificialmente. **Ressalva:** medições de fidelidade anteriores a esta correção estão subestimadas e não são comparáveis com as atuais. |
| D22 | Esforço de raciocínio limitado (`OPENAI_REASONING_EFFORT=low`) e teto de saída próprio para modelos de raciocínio | — | **Decisão de engenharia, sem embasamento acadêmico direto.** Nesses modelos `max_completion_tokens` cobre raciocínio + resposta; com o default `medium` da API e teto apertado, prompts grandes retornavam vazio, e a fidelidade falhava com "LLM indisponível". Extração e julgamento de JSON estruturado não se beneficiam de chain-of-thought longo. |
| D23 | Contexto do juiz espelhando integralmente o do gerador, inclusive o enquadramento injetado pelo prompt (período, pandemia, traduções, lacunas) | — | **Decisão de engenharia, sem embasamento acadêmico direto — correção de viés de medição.** O paper pressupõe que o contexto avaliado é o mesmo que alimentou a geração; aqui os dois divergiam, e a métrica media a diferença. A inclusão do enquadramento injetado decorre da mesma premissa: o prompt manda o modelo repetir essas afirmações, então elas fazem parte do que sustenta a geração. **Ressalva:** é discutível se instruções de sistema devem contar como "contexto recuperado"; a alternativa (excluí-las) reprovaria o texto por obedecer à instrução, o que seria pior. |
| D24 | Uso de `text-embedding-3-large` no `answer_relevancy`, divergindo do `text-embedding-ada-002` do paper | Es et al. (2024), Seção 3; OpenAI, *New embedding models and API updates*; Stekel (MTEB-BR, arXiv:2607.04581) | O paper especifica o *procedimento* ("obtemos embeddings para todas as perguntas... calculamos a similaridade de cosseno"), não o modelo — a definição de `AR` não depende de qual encoder produz os vetores; o `ada-002` foi simplesmente o disponível em 2023. Aqui os textos comparados são todos em português, e o `ada-002` é o pior dos três nesse eixo (31,4% no MIRACL, contra 54,9% do `3-large`), com custo 5× maior que o `3-small`. Manter o modelo do paper degradaria a medida sem ganho de comparabilidade. **Ressalva:** os scores de `answer_relevancy` não são numericamente comparáveis com os do paper nem com implementações que usem `ada-002`. |
| D25 | Terceiro pilar medido por `ContextRelevance` em vez de `ContextPrecisionWithoutReference` | Es et al. (2024), Seção 3 | A definição do paper é uma **proporção** ("número de sentenças extraídas / total de sentenças no contexto"), não uma *average precision* ranqueada. A precisão de contexto da biblioteca calcula AP, que pressupõe um resultado de retrieval **ordenado por relevância** e pontua conforme a posição dos chunks úteis. Aqui não há retriever nem ranking: `build_contexts` emite numa ordem estrutural fixa, então o score virava função dessa ordem arbitrária. Medido com os 77 chunks de uma execução real, variando **só** a posição dos mesmos 6 chunks úteis: AP **1,00** no início, **0,09** dispersos, **0,05** no fim. `ContextRelevance` concatena o contexto e o avalia com dois juízes (escala 0/1/2), sem depender de ordem. **Consequência:** torna obsoleto o teto de contextos de D21, e as medições de precisão de contexto anteriores (0,09 / 0,23) não são comparáveis com as atuais. |
| D26 | Limiar de 0,05 no desempate por fidelidade (`FAITHFULNESS_TIE_THRESHOLD`) | — | **Decisão de engenharia, sem embasamento acadêmico direto.** Nenhuma fonte consultada prescreve limiar de indiferença para comparação entre sistemas. O argumento é a natureza do instrumento: o juiz é um LLM e reavaliar o mesmo texto produz variação, então uma diferença de 0,01 — como a observada numa execução real (0,79 × 0,80) — distingue ruído, não arquitetura. Abaixo do limiar o veredito cai para a completude e o relatório diz explicitamente "fidelidade tecnicamente empatada", para não confundir com o caso em que a fidelidade não pôde ser medida. |
| — | Q1 (consistência determinística), Q3 (completude), R1 (cobertura parcial), pesos de Q3 (40/40/20) | — | **Decisões de engenharia pré-existentes, sem embasamento acadêmico direto** — não fizeram parte da Etapa 6 nem de nenhuma decisão citada no plano; documentadas aqui por completude do eixo de métricas, não por terem justificativa acadêmica a reportar. |

---

## Ressalvas conhecidas da avaliação RAGAS

Registradas explicitamente porque afetam a leitura dos números:

1. **O pipeline não é um RAG clássico.** Não há retriever nem corpus; `retrieved_contexts` são os achados determinísticos que os agentes entregaram ao sintetizador. O mapeamento é defensável (ver Eixo B) mas é decisão de engenharia, não algo prescrito pela fonte.
2. **A relevância do contexto não discrimina as topologias, e a diferença entre elas é ruído do juiz.** Ela julga contexto × pergunta sem ver a resposta, e o Q1 verifica que as duas arquiteturas produzem correlações e anomalias idênticas. Logo `build_contexts` entrega o mesmo contexto às duas, e qualquer diferença de score vem só da variação do juiz, que é um LLM e não responde igual a duas chamadas com a mesma entrada. Isso foi observado numa execução real: com achados idênticos, a estrela recebeu 0,75 e a hierárquica 0,50 (juiz `gpt-5-mini`, escala 0/1/2 de dois julgamentos). Uma versão anterior deste texto dizia que os scores eram "iguais por construção", o que só valeria com um juiz determinístico. A métrica descreve o conjunto de achados que o pipeline produz, que é o mesmo nas duas topologias, e não deve ser usada para compará-las. A diferença entre elas serve, no máximo, como estimativa do ruído do juiz. Uma diferença real só aparece sob falha parcial, quando uma arquitetura entrega menos achados que a outra.
3. **O juiz é um LLM.** O próprio paper registra a limitação: "It relies heavily on the performance of the LLMs used for evaluating the different components" (Seção 8). Os scores são estimativas, não medidas exatas — diferente de Q1, que é uma igualdade numérica.
4. **Q3 e a fidelidade mudaram de valor mais de uma vez.** As correções D19 (cobertura de anomalias), D21 (truncagem) e D23 (contexto incompleto) tornam os números atuais incomparáveis com execuções anteriores. A fidelidade, em particular, esteve subestimada duas vezes: uma medição de 0.28 (estrela) / 0.10 (hierárquica) obtida antes de D23 refletia sobretudo a lacuna entre o contexto do gerador e o do juiz, não infidelidade do texto.
5. **O juiz depende de acesso a embeddings.** Projetos da OpenAI restringem modelos individualmente e devolvem 403 `model_not_found`; a cadeia de fallback e o contorno do cabeçalho cobrem os casos comuns, mas se nenhum modelo estiver acessível, `answer_relevancy` fica indisponível e o payload diz isso — o pilar de relevância da resposta não é coberto naquela execução. Diagnóstico: `python -m scripts.check_embeddings`.
6. **O custo dos embeddings é contabilizado, mas por outra rota.** O endpoint de embeddings não passa por `generate()`; `SkoposRagasEmbeddings` registra o consumo à mão via `llm_client.record_token_usage`, para que `cost.ragas` não subestime o custo real da avaliação.

---

## Limitações estatísticas das correlações (conhecidas, não corrigidas)

Registradas aqui porque afetam a leitura de **todos** os resultados do eixo analítico — inclusive os que alimentam as métricas RAGAS, já que cada correlação vira um chunk de contexto. **Não foram corrigidas nesta etapa**, por decisão de escopo: o foco foi fazer a avaliação RAGAS funcionar e contextualizar as métricas. `agents/analytical/analitico.py` permanece inalterado.

**1. Correlações com n=2 não carregam informação.** O guard atual é `n < 2` ([`analitico.py`](../../backend/agents/analytical/analitico.py)), então pares com apenas dois pontos entram no cálculo. Com n=2 o coeficiente de Spearman é **sempre exatamente ±1,0**, por construção: o valor só indica se o segundo ponto é maior ou menor que o primeiro. Numa execução real de 4 anos, 9 das 29 correlações tinham n=2 — todas classificadas como "alta", todas alimentando o texto da análise e as métricas.

**2. Nenhuma correlação atinge significância estatística.** Com **n=4**, o menor p-valor bilateral possível para Spearman é **0,083** (2/4! — a probabilidade da permutação perfeita, nos dois sentidos). Ou seja: com uma série de 4 anos, *nenhuma* correlação pode atingir p<0,05, nem com ρ=±1,0. Significância só se torna alcançável a partir de n=5 (2/5! ≈ 0,017).

**3. A classificação não considera nada disso.** [`_classify`](../../backend/agents/analytical/analitico.py) rotula alta/média/baixa só por limiares de |ρ| (0,7 e 0,4), sem teste de significância e sem n mínimo. Uma correlação ρ=−1,0 com n=2 e uma ρ=−0,8 com n=4 recebem o mesmo rótulo "alta" que uma correlação robusta receberia.

**Impacto na leitura dos resultados.** Os coeficientes devem ser lidos como **descritivos e exploratórios**, não inferenciais: descrevem o comportamento conjunto observado naquela janela de anos, e não sustentam afirmação sobre associação populacional. A comparação estrela × hierárquica não é afetada — as duas topologias recebem exatamente os mesmos números (é o que Q1 verifica) —, mas qualquer conclusão substantiva sobre gasto e resultado em saúde extraída desses coeficientes precisa carregar essa ressalva.

Correções possíveis, caso o escopo se abra: guard de n mínimo (n≥3 elimina o caso degenerado), cálculo e exposição do p-valor (o `scipy.stats.spearmanr` já o retorna — hoje é descartado em `_safe_correlation`), e uma classificação que incorpore n e significância.

---

## Payload final — onde cada métrica aparece

```
GET /api/analysis/{id}/quality  (e evento WebSocket "quality_metrics")
├── efficiency.{star,hierarchical}.{coordination_overhead, latency_breakdown}   E1, E2
├── quality
│   ├── deterministic_consistency                                              Q1
│   └── {star,hierarchical}
│       ├── completeness                                                       Q3
│       ├── numeric_fidelity.{score, numeros_no_texto, verificados, nao_verificados}   Q4
│       └── ragas                             (eventos "ragas"/"ragas_done")
│           ├── metrics.faithfulness.score
│           ├── metrics.answer_relevancy.score
│           ├── metrics.context_relevance.score
│           ├── judge.{provider, model, embedding_model}
│           ├── judge.embedding_model_used   (qual modelo a cadeia usou)
│           ├── sample.{n_contexts_total, response_chars}
│           └── available / unavailable_reason / errors[]
├── resilience.{star,hierarchical}                                             R1
├── cost.{star,hierarchical,intent_interpretation,ragas}                        D (tokens)
├── communication.{star,hierarchical}                                          D (mensagens)
└── outcome.{star,hierarchical}                                                E (sucesso)

GET /api/metrics/guardrail                                                     process-wide
GET /api/metrics/query-planning-cache                                          process-wide
```

Todos os scores do bloco `ragas` estão em `[0, 1]` ou são `null` (métrica não calculável) — nunca `NaN`, nunca uma escala diferente das demais.
