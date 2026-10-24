# Documentos históricos

Registros de planejamento e de migração produzidos durante o desenvolvimento. Ficam aqui porque guardam o **raciocínio** por trás de decisões ainda vigentes, e porque o código e os documentos de [docs/](../) os citam por seção (ex.: `PLANO_NOVO_MODELO_DADOS.md §5`). Eles **não** descrevem o estado atual do sistema: para isso, use o [README](../../README.md) e [docs/](../).

| Arquivo | O que registra | Período / commits |
|---------|----------------|-------------------|
| [CONTEXTO_PROJETO.md](CONTEXTO_PROJETO.md) | Snapshot do projeto ainda sob o modelo BDI, com agentes por subfunção, FNS/PySUS e `POST /api/analysis` | 2026-08-02 |
| [PLANO_REFATORACAO.md](PLANO_REFATORACAO.md) | Plano em 6 etapas para ampliar o uso de LLM nos agentes e criar novas métricas, com justificativa acadêmica (D1–D16). A Etapa 5 (comunicação lateral) foi revertida em `38df7be` | `19473b0` → `4505fd0` |
| [PLANO_NOVO_MODELO_DADOS.md](PLANO_NOVO_MODELO_DADOS.md) | Migração da fonte de dados (PySUS/FNS → LAI + TabNet em `Dados/`), redesenho do grafo Neo4j e reparticionamento dos agentes por sistema DATASUS/subfunção | 2026-08-02 → `f4838d2` |
| [DOCUMENTACAO_ETL_MODELO_DADOS.md](DOCUMENTACAO_ETL_MODELO_DADOS.md) | Estado "as-built" do ETL e do modelo Neo4j logo após a migração. Consolidado em [docs/03-DADOS-ETL.md](../03-DADOS-ETL.md) | 2026-08-04 |
| [CHANGELOG_MIGRACAO_MODELO_DADOS.md](CHANGELOG_MIGRACAO_MODELO_DADOS.md) | Changelog do diff que virou o commit `f4838d2` | 2026-08-07 |
