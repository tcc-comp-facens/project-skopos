# Fontes de Dados e Pipeline ETL

## Sumário

1. [Origens dos Dados](#origens-dos-dados)
2. [Orçamento — Execução Municipal (LAI/Portal de Transparência)](#orçamento--execução-municipal-laiportal-de-transparência)
3. [Saúde — Extratos DATASUS/TabNet](#saúde--extratos-datasustabnet)
4. [Pipeline ETL](#pipeline-etl)
5. [Modelo de Dados Neo4j](#modelo-de-dados-neo4j)
6. [Limitações e Pontos de Melhoria](#limitações-e-pontos-de-melhoria)

---

## Origens dos Dados

O pipeline ETL foi reconstruído do zero (commit `f4838d2`, agosto de 2026), substituindo a geração anterior — que dependia de download em tempo real via FTP DataSUS (biblioteca `pysus`) e de planilhas de repasse federal do FNS. O modelo atual lê **exclusivamente arquivos locais já extraídos**, colocados em `Dados/` na raiz do repositório:

```
Dados/
├── orcamento/                              # Execução orçamentária municipal (LAI/portal de transparência)
│   ├── 2015-2019/*_empenhado_*.csv
│   ├── 2015-2019/*_previsto_e_empenhado_por_programa_e_secretaria_*.csv   (ignorado — previsto × realizado não é carregado)
│   ├── 2020-2022/... (mesmo padrão)
│   └── 2023-2025/... (mesmo padrão)
└── Sorocaba_DATASUS_2015-2025/             # Extratos DATASUS/TabNet já filtrados para Sorocaba
    ├── Sorocaba_SIM_*.xlsx
    ├── Sorocaba_SIH_*.xlsx
    ├── Sorocaba_SINAN_*.xlsx
    ├── Sorocaba_SIPNI_*.xlsx
    ├── Sorocaba_SINASC_*.xlsx
    ├── Sorocaba_SIA_*.xlsx
    ├── Sorocaba_CNES_*.xlsx (múltiplas abas)
    ├── Sorocaba_COVID19_Casos_Obitos_2020-2023.xlsx
    └── Sorocaba_Bases_Indisponiveis_2015-2025.xlsx  # só notas, não é carregado
```

Nenhum dos dois loaders faz chamada de rede — não há mais FTP, `.dbc`/`.dbf`, PySUS, nem download automatizado dentro do container. `Dados/` é montado como volume read-only no Docker (`./Dados:/Dados:ro`, variável `DADOS_DIR=/Dados`) porque o `Dockerfile` só copia `backend/` para a imagem.

**Janela temporal oficial do sistema: 01/01/2016 a 31/12/2025 (10 anos)**, aplicada como filtro por linha em ambos os loaders — qualquer registro fora desse período é descartado no ETL, não em tempo de consulta.

Ver [DOCUMENTACAO_ETL_MODELO_DADOS.md](../DOCUMENTACAO_ETL_MODELO_DADOS.md) (raiz do repo) para o detalhamento técnico completo com números reais de uma carga validada — este documento aqui resume o essencial para quem vai operar ou entender o sistema; aquele é a referência as-built de uma sessão de ETL específica (fica desatualizado nos detalhes mais finos à medida que o ETL evolui — trate como histórico, não como live state).

---

## Orçamento — Execução Municipal (LAI/Portal de Transparência)

### O que é

Diferente do modelo anterior — que usava repasses federais **recebidos** (portal `consultafns.saude.gov.br`, ~50-70% do gasto real) — os dados atuais são a **despesa municipal efetivamente executada**: recurso próprio + transferências estaduais + transferências federais + operações de crédito + emendas parlamentares, empenho a empenho.

**Formato:** CSV com delimitador `|` (pipe), encoding `latin-1`. Nome de arquivo sugere extração via portal de transparência/LAI (Lei de Acesso à Informação) do município — não há metadado de data de extração no arquivo.

**Dois tipos de CSV por período** (`2015-2019`, `2020-2022`, `2023-2025`):

| Padrão de nome | Grão | Vira |
|---|---|---|
| `*_empenhado_*.csv` | 1 linha por empenho | `Empenho` (granular) |
| `*_previsto_e_empenhado_por_programa_e_secretaria_*.csv` | Bloco plurianual inteiro (sem coluna de ano) | — (não carregado; ignorado pelo loader) |

### Mapeamentos aplicados

- **Subfunção** (`Sub Função` na planilha) → código padrão 122/301-306 (tabela fixa de 7 entradas — ver [docs/02-AGENTES.md](02-AGENTES.md#agentes-orçamentários-7--1-por-subfunção)).
- **Fonte de Recurso** → `tipoRecurso` (`proprio`/`federal`/`estadual`/`credito`/`emenda_parlamentar`/`outros`) — 13 textos originais mapeados, mantidos como chave literal do nó `FonteRecurso` (variantes como "...vinculados" vs. "...vinculados-exec.anter." marcam uma distinção contábil real, não são unificadas). `emenda_parlamentar` fica em categoria própria, deliberadamente não forçada em própria/federal, por incerteza genuína sobre a origem.
- **Fornecedor** — regex extrai CNPJ ou (fallback) CPF do campo bruto (que vem com HTML solto e o documento entre parênteses); ~15-20% dos empenhos são pessoa física (prestadores individuais, ex. home care).
- **Ação** — `"2109-Atencao Primaria em Saude"` → código antes do primeiro hífen + nome.
- **Aplicação** — persistida como está, sem mapeamento (bloco de financiamento SUS, ex. `"Bloco 01 - Assistencia Farmaceutica"`) — a dimensão mais granular disponível sobre a finalidade do gasto.

### Achado real relevante para interpretação de série temporal

A subfunção **122 (Administração Geral) não existe nos empenhos antes de 2020** — parte da folha de pagamento que antes ficava concentrada em 301 (Atenção Básica) passou a ser segregada em 122 a partir de 2020. Não é bug do ETL; é mudança real de prática de classificação orçamentária municipal. `AgenteContextoOrcamentario` resume tendência via CAGR (não média aritmética simples) parcialmente por causa desse tipo de salto de base.

---

## Saúde — Extratos DATASUS/TabNet

### O que é

Extratos já baixados e filtrados para Sorocaba (IBGE 355220) do **DATASUS** (Departamento de Informática do SUS), cobrindo **8 Sistemas de Informação** — um por agente de saúde da arquitetura (ver [docs/02-AGENTES.md](02-AGENTES.md#agentes-de-saúde-8--1-por-sistema-de-informação)):

| Sistema | O que registra | Subtipos |
|---|---|---|
| **SINAN** | Notificações compulsórias de 9 agravos | dengue, chikungunya, sífilis (adquirida/gestante/congênita), coqueluche, hepatites virais, tuberculose, hanseníase |
| **SIM** | Óbitos (causa CID-10) | mortalidade |
| **SIH** | Internações hospitalares (AIH) | internacoes |
| **SI-PNI** | Vacinação | cobertura_vacinal, doses_aplicadas |
| **SINASC** | Nascidos vivos | nascidos_vivos |
| **SIA** | Produção ambulatorial | producao_ambulatorial |
| **CNES** | Rede assistencial (12 sub-cubos) | leitos, profissionais, estabelecimentos_por_tipo, ocupacoes, equipes_saude, tipo_atendimento, leitos_consultorios, equipamentos, estabelecimentos_nivel_atencao, estabelecimentos_servico_classificacao, estabelecimentos_habilitacao, estabelecimentos_vigilancia_epidemiologica |
| **COVID** | Casos/óbitos (MS + compilação `wcota/covid19br`) | casos, obitos (único caso com granularidade mensal além da anual) |

Cada planilha tem aba "Notas" com proveniência e datas de extração (agosto de 2026). A granularidade dimensional (faixa etária, capítulo CID-10, sexo, faixa de peso, cobertura/doses por vacina, e 6 dimensões do CNES) é o que o pipeline anterior **não tinha** — extraía só um total por (sistema, tipo, ano).

### Formato e leitores

XLSX local, sem download. 4 formatos de planilha (ano simples com categorias em colunas; `Ano/Mês` mensal — usado só para os sub-cubos do CNES, agregados ao snapshot de dezembro; ano + valor único; transposto — vacina em linhas, ano em colunas, usado só pelo SI-PNI), cada um com sua própria função leitora genérica em `saude_indicadores_loader.py`. COVID tem par de leitores próprio (métricas emparelhadas com seus acumulados).

### Decisões de modelagem relevantes

- **`FaixaEtaria` NÃO é unificada entre sistemas** — cada sistema tem seu próprio espaço de faixas (chave `"{sistema}:{nome}"`): os bins não batem entre si, e SINASC mede idade da **mãe** no parto, não da pessoa do registro — um atributo conceitualmente diferente, não só um bin diferente.
- **`CapituloCID10` É unificado** entre SIM (numeração romana nos cabeçalhos) e SIH (numeração decimal) — é o ponto de correlação real entre os dois sistemas (ex.: cruzar mortalidade × internação pela mesma causa).
- Sub-cubos mensais do CNES são agregados para o snapshot de dezembro, consistente com o resto do CNES (nativamente anual).
- COVID mantém granularidade mensal além da anual — único caso onde o campo `mes` de `IndicadorSaude` é populado.

---

## Pipeline ETL

### Execução

```bash
# Os dois loaders, em sequência (ou via run_etl.py/entrypoint.sh, que os chamam automaticamente)
cd backend
python -m etl.orcamento_loader
python -m etl.saude_indicadores_loader
```

Ambos resolvem `Dados/` via variável de ambiente `DADOS_DIR`, com fallback para o caminho relativo (`backend/etl/../../../Dados`) quando rodando localmente fora do Docker.

### `orcamento_loader.py`

1. Garante as constraints de unicidade (`CREATE CONSTRAINT IF NOT EXISTS`).
2. Itera os CSVs dos 3 períodos, carregando só os de empenho (`*_empenhado_*`); os de previsto × realizado são ignorados pelo nome.
3. Lê com pandas (`sep="|"`, `encoding="latin-1"`).
4. Para `Empenho`: filtra a janela 2016-2025, mapeia subfunção/fonte de recurso/fornecedor/ação, persiste em lotes de 2.000 via `UNWIND` (nó + dimensões + relacionamentos numa única query Cypher por lote).
5. Ao final, roda **agregações Cypher** que recalculam a partir de `Empenho` já persistido (não incrementalmente durante a leitura do CSV):
   - `DespesaAnual` — total por `(subfuncaoCodigo, ano)`, com breakdown por `tipoRecurso` (`valorProprio`, `valorFederal`, `valorEstadual`, `valorCredito`, `valorEmendaParlamentar`, `valorOutros` — soma exatamente igual ao total).
   - `(:DespesaAnual)-[:POR_NATUREZA {valor}]->(:Natureza)` — quebra por natureza de despesa (o que foi comprado: medicamentos, equipamentos, salários).
   - `(:DespesaAnual)-[:POR_APLICACAO {valor}]->(:Aplicacao)` — quebra por bloco de financiamento SUS (reaproveita o nó `Aplicacao` já criado a partir de `Empenho`, via `MATCH`, nunca `MERGE`, para não divergir da instância existente).
   - `(:DespesaAnual)-[:VARIACAO_ANUAL {percentual, classificacao}]->(:DespesaAnual anterior)` — encadeia anos consecutivos da mesma subfunção com a variação já calculada, reaproveitando **literalmente** `compute_yoy_variation`/`classify_trend` de `agents/context/contexto_orcamentario.py` (calculado em Python, não reescrito em Cypher, para não haver duas lógicas de classificação divergentes no sistema).

Todo o loader usa `MERGE`, nunca `CREATE` — idempotente, rodar múltiplas vezes produz os mesmos nós/relacionamentos.

### `saude_indicadores_loader.py`

Uma função `_load_X` por sistema (`_load_sim`, `_load_sih`, ..., `_load_cnes`, mais o par `_load_covid`), cada uma: lê a(s) planilha(s) relevante(s) com o leitor apropriado ao formato, converte para linhas de `IndicadorSaude` + linhas de quebra dimensional, e persiste via 2 queries Cypher genéricas parametrizadas por lote de 2.000 (uma para `IndicadorSaude`, `MERGE` por `chave`; outra por tipo de quebra dimensional, `MATCH` no `IndicadorSaude` já existente + `MERGE` no nó de dimensão + relacionamento). Linhas inválidas (em branco, nota/rodapé, totalização) são descartadas automaticamente quando o parser da chave (ano ou ano/mês) falha.

---

## Modelo de Dados Neo4j

### Domínio Orçamento

```cypher
(:Empenho)-[:DO_FORNECEDOR]->(:Fornecedor)
(:Empenho)-[:FINANCIADO_POR]->(:FonteRecurso)
(:Empenho)-[:CLASSIFICADO_EM]->(:Subfuncao)
(:Empenho)-[:APLICADO_EM]->(:Aplicacao)
(:Empenho)-[:EXECUTA_ACAO]->(:Acao)-[:PARTE_DE]->(:ProgramaGoverno)
(:Empenho)-[:DA_UNIDADE]->(:UnidadeOrcamentaria)

(:DespesaAnual)-[:POR_NATUREZA]->(:Natureza)
(:DespesaAnual)-[:POR_APLICACAO]->(:Aplicacao)
(:DespesaAnual)-[:VARIACAO_ANUAL]->(:DespesaAnual)   # ano atual -> ano anterior, mesma subfunção
```

`Empenho` é granular (1 nó por linha do CSV, chave `"{periodoOrigem}:{índice da linha}"` — não usa `Nro Empenho`, que se repete entre parcelas/estornos). `DespesaAnual` é o agregado materializado por `(subfuncaoCodigo, ano)`, calculado a partir de `Empenho` ao final do ETL — papel equivalente ao antigo `DespesaSIOPS`, mas a partir de execução real em vez de repasse federal recebido.

### Domínio Indicadores de Saúde

```cypher
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

`IndicadorSaude` é o fato central — grão `(sistema, subtipo, ano, mes)`, chave de MERGE `"{sistema}:{subtipo}:{ano}:{mes ou 0}"`. Toda relação de quebra dimensional carrega uma única propriedade `valor` (float). A allowlist de dimensões válidas por sistema (`SISTEMA_DIMENSOES`) e para despesa (`DESPESA_DIMENSOES`) vive em `backend/db/query_builder.py` — é o que os agentes de domínio deliberam entre "sem quebra" e cada dimensão válida (ver [docs/02-AGENTES.md](02-AGENTES.md)).

### Outros nós

| Label | Papel |
|---|---|
| `Analise` | Uma análise disparada (id, período, health_params, status/texto por arquitetura) |
| `MetricaExecucao` | Métrica de execução por agente/supervisor (tempo, CPU) |
| `Fornecedor`, `FonteRecurso`, `Subfuncao`, `Aplicacao`, `Natureza`, `ProgramaGoverno`, `Acao`, `UnidadeOrcamentaria` | Dimensões do domínio orçamento |
| `FaixaEtaria`, `CapituloCID10`, `Sexo`, `FaixaPeso`, `Vacina`, `TipoEstabelecimento`, `OcupacaoProfissional`, `TipoEquipe`, `TipoAtendimento`, `TipoLeitoConsultorio`, `TipoEquipamento` | Dimensões do domínio saúde |

### Convenções Cypher

- Sempre `MERGE` para deduplicação (nunca `CREATE` direto)
- Timestamps em ISO 8601 UTC
- Consultas dos agentes sempre montadas via `db/query_builder.py` — o nome de dimensão (reltype) só é interpolado depois de validado contra a allowlist (`SISTEMA_DIMENSOES`/`DESPESA_DIMENSOES`); uma dimensão hallucinada pelo LLM nunca chega a virar Cypher

### Estratégia Offline-First

Todos os dados são pré-carregados no Neo4j via ETL. Os agentes consultam apenas o Neo4j local durante a análise — sem chamadas a APIs externas (exceto LLM para deliberação/síntese). Isso garante consistência e reprodutibilidade dos benchmarks.

---

## Limitações e Pontos de Melhoria

### Ressalvas de qualidade dos dados de origem (vale surfar se o usuário perguntar sobre confiabilidade)

- **Dados de 2025 preliminares** em SIM, SIH, SINASC e CNES Rede Assistencial (3ª prévia oficial do DATASUS, sujeita a atualização).
- **SI-PNI 2015-2022 vs. 2023-2026** vêm de fontes/metodologias diferentes (cubo TabNet legado, exportado manualmente, vs. painel novo RNDS) — nomenclatura de vacina não foi unificada entre os dois blocos; qualquer série temporal que cruze 2022/2023 deve tratá-los como séries distintas.
- **SINAN sífilis (3 subtipos) e hepatites virais** ainda não têm 2024/2025 publicados pelo MS até a data de extração — atraso normal de consolidação.
- **COVID só cobre 2020-2023** — o MS mudou a metodologia do painel oficial para periodicidade semanal a partir de 18/03/2023, descontinuando a série usada aqui.
- **Classificação de `emenda_parlamentar`** (própria vs. federal) permanece incerta — deixada como categoria própria em vez de uma classificação arriscada.

### Pontos de melhoria para trabalhos futuros

1. **Normalizar por população** — gasto per capita para comparações mais justas entre períodos.
2. **Ajustar por inflação** (IPCA/IGP-M) para comparações reais entre anos.
3. **Granularidade mensal completa do CNES** (equipes de saúde, tipo de atendimento, leitos/consultórios, equipamentos) — hoje só o snapshot de dezembro é persistido; quem precisar da série mensal completa precisa reprocessar a partir do XLSX original.
4. **Resolver a classificação de `emenda_parlamentar`** com uma fonte externa que distinga emenda de vereador (municipal) de deputado/senador (federal).
