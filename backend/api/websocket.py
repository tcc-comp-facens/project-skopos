"""
WebSocket endpoint for real-time streaming of analysis events.

Streams chunk, done, error, metric events from both architectures,
then computes quality metrics and streams the comparative report.

Requirements: 8.1, 8.6
"""

from __future__ import annotations

import asyncio
import logging
from queue import Empty

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.state import active_queues, active_results, active_threads
from core.quality_metrics import compute_all_quality_metrics, generate_comparative_report

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/{analysis_id}")
async def websocket_endpoint(websocket: WebSocket, analysis_id: str):
    """Stream events from the shared ws_queue to the client.

    Events: chunk, done, error, metric.
    Closes when both architectures have sent 'done'.
    On client disconnect, cleans up queues and threads (Req 8.6).
    """
    await websocket.accept()

    ws_queue = active_queues.get(analysis_id)
    if ws_queue is None:
        await websocket.send_json({
            "analysisId": analysis_id,
            "architecture": "",
            "type": "error",
            "payload": "No active analysis found for this ID",
        })
        await websocket.close()
        return

    done_count = 0
    loop = asyncio.get_event_loop()
    captured_agent_metrics: dict[str, list[dict]] = {"star": [], "hierarchical": []}
    captured_message_counts: dict[str, int] = {"star": 0, "hierarchical": 0}

    try:
        while done_count < 2:
            try:
                event = await loop.run_in_executor(
                    None, lambda: ws_queue.get(timeout=1.0)
                )
            except Empty:
                continue

            event_type = event.get("type", "?")
            event_arch = event.get("architecture", "?")
            logger.info(
                "WS %s: sending event type=%s arch=%s (done_count=%d)",
                analysis_id[:8], event_type, event_arch, done_count,
            )

            # Capture agent metrics from metric events for quality computation
            if event_type == "metric" and isinstance(event.get("payload"), dict):
                payload = event["payload"]
                arch = payload.get("architecture", "")
                if arch in captured_agent_metrics:
                    captured_agent_metrics[arch] = payload.get("agentMetrics", [])
                    captured_message_counts[arch] = payload.get("messageCount", 0)

            await websocket.send_json(event)

            if event_type == "done":
                done_count += 1
                logger.info(
                    "WS %s: done_count now %d", analysis_id[:8], done_count,
                )

        # Both topologies done — compute quality metrics if results available
        results = active_results.get(analysis_id, {})
        star_result = results.get("star", {})
        hier_result = results.get("hierarchical", {})

        if star_result and hier_result:
            try:
                quality = compute_all_quality_metrics(
                    star_result=star_result,
                    hier_result=hier_result,
                    star_agent_metrics=captured_agent_metrics.get("star", []),
                    hier_agent_metrics=captured_agent_metrics.get(
                        "hierarchical", []
                    ),
                    star_message_count=captured_message_counts.get("star", 0),
                    hier_message_count=captured_message_counts.get(
                        "hierarchical", 0
                    ),
                    use_llm_judge=False,
                )
                await websocket.send_json({
                    "analysisId": analysis_id,
                    "architecture": "both",
                    "type": "quality_metrics",
                    "payload": quality,
                })
                active_results[analysis_id]["quality_metrics"] = quality

                report = generate_comparative_report(
                    quality=quality,
                    star_agent_metrics=captured_agent_metrics.get("star", []),
                    hier_agent_metrics=captured_agent_metrics.get(
                        "hierarchical", []
                    ),
                    star_message_count=captured_message_counts.get("star", 0),
                    hier_message_count=captured_message_counts.get(
                        "hierarchical", 0
                    ),
                    data_coverage=star_result.get("data_coverage"),
                )
                active_results[analysis_id]["comparative_report"] = report

                chunk_size = 80
                for i in range(0, len(report), chunk_size):
                    chunk = report[i : i + chunk_size]
                    await websocket.send_json({
                        "analysisId": analysis_id,
                        "architecture": "both",
                        "type": "chunk",
                        "payload": chunk,
                    })
                await websocket.send_json({
                    "analysisId": analysis_id,
                    "architecture": "both",
                    "type": "done",
                    "payload": "",
                })

                logger.info(
                    "WS %s: comparative report sent (%d chars)",
                    analysis_id[:8],
                    len(report),
                )
            except Exception as exc:
                logger.error(
                    "WS %s: quality metrics computation failed: %s",
                    analysis_id[:8],
                    exc,
                )

    except WebSocketDisconnect:
        logger.info(
            "WebSocket disconnected for analysis %s — cleaning up (done_count=%d)",
            analysis_id,
            done_count,
        )
    except Exception as exc:
        logger.error(
            "WebSocket error for analysis %s: %s (done_count=%d)",
            analysis_id,
            exc,
            done_count,
        )
    finally:
        active_queues.pop(analysis_id, None)
        active_threads.pop(analysis_id, None)
