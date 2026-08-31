"""
WebSocket endpoint for real-time streaming of analysis events.

Streams chunk, done, error, metric events from both architectures,
then computes quality metrics and streams the comparative report.

A ordem é: métricas determinísticas → avaliação RAGAS → relatório. O
RAGAS (`core/ragas_metrics.py`) roda sempre, transmitido nos eventos
`ragas`/`ragas_done`, e vem **antes** do relatório porque a conclusão do
relatório decide a topologia vencedora pela fidelidade que ele mede.
Essa é a única parte do cálculo de métricas que gasta LLM — tudo em
`core/quality_metrics.py` é determinístico e já foi para o cliente antes.

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

ARCH_LABELS = [("star", "★ Estrela"), ("hierarchical", "◆ Hierárquica")]

# Rótulos legíveis das métricas da RAGAS, na ordem dos três aspectos de
# qualidade do paper (Es et al., 2024): fidelidade, relevância da
# resposta, relevância do contexto.
RAGAS_METRIC_LABELS = [
    ("faithfulness", "Fidelidade aos dados"),
    ("answer_relevancy", "Relevância da resposta"),
    ("context_relevance", "Relevância do contexto"),
]


def _fallback_question(results: dict) -> str:
    """Pergunta sintética para análises que não vieram do chat.

    A avaliação RAGAS precisa de um `user_input`; hoje o chat é o único
    caminho de entrada, mas a análise pode ter sido disparada sem ele.
    """
    inicio = results.get("date_from")
    fim = results.get("date_to")
    periodo = f" no período de {inicio} a {fim}" if inicio and fim else ""
    return (
        "Qual a relação entre os gastos públicos em saúde e os indicadores "
        f"de resultado{periodo}?"
    )


def _format_ragas_report(per_arch: dict[str, dict]) -> str:
    """Bloco de texto comparando os scores RAGAS das duas arquiteturas."""
    lines = ["━━━ Avaliação RAGAS — Qualidade da Resposta ━━━", ""]

    unavailable = [
        payload.get("unavailable_reason")
        for payload in per_arch.values()
        if not payload.get("available")
    ]
    if unavailable and len(unavailable) == len(per_arch):
        lines.append("Avaliação não executada.")
        lines.append(f"Motivo: {unavailable[0]}")
        lines.append("")
        return "\n".join(lines)

    any_payload = next(iter(per_arch.values()), {})
    judge = any_payload.get("judge") or {}
    # O modelo de embeddings efetivamente usado pode diferir do pedido:
    # a cadeia de fallback entra quando o projeto não libera o configurado.
    pedido = judge.get("embedding_model", "?")
    usado = judge.get("embedding_model_used")
    embeddings = pedido if not usado or usado == pedido else f"{usado} (fallback de {pedido})"
    lines.append(
        f"Juiz: {judge.get('provider', '?')}/{judge.get('model', '?')} "
        f"| embeddings: {embeddings} "
        f"| ragas {any_payload.get('version', '?')}"
    )
    lines.append("")
    lines.append("SCORES (0 a 1, quanto maior melhor)")

    for key, label in RAGAS_METRIC_LABELS:
        lines.append(f"  {label}")
        for arch_key, arch_label in ARCH_LABELS:
            metric = (per_arch.get(arch_key, {}).get("metrics") or {}).get(key) or {}
            score = metric.get("score")
            shown = "não disponível" if score is None else f"{score:.2f}"
            lines.append(f"    {arch_label}: {shown}")
    lines.append("")

    for arch_key, arch_label in ARCH_LABELS:
        payload = per_arch.get(arch_key, {})
        sample = payload.get("sample") or {}
        total = sample.get("n_contexts_total", 0)
        lines.append(f"  {arch_label}: {total} achados avaliados")
        for err in payload.get("errors") or []:
            lines.append(f"    ! {err.get('metric')}: {err.get('error')}")

    lines.append("")
    return "\n".join(lines)


async def _run_ragas_evaluation(
    *,
    analysis_id: str,
    results: dict,
    star_result: dict,
    hier_result: dict,
) -> tuple[str, dict[str, dict]]:
    """Roda o RAGAS nas duas arquiteturas e encaixa o resultado no payload.

    As duas rodam concorrentes; a justificativa e a mecânica de isolamento
    do custo estão em `_avaliar`, abaixo.
    """
    from core.quality_metrics import compute_token_cost

    try:
        from core.ragas_metrics import evaluate_architecture
    except ImportError as exc:
        logger.error("WS %s: biblioteca ragas indisponível — %s", analysis_id[:8], exc)
        unavailable = {
            "framework": "ragas",
            "available": False,
            "unavailable_reason": f"biblioteca ragas não instalada ({exc})",
            "metrics": {},
        }
        per_arch = {k: unavailable for k, _ in ARCH_LABELS}
        return _format_ragas_report(per_arch), per_arch

    user_input = results.get("source_question") or _fallback_question(results)
    date_from = results.get("date_from")
    date_to = results.get("date_to")

    async def _avaliar(arch_key: str, arch_result: dict) -> tuple[str, dict, dict]:
        # O TokenBucket é aberto DENTRO da corrotina de propósito: cada
        # Task criada por `gather` recebe uma cópia do contexto, então um
        # bucket ativado aqui fica isolado do da outra arquitetura. Abri-lo
        # fora seria um bucket só, compartilhado, e o custo das duas
        # topologias se misturaria.
        with TokenBucket() as bucket:
            payload = await evaluate_architecture(
                arch_result,
                user_input,
                caller=f"ragas-{arch_key}",
                date_from=date_from,
                date_to=date_to,
            )
        return arch_key, payload, compute_token_cost(bucket.snapshot())

    # Concorrente: a precisão de contexto faz 1 chamada LLM por achado, em
    # série dentro da métrica (o laço da ragas não paraleliza). Com dezenas
    # de achados isso domina o tempo da avaliação, e rodar as duas
    # arquiteturas ao mesmo tempo corta o relógio pela metade.
    resultados = await asyncio.gather(
        _avaliar("star", star_result),
        _avaliar("hierarchical", hier_result),
    )

    per_arch: dict[str, dict] = {}
    cost: dict[str, dict] = {}
    for arch_key, payload, arch_cost in resultados:
        per_arch[arch_key] = payload
        cost[arch_key] = arch_cost

    qm = active_results.get(analysis_id, {}).get("quality_metrics")
    if qm:
        for arch_key, payload in per_arch.items():
            qm.setdefault("quality", {}).setdefault(arch_key, {})["ragas"] = payload
        qm.setdefault("cost", {})["ragas"] = cost

    return _format_ragas_report(per_arch), per_arch


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

                chunk_size = 80

                # A avaliação RAGAS roda ANTES do relatório porque o
                # veredito do relatório depende dela: o vencedor é
                # decidido por fidelidade primeiro (ver
                # quality_metrics._decide_winner). Publicar o relatório
                # antes significaria anunciar uma vencedora escolhida sem
                # a métrica que mais pesa no critério.
                logger.info("WS %s: rodando avaliação RAGAS", analysis_id[:8])
                await websocket.send_json({
                    "analysisId": analysis_id,
                    "architecture": "both",
                    "type": "ragas",
                    "payload": "",
                })

                ragas_text, ragas_payloads = await _run_ragas_evaluation(
                    analysis_id=analysis_id,
                    results=results,
                    star_result=star_result,
                    hier_result=hier_result,
                )

                for i in range(0, len(ragas_text), chunk_size):
                    await websocket.send_json({
                        "analysisId": analysis_id,
                        "architecture": "both",
                        "type": "ragas",
                        "payload": ragas_text[i : i + chunk_size],
                    })
                await websocket.send_json({
                    "analysisId": analysis_id,
                    "architecture": "both",
                    "type": "ragas_done",
                    "payload": "",
                })
                logger.info("WS %s: avaliação RAGAS enviada", analysis_id[:8])

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
                    ragas=ragas_payloads,
                )
                active_results[analysis_id]["comparative_report"] = report

                for i in range(0, len(report), chunk_size):
                    await websocket.send_json({
                        "analysisId": analysis_id,
                        "architecture": "both",
                        "type": "chunk",
                        "payload": report[i : i + chunk_size],
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
