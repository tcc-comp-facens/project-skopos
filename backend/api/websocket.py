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

from api.state import active_queues, active_results, active_threads, active_ws_generation
from core.llm_client import TokenBucket
from core.quality_metrics import compute_all_quality_metrics, generate_comparative_report

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/{analysis_id}")
async def websocket_endpoint(websocket: WebSocket, analysis_id: str):
    """Stream events from the shared ws_queue to the client.

    Events: chunk, done, error, metric.
    Closes when both architectures have sent 'done'. Only then is the
    per-analysis queue/thread bookkeeping discarded — a disconnect before
    that point (client reload, brief network blip, React StrictMode's
    dev-only double-connect) leaves the queue in place so a reconnect can
    resume consuming it; the analysis threads run to completion
    regardless of whether any client is currently connected.
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

    # Assume a geração mais recente — qualquer conexão anterior ainda
    # rodando para este analysis_id (ex.: a conexão "canário" do
    # double-connect de desenvolvimento do React StrictMode) percebe isso
    # no início da próxima iteração do laço abaixo e para de consumir, em
    # vez de competir pelos mesmos itens da fila.
    my_generation = active_ws_generation.get(analysis_id, 0) + 1
    active_ws_generation[analysis_id] = my_generation

    done_count = 0
    loop = asyncio.get_event_loop()
    captured_agent_metrics: dict[str, list[dict]] = {"star": [], "hierarchical": []}
    captured_wall_clock: dict[str, float] = {"star": 0, "hierarchical": 0}

    try:
        while done_count < 2:
            if active_ws_generation.get(analysis_id) != my_generation:
                logger.info(
                    "WS %s: conexão substituída por uma mais nova, parando "
                    "de consumir (done_count=%d)",
                    analysis_id[:8], done_count,
                )
                return

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
                    captured_wall_clock[arch] = payload.get("totalExecutionTimeMs", 0)

            try:
                await websocket.send_json(event)
            except Exception:
                # O socket desta conexão já morreu (ex.: a conexão
                # "canário" do double-connect de dev do React StrictMode,
                # fechada do lado do cliente entre o get() acima e este
                # send). Sem isso, o evento já retirado da fila (Queue.get
                # é destrutivo) se perderia pra sempre — nem esta conexão
                # consegue entregá-lo, nem uma futura reconexão o veria de
                # novo. Devolve na FRENTE da fila (não no fim, pra não
                # embaralhar a ordem dos chunks de texto) para quem quer
                # que seja a próxima conexão a consumir. Acessa
                # queue.queue/mutex/not_empty diretamente (não expostos
                # como método público do stdlib, mas é o mesmo estado que
                # Queue.put() mexeria, só com appendleft em vez de append).
                with ws_queue.mutex:
                    ws_queue.queue.appendleft(event)
                    ws_queue.not_empty.notify()
                raise

            if event_type == "done":
                done_count += 1
                logger.info(
                    "WS %s: done_count now %d", analysis_id[:8], done_count,
                )

        # Loop terminou normalmente (done_count==2, não por desconexão) —
        # só agora é seguro descartar a fila. Popar isso em qualquer
        # desconexão (bug corrigido: estava num `finally` que rodava mesmo
        # em desconexões prematuras) derrubava uma reconexão legítima —
        # ex.: o double-connect de desenvolvimento do React StrictMode
        # (mount->cleanup->mount) fecha uma conexão "canário" que nunca
        # devia contar como "a análise acabou"; a reconexão real caía em
        # `ws_queue is None` (linha ~38) e recebia "No active analysis
        # found" mesmo com as threads star/hierarchical ainda rodando e
        # gerando resultado de verdade.
        active_queues.pop(analysis_id, None)
        active_threads.pop(analysis_id, None)
        active_ws_generation.pop(analysis_id, None)

        # Both topologies done — compute quality metrics if results available
        results = active_results.get(analysis_id, {})
        star_result = results.get("star", {})
        hier_result = results.get("hierarchical", {})

        if star_result and hier_result:
            try:
                # Compute quality metrics WITHOUT LLM Judge first (fast)
                quality = compute_all_quality_metrics(
                    star_result=star_result,
                    hier_result=hier_result,
                    star_agent_metrics=captured_agent_metrics.get("star", []),
                    hier_agent_metrics=captured_agent_metrics.get(
                        "hierarchical", []
                    ),
                    use_llm_judge=False,
                    use_llm=results.get("use_llm", True),
                    star_wall_clock_ms=captured_wall_clock.get("star", 0),
                    hier_wall_clock_ms=captured_wall_clock.get("hierarchical", 0),
                    star_token_usage=results.get("star_token_usage"),
                    hier_token_usage=results.get("hier_token_usage"),
                    intent_token_usage=results.get("intent_token_usage"),
                )
                await websocket.send_json({
                    "analysisId": analysis_id,
                    "architecture": "both",
                    "type": "quality_metrics",
                    "payload": quality,
                })
                active_results[analysis_id]["quality_metrics"] = quality
                active_results[analysis_id]["star_wall_clock_ms"] = captured_wall_clock.get("star", 0)
                active_results[analysis_id]["hier_wall_clock_ms"] = captured_wall_clock.get("hierarchical", 0)

                report = generate_comparative_report(
                    quality=quality,
                    star_agent_metrics=captured_agent_metrics.get("star", []),
                    hier_agent_metrics=captured_agent_metrics.get(
                        "hierarchical", []
                    ),
                    data_coverage=star_result.get("data_coverage"),
                    star_wall_clock_ms=captured_wall_clock.get("star", 0),
                    hier_wall_clock_ms=captured_wall_clock.get("hierarchical", 0),
                    star_result=star_result,
                    hier_result=hier_result,
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

                # Run LLM Judge AFTER report is sent (can be slow due to retries)
                use_llm_judge = results.get("use_llm_judge", False)
                if use_llm_judge:
                    logger.info("WS %s: running LLM Judge (post-report)", analysis_id[:8])
                    # Notify frontend that LLM Judge is starting
                    await websocket.send_json({
                        "analysisId": analysis_id,
                        "architecture": "both",
                        "type": "llm_judge",
                        "payload": "",
                    })
                    from core.quality_metrics import (
                        compute_faithfulness,
                        compute_faithfulness_llm,
                        compute_token_cost,
                    )

                    judge_token_usage: dict[str, dict] = {}
                    for arch_key, arch_result in [("star", star_result), ("hierarchical", hier_result)]:
                        # Etapa 6: um bucket por arquitetura, cobrindo tanto o
                        # LLM Judge (score 1-5) quanto a faithfulness
                        # claim-based (D8) — ambos rodam sequencialmente na
                        # MainThread aqui, contabilizados juntos sob "llm_judge"
                        # (a categoria de custo "avaliação opcional extra").
                        with TokenBucket() as arch_bucket:
                            judge_result = compute_faithfulness_llm(
                                arch_result.get("correlacoes", []),
                                arch_result.get("anomalias", []),
                                arch_result.get("contexto_orcamentario", {}),
                                arch_result.get("texto_analise", ""),
                                caller=f"llm_judge-{arch_key}",
                            )
                            claims_result = compute_faithfulness(
                                arch_result.get("correlacoes", []),
                                arch_result.get("anomalias", []),
                                arch_result.get("texto_analise", ""),
                                arch_result.get("contexto_orcamentario", {}),
                                use_llm=True,
                                caller=f"faithfulness_claims-{arch_key}",
                            )
                        judge_token_usage[arch_key] = arch_bucket.snapshot()

                        # Store in active_results
                        if "quality_metrics" in active_results.get(analysis_id, {}):
                            qm = active_results[analysis_id]["quality_metrics"]
                            if arch_key in qm.get("quality", {}):
                                qm["quality"][arch_key]["faithfulness_llm"] = judge_result
                                qm["quality"][arch_key]["faithfulness_claims"] = claims_result
                            qm.setdefault("cost", {})["llm_judge"] = {
                                k: compute_token_cost(v) for k, v in judge_token_usage.items()
                            }

                    # Build combined text for streaming
                    star_judge = active_results.get(analysis_id, {}).get(
                        "quality_metrics", {}
                    ).get("quality", {}).get("star", {}).get("faithfulness_llm", {})
                    hier_judge = active_results.get(analysis_id, {}).get(
                        "quality_metrics", {}
                    ).get("quality", {}).get("hierarchical", {}).get("faithfulness_llm", {})

                    star_score = star_judge.get("score", 0)
                    hier_score = hier_judge.get("score", 0)
                    star_just = star_judge.get("justificativa", "")
                    hier_just = hier_judge.get("justificativa", "")

                    judge_text = (
                        "━━━ LLM-as-Judge — Avaliação de Fidelidade ━━━\n"
                        "\n"
                        "SCORES\n"
                        f"★ Estrela: {star_score}/5\n"
                        f"◆ Hierárquica: {hier_score}/5\n"
                        "\n"
                        "JUSTIFICATIVAS\n"
                    )
                    if star_just:
                        judge_text += f"★ Estrela\n{star_just}\n\n"
                    if hier_just:
                        judge_text += f"◆ Hierárquica\n{hier_just}\n"

                    # Stream judge text in chunks
                    for i in range(0, len(judge_text), chunk_size):
                        chunk = judge_text[i : i + chunk_size]
                        await websocket.send_json({
                            "analysisId": analysis_id,
                            "architecture": "both",
                            "type": "llm_judge",
                            "payload": chunk,
                        })
                    await websocket.send_json({
                        "analysisId": analysis_id,
                        "architecture": "both",
                        "type": "llm_judge_done",
                        "payload": "",
                    })
                    logger.info("WS %s: LLM Judge results sent", analysis_id[:8])
                    logger.info("WS %s: LLM Judge metrics sent", analysis_id[:8])

            except Exception as exc:
                logger.error(
                    "WS %s: quality metrics computation failed: %s",
                    analysis_id[:8],
                    exc,
                )

    except WebSocketDisconnect:
        logger.info(
            "WebSocket disconnected for analysis %s antes de done_count=2 "
            "(done_count=%d) — fila mantida para uma possível reconexão "
            "retomar o consumo; a análise em si continua rodando nas "
            "threads star/hierarchical, independente da conexão.",
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
