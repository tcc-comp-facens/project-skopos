"""
REST API endpoints.

Endpoints:
  POST /api/analysis       — Start a new analysis
  GET  /api/analysis/{id}  — Retrieve analysis result
  GET  /api/analysis/{id}/quality — Quality metrics
  GET  /api/analysis/{id}/report  — Comparative report
  GET  /api/benchmarks     — All benchmark metrics

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 10.4
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.models import (
    AnalysisRequest,
    AnalysisResponse,
    health_params_to_list,
    validate_analysis_params,
)
from api.dispatch import dispatch_analysis, get_available_year_range
from api.state import active_results
from core.quality_metrics import compute_all_quality_metrics, generate_comparative_report

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/analysis", response_model=AnalysisResponse)
async def create_analysis(req: AnalysisRequest):
    """Start a new analysis — validates params, creates record, launches threads.

    Req 9.1: POST /api/analysis
    Req 9.4, 9.5: Validate params, return 400 on invalid
    Req 10.4: Dispatch both architectures in parallel
    """
    errors = validate_analysis_params(req)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    health_list = health_params_to_list(req.healthParams)

    analysis_id = dispatch_analysis(
        date_from=req.dateFrom,
        date_to=req.dateTo,
        health_params=health_list,
        use_llm=req.useLlm,
        use_llm_judge=req.useLlmJudge,
        interpreted_via="form",
    )

    return AnalysisResponse(analysisId=analysis_id)


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
async def get_quality_metrics(analysis_id: str, use_llm_judge: bool | None = None):
    """Return quality and efficiency metrics for a completed analysis."""
    results = active_results.get(analysis_id, {})
    star_result = results.get("star")
    hier_result = results.get("hierarchical")

    # If not explicitly passed, use the value from the original analysis request
    if use_llm_judge is None:
        use_llm_judge = results.get("use_llm_judge", False)

    # Return cached result only if llm_judge setting matches
    cached = results.get("quality_metrics")
    if cached and not use_llm_judge:
        return cached

    if not star_result or not hier_result:
        raise HTTPException(
            status_code=404,
            detail="Quality metrics not available. Both topologies must complete first.",
        )

    star_agent_metrics = results.get("star_agent_metrics", [])
    hier_agent_metrics = results.get("hier_agent_metrics", [])

    use_llm = results.get("use_llm", True)

    quality = compute_all_quality_metrics(
        star_result=star_result,
        hier_result=hier_result,
        star_agent_metrics=star_agent_metrics,
        hier_agent_metrics=hier_agent_metrics,
        use_llm_judge=use_llm_judge,
        use_llm=use_llm,
        star_wall_clock_ms=results.get("star_wall_clock_ms", 0),
        hier_wall_clock_ms=results.get("hier_wall_clock_ms", 0),
    )

    active_results[analysis_id]["quality_metrics"] = quality
    return quality


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
