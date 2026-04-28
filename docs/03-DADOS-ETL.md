# Fontes de Dados e Pipeline ETL

## Sumário

1. [Origens Institucionais dos Dados](#origens-institucionais-dos-dados)
2. [SIOPS — Despesas Municipais](#siops--despesas-municipais)
3. [DataSUS — Indicadores de Saúde](#datasus--indicadores-de-saúde)
4. [Pipeline ETL](#pipeline-etl)
5. [Modelo de Dados Neo4j](#modelo-de-dados-neo4j)

---

## Origens Institucionais dos Dados

O sistema integra dados de duas fontes públicas federais, ambas mantidas pelo Ministério da Saúde:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Ministério da Saúde                           │
│                                                                 │
│  ┌──────────────────────┐    ┌────────────────────────────────┐ │
│  │   FNS                │    │   DATASUS                      │ │
│  │   (Fundo Nacional    │    │   (Departamento de             │ │
│  │    de Saúde)         │    │    Informática do SUS)         │ │
│  │                      │    │                                │ │
│  │  Dados gerados por:  │    │  Sistemas:                     │ │
│  │  • Portarias do MS   │    │  • SINAN (Vigilância)          │ │
│  │  • Ordens bancárias  │    │  • SIM (Mortalidade)           │ │
│  │  • Sistema de gestão │    │  • SIH (Internações)           │ │
│  │    financeira FNS    │    │  • SI-PNI (Vacinação)          │ │
│  │                      │    │                                │ │
│  └──────────┬───────────┘    └──────────────┬─────────────────┘ │
└─────────────┼───────────────────────────────┼───────────────────┘
              │                               │
              ▼                               ▼
    Portal Consulta FNS               FTP DataSUS
    (consultafns.saude.gov.br)        (ftp.datasus.gov.br)
    Planilhas .xls/.xlsx              Arquivos .dbc (comprimidos)
              │                               │
              ▼                               ▼
         siops_loader.py              datasus_loader.py
              │                        download_pysus.py
              │                               │
              └───────────────┬───────────────┘
                              ▼
                         Neo4j (grafos)
```

---

## FNS — Repasses Federais Fundo a Fundo

### O que é

O **FNS** (Fundo Nacional de Saúde) é o gestor financeiro dos recursos do SUS no âmbito federal. Ele realiza as **transferências fundo a fundo** — repasses automáticos de recursos federais diretamente para os Fundos Municipais e Estaduais de Saúde, sem necessidade de convênio.

Os dados utilizados neste projeto são os **repasses federais detalhados** disponibilizados no portal de consulta do FNS: `https://consultafns.saude.gov.br/#/detalhada`

### Como funciona o financiamento fundo a fundo

O financiamento federal do SUS é organizado em **blocos de financiamento** (Portaria GM/MS nº 828/2020 e Portaria de Consolidação nº 6/2017), que agrupam os repasses por finalidade:

```
Ministério da Saúde (Orçamento Federal)
         │
         ▼
Fundo Nacional de Saúde (FNS)
         │
         │  Transferências automáticas (fundo a fundo)
         │  Sem convênio — regulares e obrigatórias
         ▼
┌─────────────────────────────────────────────────────┐
│         Fundo Municipal de Saúde (FMS)              │
│         (ex: FMS de Sorocaba)                       │
│                                                     │
│  Recursos recebidos por GRUPO:                      │
│  • Atenção Primária (Piso da APS)                   │
│  • Atenção Especializada (MAC - Média/Alta Complex.)│
│  • Assistência Farmacêutica                         │
│  • Vigilância em Saúde                              │
│  • Gestão do SUS                                    │
│  • Investimentos                                    │
└─────────────────────────────────────────────────────┘
```

### Quem alimenta os dados

Os dados de repasses são gerados pelo próprio FNS a partir de:

- **Portarias ministeriais** — definem os valores a serem repassados por município
- **Ordens bancárias** — registram as transferências efetivamente realizadas
- **Sistema de Gestão Financeira do FNS** — consolida todas as movimentações

O fluxo é:
1. O Ministério da Saúde publica portarias definindo valores por município/grupo
2. O FNS executa as transferências via ordens bancárias para os Fundos Municipais
3. Os dados são consolidados e disponibilizados no portal `consultafns.saude.gov.br`
4. Planilhas detalhadas podem ser exportadas em formato `.xls` ou `.xlsx`

### Como são coletados para este projeto

1. Acesso ao portal `https://consultafns.saude.gov.br/#/detalhada`
2. Filtro por município: Sorocaba-SP (IBGE 355220)
3. Seleção do período desejado (ex: 2019-2023)
4. Download da "Planilha Detalhada" em formato `.xls` ou `.xlsx`
5. A planilha é colocada em `backend/data/` para processamento pelo ETL

### Formato dos dados

- **Arquivo:** Planilha Excel (.xls ou .xlsx) — "Planilha Detalhada" do FNS
- **Estrutura:** Metadados nas primeiras 7 linhas (Município, Ano, IBGE), cabeçalho na linha 7-8, dados nas linhas seguintes
- **Colunas relevantes:** "Grupo" (nome do grupo de repasse) e "Valor Total" (em formato brasileiro: 1.234.567,89)
- **Granularidade:** Repasse por grupo por ano
- **Filtro:** Município de Sorocaba (IBGE 355220)
- **Natureza dos dados:** São repasses federais recebidos pelo município (não despesas executadas pelo município)

### Grupos de repasse mapeados para subfunções

Os grupos de repasse do FNS são mapeados para subfunções orçamentárias de saúde no sistema:

| Código subfunção | Nome | Grupos do FNS (planilha) |
|------------------|------|--------------------------|
| 301 | Atenção Básica | "Atenção Primária", "Atenção Básica" |
| 302 | Assistência Hospitalar | "Atenção de Média e Alta Complexidade", "MAC", "Atenção Especializada", "Coronavírus (COVID-19)" |
| 303 | Suporte Profilático | "Assistência Farmacêutica", "Suporte Profilático" |
| 305 | Vigilância Epidemiológica | "Vigilância em Saúde", "Vigilância Epidemiológica" |

**Nota:** O mapeamento grupo→subfunção usa busca exata e parcial para acomodar variações de nomenclatura entre anos e versões da planilha.

---

## DataSUS — Indicadores de Saúde

### O que é

O **DATASUS** (Departamento de Informática do SUS) é o órgão do Ministério da Saúde responsável por coletar, processar e disseminar informações de saúde. Mantém diversos sistemas de informação alimentados por diferentes atores do SUS.

### Sistemas utilizados

#### SINAN — Sistema de Informação de Agravos de Notificação

| Aspecto | Detalhe |
|---------|---------|
| **O que registra** | Notificações compulsórias de doenças (dengue, COVID-19, etc.) |
| **Quem alimenta** | Unidades de saúde → Vigilâncias Epidemiológicas municipais → estaduais → MS |
| **Fluxo** | Caso suspeito → notificação → investigação → confirmação/descarte |
| **Indicadores extraídos** | Contagem de notificações de dengue e COVID-19 por município/ano |
| **Código no FTP** | `DENGBR{YY}.dbc` (dengue), `INFLBR{YY}.dbc` (influenza/COVID) |
| **Diretório FTP** | `/dissemin/publicos/SINAN/DADOS/FINAIS` |

#### SIM — Sistema de Informações sobre Mortalidade

| Aspecto | Detalhe |
|---------|---------|
| **O que registra** | Declarações de óbito com causa (CID-10) |
| **Quem alimenta** | Cartórios de Registro Civil → Secretarias Municipais de Saúde → SES → MS |
| **Fluxo** | Óbito → Declaração de Óbito → digitação → codificação CID-10 |
| **Indicadores extraídos** | Contagem de óbitos por município/ano |
| **Código no FTP** | `DOSP{YYYY}.dbc` (óbitos de SP) |
| **Diretório FTP** | `/dissemin/publicos/SIM/CID10/DORES` |

#### SIH — Sistema de Informações Hospitalares

| Aspecto | Detalhe |
|---------|---------|
| **O que registra** | Internações hospitalares no SUS (AIH — Autorização de Internação Hospitalar) |
| **Quem alimenta** | Hospitais públicos e conveniados → Secretarias Municipais → SES → MS |
| **Fluxo** | Internação → preenchimento AIH → processamento → pagamento |
| **Indicadores extraídos** | Contagem de internações por município de residência/ano |
| **Código no FTP** | `RDSP{YY}{MM}.dbc` (12 arquivos mensais por ano) |
| **Diretório FTP** | `/dissemin/publicos/SIHSUS/200801_/Dados` |

#### SI-PNI — Sistema de Informação do Programa Nacional de Imunizações

| Aspecto | Detalhe |
|---------|---------|
| **O que registra** | Doses de vacinas aplicadas |
| **Quem alimenta** | Unidades Básicas de Saúde (UBS) → Secretarias Municipais → SES → MS |
| **Fluxo** | Vacinação → registro no sistema → consolidação municipal |
| **Indicadores extraídos** | Contagem de doses aplicadas por município/ano |
| **Código no FTP** | `CPNISP{YY}.dbc` |
| **Diretório FTP** | `/dissemin/publicos/PNI/DADOS` |

### Formato dos dados (DataSUS)

- **Formato original:** `.dbc` (formato proprietário comprimido do DataSUS)
- **Descompressão:** `.dbc` → `.dbf` (via biblioteca `datasus-dbc`)
- **Leitura:** `.dbf` → DataFrame pandas (via `dbfread`)
- **Cache local:** DataFrame → `.parquet` (formato colunar eficiente)
- **Filtro:** Município de Sorocaba (IBGE 355220) — múltiplas variantes de coluna de município
- **Valor persistido:** Contagem de registros (notificações, óbitos, internações, doses) por ano

### Coluna de município — variantes por sistema

Cada sistema DataSUS usa nomes diferentes para a coluna de município:

| Sistema | Colunas candidatas |
|---------|-------------------|
| SINAN | `CODMUNRES`, `CO_MUN_RES`, `MUNIC_RES`, `ID_MUNICIP` |
| SIM | `CODMUNRES`, `CO_MUN_RES`, `MUNIC_RES`, `CODMUNOCOR` |
| SIH | `MUNIC_RES`, `CO_MUN_RES`, `CODMUNRES`, `MUNIC_MOV` |
| SI-PNI | `CO_MUN_RES`, `CODMUNRES`, `MUNIC_RES`, `CO_MUNICIPIO_IBGE`, `MUNIC` |

O sistema tenta cada variante em ordem até encontrar uma que exista no DataFrame.

---

## Pipeline ETL

### Execução automática (Docker)

O `backend/entrypoint.sh` executa o ETL automaticamente ao iniciar o container:

```
1. Aguarda Neo4j (30 tentativas, 2s entre cada)
2. FNS: itera sobre todos .xls/.xlsx em backend/data/ (planilhas de repasses)
3. DataSUS: auto-detecta anos das planilhas FNS para limitar downloads
4. Seed fallback: garante dados mínimos caso ETLs falhem
5. Inicia FastAPI
```

### SIOPS Loader (`backend/etl/siops_loader.py`)

**Nota sobre nomenclatura:** O loader é chamado "siops_loader" por razões históricas, mas os dados que processa são planilhas de repasses do FNS (consultafns.saude.gov.br), não do SIOPS (siops.saude.gov.br). Os nós persistidos no Neo4j são chamados `DespesaSIOPS` por convenção do projeto.

**Entrada:** Planilha `.xls` ou `.xlsx` do portal Consulta FNS

**Processo:**
1. Detecta engine de leitura (`xlrd` para .xls, `openpyxl` para .xlsx)
2. Extrai ano dos metadados (busca célula "Ano:" nas primeiras 7 linhas)
3. Detecta linha de cabeçalho (procura "Grupo" ou "Bloco")
4. Detecta colunas "Grupo" e "Valor Total" dinamicamente
5. Para cada linha de dados:
   - Mapeia nome do grupo → código de subfunção (busca exata + parcial)
   - Converte valor do formato brasileiro (1.234.567,89) para float
   - Agrega valores por subfunção
6. Persiste como `DespesaSIOPS` via MERGE (deduplicação por subfuncao+ano)

**Saída:** Nós `DespesaSIOPS` no Neo4j

**Comando:**
```bash
python -m etl.siops_loader data/PlanilhaDetalhada.xls
```

### DataSUS Loader (`backend/etl/datasus_loader.py`)

**Entrada:** Download automático via PySUS ou cache local

**Estratégia de cache (3 níveis):**
1. Cache local: `backend/data/datasus/{sistema}_{tipo}_{ano}.parquet`
2. Cache global: `datasus_cache/{sistema}_{tipo}_{ano}.parquet` (pré-baixado por `download_pysus.py`)
3. Download FTP: via biblioteca PySUS (último recurso)

**Processo por sistema:**
1. Verifica se cache local ou global existe
2. Se não: baixa via PySUS do FTP DataSUS
3. Lê resultado (ParquetSet, lista de paths, ou path único)
4. Identifica coluna de município (tenta múltiplas variantes)
5. Filtra para Sorocaba (IBGE 355220, com tratamento de zeros à esquerda)
6. Salva cache local como Parquet
7. Conta registros e persiste como `IndicadorDataSUS` via MERGE

**Sistemas baixados:**
- SINAN dengue (`disease="DENG"`)
- SINAN COVID (`disease="INFL"`)
- SI-PNI vacinação (`group="CPNI"`, `states="SP"`)
- SIM mortalidade (`groups="CID10"`, `states="SP"`)
- SIH internações (`states="SP"`, `groups="RD"`, `months=1-12`)

**Comando:**
```bash
python -m etl.datasus_loader 2019 2023
```

### Download Direto FTP (`download_pysus.py`)

**Arquivo:** `download_pysus.py` (raiz do projeto)

Script standalone que acessa o FTP do DataSUS diretamente (sem PySUS), com:
- Barra de progresso (tqdm)
- Conversão `.dbc` → `.dbf` → DataFrame (via `datasus-dbc` + `dbfread`)
- Filtro para Sorocaba
- Cache em `datasus_cache/` como Parquet
- Resumo final com contagem por sistema/ano

**Dependências extras:** `dbfread`, `datasus-dbc`, `pyarrow`, `tqdm`

**Comando:**
```bash
python download_pysus.py 2019 2025
```

### Seed Data (`backend/etl/seed_data.py`)

Dados hardcoded de Sorocaba 2019-2021 como fallback:
- 12 registros de despesas (4 subfunções × 3 anos)
- 15 registros de indicadores (5 tipos × 3 anos)

Garante que o sistema funcione mesmo sem acesso ao FTP ou planilhas SIOPS.

### Detect Years (`backend/etl/detect_years.py`)

Auto-detecta anos disponíveis nos arquivos de repasses FNS em `backend/data/` para limitar downloads DataSUS apenas aos anos relevantes.

---

## Modelo de Dados Neo4j

### Nós

| Label | Chave natural | Campos principais |
|-------|---------------|-------------------|
| `Analise` | `id` (UUID) | dateFrom, dateTo, healthParams (JSON), starStatus, hierStatus, starTextAnalysis, hierTextAnalysis, starMessageCount, hierMessageCount, starCompletedAt, hierCompletedAt, createdAt |
| `DespesaSIOPS` | `subfuncao + ano` | id (UUID), subfuncao (int), subfuncaoNome (str), ano (int), valor (float), fonte ("siops"), importedAt (ISO 8601). **Nota:** apesar do nome, os dados vêm do portal FNS (repasses federais), não do SIOPS. |
| `IndicadorDataSUS` | `sistema + tipo + ano` | id (UUID), sistema (str), tipo (str), ano (int), valor (float), fonte ("datasus"), importedAt (ISO 8601) |
| `MetricaExecucao` | `id` (UUID) | architecture (str), agentId (str), agentType (str), executionTimeMs (float), cpuPercent (float), recordedAt (ISO 8601) |

### Relacionamentos

```cypher
(:Analise)-[:POSSUI_DESPESA]->(:DespesaSIOPS)
(:Analise)-[:POSSUI_INDICADOR]->(:IndicadorDataSUS)
(:Analise)-[:GEROU_METRICA]->(:MetricaExecucao)
(:DespesaSIOPS)-[:CORRELACIONA_COM]->(:IndicadorDataSUS)
```

### Convenções Cypher

- Sempre `MERGE` para deduplicação (nunca `CREATE` direto)
- `COALESCE(d.id, $id)` para preservar IDs existentes em MERGE
- Parâmetros nomeados com `$` (ex: `$analysisId`, `$dateFrom`)
- Timestamps em ISO 8601 UTC
- Sessions via `with neo4j_client._driver.session() as session:`

### Estratégia Offline-First

Todos os dados são pré-carregados no Neo4j via ETL. Os agentes consultam apenas o Neo4j local durante a análise — sem chamadas a APIs externas (exceto LLM para síntese textual). Isso garante consistência e reprodutibilidade dos benchmarks.


---

## Limitações e Pontos de Melhoria

### Observação sobre a natureza dos dados financeiros

Os dados financeiros utilizados neste projeto representam **repasses federais fundo a fundo** (transferências do FNS para o Fundo Municipal de Saúde de Sorocaba), e **não** a despesa total efetivamente executada pelo município em saúde. Essa distinção é importante para a interpretação dos resultados:

| Aspecto | O que o projeto usa | O que seria ideal |
|---------|--------------------|--------------------|
| Fonte | Portal FNS (consultafns.saude.gov.br) | SIOPS (siops.saude.gov.br) + FNS |
| Natureza | Repasses federais recebidos | Despesa total executada (federal + estadual + municipal) |
| Cobertura | Apenas parcela federal | Financiamento tripartite completo |
| Significado | "Quanto a União transferiu" | "Quanto o município efetivamente gastou" |

**Justificativa da escolha:** Para o objetivo do TCC (comparar duas arquiteturas multiagente), a fonte é adequada porque ambas as topologias processam os mesmos dados, garantindo uma comparação justa. Os dados são reais, oficiais e públicos, e a correlação entre repasses federais e indicadores de saúde ainda é válida como proxy da relação gasto-resultado.

### Pontos que podem passar despercebidos

1. **Repasses ≠ Gastos efetivos** — O município pode receber recursos e não executá-los no mesmo exercício, ou complementar com recursos próprios. A execução real pode diferir significativamente do valor repassado.

2. **Ausência de recursos próprios municipais** — O financiamento do SUS é tripartite (União + Estado + Município). Em Sorocaba, os recursos próprios municipais podem representar 50-70% do gasto total em saúde. Esses valores não aparecem nos dados do FNS.

3. **Ausência de transferências estaduais** — Repasses do Estado de São Paulo para o município também não estão contemplados.

4. **Defasagem temporal** — Repasses podem ser feitos em um ano mas executados no seguinte. Portarias de habilitação podem ter efeito retroativo. Valores podem ser estornados posteriormente.

5. **Mudanças de nomenclatura** — A estrutura de blocos/grupos do FNS mudou ao longo dos anos (Portaria de Consolidação nº 6/2017, Portaria GM/MS nº 828/2020), o que pode causar inconsistências na série temporal.

6. **Sobreposição de grupos** — Os grupos de repasse federal não necessariamente refletem como o município organiza internamente seus gastos. Ações de "Atenção Primária" podem se sobrepor com "Vigilância" na prática.

### Pontos de melhoria para trabalhos futuros

1. **Integrar dados do SIOPS** — Usar os dados de execução orçamentária declarados pelo município no SIOPS (siops.saude.gov.br) para ter a despesa total efetiva, não apenas os repasses federais.

2. **Incluir financiamento tripartite** — Somar repasses federais (FNS) + estaduais + recursos próprios municipais para ter o quadro completo do financiamento.

3. **Normalizar por população** — Calcular gasto per capita para permitir comparações mais justas entre períodos (a população de Sorocaba cresce ao longo dos anos).

4. **Ajustar por inflação** — Deflacionar valores usando IPCA ou IGP-M para comparações reais entre anos.

5. **Cruzar com dados de cobertura** — Usar dados de cobertura de equipes (e-SUS, CNES) para contextualizar a relação gasto-resultado.

6. **Validação cruzada** — Comparar os valores do FNS com os declarados no SIOPS para identificar discrepâncias e validar a consistência dos dados.

7. **Séries temporais mais longas** — Expandir o período de análise para capturar tendências de longo prazo e reduzir o impacto de eventos pontuais (ex: pandemia COVID-19 em 2020-2021).
