# Plano — Novo Modelo de Dados e ETL (Skopos)

> Documento gerado em 2026-08-02 a partir de uma sessão de planejamento (Claude Code). Registra o raciocínio e as decisões sobre a migração da fonte de dados do projeto — de PySUS/FTP DataSUS + planilhas FNS para arquivos locais já extraídos em `Dados/` — e o redesenho do modelo de entidades/relacionamentos no Neo4j. **Nenhum código foi alterado nesta sessão** — é só planejamento. Destinado a orientar quem for implementar o ETL/banco e, em paralelo, a migração da arquitetura de agentes de BDI para CoALA.
>
> Ver também [`CONTEXTO_PROJETO.md`](CONTEXTO_PROJETO.md) para o panorama geral do projeto (arquitetura de agentes atual, API, stack).

---

## 1. Por que mudar

O pipeline atual (`backend/etl/siops_loader.py` + `backend/etl/datasus_loader.py` + PySUS) tem duas fragilidades que motivaram esta revisão:

1. **Dado financeiro incompleto**: `siops_loader.py` lê planilhas do FNS (repasses federais **recebidos**, não despesa **executada**). Isso é uma limitação documentada em `docs/03-DADOS-ETL.md` — recursos próprios municipais (50-70% do gasto real) ficavam de fora.
2. **Pipeline de saúde frágil**: `datasus_loader.py` depende de download FTP + parsing `.dbc` via PySUS — lento, sujeito a falha de rede e a OOM em containers, e só extrai uma contagem total por (sistema, tipo, ano), sem nenhuma granularidade.

Agora existem em `Dados/` extrações já prontas que resolvem os dois problemas ao mesmo tempo: dado de **execução orçamentária real** (LAI/transparência, empenho a empenho) e planilhas **DATASUS TabNet já filtradas para Sorocaba**, com granularidade (faixa etária, capítulo CID-10, por vacina etc.) que não existia antes.

**Decisão explícita desta sessão**: não há preocupação de retrocompatibilidade com o código atual (`backend/api/dispatch.py`, `backend/db/neo4j_client.py`, agentes BDI). Outro desenvolvedor está migrando a arquitetura de agentes de BDI para **CoALA** em paralelo — este modelo de dados deve ser desenhado da forma mais correta possível, sem amarras ao formato que os agentes/API atuais esperam.

---

## 2. Inventário dos dados disponíveis

### 2.1 Orçamento — `Dados/orcamento/{2015-2019,2020-2022,2023-2025}/`

Dois arquivos CSV por período (extração LAI/portal de transparência de Sorocaba). **Nota**: existem cópias idênticas (mesmo MD5) desses CSVs na raiz do repositório (`2015-2019/`, `2020-2022/`, `2023-2025/`) — decidido **não mexer nisso agora**, mas o ETL deve ler apenas de `Dados/orcamento/`.

**a) `*_empenhado_*.csv` — empenho individual (grão: 1 linha = 1 lançamento)**

- Delimitador: `|` · Encoding: `latin-1` (utf-8 falha em caracteres acentuados)
- Volume: 16.584 linhas (2015-2019, embora `Data Empenho` real vá de 2016 a 2019) + 15.817 (2020-2022) + 20.503 (2023-2025) ≈ 53 mil linhas
- Colunas: `Nro Empenho, Nro Processo, Fornecedor, Data Empenho, Descrição Empenho, Função, Sub Função, Fonte Recurso, Aplicação, Natureza, Unidade Orçamentária, Programa, Ação, Modalidade de Licitação, Empenho Original, Estorno Empenho, Saldo Empenho, Valor Processado, Valor Pago`
- `Fornecedor` vem com marcação HTML solta (`<b>`, `<br>`) e CNPJ entre parênteses no fim da string — precisa regex para extrair nome limpo + CNPJ
- `Ação` vem como `"2109-Atencao Primaria em Saude"` — código antes do primeiro hífen
- `Nro Empenho + Nro Processo` **se repete** entre linhas (parcelas/estornos do mesmo empenho ao longo do tempo) — não serve como chave única sozinho

**b) `*_previsto_e_empenhado_por_programa_e_secretaria_*.csv` — previsto × realizado**

- Mesmo delimitador/encoding
- Colunas: `Programa de Governo, Ação, Órgão Responsável, Quanto foi previsto, Quanto já foi realizado (Empenhado ou Pago)`
- **Atenção**: este arquivo é agregado pelo **bloco de anos inteiro** do nome do arquivo (2015-2019, 2020-2022, 2023-2025) — **não tem coluna de ano**. Não dá para reconstruir "previsto vs. realizado" como série anual a partir dele, só por bloco plurianual.

**Valores de `Sub Função` encontrados** (mapeiam para código padrão da função 10-Saúde):

| Texto na planilha | Código sugerido | Nome padrão |
|---|---|---|
| Atencao Basica | 301 | Atenção Básica |
| Assistencia Hospitalar e Ambulatorial | 302 | Assistência Hospitalar e Ambulatorial |
| Suporte Profilatico e Terapeutico | 303 | Suporte Profilático e Terapêutico |
| Vigilancia Sanitaria | 304 | Vigilância Sanitária |
| Vigilancia Epidemiologica | 305 | Vigilância Epidemiológica |
| Alimentacao e Nutricao | 306 | Alimentação e Nutrição |
| Administracao Geral | 122 | Administração Geral (subfunção genérica, não exclusiva de saúde) |

**Valores de `Fonte Recurso` encontrados** — proposta de classificação em `tipoRecurso` (**precisa validação**, ver §7):

| Texto na planilha (variantes observadas) | `tipoRecurso` proposto |
|---|---|
| Tesouro | `proprio` |
| Rec.prop.de Fdos Especiais de Despesa... / Rec. Prop. Fundos Especiais... | `proprio` |
| Transferencias/Transf.e Convenios **Federais**... (qualquer variante/sufixo "-exec.anter.") | `federal` |
| Transferencias/Transf.e Convenios **Estaduais**... (qualquer variante) | `estadual` |
| Operacoes de Credito | `credito` |
| Outras Fontes de Recursos (+ "- Exercicios Anteriores") | `outros` |
| Emendas Parlamentares Individuais | `federal` (⚠️ incerto — ver §7) |
| Emendas Parlamentares Individuais **- Leg. Municipal** (só aparece em 2023-2025) | `proprio` (⚠️ incerto — ver §7) |

### 2.2 Indicadores de saúde — `Dados/Sorocaba_DATASUS_2015-2025/*.xlsx`

Extração TabNet (DATASUS) já filtrada para Sorocaba/SP (IBGE 355220), cobrindo 2015-2025/2026. **Substitui inteiramente o PySUS** — leitura local, sem rede, sem `.dbc`.

**Padrão estrutural comum a quase todas as abas**: linha 0 = título (1 célula), linha 1 = vazia, linha 2 = cabeçalho real, linha 3+ = dados. Primeira coluna = `Ano` (ou `Ano/Mês` em alguns sub-cubos do CNES), demais colunas = categorias, geralmente com `Total` ao final.

| Arquivo | Abas relevantes | `sistema` proposto | Observações |
|---|---|---|---|
| `Sorocaba_Mortalidade_SIM_2015-2025.xlsx` | Óbitos por Faixa Etária · Óbitos por Capítulo CID-10 | `sim` | 2 sub-cubos, mesmo agente cobre os dois |
| `Sorocaba_Internacoes_SIH_2015-2025.xlsx` | Internações por Capítulo CID-10 | `sih` | |
| `Sorocaba_SINAN_Doencas_Notificacao_2015-2025.xlsx` | Dengue · Chikungunya · Sífilis Adquirida · Sífilis em Gestante · Sífilis Congênita · Coqueluche · Hepatites Virais · Tuberculose · Hanseníase (9 abas, cada uma "por Faixa Etária" ou "por Sexo") | `sinan` | **1 agente só cobre as 9 doenças** (decisão tomada — ver §5) |
| `Sorocaba_SIPNI_Cobertura_Vacinal_2015-2026.xlsx` | Cobertura 2015-2022 · Doses 2015-2022 · Cobertura 2023-2025 · Doses 2023-2026 | `sipni` | **Layout transposto**: linhas = vacina, colunas = ano — parser precisa tratamento à parte |
| `Sorocaba_Nascidos_Vivos_SINASC_2015-2025.xlsx` | Nascimentos por Idade da Mãe · Nascimentos por Peso ao Nascer | `sinasc` | |
| `Sorocaba_SIA_Producao_Ambulatorial_2015-2025.xlsx` | Producao Ambulatorial por Ano | `sia` | Só 1 métrica (`Qtd_Aprovada_Total`), sem dimensão |
| `Sorocaba_CNES_Recursos_2015-2025.xlsx` | Leitos de Internacao (Dez) · Profissionais de Saude (Dez) | `cnes` | Snapshot de dezembro, granularidade anual |
| `Sorocaba_CNES_Dados_Adicionais_2015-2025.xlsx` | Estabelecimentos (mensal) · Tipo Atendimento (SUS) · Rec.Fisicos-Leitos e Consult · Rec.Fisicos-Equipamentos · Rec.Humanos-Ocupacoes (Dez) · Equipes de Saude (mensal) | `cnes` | **"Estabelecimentos" e "Equipes de Saude" são mensais** (`Ano/Mês`, ex. "2015/Jan") — granularidade diferente do resto do CNES, que é anual (dezembro). Decisão em aberto — ver §7 |
| `Sorocaba_Rede_Assistencial_CNES_2015-2025.xlsx` | Estabelecimentos por Tipo | `cnes` | |
| `Sorocaba_COVID19_Casos_Obitos_2020-2023.xlsx` | COVID Casos-Obitos Anual · COVID Casos-Obitos Mensal | `covid` | Fonte própria (covid.saude.gov.br), não é parte do SINAN |
| `Sorocaba_Bases_Indisponiveis_2015-2025.xlsx` | (só notas) | — | **Não é dado a ingerir** — documenta cubos TabNet descontinuados (HIPERDIA desde abr/2013, SISVAN desde 2007-2008, RECSUS financeiro desde 2006-2007). Útil para a seção de limitações do TCC. |

---

## 3. Modelo de entidades e relacionamentos proposto

**Decisão desta sessão**: sem preocupação de retrocompatibilidade, os nomes antigos e tecnicamente incorretos (`DespesaSIOPS` — nunca foi SIOPS de fato, era FNS; agora nem FNS é mais) são **abandonados**. Nomenclatura nova:

### 3.1 Domínio Orçamento

```cypher
(:Empenho)-[:DO_FORNECEDOR]->(:Fornecedor {cnpj, nome})
(:Empenho)-[:FINANCIADO_POR]->(:FonteRecurso {nome, tipoRecurso})
(:Empenho)-[:CLASSIFICADO_EM]->(:Subfuncao {codigo, nome})
(:Empenho)-[:EXECUTA_ACAO]->(:Acao {codigo, nome})-[:PARTE_DE]->(:ProgramaGoverno {nome})
(:Empenho)-[:DA_UNIDADE]->(:UnidadeOrcamentaria {nome})
```

- **`Empenho`** — nó granular, 1 por linha do CSV (~53 mil nós). Propriedades: `nroEmpenho, nroProcesso, dataEmpenho (date), ano, mes, descricao, funcao, subfuncaoCodigo, subfuncaoNome, valorEmpenhoOriginal, valorEstorno, saldoEmpenho, valorProcessado, valorPago, periodoOrigem, fonte="lai_transparencia", importedAt`. **Chave de MERGE**: como `nroEmpenho+nroProcesso` se repete, usar hash determinístico da linha inteira (idempotente em reprocessamento), não os números originais.
- **`Fornecedor {documento, tipoDocumento, nome}`** — chave natural `documento` (CNPJ ou CPF, extraído por regex; `tipoDocumento` ∈ `cnpj | pessoa_fisica | desconhecido`). **Descoberto na implementação**: ~15-20% dos empenhos são para pessoa física (CPF) — prestadores de serviço individuais (ex. home care) — não só empresas. Fallback por nome normalizado quando nenhum documento é extraído (casos legítimos: folha de pagamento agregada, órgãos públicos sem CNPJ citado no texto).
- **`FonteRecurso {nome, tipoRecurso}`** — `tipoRecurso` ∈ `proprio | federal | estadual | credito | outros` (mapa em §2.1). **Este é o campo que resolve a limitação documentada do dado antigo** — agora dá para separar recurso próprio municipal de repasse.
- **`Subfuncao {codigo, nome}`**, **`ProgramaGoverno {nome}`**, **`Acao {codigo, nome}`**, **`UnidadeOrcamentaria {nome}`** — nós de dimensão.
- **`DespesaAnual`** (agregado materializado, calculado a partir de `Empenho` no fim do ETL) — mesma granularidade do antigo `DespesaSIOPS` (subfuncao×ano), mas a partir de execução real. Breakdown por `tipoRecurso` guardado como propriedades-mapa (`valorProprio, valorFederal, valorEstadual, valorCredito, valorOutros`) em vez de multiplicar nós.
- **`OrcamentoPrograma {programa, acao, orgao, periodoOrigem, valorPrevisto, valorRealizado}`** — do CSV previsto×realizado. Grão por bloco plurianual, não anual (ver limitação em §2.1b).

**Melhoria de modelagem combinada (guardada para execução futura, ver §5)**: encadear os nós `DespesaAnual` de anos consecutivos via relacionamento com a variação já calculada:

```cypher
(:DespesaAnual {subfuncaoCodigo: 301, ano: 2023})
  -[:VARIACAO_ANUAL {percentual: 12.3, classificacao: 'crescimento'}]->
(:DespesaAnual {subfuncaoCodigo: 301, ano: 2022})
```

Calculado uma vez no ETL (não recalculado a cada execução de agente); `classificacao` ∈ `crescimento | corte | estagnacao | insuficiente` (mesma lógica que o `AgenteContextoOrcamentario` de hoje usa).

### 3.2 Domínio Indicadores de Saúde

```cypher
(:IndicadorSaude {sistema, subtipo, ano, valorTotal, fonte="datasus_tabnet"})
    -[:POR_FAIXA_ETARIA {valor}]->(:FaixaEtaria {nome})
    -[:POR_CAPITULO_CID {valor}]->(:CapituloCID10 {codigo, nome})
    -[:COBERTURA_VACINAL {percentual}]->(:Vacina {nome})
    -[:DOSES_APLICADAS {quantidade}]->(:Vacina {nome})
    -[:POR_TIPO_ESTABELECIMENTO {quantidade}]->(:TipoEstabelecimento {nome})
    -[:POR_OCUPACAO {quantidade}]->(:OcupacaoProfissional {nome})
    -[:POR_TIPO_EQUIPE {quantidade}]->(:TipoEquipe {codigo, nome})
```

- **`IndicadorSaude`** substitui `IndicadorDataSUS`. Grão: `(sistema, subtipo, ano)`. `sistema` ∈ `sim | sih | sinan | sipni | sinasc | sia | cnes | covid` (ver §5 — é a chave de particionamento dos agentes de saúde). `subtipo` é a sub-dimensão dentro do sistema (ex: dentro de `sinan`, `subtipo` ∈ `dengue | chikungunya | sifilis_adquirida | sifilis_gestante | sifilis_congenita | coqueluche | hepatites_virais | tuberculose | hanseniase`).
- **Decisão tomada** (nós de dimensão, não só propriedades): usar relacionamentos com propriedade (`{valor}`) para as quebras dimensionais em vez de guardar tudo achatado — isso é o que abre correlações novas: comparar óbitos × internações pelo **mesmo** capítulo CID-10, ou cruzar gasto por subfunção com mortalidade numa faixa etária específica.
- **Pendente**: normalização de `FaixaEtaria` e `CapituloCID10` entre sistemas (ver §7 — decisões em aberto).

---

## 4. Pipeline ETL — o que muda

| Hoje | Proposta |
|---|---|
| `siops_loader.py` (lê `.xls`/`.xlsx` do FNS em `backend/data/`) | `orcamento_loader.py` — lê `Dados/orcamento/*/​*_empenhado_*.csv` (`\|`, `latin-1`) + `*_previsto_e_empenhado_*.csv` |
| `datasus_loader.py` + `download_pysus.py` (FTP, `.dbc`, cache parquet, risco de OOM) | `saude_indicadores_loader.py` — lê `Dados/Sorocaba_DATASUS_2015-2025/*.xlsx` local, sem rede, sem PySUS. Um parser por formato de aba (ano simples wide-by-category, ano/mês mensal, vacina×ano transposta) |
| `seed_data.py` (fallback hardcoded de COVID) | Elimina-se — COVID mensal/anual real já vem no xlsx |
| `detect_years.py` (lê metadado "Ano:" do FNS) | Adapta para detectar anos a partir de `Data Empenho` |
| Dependências `pysus`, `datasus-dbc`, `dbfread`, `tqdm` | Removidas |
| `entrypoint.sh` (`--skip-download`, espera de FTP) | Simplifica — leitura de arquivo local é rápida e determinística |

**Constraints necessárias no Neo4j** (nenhuma existe hoje — falta importante mesmo no schema atual): unicidade em `Fornecedor.cnpj`, `Subfuncao.codigo`, `FonteRecurso.nome`, `IndicadorSaude(sistema, subtipo, ano)`, `FaixaEtaria.nome`, `CapituloCID10.codigo`. Sem isso, todo `MERGE` faz varredura completa do label.

---

## 5. Arquitetura de agentes — decisões que impactam o modelo de dados

Não é escopo deste ETL/banco implementar agentes, mas as decisões de particionamento abaixo **determinam as chaves de indexação/constraint** do grafo, por isso ficam registradas aqui.

**Princípio acordado**: 1 agente de saúde por Sistema de Informação (SI), 1 agente orçamentário por subfunção.

| Camada | Agentes | Partição no grafo |
|---|---|---|
| Saúde | 8 — `AgenteSIM, AgenteSIH, AgenteSINAN, AgenteSIPNI, AgenteSINASC, AgenteSIA, AgenteCNES, AgenteCOVID` | `IndicadorSaude.sistema` |
| Orçamento | 7 — 1 por subfunção (301/302/303/304/305/306/122) | `Subfuncao.codigo` / `Empenho.subfuncaoCodigo` |
| Analítico | 2 — `AgenteCorrelacao`, `AgenteAnomalias` | cruza as duas partições acima (até 7×8 = 56 pares) |
| Contexto | **0** — eliminado como agente separado | absorvido pelos 7 agentes orçamentários (cada um também classifica sua própria tendência ano-a-ano, usando `VARIACAO_ANUAL` do §3.1) |
| Sintetizador | 1 — não-BDI, narrativa final | — |

**Total: 18 agentes-folha/serviço**, compartilhados entre as topologias Star e Hierárquica.

Decisões explícitas tomadas nesta sessão:
- SINAN = **1 agente só** cobrindo as 9 doenças internamente (não splitar por doença).
- CNES = **1 agente só** cobrindo os 4 sub-cubos internamente (não splitar por sub-cubo).
- `AgenteContextoOrcamentario` **eliminado como agente separado** (Opção C) — cada agente orçamentário absorve a responsabilidade de calcular sua própria tendência.
- Split do supervisor Hierárquica (`SupervisorOrcamento`/`SupervisorSaude` vs. `SupervisorDominio` único) — **deixado em aberto**, decisão de quem for implementar.

### Por que `AgenteCorrelacao`/`AgenteAnomalias` continuam necessários mesmo com Cypher fazendo mais trabalho

Cypher/Neo4j não tem correlação de Spearman nativa (nem em APOC de forma confiável) — é agregação/pattern matching, não é motor estatístico. O que o grafo novo permite é o **pareamento ano-a-ano já feito na query** (via `MATCH` cruzando `Empenho→Subfuncao` e `IndicadorSaude` pelo mesmo `ano`), eliminando o código de "casar por ano manualmente" que hoje vive dentro do agente Python. Mas o cálculo estatístico em si (`scipy.stats.spearmanr`, p-valor) continua sendo trabalho do agente — não vira query. O papel do agente encolhe (menos *glue code*) mas não desaparece: ele decide **o quê** correlacionar, dispara a query, roda a estatística, interpreta o resultado.

---

## 6. Nota para a migração BDI → CoALA

O outro desenvolvedor está migrando os agentes de BDI para **CoALA** (Cognitive Architectures for Language Agents — LLM como motor cognitivo/decisão, memória de trabalho + episódica + semântica, ações internas vs. de *grounding*). Conclusões desta sessão relevantes para essa migração:

1. **O modelo de dados (grafo) não muda por causa do CoALA** — ele serve de **memória semântica** para o LLM raciocinar em cima, independente do paradigma do agente. Na verdade, CoALA se beneficia de um grafo **mais** rico e explícito (nós de dimensão, relacionamentos como `VARIACAO_ANUAL`), porque o LLM precisa de fatos recuperáveis e concretos para fundamentar decisões e evitar alucinação.
2. **Correlação/estatística deve continuar sendo ferramenta determinística (Cypher + SciPy), nunca "calculada" pelo LLM** — LLMs não são confiáveis para aritmética/estatística exata. Em termos CoALA: a query Cypher + `scipy.stats.spearmanr` já desenhadas aqui são candidatas diretas a virar **ferramentas de ação externa (grounding)** que o LLM invoca, não algo que o LLM computa por conta própria.
3. **Reavaliar a granularidade de agentes ao migrar**: BDI tende a exigir 1 classe de agente por capacidade; CoALA tende a favorecer poucos agentes com muitas ferramentas, já que o LLM escolhe dinamicamente qual ferramenta usar. Especificamente, `AgenteCorrelacao` + `AgenteAnomalias` são candidatos naturais a virar **1 agente com 2 ferramentas** (`ferramenta_correlacionar`, `ferramenta_detectar_anomalia`) em vez de 2 classes separadas — mas isso não foi decidido, é uma reavaliação sugerida para quando a migração acontecer, não algo a implementar agora.
4. **Memória episódica** (conceito formal do CoALA, distinto de memória semântica) — o nó `Analise` que já existe no schema atual é um embrião disso (registra análises passadas). Vale considerar enriquecê-lo para o LLM conseguir "lembrar" de análises/decisões anteriores ao raciocinar sobre uma nova pergunta.

---

## 7. Decisões em aberto (para quem for implementar)

| # | Decisão | Opções | Observação |
|---|---|---|---|
| 1 | Normalização de `FaixaEtaria` entre sistemas | SIM usa faixas de 10 em 10; SINASC de 5 em 5; SINAN dengue usa outra granularidade ainda | Canônico (cruzável entre sistemas) vs. nativo por sistema (mais fiel, não cruzável direto) |
| 2 | Mapeamento `CapituloCID10` SIM (romano, "Cap IX") ↔ SIH (decimal, "Cap09") | — | Tarefa de ETL (construir tabela de correspondência), não decisão de arquitetura |
| 3 | Classificação `tipoRecurso` de **"Emendas Parlamentares Individuais"** | `federal` nos períodos 2015-2022 vs. `federal` **ou** `proprio` em 2023-2025 (aparece como "- Leg. Municipal", sugerindo emenda de vereador = recurso do próprio orçamento municipal, não repasse) | **Precisa validação** — classificação errada aqui compromete diretamente a resolução da limitação "FNS ≠ despesa total", que é o principal ganho deste novo modelo |
| 4 | Granularidade mensal do CNES ("Estabelecimentos" e "Equipes de Saude", formato `Ano/Mês`) | Ingerir mensal (fiel à fonte, mais nós) vs. agregar/amostrar para dezembro (consistente com o resto do CNES, que é anual) | |
| 5 | Split do supervisor Hierárquica (`SupervisorOrcamento`+`SupervisorSaude` vs. `SupervisorDominio` único cobrindo os 15 agentes de domínio) | — | Deixado em aberto deliberadamente |
| 6 | Consolidação de `AgenteCorrelacao`+`AgenteAnomalias` em 1 agente com 2 ferramentas | Só relevante na migração CoALA (§6, item 3) | Não decidir agora |

---

## 7.1 Janela temporal oficial do sistema (decidida após validação em Neo4j real)

**Decisão**: todos os dados do sistema — orçamento e, futuramente, indicadores de saúde — devem cobrir exclusivamente **01/01/2016 a 31/12/2025 (10 anos)**. Qualquer registro fora desse período é descartado no ETL, não só filtrado em tempo de consulta.

Implementado em `orcamento_loader.py` via `PERIODO_ANO_MIN=2016`/`PERIODO_ANO_MAX=2025`, aplicado por linha em `_read_empenho_csv` (compara o ano de `Data Empenho`). Validado contra o dado real: os ~53 mil empenhos já caíam inteiramente dentro da janela (nenhum registro foi de fato descartado), mas o filtro fica no código como regra permanente — importante principalmente para o loader de saúde a seguir, já que os arquivos DATASUS têm nomes como `..._2015-2025.xlsx` e `..._2015-2026.xlsx` (SI-PNI), que **extrapolam** a janela oficial e vão precisar descartar linhas de 2015 e 2026.

**Limitação conhecida, não resolvida**: `OrcamentoPrograma` (previsto × realizado) não tem coluna de ano por linha — o grão é o bloco plurianual inteiro do arquivo de origem (`periodoOrigem` = "2015-2019", "2020-2022" ou "2023-2025", ver §2.1b). Não há como aplicar o filtro 2016-2025 dentro desse nó com precisão — os três blocos foram mantidos integralmente. Quem for consumir `OrcamentoPrograma` deve estar ciente que o bloco "2015-2019" nominalmente inclui o ano de 2015, ainda que os `Empenho` reais desse período comecem em 2016.

## 7.2 Nota sobre classificação de subfunção 122 (Administração Geral) — achado da validação

Ao validar os dados carregados, identificamos que a subfunção **122 (Administração Geral) não existe nos empenhos do período 2015-2019** — só passa a ser usada a partir de 2020. Isso explica uma variação grande (não é bug): o gasto próprio (`tipoRecurso=proprio`) classificado em **301 (Atenção Básica)** salta de ~R$16M em 2016 para ~R$355M em 2018, porque itens de folha de pagamento e obrigações patronais (`3.1.90.11.00`, `3.1.91.13.00`) que em anos posteriores são segregados em 122 ficavam concentrados em 301 antes de 2020. É uma mudança real de prática de classificação orçamentária municipal ao longo do tempo, não um erro de mapeamento do ETL — mesma natureza das inconsistências de nomenclatura já documentadas em `docs/03-DADOS-ETL.md`. Relevante para qualquer análise de série temporal de 301 que cruze o limite 2019/2020.

## 8. Resumo do que foi decidido nesta sessão

- Abandonar PySUS/FTP DataSUS e planilhas FNS — usar exclusivamente `Dados/orcamento/` e `Dados/Sorocaba_DATASUS_2015-2025/`.
- Modelo de orçamento passa a ser granular (`Empenho`, 1 nó por lançamento, ~53 mil nós) com dimensões (`Fornecedor`, `FonteRecurso`, `Subfuncao`, `ProgramaGoverno`, `Acao`), mais um agregado anual materializado (`DespesaAnual`).
- Modelo de saúde ganha nós de dimensão compartilhados (`FaixaEtaria`, `CapituloCID10`, `Vacina`, `TipoEstabelecimento`, `OcupacaoProfissional`, `TipoEquipe`) conectados a `IndicadorSaude` via relacionamentos com propriedade — habilita correlações multi-hop que hoje são impossíveis.
- Sem preocupação de retrocompatibilidade — nomenclatura antiga (`DespesaSIOPS`, `IndicadorDataSUS`) abandonada em favor de nomes tecnicamente corretos (`Empenho`/`DespesaAnual`, `IndicadorSaude`).
- Arquitetura de agentes: 8 saúde (1/SI) + 7 orçamento (1/subfunção, absorvendo também a responsabilidade de tendência) + 2 analíticos + 1 sintetizador = 18 agentes-folha.
- `VARIACAO_ANUAL` (relacionamento entre `DespesaAnual` de anos consecutivos, calculado no ETL) — guardado para execução futura.
- Janela temporal oficial fixada em 01/01/2016-31/12/2025 (10 anos) para todo o sistema — descarte no ETL, não em query (§7.1).
- `orcamento_loader.py` implementado, testado contra Neo4j real (52.904 `Empenho`, 943 `OrcamentoPrograma`, 53 `DespesaAnual`), validado (soma de `tipoRecurso` bate exatamente com o total, zero exceção de parsing).
- Correção descoberta na implementação: `Fornecedor` precisa suportar CPF além de CNPJ (~15-20% dos empenhos são pessoa física).
- `saude_indicadores_loader.py` implementado com cobertura completa dos 8 sistemas (todos os sub-cubos, incluindo os mais niche do CNES e COVID mensal), testado contra Neo4j real: 346 `IndicadorSaude`, `CapituloCID10` canônico unificado entre SIM e SIH (21 códigos), `FaixaEtaria` escopada por sistema (40 nós, sem falsa unificação SIM/SINASC/SINAN). Query de correlação real (Vigilância Epidemiológica × dengue) e query multi-hop nova (mortalidade × internações pelo mesmo capítulo CID) validadas com dado real.
- Adicionada a dimensão `Aplicacao` (333 nós, `(:Empenho)-[:APLICADO_EM]->(:Aplicacao)`), a partir da coluna `Aplicação` do CSV de empenho — vinha sendo lida/validada mas não persistida. É a dimensão mais específica sobre finalidade do gasto que os dados oferecem (ex. isola "Assistência Farmacêutica" do resto de "Material de Consumo"). Motivada por revisão explícita: todas as 19 colunas do CSV de empenho agora têm destino no grafo (propriedade ou dimensão), nenhuma é descartada.
- Vocabulário de relacionamentos de quebra dimensional ficou maior que os 7 originalmente desenhados — 11 no total (`POR_FAIXA_ETARIA`, `POR_CAPITULO_CID`, `POR_SEXO`, `POR_FAIXA_PESO`, `COBERTURA_VACINAL`, `DOSES_APLICADAS`, `POR_TIPO_ESTABELECIMENTO`, `POR_OCUPACAO`, `POR_TIPO_EQUIPE`, `POR_TIPO_ATENDIMENTO`, `POR_TIPO_LEITO_CONSULTORIO`, `POR_TIPO_EQUIPAMENTO`) — a diversidade real de sub-cubos do CNES era maior do que a modelagem inicial previa.
- Notas de compatibilidade com a futura migração CoALA registradas em §6.
- 6 decisões deixadas explicitamente em aberto (§7) para quem for implementar.

---

## 9. Próximos passos sugeridos

1. Validar a classificação de `tipoRecurso` para "Emendas Parlamentares" (item #3 de §7) — idealmente com alguém que conheça a estrutura orçamentária de Sorocaba, ou checando a legislação/portaria referenciada nos empenhos.
2. Fechar a normalização de `FaixaEtaria` (#1) e decidir granularidade do CNES mensal (#4).
3. Implementar `orcamento_loader.py` (Empenho + dimensões + agregação `DespesaAnual` + `VARIACAO_ANUAL`).
4. Implementar `saude_indicadores_loader.py` (um parser por formato de aba, incluindo o caso transposto do SI-PNI).
5. Declarar as constraints de unicidade listadas em §4 antes de rodar o ETL em volume.
6. Coordenar com a migração CoALA os pontos de §6 (especialmente: tratar as queries de correlação como candidatas a ferramentas/ações de grounding desde o início da implementação dos agentes novos).
