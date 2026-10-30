# Bateria de testes — o que ela avalia

Documento de revisão de `backend/scripts/batch_eval.py`, feito antes da nova campanha. Os resultados das execuções anteriores (10 execuções, 60 arquivos) foram movidos para `execucoes_anteriores/`.

## 1. Como uma análise é executada

Cada cenário passa pelo mesmo caminho do chat da interface:

1. **Recusa por tamanho.** Mensagens acima de `MAX_MESSAGE_LENGTH` são recusadas, como no chat. Nenhum cenário atual chega a esse limite.
2. **Interpretação.** `AgenteInterpretacaoIntencao.parse()` roda com **ano de referência fixo em 2025** e **sem memória episódica**: não consulta análises anteriores gravadas no Neo4j, para a campanha ser reprodutível. A interface consulta esse histórico, e essa é a única diferença deliberada em relação a ela.
3. **Disparo.** Se a pergunta é aceita, `run_chat_analysis()` dispara **estrela e hierárquica em threads concorrentes**, com os runners reais.
4. **Leitura da fila.** A bateria lê a fila de eventos como o WebSocket faz (`metric`, `error`, `done`). O tempo limite é de 900 s por análise.
5. **Métricas.** `compute_all_quality_metrics()` calcula as métricas determinísticas, e depois o **RAGAS** roda as duas topologias em paralelo, com `gpt-5-mini` como juiz (fixo, diferente do `gpt-5.6-luna` que escreve os textos) e `text-embedding-3-large` nos embeddings.

Nada é gravado no Neo4j, porque `_SemGravacao` impede gravar o nó `Analise`. A verificação pós-síntese (self-check) fica desligada; `--use-self-check` liga.

Cada rodada gera três arquivos:

| Arquivo | Conteúdo |
|---|---|
| `<saida>.xlsx` | Relatório com tabelas e gráficos |
| `<saida>.jsonl` | Uma linha por cenário e topologia |
| `<saida>.payloads.jsonl` | Payload completo por análise: qualidade, RAGAS, achados, revisão, **texto final**, **plano**, **redação** e **textos parciais** de cada topologia |

## 2. Os 51 cenários

São 51 perguntas, nenhuma com falha injetada. As colunas "Período", "Indicadores" e "Sistemas" mostram o que a interpretação resolveu na **rodada 5 da campanha antiga**, só como referência: a interpretação roda de novo a cada rodada e pode variar.

Os agentes de saúde consultam só os subtipos pedidos na pergunta. Numa pergunta sobre dengue, o SINAN traz só dengue, e não os 9 agravos. Nas rodadas antigas, cada agente acionado trazia o domínio inteiro, por isso as contagens de achados delas não são comparáveis com as novas.

A coluna "Sistemas" é o que decide a redação na hierárquica:
- com **2 ou mais sistemas**, a hierárquica redige por ramo e depois compõe. São cerca de 18 cenários.
- com **1 sistema**, as duas topologias escrevem do mesmo jeito. São cerca de 33 cenários, e eles funcionam como controle.

| Cenário | Categoria | Pergunta | Período resolvido | Indicadores resolvidos | Sistemas |
|---|---|---|---|---|---|
| `obvio_dengue_periodo` | obvio_explicito | Quantos casos de dengue foram registrados em Sorocaba entre 2020 e 2023, e como isso se relacionou com o orçamento de vigilância epidemiológica no mesmo período? | 2020–2023 | dengue, estabelecimentos_vigilancia_epidemiologica | 2 |
| `obvio_vacinacao_periodo` | obvio_explicito | Compare a cobertura vacinal em Sorocaba entre 2018 e 2022 com o investimento em atenção básica. | 2018–2022 | vacinacao | 1 |
| `obvio_internacoes_periodo` | obvio_explicito | Como evoluiu o número de internações hospitalares entre 2019 e 2021, e isso teve relação com o orçamento de assistência hospitalar? | 2019–2021 | internacoes | 1 |
| `obvio_mortalidade_periodo` | obvio_explicito | Qual foi a variação da mortalidade em Sorocaba entre 2016 e 2020? | 2016–2020 | mortalidade | 1 |
| `obvio_tuberculose_periodo` | obvio_explicito | Como se comportaram os casos de tuberculose entre 2016 e 2022 e o gasto com vigilância epidemiológica? | 2016–2022 | tuberculose | 1 |
| `obvio_covid_periodo` | obvio_explicito | Quantos casos e óbitos de covid ocorreram entre 2020 e 2022 e como o orçamento de saúde reagiu? | 2020–2022 | covid | 1 |
| `obvio_nascidos_vivos_periodo` | obvio_explicito | Os nascidos vivos em Sorocaba aumentaram ou diminuíram entre 2016 e 2024? Compare com o orçamento da atenção básica. | 2016–2024 | nascidos_vivos | 1 |
| `obvio_hepatites_periodo` | obvio_explicito | Analise os casos de hepatites virais entre 2016 e 2023 frente ao orçamento de vigilância epidemiológica. | 2016–2023 | hepatites_virais | 1 |
| `ambiguo_sistema_saude_geral` | ambiguo_sistema | Como está a situação da saúde pública em Sorocaba entre 2020 e 2023? | 2020–2023 | todos (27) | 8 |
| `ambiguo_sistema_mudou` | ambiguo_sistema | O que mudou na saúde da cidade nos últimos 5 anos? | params_invalidos | — | 0 |
| `ambiguo_sistema_melhora` | ambiguo_sistema | Dá pra ver alguma melhora nos indicadores de saúde entre 2018 e 2022? | 2018–2022 | todos (27) | 8 |
| `ambiguo_sistema_pandemia` | ambiguo_sistema | Como foi o desempenho da saúde pública de Sorocaba durante a pandemia, entre 2020 e 2021? | 2020–2021 | covid | 1 |
| `ambiguo_sistema_mulher_bebe` | ambiguo_sistema | Como evoluiu a saúde da mulher e do recém-nascido em Sorocaba entre 2018 e 2024? | 2018–2024 | mortalidade, nascidos_vivos | 2 |
| `ambiguo_sistema_infecciosas` | ambiguo_sistema | Quais doenças infecciosas pesaram mais na cidade entre 2019 e 2023? | 2019–2023 | todos (27) | 8 |
| `ambiguo_periodo_vacinacao` | ambiguo_periodo | A cobertura vacinal em Sorocaba tem melhorado? | 2016–2025 | vacinacao | 1 |
| `ambiguo_periodo_dengue` | ambiguo_periodo | Os casos de dengue estão diminuindo? | 2016–2025 | dengue | 1 |
| `ambiguo_periodo_mortalidade` | ambiguo_periodo | Como está a mortalidade na cidade? | 2016–2025 | mortalidade | 1 |
| `ambiguo_periodo_leitos` | ambiguo_periodo | O número de leitos hospitalares tem acompanhado o crescimento da cidade? | 2016–2025 | leitos | 1 |
| `ambiguo_periodo_mortalidade_internacoes` | ambiguo_periodo | A mortalidade e as internações caíram ao longo do tempo? | 2016–2025 | internacoes, mortalidade | 2 |
| `ambiguo_total_investimento` | ambiguo_total | O dinheiro público está sendo bem investido na saúde? | 2016–2025 | todos (27) | 8 |
| `ambiguo_total_melhora` | ambiguo_total | A saúde em Sorocaba está melhorando? | 2016–2025 | todos (27) | 8 |
| `ambiguo_total_vale_a_pena` | ambiguo_total | Vale a pena o investimento atual em saúde pública? | 2016–2025 | todos (27) | 8 |
| `data_invertida` | data_invalida | Compare os casos de dengue entre 2023 e 2019, nessa ordem exata (do mais recente para o mais antigo). | 2019–2023 | dengue | 1 |
| `data_curta_dengue` | data_estreita | Como foi a dengue entre 2021 e 2022? | 2021–2022 | dengue | 1 |
| `data_curta_vacinacao` | data_estreita | O que aconteceu com a cobertura vacinal entre 2020 e 2021? | 2020–2021 | vacinacao | 1 |
| `data_curta_covid_pico` | data_estreita | Como a covid evoluiu entre 2021 e 2022, no auge das ondas? | 2021–2022 | covid | 1 |
| `multi_sistema_tres` | multi_sistema_explicito | Compare dengue, cobertura vacinal e leitos hospitalares em Sorocaba entre 2019 e 2023. | 2019–2023 | dengue, vacinacao, leitos | 3 |
| `multi_sistema_internacao_mortalidade_profissionais` | multi_sistema_explicito | Quero entender a relação entre internações, mortalidade e o número de profissionais de saúde entre 2017 e 2022. | 2017–2022 | internacoes, mortalidade, profissionais | 3 |
| `multi_sistema_doencas_negligenciadas` | multi_sistema_explicito | Analise conjuntamente tuberculose, hanseníase e hepatites virais entre 2016 e 2023. | 2016–2023 | hepatites_virais, tuberculose, hanseniase | 1 |
| `multi_sistema_arboviroses` | multi_sistema_explicito | Compare dengue, chikungunya e covid entre 2020 e 2024. | 2020–2024 | dengue, covid, chikungunya | 2 |
| `multi_sistema_capacidade` | multi_sistema_explicito | Relacione profissionais, equipes de saúde e leitos entre 2018 e 2024. | 2018–2024 | leitos, profissionais, equipes_saude | 1 |
| `sistema_raro_hanseniase` | sistema_raro | Como evoluíram os casos de hanseníase em Sorocaba entre 2016 e 2023? | 2016–2023 | hanseniase | 1 |
| `sistema_raro_estabelecimentos` | sistema_raro | Quantos estabelecimentos de saúde por tipo existem em Sorocaba e como isso mudou frente ao orçamento entre 2018 e 2023? | 2018–2023 | estabelecimentos_por_tipo | 1 |
| `sistema_raro_equipes` | sistema_raro | Como variou o número de equipes de saúde da família entre 2019 e 2023? | 2019–2023 | equipes_saude | 1 |
| `sistema_raro_sifilis_congenita` | sistema_raro | Os casos de sífilis congênita aumentaram ou diminuíram entre 2018 e 2022? | 2018–2022 | sifilis_congenita | 1 |
| `sistema_raro_coqueluche` | sistema_raro | Houve surtos de coqueluche em Sorocaba entre 2016 e 2024? | 2016–2024 | coqueluche | 1 |
| `sistema_raro_equipamentos_ocupacoes` | sistema_raro | Como evoluíram os equipamentos de saúde e as ocupações de profissionais entre 2019 e 2025? | 2019–2025 | ocupacoes, equipamentos | 1 |
| `complexa_pre_pos_pandemia` | comparacao_complexa | Como os indicadores de internação mudaram do período pré-pandemia (2018-2019) para o pós-pandemia (2022-2023)? | 2018–2023 | internacoes | 1 |
| `complexa_correlacao_orcamento_dengue` | comparacao_complexa | Existe correlação entre o aumento do orçamento de vigilância epidemiológica e a queda nos casos de dengue entre 2017 e 2023? | 2017–2023 | dengue | 1 |
| `complexa_variacao_subfuncoes` | comparacao_complexa | Quais subfunções orçamentárias tiveram maior variação percentual entre 2016 e 2024, e isso se refletiu em algum indicador de saúde? | 2016–2024 | todos (27) | 8 |
| `complexa_investimento_vs_atencao_basica` | comparacao_complexa | O investimento em atenção básica cresceu mais rápido que os indicadores de produção ambulatorial entre 2016 e 2023? | 2016–2023 | producao_ambulatorial | 1 |
| `complexa_arbovirose_vs_vigilancia` | comparacao_complexa | O gasto com vigilância epidemiológica acompanhou a evolução de dengue e chikungunya entre 2018 e 2024? | 2018–2024 | dengue, chikungunya | 1 |
| `complexa_orcamento_vs_mortalidade` | comparacao_complexa | Em que anos o orçamento de saúde cresceu mais e o que aconteceu com a mortalidade e as internações entre 2016 e 2025? | 2016–2025 | internacoes, mortalidade | 2 |
| `pouco_objetiva_sei_la` | pouco_objetiva | Sei lá, queria entender melhor como anda a saúde por aqui, será que dá pra dizer alguma coisa? | 2016–2025 | todos (27) | 8 |
| `pouco_objetiva_tipo` | pouco_objetiva | Tipo, queria saber se as coisas melhoraram ou pioraram na saúde, não sei bem o período. | 2016–2025 | todos (27) | 8 |
| `pouco_objetiva_preocupado` | pouco_objetiva | Estou preocupado com a saúde pública da minha cidade, pode me contar alguma coisa relevante? | 2016–2025 | todos (27) | 8 |
| `pouco_objetiva_interessante` | pouco_objetiva | Me conta algo interessante sobre a saúde de Sorocaba entre 2019 e 2023. | 2019–2023 | todos (27) | 8 |
| `muito_objetiva_dengue` | muito_objetiva | Casos de dengue, 2021-2022. | 2021–2022 | dengue | 1 |
| `muito_objetiva_vacinacao_orcamento` | muito_objetiva | Cobertura vacinal 2020-2021 vs orçamento. | 2020–2021 | vacinacao | 1 |
| `muito_objetiva_leitos` | muito_objetiva | Leitos hospitalares 2022-2023. | 2022–2023 | leitos | 1 |
| `muito_objetiva_tuberculose` | muito_objetiva | Tuberculose 2018-2023. | 2018–2023 | tuberculose | 1 |

Contagem por categoria:

| Categoria | Qtd. | Observação |
|---|---|---|
| `obvio_explicito` | 8 | Tema e período claros |
| `ambiguo_sistema` | 6 | Período claro, tema vago |
| `ambiguo_periodo` | 5 | Tema claro, período vago |
| `ambiguo_total` | 3 | Nem tema nem período |
| `data_invalida` | 1 | Período invertido, que a interpretação normaliza |
| `data_estreita` | 3 | Período de 2 anos |
| `multi_sistema_explicito` | 5 | Vários indicadores citados |
| `sistema_raro` | 6 | Indicadores pouco usados |
| `comparacao_complexa` | 6 | Comparações e correlações |
| `pouco_objetiva` | 4 | Vagas ou coloquiais |
| `muito_objetiva` | 4 | Telegráficas |

## 3. Falha injetada (resiliência)

A resiliência é medida só pela falha semântica, que roda numa execução própria. As antigas falhas por queda (agentes de saúde, supervisor analítico ou LLM lançando erro) foram retiradas da bateria. Elas mediam degradação graciosa e não o que as referências descrevem, e a do supervisor analítico nem tinha equivalente na estrela.

### Falha semântica, no padrão de Huang et al. (2025)

**Arquivo:** `backend/scripts/falhas_semanticas.py`. Foi este o sistema refeito para seguir a referência. Ele equivale ao AutoInject do artigo:
- **o agente não cai.** Ele continua respondendo, mas parte da entrega chega errada;
- **um agente defeituoso por vez**, escolhido como alvo (`alvo_falha`);
- **cerca de 20% dos registros** com valor diferente de zero são corrompidos, que é o P_e = 0,2 padrão do artigo. O valor é multiplicado por um fator sorteado entre 0,1, 0,5, 2 e 10. Valores inteiros continuam inteiros, para a contagem não denunciar o erro;
- **a corrupção acontece em trânsito**, entre o agente e o nó que recebe a entrega (`query` e `reenviar`). A memória do agente continua limpa;
- **a semente** é (análise, agente, número da entrega), então as duas topologias recebem exatamente a mesma corrupção.

Há duas variantes:

| Variante | Comando | O que acontece |
|---|---|---|
| Transitória | `--experimento-revisao` | Só a 1ª entrega é corrompida; o reenvio pedido pela revisão chega limpo |
| Persistente | `--experimento-revisao dados_corrompidos_persistente` | Toda entrega é corrompida, com sorteio novo a cada vez |

São **12 perguntas**, 4 por amplitude de escopo, cada uma com um agente-alvo que a pergunta aciona:

| Escopo | Pergunta (agente corrompido) |
|---|---|
| Focal (1 indicador) | `obvio_mortalidade_periodo` (SIM), `obvio_tuberculose_periodo` (SINAN), `obvio_covid_periodo` (COVID), `obvio_nascidos_vivos_periodo` (SINASC) |
| Intermediário (2 a 4) | `multi_sistema_internacao_mortalidade_profissionais` (SIH), `multi_sistema_doencas_negligenciadas` (SINAN), `multi_sistema_arboviroses` (SINAN), `ambiguo_periodo_mortalidade_internacoes` (SIM) |
| Amplo (todos) | `ambiguo_total_investimento` (SINAN), `ambiguo_total_melhora` (SIM), `pouco_objetiva_sei_la` (SIH), `ambiguo_sistema_saude_geral` (SI-PNI) |

Um gabarito guarda o valor limpo de cada registro corrompido, e com ele a bateria mede o que a revisão das entregas fez:

| Coluna | O que mede |
|---|---|
| `corrupcao_registros` | Registros corrompidos na 1ª entrega |
| `revisao_vp` / `revisao_fp` | Sinais da revisão que acertaram / não acertaram um registro corrompido |
| `contaminados_finais` | Registros que chegaram aos achados ainda com valor errado |
| `perdidos` | Registros corrompidos que a revisão descartou |
| `achados_iguais_limpos` | 1 se as correlações e anomalias finais são as mesmas que os dados limpos dariam |

É neste experimento que as topologias se separam como no artigo:
- **na estrela**, o orquestrador revisa todas as entregas numa única chamada;
- **na hierárquica**, cada supervisor revisa só o seu ramo, com menos contexto por chamada.

A consolidação é `scripts/analise_revisao.py`. **Esse experimento nunca foi rodado**: não há resultado dele em `execucoes_anteriores/`.

## 4. O que cada linha grava (por topologia)

| Eixo | Colunas |
|---|---|
| Interpretação | `intent_success`, `intent_missing`, `intent_elapsed_ms`, `intent_tokens`, `health_params_inferred`, `date_range_inferred`, `resolved_date_from/to`, `resolved_health_params(_count)`, `reference_year` |
| Tempo | `wall_clock_ms` (relógio determinístico), **`tempo_total_ms`** (até o texto final, com a redação), fases `fase_dominio/analitico/contexto/supervisores_ms` |
| Custo de coordenação | `overhead_ms`, `overhead_percent` e **`overhead_deliberacao_ms`** / **`overhead_estrutural_ms`** (espera pelo LLM dos coordenadores × o restante) |
| Custo em tokens | `prompt_tokens`, `completion_tokens`, `total_tokens`, `llm_call_count` (pipeline de cada topologia, sem o RAGAS) |
| Comunicação | `agent_invocations` (só agentes de linguagem), **`ferramentas_acionadas`**, `message_count`, **`mensagens_dados`**, **`mensagens_texto`**, **`caracteres_texto`**, `payload_records` |
| Planejamento e redação | **`plano_nos`**, **`plano_origem`** (llm / llm_failed_fallback / proposta, por nó), **`plano_acrescimos`**, **`plano_retiradas`**, **`redacao_modo`** (unica / por_ramo), **`redacao_ramos`**, **`redacao_falhas`** |
| Qualidade | `completude` (+ correlações, anomalias e contexto), `consistencia_identica`, **`fidelidade_numerica`**, **`numeros_texto`**, **`numeros_nao_verificados`**, `ragas_faithfulness`, `ragas_answer_relevancy`, `ragas_context_relevance`, `texto_analise_len` |
| Resiliência | `r1_score`, `r1_completed`, `texto_gerado`, `error_events`, `sucesso`, `dentro_do_orcamento_de_tempo` |
| Revisão das entregas | `revisao_chamadas`, `revisao_registros`, `revisao_sinalizados`, `revisao_confirmados`, `revisao_corrigidos`, `revisao_descartados`, `revisao_nao_verificados`, `revisao_reenvios`, `revisao_falhou` |

Em negrito estão as colunas novas desta versão. A consolidação (`analise_rodadas.py`) faz os testes pareados sobre `wall_clock_ms`, `tempo_total_ms`, `total_tokens`, `completion_tokens`, `message_count`, `texto_analise_len`, `completude`, `ragas_faithfulness`, `ragas_answer_relevancy` e `fidelidade_numerica`.

## 5. Pontos para decidir antes de rodar

1. ~~**Memória episódica ligada.**~~ **Resolvido:** a bateria roda sempre sem memória episódica, sem opção para ligar. Cada cenário é interpretado sem o histórico de análises do banco, e a campanha fica reprodutível.
2. ~~**Custo do RAGAS não é gravado.**~~ **Resolvido:** colunas `ragas_prompt_tokens`, `ragas_completion_tokens` e `ragas_total_tokens`, mais `ragas_juiz`. Era assim: No chat, o custo do juiz vai para `cost.ragas`. Na bateria, ele se perde, porque a qualidade é calculada fora de `active_results`. A correção é pequena, e com ela o TCC ganha o custo real da avaliação.
3. ~~**Covid antes de 2020 contada como lacuna.**~~ **Resolvido.** Anos anteriores ao início da série (`INICIO_DA_SERIE` em `agents/data_crossing.py`, covid a partir de 2020) ficam fora de vigência. Eles não são lacuna e não entram na completude nem nos descasamentos de cruzamento. 2024 e 2025 sem dado continuam sendo lacuna real.
4. **Peculiaridades da interpretação**, que valem igualmente para as duas topologias:
   - ~~`obvio_dengue_periodo` resolve `dengue` e mais o indicador CNES `estabelecimentos_vigilancia_epidemiologica`~~ **Corrigido no prompt da interpretação:** citar orçamento, gasto ou investimento de uma área não seleciona indicador, e indicadores de estabelecimentos, profissionais, leitos e equipes só entram quando a pergunta fala deles. A confusão chegou a puxar 5 subfunções e 78 achados para uma pergunta sobre dengue.
   - `ambiguo_sistema_infecciosas` ("doenças infecciosas") resolve **todos os 27** indicadores, e não só os agravos do SINAN.
   - `ambiguo_sistema_mudou` ("últimos 5 anos") foi recusada na campanha antiga. Com o ano fixo em 2025, deve passar como 2021–2025.
5. **Número de rodadas.** A campanha antiga teve 5 rodadas. Com 3, o custo estimado é de US$ 4 a 10; com 5, fica entre US$ 7 e 17. Os testes pareados usam a mediana por pergunta entre as rodadas.
6. **Rodar o experimento de falha semântica.** Ele é o teste de resiliência alinhado às referências, mas não roda na campanha principal. Com `--no-ragas`, que basta porque as métricas dele vêm do gabarito, cada variante custa cerca de US$ 0,15 por rodada (12 análises). As duas variantes em 3 rodadas dão menos de US$ 1.
7. **Perguntas curtas** (`muito_objetiva`) tendem a ter relevância da resposta baixa no RAGAS, porque a métrica compara a semelhança com o texto da pergunta. Isso é esperado.

## 6. Como rodar (depois das decisões)

Na pasta `backend/`, com o `.env` carregado, uma rodada por comando:

```
python -m scripts.batch_eval --out ../resultados_rodada1.xlsx
python -m scripts.batch_eval --out ../resultados_rodada2.xlsx
python -m scripts.batch_eval --out ../resultados_rodada3.xlsx
```

Para um teste curto antes: `--limit 1 --out ../teste_curto.xlsx`.

Experimento de falha semântica (item 5.6), uma rodada de cada variante:

```
python -m scripts.batch_eval --experimento-revisao --no-ragas --out ../revisao_transitoria_rodada1.xlsx
python -m scripts.batch_eval --experimento-revisao dados_corrompidos_persistente --no-ragas --out ../revisao_persistente_rodada1.xlsx
```
