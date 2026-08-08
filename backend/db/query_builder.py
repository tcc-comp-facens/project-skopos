"""
Construção segura de Cypher dinâmica para consultas de IndicadorSaude.

O LLM (dentro dos agentes de domínio, via agents.domain.query_planning)
decide QUAIS filtros aplicar — nunca escreve Cypher. Este módulo traduz
essa decisão numa query parametrizada.

Neo4j não permite parametrizar nomes de label/relationship type (só
valores de propriedade) — por isso `dimensao` é a única parte deste
módulo que vira texto interpolado na query, e só depois de validada
contra `SISTEMA_DIMENSOES`. Qualquer nome fora da allowlist é rejeitado
antes de tocar a string da query (`DimensaoInvalida`); o chamador
(`db.neo4j_client.Neo4jClient.get_indicadores_por_sistema`) cai para
`dimensao=None` nesse caso — uma dimensão hallucinada pelo LLM nunca
propaga como erro fatal.
"""

from __future__ import annotations


class DimensaoInvalida(ValueError):
    """Levantada quando uma dimensão solicitada não é válida para o sistema."""


# Allowlist: sistema -> relacionamentos de dimensão válidos (nome exato do
# reltype no grafo). Fonte: DOCUMENTACAO_ETL_MODELO_DADOS.md e
# backend/etl/saude_indicadores_loader.py.
SISTEMA_DIMENSOES: dict[str, list[str]] = {
    "sim": ["POR_FAIXA_ETARIA", "POR_CAPITULO_CID"],
    "sih": ["POR_CAPITULO_CID"],
    "sinan": ["POR_FAIXA_ETARIA", "POR_SEXO"],
    "sipni": ["COBERTURA_VACINAL", "DOSES_APLICADAS"],
    "sinasc": ["POR_FAIXA_ETARIA", "POR_FAIXA_PESO"],
    "sia": [],
    "covid": [],
    "cnes": [
        "POR_TIPO_ESTABELECIMENTO",
        "POR_OCUPACAO",
        "POR_TIPO_EQUIPE",
        "POR_TIPO_ATENDIMENTO",
        "POR_TIPO_LEITO_CONSULTORIO",
        "POR_TIPO_EQUIPAMENTO",
    ],
}


def dimensoes_validas(sistema: str) -> list[str]:
    """Lista as dimensões válidas para um sistema — usada como fato de
    domínio em `semantic_memory` pelos agentes (ex.: `AgenteSINAN`)."""
    return list(SISTEMA_DIMENSOES.get(sistema, []))


def build_indicador_cypher(
    sistema: str,
    subtipos: list[str],
    date_from: int,
    date_to: int,
    dimensao: str | None = None,
) -> tuple[str, dict]:
    """Monta a query Cypher parametrizada para `IndicadorSaude`.

    `sistema`, `subtipos`, `date_from`, `date_to` são sempre bind
    parameters. `dimensao` (nome do relacionamento) é validada contra
    `SISTEMA_DIMENSOES[sistema]` antes de ser inserida na estrutura da
    query — é a única forma segura de compor Cypher dinâmica com um
    reltype variável.

    Sem `dimensao`: retorna o total por (subtipo, ano) — `i.valorTotal`.
    Com `dimensao`: retorna uma linha por quebra dimensional — `r.valor`
    (propriedade do relacionamento) e `dimensao_valor` (nome/código do nó
    de dimensão).

    Raises:
        DimensaoInvalida: se `dimensao` não é válida para `sistema`.
    """
    params: dict = {
        "sistema": sistema,
        "subtipos": subtipos,
        "dateFrom": date_from,
        "dateTo": date_to,
    }

    if dimensao is None:
        query = """
        MATCH (i:IndicadorSaude)
        WHERE i.sistema = $sistema AND i.subtipo IN $subtipos
          AND i.ano >= $dateFrom AND i.ano <= $dateTo
        RETURN i.subtipo AS tipo, i.ano AS ano, i.valorTotal AS valor
        ORDER BY i.ano, i.subtipo
        """
        return query, params

    validas = SISTEMA_DIMENSOES.get(sistema, [])
    if dimensao not in validas:
        raise DimensaoInvalida(
            f"dimensão '{dimensao}' inválida para sistema '{sistema}' "
            f"(válidas: {validas})"
        )

    # `dimensao` já validada contra a allowlist acima — seguro interpolar
    # como nome de relacionamento (não é dado de usuário nem de LLM cru).
    query = f"""
    MATCH (i:IndicadorSaude)-[r:{dimensao}]->(dim)
    WHERE i.sistema = $sistema AND i.subtipo IN $subtipos
      AND i.ano >= $dateFrom AND i.ano <= $dateTo
    RETURN i.subtipo AS tipo, i.ano AS ano, r.valor AS valor,
           coalesce(dim.nome, dim.codigo) AS dimensao_valor
    ORDER BY i.ano, i.subtipo, dimensao_valor
    """
    return query, params


# ---------------------------------------------------------------------------
# Construção segura de Cypher dinâmica para consultas de DespesaAnual
# (Fase 3) — mesmo mecanismo acima, adaptado ao lado orçamento.
#
# Diferente de SISTEMA_DIMENSOES (particionado por sistema de saúde, já que
# sistemas diferentes têm dimensões válidas diferentes), as dimensões de
# despesa valem igualmente para as 7 subfunções — não há partição real a
# modelar, por isso é uma lista simples em vez de um dict.
# ---------------------------------------------------------------------------

DESPESA_DIMENSOES: list[str] = ["POR_NATUREZA", "POR_APLICACAO"]


def dimensoes_validas_despesa() -> list[str]:
    """Lista as dimensões válidas para DespesaAnual — usada como fato de
    domínio em `semantic_memory` por `AgenteOrcamentoSubfuncao`, espelhando
    `dimensoes_validas` para IndicadorSaude."""
    return list(DESPESA_DIMENSOES)


def build_despesa_cypher(
    subfuncao_codigos: list[int],
    date_from: int,
    date_to: int,
    dimensao: str | None = None,
) -> tuple[str, dict]:
    """Monta a query Cypher parametrizada para `DespesaAnual`.

    Espelha `build_indicador_cypher` — mesma razão de segurança: `dimensao`
    é a única parte deste módulo que vira texto interpolado na query, e só
    depois de validada contra `DESPESA_DIMENSOES`.

    Sem `dimensao`: retorna o total por (subfuncao, ano) — `d.valorProcessado`
    (byte-idêntica à query antes embutida em
    `db.neo4j_client.Neo4jClient.get_despesas_por_subfuncao`, só relocada
    para cá). Com `dimensao`: uma linha por quebra dimensional — `r.valor`
    (propriedade do relacionamento, ver `POR_NATUREZA`/`POR_APLICACAO` em
    `etl/orcamento_loader.py`) e `dimensao_valor` (nome da Natureza/Aplicacao).

    Raises:
        DimensaoInvalida: se `dimensao` não está em `DESPESA_DIMENSOES`.
    """
    params: dict = {
        "subfuncaoCodigos": subfuncao_codigos,
        "dateFrom": date_from,
        "dateTo": date_to,
    }

    if dimensao is None:
        query = """
        MATCH (d:DespesaAnual)
        WHERE d.subfuncaoCodigo IN $subfuncaoCodigos
          AND d.ano >= $dateFrom AND d.ano <= $dateTo
        RETURN d.subfuncaoCodigo AS subfuncao, d.subfuncaoNome AS subfuncaoNome,
               d.ano AS ano, d.valorProcessado AS valor
        ORDER BY d.ano, d.subfuncaoCodigo
        """
        return query, params

    if dimensao not in DESPESA_DIMENSOES:
        raise DimensaoInvalida(
            f"dimensão '{dimensao}' inválida para despesas "
            f"(válidas: {DESPESA_DIMENSOES})"
        )

    # `dimensao` já validada acima — seguro interpolar como nome de
    # relacionamento (não é dado de usuário nem de LLM cru).
    query = f"""
    MATCH (d:DespesaAnual)-[r:{dimensao}]->(dim)
    WHERE d.subfuncaoCodigo IN $subfuncaoCodigos
      AND d.ano >= $dateFrom AND d.ano <= $dateTo
    RETURN d.subfuncaoCodigo AS subfuncao, d.subfuncaoNome AS subfuncaoNome,
           d.ano AS ano, r.valor AS valor,
           coalesce(dim.nome, dim.codigo) AS dimensao_valor
    ORDER BY d.ano, d.subfuncaoCodigo, dimensao_valor
    """
    return query, params
