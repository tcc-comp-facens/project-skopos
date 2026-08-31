"""
REST API endpoints.

O disparo de análise (antigo POST /api/analysis, formulário com um botão
por categoria de indicador) foi removido — o único caminho de entrada
hoje é o chat (/ws/chat/{session_id}, ver api/chat_websocket.py), que
usa api.dispatch.dispatch_analysis diretamente.

Endpoints:
  GET  /api/analysis/{id}  — Retrieve analysis result
  GET  /api/analysis/{id}/quality — Quality metrics
  GET  /api/analysis/{id}/report  — Comparative report
  GET  /api/benchmarks     — All benchmark metrics

Requirements: 9.2, 9.3, 10.4
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from agents.domain.query_planning import compute_cache_hit_rate
from api.dispatch import get_available_year_range
from api.state import active_results, get_neo4j_client
from core.guardrail_stats import compute_guardrail_rejection_rate
from core.quality_metrics import compute_all_quality_metrics, generate_comparative_report

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/data-range")
async def get_data_range():
    """Retorna o intervalo de anos com dados carregados no Neo4j.

    Usado pelo chat para validar períodos solicitados e para compor a
    mensagem de boas-vindas com o intervalo real disponível.
    """
    year_range = get_available_year_range()
    if year_range is None:
        return {"minYear": None, "maxYear": None}
    return {"minYear": year_range[0], "maxYear": year_range[1]}


@router.get("/api/analysis/{analysis_id}")
async def get_analysis(analysis_id: str):
    """Retrieve analysis result from Neo4j. Req 9.2."""
    neo4j_client = get_neo4j_client()
    try:
        query = """
        MATCH (a:Analise {id: $analysisId})
        RETURN a {.*} AS analise
        """
        with neo4j_client._driver.session() as session:
            result = session.run(query, analysisId=analysis_id)
            record = result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return record["analise"]
    finally:
        neo4j_client.close()


@router.get("/api/benchmarks")
async def get_benchmarks():
    """Return comparative metrics from Neo4j. Req 9.3."""
    neo4j_client = get_neo4j_client()
    try:
        query = """
        MATCH (a:Analise)-[:GEROU_METRICA]->(m:MetricaExecucao)
        RETURN a.id AS analysisId,
               m.architecture AS architecture,
               m.agentId AS agentId,
               m.executionTimeMs AS executionTimeMs,
               m.cpuPercent AS cpuPercent
        ORDER BY a.createdAt DESC, m.architecture, m.agentId
        """
        with neo4j_client._driver.session() as session:
            result = session.run(query)
            return [dict(record) for record in result]
    finally:
        neo4j_client.close()


@router.get("/api/analysis/{analysis_id}/quality")
async def get_quality_metrics(analysis_id: str):
    """Return quality and efficiency metrics for a completed analysis.

    Sempre devolve o cache quando ele existe: a avaliação RAGAS é
    assíncrona, roda uma única vez em `api/websocket.py` e já chega
    encaixada em `quality.{arch}.ragas`. Recomputar aqui gastaria LLM de
    novo para produzir o mesmo payload.
    """
    results = active_results.get(analysis_id, {})
    star_result = results.get("star")
    hier_result = results.get("hierarchical")

    cached = results.get("quality_metrics")
    if cached:
        return cached

    if not star_result or not hier_result:
        raise HTTPException(
            status_code=404,
            detail="Quality metrics not available. Both topologies must complete first.",
        )

    star_agent_metrics = results.get("star_agent_metrics", [])
    hier_agent_metrics = results.get("hier_agent_metrics", [])

    quality = compute_all_quality_metrics(
        star_result=star_result,
        hier_result=hier_result,
        star_agent_metrics=star_agent_metrics,
        hier_agent_metrics=hier_agent_metrics,
        star_wall_clock_ms=results.get("star_wall_clock_ms", 0),
        hier_wall_clock_ms=results.get("hier_wall_clock_ms", 0),
        star_token_usage=results.get("star_token_usage"),
        hier_token_usage=results.get("hier_token_usage"),
        intent_token_usage=results.get("intent_token_usage"),
    )

    active_results[analysis_id]["quality_metrics"] = quality
    return quality


@router.get("/api/metrics/guardrail")
async def get_guardrail_metrics():
    """Etapa 6 — taxa de rejeição do guardrail de escopo (Etapa 1).

    Métrica process-wide (não por análise — uma análise sequer é
    disparada quando o guardrail rejeita), acumulada desde o início do
    processo backend (ou desde o último reset). Útil para monitorar
    falsos positivos/negativos do prompt de classificação ao longo do
    tempo (ver risco registrado na Etapa 1 do plano de refatoração).
    """
    return compute_guardrail_rejection_rate()


@router.get("/api/metrics/query-planning-cache")
async def get_query_planning_cache_metrics():
    """Etapa 6 — taxa de acerto do cache/fast-path de planejamento de
    consulta dos agentes de domínio (Etapa 2).

    Métrica process-wide, acumulada desde o início do processo. Evidencia
    o trade-off custo-atual/preparação-futura: enquanto a base tiver
    mapeamento trivial e/ou a flag USE_LLM_QUERY_PLANNING estiver
    desligada, a taxa fica em 100% (nenhuma chamada LLM).
    """
    return compute_cache_hit_rate()


@router.get("/api/analysis/{analysis_id}/report")
async def get_comparative_report(analysis_id: str):
    """Return the comparative textual report for a completed analysis."""
    results = active_results.get(analysis_id, {})

    if "comparative_report" in results:
        return {"report": results["comparative_report"]}

    star_result = results.get("star")
    hier_result = results.get("hierarchical")
    if not star_result or not hier_result:
        raise HTTPException(
            status_code=404,
            detail="Report not available. Both topologies must complete first.",
        )

    quality = results.get("quality_metrics")
    if not quality:
        raise HTTPException(
            status_code=404,
            detail="Quality metrics not computed yet. Access /quality first.",
        )

    report = generate_comparative_report(
        quality=quality,
        star_agent_metrics=[],
        hier_agent_metrics=[],
        star_result=star_result,
        hier_result=hier_result,
    )
    active_results[analysis_id]["comparative_report"] = report
    return {"report": report}
