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
import threading
import uuid
from datetime import datetime, timezone
from queue import Queue
from typing import Any

from fastapi import APIRouter, HTTPException

from api.models import (
    AnalysisRequest,
    AnalysisResponse,
    health_params_to_list,
    validate_analysis_params,
)
from api.state import (
    active_queues,
    active_results,
    active_threads,
    get_neo4j_client,
)
from api.runners import run_star, run_hierarchical
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

    analysis_id = str(uuid.uuid4())
    health_list = health_params_to_list(req.healthParams)

    neo4j_client = get_neo4j_client()
    try:
        neo4j_client.save_analise({
            "id": analysis_id,
            "dateFrom": req.dateFrom,
            "dateTo": req.dateTo,
            "healthParams": req.healthParams.model_dump(),
            "starStatus": "pending",
            "hierStatus": "pending",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

        with neo4j_client._driver.session() as session:
            session.run(
                """
                MATCH (a:Analise {id: $id}), (d:DespesaSIOPS)
                WHERE d.ano >= $dateFrom AND d.ano <= $dateTo
                MERGE (a)-[:POSSUI_DESPESA]->(d)
                """,
                id=analysis_id,
                dateFrom=req.dateFrom,
                dateTo=req.dateTo,
            )
            session.run(
                """
                MATCH (a:Analise {id: $id}), (i:IndicadorDataSUS)
                WHERE i.ano >= $dateFrom AND i.ano <= $dateTo
                  AND i.tipo IN $healthParams
                MERGE (a)-[:POSSUI_INDICADOR]->(i)
                """,
                id=analysis_id,
                dateFrom=req.dateFrom,
                dateTo=req.dateTo,
                healthParams=health_list,
            )
    finally:
        neo4j_client.close()

    ws_queue: Queue = Queue()
    active_queues[analysis_id] = ws_queue

    params: dict[str, Any] = {
        "date_from": req.dateFrom,
        "date_to": req.dateTo,
        "health_params": health_list,
        "use_llm": req.useLlm,
    }

    t_star = threading.Thread(
        target=run_star,
        args=(analysis_id, params, ws_queue),
        daemon=True,
    )
    t_hier = threading.Thread(
        target=run_hierarchical,
        args=(analysis_id, params, ws_queue),
        daemon=True,
    )
    active_threads[analysis_id] = [t_star, t_hier]
    t_star.start()
    t_hier.start()

    return AnalysisResponse(analysisId=analysis_id)


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
    """Return quality and efficiency metrics for a completed analysis."""
    results = active_results.get(analysis_id, {})
    star_result = results.get("star")
    hier_result = results.get("hierarchical")

    if "quality_metrics" in results:
        return results["quality_metrics"]

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
        star_message_count=star_result.get("message_count", 0),
        hier_message_count=hier_result.get("message_count", 0),
        use_llm_judge=False,
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
        star_message_count=star_result.get("message_count", 0),
        hier_message_count=hier_result.get("message_count", 0),
    )
    active_results[analysis_id]["comparative_report"] = report
    return {"report": report}
