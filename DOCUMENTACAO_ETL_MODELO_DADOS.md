# Documentação — ETL e Modelo de Dados Neo4j (Skopos)

> Documento gerado em 2026-08-04. Descreve, em detalhe técnico, o que foi **efetivamente implementado e validado** nesta sessão: a estrutura final do banco Neo4j, os dois módulos de ETL que a populam, e os dados reais carregados. Complementa o [`PLANO_NOVO_MODELO_DADOS.md`](PLANO_NOVO_MODELO_DADOS.md), que registra o *raciocínio* e as *decisões* tomadas durante o design — este documento aqui é a referência do *resultado* (estado "as-built"), com números reais extraídos de um banco Neo4j populado e validado.
>
> Contexto: outro desenvolvedor está migrando a arquitetura de agentes de BDI para CoALA em paralelo. **Nada em `backend/agents/`, `backend/api/` ou no frontend foi alterado nesta sessão** — só ETL (`backend/etl/`), schema Neo4j, e a infraestrutura estritamente necessária para o ETL rodar (`docker-compose.yml`, `entrypoint.sh`, `run_etl.py`, `requirements.txt`).

---

## 1. O que mudou, em uma frase

O sistema deixou de depender de PySUS/FTP DataSUS e de planilhas FNS (repasses federais recebidos) e passou a ler exclusivamente arquivos locais já extraídos em `Dados/`: **execução orçamentária municipal real** (LAI/portal de transparência, empenho a empenho) e **extratos DATASUS/TabNet já filtrados para Sorocaba** com granularidade dimensional (faixa etária, capítulo CID-10, vacina, tipo de estabelecimento etc.) que o pipeline antigo não tinha.

## 1.1 Origem institucional dos dados (proveniência)

Esta seção documenta **de onde os arquivos em `Dados/` vieram antes de chegar no ETL** — extraída das próprias planilhas (abas "Notas" dos xlsx do DATASUS) e do que é inferível dos CSVs de orçamento. É informação que **não estava** na versão anterior deste documento.

### 1.1.1 Orçamento (`Dados/orcamento/`)

Proveniência limitada ao que o próprio nome dos arquivos indica — os CSVs não têm metadado de origem embutido (diferente das planilhas DATASUS, ver abaixo). Nome do arquivo: `guestuser_lai_-_empenhado_(extraçao_de_dados)_<hash>.csv` — sugere extração de um portal de transparência/LAI (Lei de Acesso à Informação) do município de Sorocaba, acessado como usuário convidado, via alguma funcionalidade de "extração de dados". **Não há data de extração registrada no arquivo.** Isso é uma assimetria real em relação ao lado saúde: se em algum momento os números do orçamento precisarem ser auditados/atualizados, não há como saber exatamente quando foram extraídos nem qual ferramenta/portal exato gerou o export, só pelo conteúdo do arquivo.

### 1.1.2 Indicadores de saúde (`Dados/Sorocaba_DATASUS_2015-2025/`)

Todas as planilhas têm aba **"Notas"** com proveniência detalhada. Município: sempre 355220 (Sorocaba/SP, IBGE 3552205). Datas de extração: **01/08/2026 e 02/08/2026** (extração muito recente, feita pouco antes desta sessão). Achados relevantes que a versão anterior deste documento não capturava:

| Sistema | Fonte exata | Caveats importantes |
|---|---|---|
| **SIM** (mortalidade) | TabNet/DATASUS | **Dados de 2025 são preliminares (3ª prévia), sujeitos a atualização** |
| **SIH** (internações) | TabNet/DATASUS | **Dados de 2025 são preliminares (3ª prévia)** |
| **SINASC** (nascidos vivos) | TabNet/DATASUS | **Dados de 2025 são preliminares (3ª prévia)** |
| **Rede Assistencial CNES** ("Estabelecimentos por Tipo") | TabNet/DATASUS | **Dados de 2025 são preliminares (3ª prévia)** |
| **SIA** (produção ambulatorial) | TabNet/DATASUS | Metodologia oficial: total anual = soma de 12 arquivos mensais de competência (`qaspYYMM.dbf`); atendimentos processados com atraso "vazam" entre anos adjacentes — é o comportamento oficial do TabNet, não erro |
| **SINAN** (doenças de notificação) | TabNet/DATASUS | A variável de tempo usada **varia por doença** (ano de notificação, de diagnóstico, ou do 1º sintoma — conforme o que o cubo TabNet daquele agravo disponibiliza). **Sífilis (adquirida/gestante/congênita) e Hepatites Virais ainda não têm dados de 2024 e/ou 2025** publicados pelo MS até a data de extração — é atraso normal de consolidação, não falha de extração (é por isso que esses subtipos aparecem com 8-9 anos em vez de 10 na validação da §7 do documento anterior). PNI/vacinação **não está** disponível nesse cubo — por isso existe um arquivo SI-PNI separado. |
| **SI-PNI** (vacinação) | **Duas fontes com metodologias diferentes**, combinadas numa planilha | Ver detalhe abaixo — é o caso mais delicado de todos |
| **CNES Recursos** (Leitos, Profissionais) | TabNet/DATASUS (CNES) | Variáveis de **estoque** (situação em 31/12), não de fluxo. "Profissionais" conta vínculos, não pessoas físicas (um profissional com 2 empregos conta 2x) |
| **CNES Dados Adicionais** (7 abas) | TabNet/DATASUS (CNES) | Ver detalhe abaixo |
| **COVID-19** | MS (covid.saude.gov.br) + compilação do projeto `wcota/covid19br` | Ver detalhe abaixo |
| **Bases Indisponíveis** | — | Não é dado, é documentação de cubos TabNet checados e confirmados como indisponíveis no período (HIPERDIA/SISVAN migraram para e-SUS APS/SISAB, não público via TabNet legado; dados financeiros do SUS 2007+ não têm cubo TabNet público — exigiria portal FNS ou SIOPS) |

**SI-PNI — o caso mais delicado**: a planilha combina dois períodos com fontes e metodologias diferentes, e isso é importante para qualquer análise de série temporal que cruze 2022/2023:
- **2015-2022**: cubo TabNet legado (`bd_pni/cpnibr.def`/`dpnibr.def`). Nesta sessão de extração, o acesso automatizado a esse cubo retornou consistentemente "Nenhum dado disponível" (a base estava em revisão, segundo aviso oficial do DATASUS). Os dados vieram de uma **exportação manual feita pelo usuário via navegador** (02/08/2026) — não puderam ser validados/atualizados de forma automatizada na mesma sessão.
- **2023-2026**: painel novo "Vacinação do Calendário Nacional" (infoms.saude.gov.br, metodologia RNDS), que substituiu o TabNet a partir de 2023.
- **Nomenclatura de vacina não foi unificada entre os dois períodos** (ex.: `"Meningococo C"` no período legado vs. `"Meningocócica Conjugada"` no painel novo) — a planilha de origem manteve os nomes exatamente como cada fonte reportou, deliberadamente, para não introduzir suposição de equivalência.
- A coluna `Media_Periodo_SemRotulo` (aba "Cobertura 2015-2022") tem significado **incerto** — o próprio TabNet gera essa coluna quando múltiplos anos são selecionados, mas não é possível confirmar se é média simples, ponderada, ou outro cálculo.
- Valores de cobertura vacinal **acima de 100%** (ex. BCG em alguns anos) são o que o TabNet retornou originalmente — provavelmente reflexo de estimativa populacional (denominador do SINASC) desatualizada/revisada, não erro de extração nem do nosso ETL.

**CNES Dados Adicionais — notas que valem saber**: a aba "Recursos Humanos - Ocupações" só tem snapshots de dezembro (não a série mensal completa como as outras abas do mesmo arquivo) porque consultas com todos os ~230 meses causaram **timeout repetido no servidor do TabNet** na extração original — ou seja, a granularidade "só dezembro" que documentamos como decisão nossa (§4.2.5 do corpo principal) coincide com uma limitação que já vinha do lado da fonte para esse sub-cubo específico. Também há um **gap de dados conhecido entre mai/2010 e jan/2012** no sub-cubo de Serviço/Classificação, e uma **anomalia registrada em ago/2025** no sub-cubo de Equipamentos (todos os grupos zerados exceto Diálise) — ambos comportamento real do TabNet, não falha nossa. A codificação de códigos de equipe de saúde mudou ao longo do tempo (`24 ESF1`/`27 ESF2` até ~2020, `70 ESF`/`71 ESB` depois) e foi mantida sem unificação na fonte original — mesma filosofia que aplicamos para `FaixaEtaria` (§4.2.2): não forçar equivalência sem certeza.

**COVID-19 — descontinuidade de série conhecida**: a série termina em março/2023 porque o Ministério da Saúde mudou a metodologia do painel oficial para periodicidade semanal a partir de 18/03/2023, descontinuando a série diária/mensal por município que esta planilha usa. Existe um dado de referência mais recente (snapshot de 08/09/2025 do painel oficial: 116.952 casos / 3.320 óbitos acumulados) que diverge do acumulado de mar/2023 nesta planilha (111.410/3.240) — a diferença reflete tanto a continuidade real da pandemia quanto revisões/consolidações feitas pelo MS depois de março/2023. Isso já é coerente com a decisão de janela temporal do nosso sistema (§3): mesmo sem esse corte de metodologia, COVID só teria dado de 2020-2023 mesmo.

---

## 2. Infraestrutura alterada

| Arquivo | O que mudou |
|---|---|
| `backend/etl/orcamento_loader.py` | **Novo.** Lê `Dados/orcamento/`, substitui `siops_loader.py`. |
| `backend/etl/saude_indicadores_loader.py` | **Novo.** Lê `Dados/Sorocaba_DATASUS_2015-2025/`, substitui `datasus_loader.py` + PySUS. |
| `backend/etl/siops_loader.py` | **Removido.** |
| `backend/etl/datasus_loader.py` | **Removido.** |
| `backend/etl/seed_data.py` | **Removido** (fallback hardcoded de COVID — dado real já vem do xlsx agora). |
| `backend/etl/detect_years.py` | **Removido** (só existia para extrair ano de metadados de planilha FNS, que não existe mais). |
| `backend/requirements.txt` | Removida a dependência `pysus`. |
| `backend/run_etl.py` | Reescrito: chama `orcamento_loader.load()` e `saude_indicadores_loader.load()` em sequência; não depende mais de flags de download/cache do PySUS. |
| `backend/entrypoint.sh` | Reescrito: chama os dois loaders novos diretamente (leitura local é rápida, não precisa mais da distinção "cache-only vs. download" que existia para evitar OOM/rede dentro do container). |
| `docker-compose.yml` | Adicionado volume `./Dados:/Dados:ro` e env var `DADOS_DIR=/Dados` no serviço `backend` — **necessário** porque o `Dockerfile` só copia `backend/` para dentro da imagem; sem esse mount, `Dados/` (que fica na raiz do repo) não existiria dentro do container. |
| `backend/.env` | **Criado** (não existia, só `.env.example`). Credenciais de desenvolvimento local, consistentes com o default do `docker-compose.yml` (`neo4j/your_password_here`). Está no `.gitignore`, não é versionado. |

Ambos os loaders resolvem o caminho de `Dados/` via variável de ambiente `DADOS_DIR`, com fallback para o caminho relativo (`backend/etl/../../../Dados`) quando rodando localmente fora do Docker — o mesmo padrão de auto-detecção que o projeto já usava para `NEO4J_URI`.

## 3. Janela temporal oficial do sistema

**Todos os dados — orçamento e saúde — são restritos a 01/01/2016 a 31/12/2025 (10 anos).** Qualquer registro fora dessa janela é descartado **no ETL** (não em tempo de consulta), decisão tomada explicitamente durante a sessão. Implementado como filtro por linha em ambos os loaders (`PERIODO_ANO_MIN=2016`, `PERIODO_ANO_MAX=2025`).

Isso importa porque os arquivos de origem extrapolam a janela: os CSVs de orçamento têm nomes de pasta como `2015-2019` (mas os dados reais de `Data Empenho` começam em 2016 — nenhuma linha foi de fato descartada aí), e os arquivos DATASUS têm nomes como `..._2015-2025.xlsx` e `..._2015-2026.xlsx` (SI-PNI) — esses **sim** tiveram linhas descartadas (ano 2015 nos extratos DATASUS, ano 2026 nas doses do SI-PNI).

**Exceção conhecida**: `OrcamentoPrograma` (ver §4.1.9) não tem coluna de ano por linha — o grão é o bloco plurianual inteiro do arquivo de origem. Não há como aplicar o filtro com precisão aí; os três blocos (`"2015-2019"`, `"2020-2022"`, `"2023-2025"`) foram mantidos integralmente, sabendo que o primeiro nominalmente inclui 2015.

---

## 4. Modelo de dados Neo4j — estrutura final

Estado real do banco após o ETL completo (validado nesta sessão):

**57.951 nós, 321.127 relacionamentos, 20 constraints de unicidade** (após a adição da dimensão `Aplicacao` — ver §4.1.5).

### 4.1 Domínio Orçamento

```
(:Empenho)-[:DO_FORNECEDOR]->(:Fornecedor)
(:Empenho)-[:FINANCIADO_POR]->(:FonteRecurso)
(:Empenho)-[:CLASSIFICADO_EM]->(:Subfuncao)
(:Empenho)-[:APLICADO_EM]->(:Aplicacao)
(:Empenho)-[:EXECUTA_ACAO]->(:Acao)-[:PARTE_DE]->(:ProgramaGoverno)
(:Empenho)-[:DA_UNIDADE]->(:UnidadeOrcamentaria)
```

#### 4.1.1 `Empenho` — 52.904 nós

Nó granular, **1 por linha do CSV de empenho** (não agregado). Fonte: `Dados/orcamento/{periodo}/*_empenhado_*.csv`.

| Propriedade | Tipo | Exemplo | Observação |
|---|---|---|---|
| `id` | string | `"2015-2019:0"` | Chave de MERGE = `"{periodoOrigem}:{índice da linha no arquivo}"` — não usa `Nro Empenho`/`Nro Processo` porque esses **se repetem** entre linhas (parcelas/estornos do mesmo empenho ao longo do tempo) |
| `nroEmpenho`, `nroProcesso` | string | `"00001-01"`, `"G00033/2015"` | Como vêm da planilha |
| `dataEmpenho` | date (Neo4j Date) | `2016-01-04` | Parseado de `DD/MM/YYYY` |
| `ano`, `mes` | int | `2016`, `1` | Derivados de `dataEmpenho` |
| `descricao` | string | | |
| `funcao` | string | sempre `"Saude"` | |
| `subfuncaoCodigo` | int | `301` | Ver mapeamento §5.2 |
| `subfuncaoNome` | string | `"Atenção Básica"` | |
| `natureza` | string | `"3.3.90.30.00-Material de Consumo"` | Natureza de despesa (classificação orçamentária padrão) |
| `modalidadeLicitacao` | string | | |
| `valorEmpenhoOriginal`, `valorEstorno`, `saldoEmpenho`, `valorProcessado`, `valorPago` | float | | Valores em R$, já em formato numérico direto no CSV (não precisou conversão de formato brasileiro) |
| `periodoOrigem` | string | `"2015-2019"` | Qual dos 3 blocos de arquivo originou o registro |
| `fonte` | string | sempre `"lai_transparencia"` | |
| `importedAt` | string ISO 8601 | | |

**Soma total de `valorProcessado` no banco: R$ 7.153.638.568,10** (52.904 empenhos, 2016-2025).

#### 4.1.2 `Fornecedor` — 2.170 nós

Chave de MERGE: `documento` (CNPJ **ou** CPF). Descoberto durante a implementação: ~15-20% dos empenhos são para **pessoa física** (CPF — prestadores individuais, ex. home care), não só empresas — o parser original só reconhecia CNPJ e foi corrigido.

| Propriedade | Observação |
|---|---|
| `documento` | CNPJ (`99.999.999/9999-99`) ou CPF (`999.999.999-99`) extraído por regex do campo `Fornecedor` do CSV (que vem com HTML solto: `<b>`, `<br>`, e o documento entre parênteses no final). Fallback `"SEM_DOC:{nome}"` quando nenhum documento é extraído (casos legítimos: folha de pagamento agregada, órgãos públicos sem CNPJ citado no texto — 17 casos no total) |
| `tipoDocumento` | `cnpj` \| `pessoa_fisica` \| `desconhecido` |
| `nome` | Nome limpo (tags HTML removidas) |

Distribuição real: 1.622 CNPJ, 531 pessoa física (CPF), 17 desconhecido.

#### 4.1.3 `FonteRecurso` — 13 nós

Chave de MERGE: `nome` (texto **literal** da planilha — variantes de redação entre períodos **não são unificadas** propositalmente, porque `"...vinculados"` vs. `"...vinculados-exec.anter."` marca uma distinção contábil real: recurso do exercício corrente vs. recurso vinculado carregado de exercício anterior).

| Propriedade | Valores |
|---|---|
| `nome` | Texto original da planilha (13 variantes distintas encontradas — ver tabela completa abaixo) |
| `tipoRecurso` | `proprio` \| `federal` \| `estadual` \| `credito` \| `emenda_parlamentar` \| `outros` |

Todas as 13 fontes reais e sua classificação:

| tipoRecurso | Textos mapeados |
|---|---|
| `proprio` | `Tesouro`, `Rec.prop.de Fdos Especiais de Despesa-vinc.-ex.ant`, `Rec.prop.de Fdos Especiais de Despesa-vinculados`, `Rec. Prop. Fundos Especiais de Despesa-vinculados` |
| `federal` | `Transf.e Convenios Federais-vinculados-exec.anter.`, `Transferencias e Convenios Federais - Vinculados` |
| `estadual` | `Transf.e Convenios Estaduais-vinculados-exec.ant.`, `Transferencias e Convenios Estaduais - Vinculados` |
| `credito` | `Operacoes de Credito` |
| `emenda_parlamentar` | `Emendas Parlamentares Individuais`, `Emendas Parlamentares Individuais - Leg. Municipal` |
| `outros` | `Outras Fontes de Recursos`, `Outras Fontes de Recursos - Exercicios Anteriores` |

**Nota sobre `emenda_parlamentar`**: decisão explícita de **não** forçar essa categoria em própria/federal/estadual — há incerteza genuína (a variante `"- Leg. Municipal"`, que só aparece em 2023-2025, sugere emenda de vereador = recurso do próprio orçamento municipal, mas as variantes sem qualificador dos períodos anteriores podem ser de deputado/senador = repasse federal real). Fica como categoria própria, explícita, em vez de uma classificação arriscada.

#### 4.1.4 `Subfuncao` — 7 nós

| Código | Nome |
|---|---|
| 122 | Administração Geral |
| 301 | Atenção Básica |
| 302 | Assistência Hospitalar e Ambulatorial |
| 303 | Suporte Profilático e Terapêutico |
| 304 | Vigilância Sanitária |
| 305 | Vigilância Epidemiológica |
| 306 | Alimentação e Nutrição |

**Achado importante**: a subfunção **122 não existe nos empenhos do período 2015-2019** — só passa a ser usada a partir de 2020. Isso explica uma variação grande e real nos dados: o gasto próprio classificado em 301 (Atenção Básica) salta de ~R$16M em 2016 para ~R$355M em 2018, porque itens de folha de pagamento e obrigações patronais que em anos posteriores são segregados em 122 ficavam concentrados em 301 antes de 2020. **Não é bug do ETL** — é mudança real de prática de classificação orçamentária municipal ao longo do tempo. Relevante para qualquer análise de série temporal de 301 que cruze o limite 2019/2020.

#### 4.1.5 `Aplicacao` — 333 nós

Chave de MERGE: `nome`. Vem da coluna `Aplicação` do CSV — descoberta tardia: essa coluna existia na fonte desde o início mas não estava sendo persistida. É a dimensão **mais específica** que os dados de empenho oferecem sobre a finalidade do gasto (bem mais granular que `subfuncaoCodigo` ou `natureza`, que só classificam por área de política e por grupo econômico de despesa, respectivamente) — inclui blocos de financiamento nomeados como `"Bloco 01 - Assistencia Farmaceutica"`, `"Covid-19/resolucao Ss 82/2021-IMUNIZ.COVID-19"`, `"Atencao Basica"`. Ainda não é "item de compra" (não distingue medicamento A de medicamento B), mas já permite isolar gasto por programa/finalidade específica — ex. Assistência Farmacêutica soma ~R$51M no período, antes indistinguível dentro de "Material de Consumo".

`(:Empenho)-[:APLICADO_EM]->(:Aplicacao)` — relacionamento 1:1 por empenho (cada empenho tem exatamente uma aplicação).

#### 4.1.6 `ProgramaGoverno` — 4 nós, `Acao` — 904 nós

Dimensões de programa/ação orçamentária, extraídas do campo `Ação` do CSV (formato `"2109-Atencao Primaria em Saude"` → código antes do primeiro hífen, nome depois). Quando o texto não tem hífen, vira só nome (`acaoCodigo` fica com fallback `"SEM_CODIGO:{nome}"`).

#### 4.1.7 `UnidadeOrcamentaria` — 1 nó

Só `"Secr.da Saude"` aparece nos dados (mantido como dimensão para eventual expansão futura a outras secretarias).

#### 4.1.8 `DespesaAnual` — 53 nós (agregado materializado)

Calculado a partir de `Empenho` **no fim do ETL** via uma agregação Cypher (não em pandas) — grão: `(subfuncaoCodigo, ano)`. Papel equivalente ao antigo `DespesaSIOPS`, mas a partir de execução real em vez de repasse federal.

| Propriedade | Observação |
|---|---|
| `subfuncaoCodigo`, `subfuncaoNome`, `ano` | Chave lógica (índice composto, não constraint de unicidade — Neo4j Community Edition) |
| `valorProcessado`, `valorPago`, `valorEmpenhado` | Somas de `Empenho` |
| `valorProprio`, `valorFederal`, `valorEstadual`, `valorCredito`, `valorEmendaParlamentar`, `valorOutros` | Breakdown por `tipoRecurso` — **soma exatamente igual** ao `valorProcessado` total (validado: diferença = 0,0000 em toda a base) |
| `fonte`, `importedAt` | |

Este breakdown por `tipoRecurso` é o que resolve a limitação documentada do modelo antigo (dado FNS = só repasse federal recebido, ~50-70% do gasto real de fora). Agora dá para separar recurso próprio municipal de repasse em qualquer consulta.

**Nota**: o relacionamento `VARIACAO_ANUAL` (encadear `DespesaAnual` de anos consecutivos com a variação percentual já calculada) foi desenhado e aprovado durante a sessão, mas **não foi implementado** — fica para execução futura (ver `PLANO_NOVO_MODELO_DADOS.md` §3.1/§5).

#### 4.1.9 `OrcamentoPrograma` — 941 nós

Fonte: `Dados/orcamento/{periodo}/*_previsto_e_empenhado_por_programa_e_secretaria_*.csv`. Dado **novo**, que não existia no modelo antigo — permite análise de execução orçamentária (previsto vs. realizado).

| Propriedade | Observação |
|---|---|
| `programa`, `acao`, `orgao` | Texto livre do CSV (não vinculado aos nós `ProgramaGoverno`/`Acao` de §4.1.6 — formatos de texto não batem entre os dois CSVs, ligação exigiria fuzzy-matching não implementado) |
| `periodoOrigem` | `"2015-2019"` \| `"2020-2022"` \| `"2023-2025"` — **grão é o bloco inteiro, não ano individual** (o CSV de origem não tem coluna de ano) |
| `valorPrevisto`, `valorRealizado` | |

943 linhas no CSV → 941 nós (2 pares de linhas com chave idêntica colapsaram via MERGE — comportamento esperado, não é perda de dado).

#### 4.1.10 Constraints do domínio orçamento

```
fornecedor_documento     UNIQUE (Fornecedor.documento)
subfuncao_codigo         UNIQUE (Subfuncao.codigo)
fonte_recurso_nome       UNIQUE (FonteRecurso.nome)
aplicacao_nome           UNIQUE (Aplicacao.nome)
programa_governo_nome    UNIQUE (ProgramaGoverno.nome)
acao_codigo              UNIQUE (Acao.codigo)
unidade_orcamentaria_nome UNIQUE (UnidadeOrcamentaria.nome)
empenho_id               UNIQUE (Empenho.id)
```
(mais 2 índices compostos não-unique: `DespesaAnual(subfuncaoCodigo, ano)` e `OrcamentoPrograma(periodoOrigem)` — Neo4j Community Edition não suporta constraint de unicidade composta, só single-property)

---

### 4.2 Domínio Indicadores de Saúde

```
(:IndicadorSaude)-[:POR_FAIXA_ETARIA]->(:FaixaEtaria)
(:IndicadorSaude)-[:POR_CAPITULO_CID]->(:CapituloCID10)
(:IndicadorSaude)-[:POR_SEXO]->(:Sexo)
(:IndicadorSaude)-[:POR_FAIXA_PESO]->(:FaixaPeso)
(:IndicadorSaude)-[:COBERTURA_VACINAL]->(:Vacina)
(:IndicadorSaude)-[:DOSES_APLICADAS]->(:Vacina)
(:IndicadorSaude)-[:POR_TIPO_ESTABELECIMENTO]->(:TipoEstabelecimento)
(:IndicadorSaude)-[:POR_OCUPACAO]->(:OcupacaoProfissional)
(:IndicadorSaude)-[:POR_TIPO_EQUIPE]->(:TipoEquipe)
(:IndicadorSaude)-[:POR_TIPO_ATENDIMENTO]->(:TipoAtendimento)
(:IndicadorSaude)-[:POR_TIPO_LEITO_CONSULTORIO]->(:TipoLeitoConsultorio)
(:IndicadorSaude)-[:POR_TIPO_EQUIPAMENTO]->(:TipoEquipamento)
```

11 tipos de relacionamento (o plano original previa 7 — a diversidade real de sub-cubos do CNES era maior do que a modelagem inicial havia antecipado). Toda relação de quebra dimensional carrega uma única propriedade `valor` (float) — o tipo da relação já indica a semântica (contagem, percentual, quantidade).

#### 4.2.1 `IndicadorSaude` — 346 nós (fato central)

Fonte: `Dados/Sorocaba_DATASUS_2015-2025/*.xlsx` (8 arquivos, exceto `Bases_Indisponiveis` que só tem notas). Grão: `(sistema, subtipo, ano, mes)` — `mes` é `null` para todo sistema anual, só é populado para COVID mensal (único caso de granularidade sub-anual no banco).

| Propriedade | Observação |
|---|---|
| `chave` | `"{sistema}:{subtipo}:{ano}:{mes ou 0}"` — chave de MERGE, constraint de unicidade real (single-property) |
| `sistema` | `sim`\|`sih`\|`sinan`\|`sipni`\|`sinasc`\|`sia`\|`cnes`\|`covid` — **partição usada pelos 8 agentes de saúde da arquitetura (1 agente por Sistema de Informação)** |
| `subtipo` | Ver tabela completa abaixo |
| `ano`, `mes` | `mes` é `null` exceto para COVID mensal |
| `valorTotal` | Nem sempre é uma soma das quebras dimensionais — usa o valor de `Total` da própria planilha quando existe (mais confiável que recalcular, já que somar categorias nem sempre é semanticamente correto — ex. um estabelecimento pode oferecer múltiplos tipos de atendimento, somar "dobraria" a contagem); fica `null` quando a planilha não tem `Total` e somar não faria sentido |
| `valorAcumulado` | Só usado por COVID (`casos_acumulado`/`obitos_acumulado` desde o início da pandemia) |
| `fonte` | sempre `"datasus_tabnet"` |

**`subtipo` por sistema** (o que cada um dos 8 agentes de saúde vai encontrar):

| sistema | subtipos |
|---|---|
| `sim` | `mortalidade` |
| `sih` | `internacoes` |
| `sinan` | `dengue`, `chikungunya`, `sifilis_adquirida`, `sifilis_gestante`, `sifilis_congenita`, `coqueluche`, `hepatites_virais`, `tuberculose`, `hanseniase` (9 doenças, todas sob o mesmo agente/sistema) |
| `sipni` | `cobertura_vacinal`, `doses_aplicadas` |
| `sinasc` | `nascidos_vivos` |
| `sia` | `producao_ambulatorial` |
| `covid` | `casos`, `obitos` (cada um existe em grão anual **e** mensal — `mes=null` vs. `mes` populado) |
| `cnes` | `leitos`, `profissionais`, `estabelecimentos_por_tipo`, `ocupacoes`, `equipes_saude`, `tipo_atendimento`, `leitos_consultorios`, `equipamentos`, `estabelecimentos_nivel_atencao`, `estabelecimentos_servico_classificacao`, `estabelecimentos_habilitacao`, `estabelecimentos_vigilancia_epidemiologica` (12 subtipos — CNES é, de longe, o sistema com mais sub-cubos) |

Faixa de anos por sistema, real, pós-filtro 2016-2025:

| sistema | min | max | n nós |
|---|---|---|---|
| cnes | 2016 | 2025 | 120 |
| covid | 2020 | 2023 | 82 |
| sia | 2016 | 2025 | 10 |
| sih | 2016 | 2025 | 10 |
| sim | 2016 | 2025 | 10 |
| sinan | 2016 | 2025 | 84 |
| sinasc | 2016 | 2025 | 10 |
| sipni | 2016 | 2025 | 20 |

COVID só cobre 2020-2023 porque é o que a fonte (`Sorocaba_COVID19_Casos_Obitos_2020-2023.xlsx`) realmente tem — não é um bug de filtro.

#### 4.2.2 `FaixaEtaria` — 40 nós (decisão: **não unificada** entre sistemas)

Chave de MERGE: `chave = "{sistema}:{nome}"` — **cada sistema tem seu próprio espaço de faixas etárias**, decisão tomada depois de constatar que unificar seria enganoso: SIM usa faixas de 10 em 10 anos, SINAN dengue mistura faixas de 20 em 20 (`20-39`, `40-59`), e mais importante — **SINASC mede idade da mãe no parto**, não a idade da pessoa do registro, um atributo conceitualmente diferente, não só um bin diferente. Sem acesso a microdado bruto (só temos os extratos TabNet já agregados), re-binning para uma faixa canônica seria uma aproximação artificial. Usado por: `sim` (óbitos), `sinasc` (nascimentos, mas escopado por sistema — é idade da mãe), `sinan` (7 das 9 doenças).

#### 4.2.3 `CapituloCID10` — 21 nós (decisão: **unificado**, é o ponto de correlação real)

Chave de MERGE: `codigo` (formato `"01"`-`"21"`, canônico, **arábico**). Ao contrário da faixa etária, este dimensão **precisava** ser unificada — é o que permite cruzar mortalidade (SIM) e internação (SIH) pela mesma causa. Problema real resolvido: SIM usa numeração **romana** nos cabeçalhos (`"Cap IX(Circulatorio)"`) e SIH usa numeração **decimal** (`"Cap09(Circulatorio)"`) — ambos convertidos para o mesmo código canônico via parser de numeral romano dedicado (dicionário fixo I-XXI, não é um conversor genérico).

`nome` vem de um dicionário canônico próprio (não do texto abreviado da planilha, que varia entre os dois sistemas — ex. SIM usa `"Gravidez"`, SIH usa `"GravidezParto"` para o mesmo capítulo XV).

SIM usa os capítulos 01-18 e 20 (não usa 19 nem 21 — convenção real do cubo de mortalidade do TabNet). SIH usa todos os 21.

#### 4.2.4 `Sexo` — 2 nós, `FaixaPeso` — 8 nós, `Vacina` — 106 nós

- `Sexo {chave: nome}`: `Masculino`, `Feminino`. Usado por SINAN tuberculose/hanseníase.
- `FaixaPeso {chave: nome}`: peso ao nascer (SINASC), ex. `"Menos de 500g"`, `"3000 a 3999g"`.
- `Vacina {chave: nome}`: SI-PNI. `IndicadorSaude.valorTotal` para `cobertura_vacinal` = **média** da cobertura % de todas as vacinas naquele ano (métrica-resumo); para `doses_aplicadas` = **soma** das doses de todas as vacinas (essa sim uma contagem real agregável). O detalhe por vacina está sempre nas relações `COBERTURA_VACINAL`/`DOSES_APLICADAS`.

#### 4.2.5 Dimensões do CNES — `TipoEstabelecimento` (26), `OcupacaoProfissional` (20), `TipoEquipe` (15), `TipoAtendimento` (5), `TipoLeitoConsultorio` (6), `TipoEquipamento` (10)

Todas chaveadas por `chave = nome`, sem escopo por sistema (CNES é o único sistema que usa cada uma). `TipoEquipe`, `TipoAtendimento`, `TipoLeitoConsultorio` e `TipoEquipamento` vêm de sub-cubos **mensais** do CNES (`Ano/Mês`, ex. `"2015/Jan"`) — decisão tomada na sessão: **agregados para o snapshot de dezembro** de cada ano, e não a granularidade mensal completa, para manter grão anual uniforme com o resto do CNES (que já é nativamente anual/dezembro).

#### 4.2.6 Constraints do domínio saúde

```
indicador_saude_chave         UNIQUE (IndicadorSaude.chave)
faixa_etaria_chave            UNIQUE (FaixaEtaria.chave)
capitulo_cid10_codigo         UNIQUE (CapituloCID10.codigo)
sexo_chave                    UNIQUE (Sexo.chave)
faixa_peso_chave              UNIQUE (FaixaPeso.chave)
vacina_chave                  UNIQUE (Vacina.chave)
tipo_estabelecimento_chave    UNIQUE (TipoEstabelecimento.chave)
ocupacao_profissional_chave   UNIQUE (OcupacaoProfissional.chave)
tipo_equipe_chave             UNIQUE (TipoEquipe.chave)
tipo_atendimento_chave        UNIQUE (TipoAtendimento.chave)
tipo_leito_consultorio_chave  UNIQUE (TipoLeitoConsultorio.chave)
tipo_equipamento_chave        UNIQUE (TipoEquipamento.chave)
```

---

## 5. Como o ETL funciona — `orcamento_loader.py`

**Comando**: `python -m etl.orcamento_loader` (ou via `run_etl.py`/`entrypoint.sh`, que o chamam automaticamente).

### 5.1 Fluxo

1. Garante as constraints (§4.1.9) via `CREATE CONSTRAINT IF NOT EXISTS`.
2. Itera `Dados/orcamento/{2015-2019,2020-2022,2023-2025}/*.csv`.
3. Para cada arquivo, decide o tipo pelo nome (`"previsto_e_empenhado"` no nome → `OrcamentoPrograma`; senão → `Empenho`).
4. Lê com pandas (`sep="|"`, `encoding="latin-1"` — os CSVs usam pipe como delimitador e não são UTF-8).
5. Para `Empenho`: filtra a janela 2016-2025, mapeia subfunção/fonte de recurso/fornecedor/ação, monta os registros, persiste em lotes de 2.000 via `UNWIND` (todas as dimensões + o nó `Empenho` + todos os relacionamentos numa única query Cypher por lote).
6. Ao final, roda **uma agregação Cypher** (`MATCH (e:Empenho)... WITH ... MERGE (d:DespesaAnual)...`) que recalcula `DespesaAnual` inteiro a partir do que está em `Empenho` — não é acumulado incrementalmente durante a leitura do CSV, é uma passada de agregação sobre o grafo já carregado.

### 5.2 Mapeamentos aplicados

- **Subfunção**: texto da planilha (`"Atencao Basica"`, sem acento) → código padrão 122/301-306 (tabela fixa, 7 entradas, validada contra os ~53 mil registros reais sem nenhum caso não mapeado).
- **Fonte de recurso → tipoRecurso**: tabela fixa de 13 entradas (§4.1.3), com fallback `"outros"` + log de aviso para qualquer texto não reconhecido (não ocorreu nos dados reais).
- **Fornecedor**: regex extrai CNPJ (`\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}`) ou, se não achar, CPF (`\d{3}\.\d{3}\.\d{3}-\d{2}`); nome é o texto remanescente com tags HTML removidas.
- **Ação**: `"2109-Atencao Primaria em Saude"` → código antes do primeiro hífen, nome depois. Casos sem hífen viram só nome.
- **Aplicação**: persistida como está, sem mapeamento — vira o nó `Aplicacao` (§4.1.5). Todas as 19 colunas do CSV de empenho são carregadas — nenhuma é descartada (conferido coluna a coluna contra o header real do arquivo).

### 5.3 Idempotência

Todo o loader usa `MERGE`, nunca `CREATE`. Rodar `run_etl.py` várias vezes seguidas produz exatamente os mesmos nós/relacionamentos — **validado nesta sessão** (rodei duas vezes, contagem de nós idêntica: 52.904/941/53).

---

## 6. Como o ETL funciona — `saude_indicadores_loader.py`

**Comando**: `python -m etl.saude_indicadores_loader`.

### 6.1 Quatro formatos de planilha, quatro leitores genéricos

As planilhas TabNet seguem um padrão comum (linha 0 = título, linha 1 = vazia, linha 2 = cabeçalho real, linha 3+ = dados), mas se dividem em 4 formatos que exigiram leitores diferentes:

| Formato | Leitor | Onde aparece |
|---|---|---|
| **Ano simples, categorias em colunas, `Total` opcional** | `_read_wide_sheet(mensal=False)` | Maioria das planilhas (SIM, SIH, SINASC, SINAN, CNES-Ocupações, CNES-Estabelecimentos-por-Tipo) |
| **`Ano/Mês` em linhas (ex. `"2015/Jan"`), categorias em colunas** | `_read_wide_sheet(mensal=True)` | 5 sub-cubos do CNES — filtra só as linhas de **Dezembro** (decisão de agregação, §4.2.5) |
| **Ano + 1 única coluna de valor** | `_read_single_valor_sheet` | SIA (produção ambulatorial), CNES-Leitos, CNES-Profissionais |
| **Transposto: vacina em linhas, ano em colunas** | `_read_vacina_transposta` | SI-PNI (único caso — layout invertido em relação a todo o resto) |

Mais um caso especial: **COVID** tem seu próprio par de leitores (`_read_covid_anual`, `_read_covid_mensal`) porque a planilha traz 2 métricas emparelhadas com seus acumulados (`Casos_Novos`+`Casos_Acumulado_Fim_Ano`, idem óbitos) — não é uma quebra dimensional, é uma estrutura própria.

### 6.2 Tratamento de linhas inválidas

Todos os leitores tentam parsear a célula da chave (ano ou ano/mês) e simplesmente **pulam a linha** (`continue`) quando falha — isso descarta automaticamente linhas em branco, linhas de nota/rodapé, e linhas de totalização tipo `"TOTAL (soma - nao usar p/ acumulados)"` (existe literalmente uma linha assim na planilha de COVID anual, com esse aviso explícito da própria fonte).

### 6.3 Fluxo por sistema

Uma função `_load_X` por sistema (`_load_sim`, `_load_sih`, ..., `_load_cnes`), cada uma:
1. Lê a(s) planilha(s) relevante(s) com o leitor apropriado.
2. Converte para linhas de `IndicadorSaude` + linhas de quebra dimensional (helpers `_wide_to_rows`, `_wide_to_rows_capitulo`, `_vacina_to_rows`).
3. Persiste via 2 queries Cypher genéricas parametrizadas por lote de 2.000: uma para `IndicadorSaude` (`MERGE` por `chave`), outra para cada tipo de quebra dimensional (`MATCH` no `IndicadorSaude` já existente + `MERGE` no nó de dimensão + `MERGE` do relacionamento).

Quando um sistema tem mais de uma planilha que descreve o **mesmo** fato (ex. SIM: "Óbitos por Faixa Etária" e "Óbitos por Capítulo CID-10" são duas quebras diferentes do mesmo total de óbitos), ambas convergem para o **mesmo nó** `IndicadorSaude` (mesma `chave = sistema:subtipo:ano:mes`), cada uma só adicionando seu próprio tipo de relacionamento de quebra — não há duplicação.

### 6.4 CNES — o sistema com mais complexidade interna

3 arquivos, 10 abas, 12 `subtipo`s diferentes — de longe o mais heterogêneo:
- 2 fatos de valor único (`leitos`, `profissionais`)
- 1 quebra por tipo de estabelecimento
- 1 quebra por ocupação profissional
- 4 sub-cubos mensais → dezembro (equipes de saúde, tipo de atendimento, leitos/consultórios, equipamentos)
- 4 métricas independentes sem quebra dimensional, vindas de uma única aba ("Estabelecimentos" — nível de atenção, classificação de serviço, habilitação, vigilância epidemiológica/sanitária — são 4 contagens **diferentes**, não categorias de uma mesma grandeza, por isso viram 4 `subtipo`s ao invés de uma quebra dimensional)

---

## 7. Validações realizadas nesta sessão (com dado real)

| # | Validação | Resultado |
|---|---|---|
| 1 | ETL roda sem exceção (parsing isolado, sem gravar no banco) | 52.904 empenhos + 943 previsto/realizado processados, zero warning de mapeamento não coberto |
| 2 | ETL completo contra Neo4j real | 57.951 nós, 321.127 relacionamentos, sem erro |
| 3 | Idempotência (rodar 2x) | Contagens idênticas na segunda execução |
| 4 | Soma `valorProcessado` (Empenho) bate com soma dos `tipoRecurso` em `DespesaAnual` | Diferença = 0,0000 em toda a base |
| 5 | Nenhum registro fora da janela 2016-2025 | Confirmado via `MATCH (e:Empenho) WHERE e.ano < 2016 OR e.ano > 2025` → 0 |
| 6 | Query de correlação real (o propósito central do projeto) | `MATCH (d:DespesaAnual {subfuncaoCodigo:305})` × `MATCH (i:IndicadorSaude {sistema:'sinan', subtipo:'dengue', ano: d.ano})` funciona e revela um achado genuíno: dengue salta de ~1-4 mil casos/ano para **46.635 casos em 2024**, coincidindo com o gasto em vigilância epidemiológica também disparando para R$35M |
| 7 | Query multi-hop nova (capacidade que não existia no modelo antigo) | `MATCH (sim)-[:POR_CAPITULO_CID]->(cap)<-[:POR_CAPITULO_CID]-(sih)` cruza mortalidade × internações pelo **mesmo** capítulo CID-10 — testado para 2020: doenças circulatórias lideram tanto óbitos (1.163) quanto internações (6.200) |
| 8 | Achado sobre CPF em Fornecedor | Regex original só pegava CNPJ; ~15-20% dos empenhos são pessoa física — corrigido |
| 9 | Achado sobre subfunção 122 | Não existe nos dados antes de 2020 — explica salto grande em 301 no período 2016-2019 vs. 2020+ |
| 10 | Cobertura completa de colunas do CSV de empenho | Coluna `Aplicação` estava sendo lida (validada como existente) mas não persistida — corrigido, agora todas as 19 colunas do CSV viram propriedade ou dimensão no grafo. Nova dimensão `Aplicacao` (333 nós) permite isolar gasto por finalidade específica (ex. Assistência Farmacêutica: ~R$51M, antes diluído em "Material de Consumo") |

---

## 8. Limitações conhecidas (permanecem em aberto)

1. **`OrcamentoPrograma` não é filtrável com precisão pela janela 2016-2025** — grão é o bloco plurianual do arquivo de origem, sem coluna de ano por linha.
2. **`FaixaEtaria` não é comparável entre sistemas** — decisão deliberada (ver §4.2.2), mas significa que "faixa etária X" em SIM e em SINASC não são a mesma coisa e não devem ser somadas/comparadas ingenuamente.
3. **`OrcamentoPrograma.programa`/`.acao` não estão ligados aos nós `ProgramaGoverno`/`Acao`** de `Empenho` — os textos não batem entre os dois CSVs de origem; uma ligação exigiria fuzzy-matching, não implementado.
4. **Classificação de `emenda_parlamentar`** como própria vs. federal não foi resolvida — ficou como categoria própria em vez de forçar uma classificação incerta (ver §4.1.3).
5. **`VARIACAO_ANUAL`** (relacionamento entre anos consecutivos de `DespesaAnual` com variação percentual pré-calculada) foi desenhado mas não implementado — guardado para execução futura.
6. **CNES mensal foi agregado para dezembro** — quem precisar de granularidade mensal completa de equipes de saúde, tipo de atendimento, leitos/consultórios ou equipamentos vai precisar reprocessar a partir do xlsx original (o dado mensal não está no Neo4j, só o snapshot de dezembro).
7. **Dados de 2025 são preliminares** em SIM, SIH, SINASC e Rede Assistencial CNES (3ª prévia oficial do DATASUS, sujeita a atualização) — carregados no Neo4j como estão, mas qualquer conclusão do TCC que dependa fortemente do ano 2025 nesses 4 sistemas deve sinalizar essa ressalva (ver §1.1.2).
8. **SI-PNI 2015-2022 não pôde ser validado/atualizado de forma automatizada na extração de origem** — veio de exportação manual porque o cubo TabNet legado estava indisponível na sessão de extração original, e a nomenclatura de vacina não é a mesma entre 2015-2022 e 2023-2026 (ver §1.1.2) — qualquer série temporal de SI-PNI que cruze 2022/2023 deve tratar os dois blocos como séries distintas, não contínuas.

## 9. Como reproduzir

```bash
# 1. Subir o Neo4j
docker compose up neo4j -d

# 2. Rodar o ETL completo (local, fora do container)
cd backend
python run_etl.py

# 3. Ou, individualmente:
python -m etl.orcamento_loader
python -m etl.saude_indicadores_loader
```

Requer `backend/.env` com `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` (o script local substitui automaticamente o hostname Docker `neo4j:` por `localhost:` quando detecta que não está rodando em container).

## 10. Próximos passos (fora do escopo desta sessão)

Ver `PLANO_NOVO_MODELO_DADOS.md` §6 e §7 para as notas de compatibilidade com a migração CoALA e a lista de decisões que ainda dependem de quem for implementar os agentes (compatibilidade de labels com `dispatch.py`, granularidade de agentes, etc.) — este documento aqui cobre só o que foi construído e validado em ETL/banco.
