"""
Cliente Neo4j para o sistema de comparação de arquiteturas multiagente.

Gerencia conexão com o banco de dados Neo4j e expõe queries Cypher para o
modelo de dados novo (Empenho/DespesaAnual, IndicadorSaude + nós de
dimensão — ver PLANO_NOVO_MODELO_DADOS.md e DOCUMENTACAO_ETL_MODELO_DADOS.md)
e para os nós Analise/MetricaExecucao.

Diferente do schema antigo, Empenho/DespesaAnual/IndicadorSaude não têm
relação com Analise (sem POSSUI_DESPESA/POSSUI_INDICADOR) — são fatos
globais no grafo, filtrados por ano/subfunção/sistema.
"""

import logging
import os
import json
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from neo4j import GraphDatabase

from db.query_builder import DimensaoInvalida, build_despesa_cypher, build_indicador_cypher

load_dotenv()

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Driver Neo4j com queries Cypher para o domínio de análise de saúde."""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._uri = uri or os.environ["NEO4J_URI"]
        self._user = user or os.environ["NEO4J_USER"]
        self._password = password or os.environ["NEO4J_PASSWORD"]
        self._driver = GraphDatabase.driver(
            self._uri, auth=(self._user, self._password)
        )
        # Log de queries executadas por get_indicadores_por_sistema,
        # get_despesas_por_subfuncao e get_variacao_anual — consumido pelo
        # orquestrador/coordenador para expor ao frontend (aba técnica) a
        # query Cypher + dados brutos que cada agente usou, sem alterar a
        # assinatura desses métodos.
        self.query_log: list[dict] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        """Fecha a conexão com o Neo4j."""
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ------------------------------------------------------------------
    # Consultas de leitura
    # ------------------------------------------------------------------

    def get_despesas_por_subfuncao(
        self,
        subfuncao_codigos: list[int],
        date_from: int,
        date_to: int,
        dimensao: str | None = None,
    ) -> list[dict]:
        """
        Retorna despesas anuais agregadas (`DespesaAnual`) para as
        subfunções e período dados.

        Usa `valorProcessado` como valor "oficial" — é o campo que a
        própria validação do ETL confirma como soma exata do breakdown
        por `tipoRecurso` (DOCUMENTACAO_ETL_MODELO_DADOS.md), e o que a
        query de correlação-exemplo do projeto usa.

        `dimensao` (opcional, Fase 3) é o nome de um relacionamento
        dimensional (`POR_NATUREZA` | `POR_APLICACAO`) — decidido pelo
        agente de domínio via deliberação CoALA
        (`agents/domain/query_planning.py`) e montado de forma segura por
        `db.query_builder.build_despesa_cypher`. Uma dimensão inválida
        nunca propaga como erro: cai automaticamente para a consulta sem
        quebra dimensional.

        Shape de retorno compatível com o schema antigo (`subfuncao`,
        `subfuncaoNome`, `ano`, `valor`) para não exigir mudanças em
        `agents/data_crossing.py` nem nos agentes analíticos nesta fase.
        Quando `dimensao` está presente, cada linha ganha também
        `dimensao_valor`.
        """
        try:
            query, params = build_despesa_cypher(
                subfuncao_codigos, date_from, date_to, dimensao
            )
        except DimensaoInvalida as exc:
            logger.warning(
                "get_despesas_por_subfuncao: %s — consultando sem quebra dimensional", exc
            )
            query, params = build_despesa_cypher(
                subfuncao_codigos, date_from, date_to, None
            )

        with self._driver.session() as session:
            result = session.run(query, **params)
            rows = [dict(record) for record in result]
            self.query_log.append(
                {"query": query.strip(), "params": params, "rowCount": len(rows), "rows": rows}
            )
            return rows

    def get_variacao_anual(
        self, subfuncao_codigos: list[int], date_from: int, date_to: int
    ) -> list[dict]:
        """
        Retorna a variação percentual ano-a-ano pré-computada no ETL
        (`VARIACAO_ANUAL`, PLANO_NOVO_MODELO_DADOS.md §3.1) para as
        subfunções e período dados.

        Diferente de `POR_NATUREZA`/`POR_APLICACAO`, não é uma dimensão
        escolhida dinamicamente por deliberação CoALA — é um único
        reltype fixo, sempre a mesma consulta, sem `db.query_builder`
        envolvido (não há nome de reltype variável a validar).

        `ano` no filtro de período se refere a `anoAtual` (o ano cuja
        variação está sendo descrita, em relação ao ano imediatamente
        anterior com dado disponível para a mesma subfunção — não
        necessariamente o ano civil anterior, se houver lacuna).

        Returns:
            Lista de dicts com `subfuncao`, `ano_atual`, `ano_anterior`,
            `percentual`, `classificacao`. Lista vazia se não houver
            dados (subfunção nova, só 1 ano de dados, etc.).
        """
        query = """
        MATCH (atual:DespesaAnual)-[v:VARIACAO_ANUAL]->(anterior:DespesaAnual)
        WHERE atual.subfuncaoCodigo IN $subfuncaoCodigos
          AND atual.ano >= $dateFrom AND atual.ano <= $dateTo
        RETURN atual.subfuncaoCodigo AS subfuncao, atual.ano AS ano_atual,
               anterior.ano AS ano_anterior, v.percentual AS percentual,
               v.classificacao AS classificacao
        ORDER BY atual.ano, atual.subfuncaoCodigo
        """
        params = {
            "subfuncaoCodigos": subfuncao_codigos,
            "dateFrom": date_from,
            "dateTo": date_to,
        }
        with self._driver.session() as session:
            result = session.run(query, **params)
            rows = [dict(record) for record in result]
            self.query_log.append(
                {"query": query.strip(), "params": params, "rowCount": len(rows), "rows": rows}
            )
            return rows

    def get_indicadores_por_sistema(
        self,
        sistema: str,
        subtipos: list[str],
        date_from: int,
        date_to: int,
        dimensao: str | None = None,
    ) -> list[dict]:
        """
        Retorna indicadores de saúde (`IndicadorSaude`) para o sistema e
        subtipos dados, filtrados por período.

        `dimensao` (opcional) é o nome de um relacionamento dimensional
        (ex.: "POR_FAIXA_ETARIA") — decidido pelo agente de domínio via
        deliberação CoALA (`agents/domain/query_planning.py`) e montado
        de forma segura por `db.query_builder.build_indicador_cypher`. Uma
        dimensão inválida para o sistema nunca propaga como erro: cai
        automaticamente para a consulta sem quebra dimensional.

        Shape de retorno compatível com o schema antigo (`tipo`, `ano`,
        `valor`) — `tipo` = subtipo do sistema. Quando `dimensao` está
        presente, cada linha ganha também `dimensao_valor`.
        """
        try:
            query, params = build_indicador_cypher(
                sistema, subtipos, date_from, date_to, dimensao
            )
        except DimensaoInvalida as exc:
            logger.warning(
                "get_indicadores_por_sistema: %s — consultando sem quebra dimensional", exc
            )
            query, params = build_indicador_cypher(
                sistema, subtipos, date_from, date_to, None
            )

        with self._driver.session() as session:
            result = session.run(query, **params)
            rows = [dict(record) for record in result]
            self.query_log.append(
                {"query": query.strip(), "params": params, "rowCount": len(rows), "rows": rows}
            )
            return rows

    def get_past_analises(
        self,
        health_params: list[str] | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Retorna as análises anteriores mais recentes (nó `Analise`) —
        memória episódica real (retrieval), usada por
        `AgenteInterpretacaoIntencao` para informar o `intent_summary`
        com o que já foi perguntado antes.

        Filtro por `health_params` é aproximado (substring sobre o JSON
        armazenado em `healthParams`) — relevância fina fica a cargo de
        quem consome o resultado (o LLM), não do banco. Filtro por
        `date_from`/`date_to` exige sobreposição de período quando ambos
        são informados.
        """
        conditions = ["a.sourceQuestion IS NOT NULL"]
        params: dict = {"limit": limit}

        if health_params:
            or_clauses = []
            for i, token in enumerate(health_params):
                key = f"hp{i}"
                or_clauses.append(f"a.healthParams CONTAINS ${key}")
                params[key] = token
            conditions.append("(" + " OR ".join(or_clauses) + ")")

        if date_from is not None and date_to is not None:
            conditions.append("a.dateTo >= $dateFrom AND a.dateFrom <= $dateTo")
            params["dateFrom"] = date_from
            params["dateTo"] = date_to

        where_clause = " AND ".join(conditions)
        query = f"""
        MATCH (a:Analise)
        WHERE {where_clause}
        RETURN a.id AS id, a.sourceQuestion AS sourceQuestion,
               a.healthParams AS healthParams, a.dateFrom AS dateFrom,
               a.dateTo AS dateTo, a.starTextAnalysis AS starTextAnalysis,
               a.hierTextAnalysis AS hierTextAnalysis, a.createdAt AS createdAt
        ORDER BY a.createdAt DESC
        LIMIT $limit
        """
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [dict(record) for record in result]

    def get_benchmarks(self, analysis_id: str) -> list[dict]:
        """
        Retorna métricas de execução vinculadas a uma análise.

        Requisitos: 11.1, 11.2, 11.3, 12.4
        """
        query = """
        MATCH (a:Analise {id: $analysisId})-[:GEROU_METRICA]->(m:MetricaExecucao)
        RETURN m.architecture AS architecture, m.agentId AS agentId,
               m.executionTimeMs AS executionTimeMs,
               m.cpuPercent AS cpuPercent
        ORDER BY m.architecture, m.agentId
        """
        with self._driver.session() as session:
            result = session.run(query, analysisId=analysis_id)
            return [dict(record) for record in result]

    # ------------------------------------------------------------------
    # Operações de escrita
    # ------------------------------------------------------------------

    def save_analise(self, analise: dict) -> None:
        """
        Persiste (ou atualiza) um nó Analise no Neo4j via MERGE.

        Campos esperados em `analise`:
          id, dateFrom, dateTo, healthParams (dict ou str JSON),
          starStatus, hierStatus, createdAt (opcional),
          sourceQuestion (opcional, texto original da pergunta no chat),
          interpretedVia (opcional, "regex" | "llm" | "form")

        Requisitos: 12.3, 12.5
        """
        health_params = analise.get("healthParams", {})
        if isinstance(health_params, dict):
            health_params = json.dumps(health_params)

        created_at = analise.get("createdAt") or datetime.now(timezone.utc).isoformat()

        query = """
        MERGE (a:Analise {id: $id})
        SET a.dateFrom          = $dateFrom,
            a.dateTo            = $dateTo,
            a.healthParams      = $healthParams,
            a.starStatus        = $starStatus,
            a.starTextAnalysis  = $starTextAnalysis,
            a.starCompletedAt   = $starCompletedAt,
            a.hierStatus        = $hierStatus,
            a.hierTextAnalysis  = $hierTextAnalysis,
            a.hierCompletedAt   = $hierCompletedAt,
            a.createdAt         = $createdAt,
            a.sourceQuestion    = $sourceQuestion,
            a.interpretedVia    = $interpretedVia
        """
        with self._driver.session() as session:
            session.run(
                query,
                id=analise["id"],
                dateFrom=analise.get("dateFrom"),
                dateTo=analise.get("dateTo"),
                healthParams=health_params,
                starStatus=analise.get("starStatus", "pending"),
                starTextAnalysis=analise.get("starTextAnalysis"),
                starCompletedAt=analise.get("starCompletedAt"),
                hierStatus=analise.get("hierStatus", "pending"),
                hierTextAnalysis=analise.get("hierTextAnalysis"),
                hierCompletedAt=analise.get("hierCompletedAt"),
                createdAt=created_at,
                sourceQuestion=analise.get("sourceQuestion"),
                interpretedVia=analise.get("interpretedVia"),
            )

    def get_year_range(self) -> tuple[int, int] | None:
        """
        Retorna (ano_min, ano_max) entre os anos de Empenho e IndicadorSaude
        carregados no banco, ou None se não houver dados.

        Usado para validar períodos solicitados via chat contra os dados
        realmente disponíveis, em vez de disparar análises que retornam
        vazio silenciosamente.

        Requisitos: 3.4 (validação adicional, não descrita na spec original)
        """
        query = """
        MATCH (n)
        WHERE n:Empenho OR n:IndicadorSaude
        RETURN min(n.ano) AS anoMin, max(n.ano) AS anoMax
        """
        with self._driver.session() as session:
            record = session.run(query).single()
        if record is None or record["anoMin"] is None:
            return None
        return (int(record["anoMin"]), int(record["anoMax"]))

    def save_metrica(self, metrica: dict, analysis_id: str) -> None:
        """
        Persiste um nó MetricaExecucao e cria o relacionamento
        (:Analise)-[:GEROU_METRICA]->(:MetricaExecucao).

        Campos esperados em `metrica`:
          id, architecture, agentId, agentType,
          executionTimeMs, cpuPercent, recordedAt (opcional)

        Requisitos: 11.4, 12.4
        """
        recorded_at = metrica.get("recordedAt") or datetime.now(timezone.utc).isoformat()

        query = """
        MERGE (m:MetricaExecucao {id: $id})
        SET m.architecture    = $architecture,
            m.agentId         = $agentId,
            m.agentType       = $agentType,
            m.executionTimeMs = $executionTimeMs,
            m.cpuPercent      = $cpuPercent,
            m.recordedAt      = $recordedAt
        WITH m
        MATCH (a:Analise {id: $analysisId})
        MERGE (a)-[:GEROU_METRICA]->(m)
        """
        with self._driver.session() as session:
            session.run(
                query,
                id=metrica["id"],
                architecture=metrica.get("architecture"),
                agentId=metrica.get("agentId"),
                agentType=metrica.get("agentType"),
                executionTimeMs=metrica.get("executionTimeMs"),
                cpuPercent=metrica.get("cpuPercent"),
                recordedAt=recorded_at,
                analysisId=analysis_id,
            )
