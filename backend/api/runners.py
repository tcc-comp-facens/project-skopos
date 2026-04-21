"""
Thread runners for star and hierarchical architecture pipelines.

Each runner executes in a dedicated daemon thread, stores results
in shared state, persists completion metadata in Neo4j, and sends
a 'done' sentinel to the WebSocket queue.

Requirements: 9.1, 10.4, 11.3, 11.4
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from queue import Queue
from typing import Any

from agents.star.orchestrator import OrquestradorEstrela
from agents.hierarchical.coordinator import CoordenadorGeral
from db.neo4j_client import Neo4jClient
from api.state import active_results, get_neo4j_client

logger = logging.getLogger(__name__)


def _persist_topology_result(
    neo4j_client: Neo4jClient,
    analysis_id: str,
    architecture: str,
    message_count: int,
    texto_analise: str,
) -> None:
    """Persist topology completion metadata in Neo4j (Req 11.3, 11.4)."""
    completed_at = datetime.now(timezone.utc).isoformat()

    if architecture == "star":
        query = """
        MATCH (a:Analise {id: $analysisId})
        SET a.starStatus       = 'completed',
            a.starMessageCount = $messageCount,
            a.starTextAnalysis = $textoAnalise,
            a.starCompletedAt  = $completedAt
        """
    else:
        query = """
        MATCH (a:Analise {id: $analysisId})
        SET a.hierStatus       = 'completed',
            a.hierMessageCount = $messageCount,
            a.hierTextAnalysis = $textoAnalise,
            a.hierCompletedAt  = $completedAt
        """

    with neo4j_client._driver.session() as session:
        session.run(
            query,
            analysisId=analysis_id,
            messageCount=message_count,
            textoAnalise=texto_analise,
            completedAt=completed_at,
        )


def run_star(analysis_id: str, params: dict[str, Any], ws_queue: Queue) -> None:
    """Execute the star architecture pipeline in a dedicated thread."""
    neo4j_client: Neo4jClient | None = None
    try:
        neo4j_client = get_neo4j_client()
        orchestrator = OrquestradorEstrela(
            agent_id=f"star-orch-{uuid.uuid4().hex[:8]}",
            neo4j_client=neo4j_client,
        )
        result = orchestrator.run(analysis_id, params, ws_queue)

        if analysis_id not in active_results:
            active_results[analysis_id] = {}
        active_results[analysis_id]["star"] = result

        _persist_topology_result(
            neo4j_client,
            analysis_id,
            architecture="star",
            message_count=result.get("message_count", 0),
            texto_analise=result.get("texto_analise", ""),
        )

        ws_queue.put({
            "analysisId": analysis_id,
            "architecture": "star",
            "type": "done",
            "payload": "",
        })
    except Exception as exc:
        logger.error("Star thread failed: %s", exc)
        ws_queue.put({
            "analysisId": analysis_id,
            "architecture": "star",
            "type": "done",
            "payload": "",
        })
    finally:
        try:
            if neo4j_client is not None:
                neo4j_client.close()
        except Exception:
            pass


def run_hierarchical(analysis_id: str, params: dict[str, Any], ws_queue: Queue) -> None:
    """Execute the hierarchical architecture pipeline in a dedicated thread."""
    neo4j_client: Neo4jClient | None = None
    try:
        neo4j_client = get_neo4j_client()
        coordinator = CoordenadorGeral(
            agent_id=f"hier-coord-{uuid.uuid4().hex[:8]}",
            neo4j_client=neo4j_client,
        )
        result = coordinator.run(analysis_id, params, ws_queue)

        if analysis_id not in active_results:
            active_results[analysis_id] = {}
        active_results[analysis_id]["hierarchical"] = result

        _persist_topology_result(
            neo4j_client,
            analysis_id,
            architecture="hierarchical",
            message_count=result.get("message_count", 0),
            texto_analise=result.get("texto_analise", ""),
        )

        ws_queue.put({
            "analysisId": analysis_id,
            "architecture": "hierarchical",
            "type": "done",
            "payload": "",
        })
    except Exception as exc:
        logger.error("Hierarchical thread failed: %s", exc)
        ws_queue.put({
            "analysisId": analysis_id,
            "architecture": "hierarchical",
            "type": "done",
            "payload": "",
        })
    finally:
        try:
            if neo4j_client is not None:
                neo4j_client.close()
        except Exception:
            pass
