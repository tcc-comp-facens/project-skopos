"""
WebSocket de chat — /ws/chat/{session_id}

Cuida apenas do turno de intenção: interpreta a mensagem do usuário,
dispara a análise (reaproveitando dispatch_analysis) e confirma em texto.
O streaming dos resultados da análise continua exclusivamente pelo
/ws/{analysisId} existente (api/websocket.py) — esse WebSocket de chat
não lê a mesma ws_queue (consumidor único, removida ao terminar), então
não pode competir por ela.

Protocolo:
  cliente → servidor:
    {"type": "user_message", "payload": {"text": str, "useLlm": bool,
     "useLlmJudge": bool}}
  servidor → cliente:
    {"type": "user_ack", "payload": ""}
    {"type": "system_chunk", "payload": str}      # texto em ~80 chars
    {"type": "system_done", "payload": ""}
    {"type": "analysis_started", "payload": analysisId}
    {"type": "error", "payload": str}

Validações adicionais (não descritas na spec original, importantes para
uma aba pública): session_id deve ser um UUID válido; mensagens acima de
MAX_MESSAGE_LENGTH são rejeitadas; apenas uma rodada por vez por sessão
é aceita no servidor (não confia só no frontend desabilitar o input).

Requisitos: 4.1-4.6, 6.1-6.6, 8.1-8.5 (spec realtime-chat-interface)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agents.intent import AgenteInterpretacaoIntencao
from agents.intent.agente_interpretacao_intencao import (
    MISSING_LLM_UNAVAILABLE,
    MISSING_OUT_OF_SCOPE,
    MISSING_TEXT,
)
from api.chat_runner import run_chat_analysis
from api.dispatch import get_available_year_range
from api.state import active_chat_sessions, get_neo4j_client
from core import guardrail_stats
from core.llm_client import TokenBucket

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_MESSAGE_LENGTH = 1000
CHUNK_SIZE = 80


async def _send_chunks(websocket: WebSocket, text: str) -> None:
    """Transmite texto em chunks (~80 chars) seguido de system_done."""
    for i in range(0, len(text), CHUNK_SIZE):
        await websocket.send_json({
            "type": "system_chunk",
            "payload": text[i : i + CHUNK_SIZE],
        })
    await websocket.send_json({"type": "system_done", "payload": ""})


@router.websocket("/ws/chat/{session_id}")
async def chat_websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """Aceita conexões de chat, interpreta mensagens e dispara análises.

    Requisitos: 4.1, 4.2, 8.1
    """
    try:
        uuid.UUID(session_id)
    except ValueError:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    year_range = get_available_year_range()
    min_year, max_year = year_range if year_range else (None, None)
    # Cliente dedicado à sessão de chat inteira (não por mensagem) — usado
    # só para memória episódica (get_past_analises), leitura leve e
    # infrequente; fechado no finally ao encerrar a conexão.
    intent_neo4j_client = get_neo4j_client()
    interpreter = AgenteInterpretacaoIntencao(
        agent_id=f"intent-{uuid.uuid4().hex[:8]}",
        min_year=min_year,
        max_year=max_year,
        neo4j_client=intent_neo4j_client,
    )

    try:
        while True:
            raw = await websocket.receive_json()

            if raw.get("type") != "user_message":
                continue

            payload = raw.get("payload") or {}
            text = str(payload.get("text", ""))
            use_llm = bool(payload.get("useLlm", True))
            use_llm_judge = bool(payload.get("useLlmJudge", False))
            use_self_check = bool(payload.get("useSelfCheck", False))

            await websocket.send_json({"type": "user_ack", "payload": ""})

            if len(text) > MAX_MESSAGE_LENGTH:
                await websocket.send_json({
                    "type": "error",
                    "payload": (
                        f"Mensagem muito longa (máximo {MAX_MESSAGE_LENGTH} "
                        "caracteres)."
                    ),
                })
                continue

            if active_chat_sessions.get(session_id):
                await websocket.send_json({
                    "type": "error",
                    "payload": "Aguarde a resposta da pergunta anterior antes de enviar outra.",
                })
                continue

            active_chat_sessions[session_id] = True
            try:
                logger.info(
                    "Chat WS %s: interpretando mensagem (%d chars): %r",
                    session_id[:8], len(text), text[:120],
                )
                with TokenBucket() as intent_bucket:
                    result = interpreter.parse(text)
                logger.info(
                    "Chat WS %s: interpretação -> success=%s missing=%s",
                    session_id[:8], result.success, result.missing,
                )

                # Etapa 6 — registra a decisão do guardrail (dentro/fora de
                # escopo), exceto quando não houve decisão real: mensagem
                # vazia (nem chega ao LLM) ou falha técnica do LLM.
                if MISSING_TEXT not in result.missing and MISSING_LLM_UNAVAILABLE not in result.missing:
                    guardrail_stats.record_guardrail_decision(
                        out_of_scope=MISSING_OUT_OF_SCOPE in result.missing
                    )

                if not result.success:
                    await _send_chunks(websocket, result.clarification_message)
                    continue

                assert result.params is not None
                analysis_id, confirmation = run_chat_analysis(
                    params=result.params,
                    use_llm=use_llm,
                    use_llm_judge=use_llm_judge,
                    source_question=text,
                    interpreted_via=result.interpreted_via,
                    interpreter=interpreter,
                    use_self_check=use_self_check,
                    intent_token_usage=intent_bucket.snapshot(),
                )

                await websocket.send_json({
                    "type": "analysis_started",
                    "payload": analysis_id,
                })
                await _send_chunks(websocket, confirmation)
            except Exception as exc:
                logger.error(
                    "Chat WS %s: erro ao processar turno — %s", session_id[:8], exc
                )
                await websocket.send_json({
                    "type": "error",
                    "payload": "Ocorreu um erro ao processar sua pergunta. Tente novamente.",
                })
            finally:
                active_chat_sessions[session_id] = False

    except WebSocketDisconnect:
        logger.info("Chat WS %s: cliente desconectado", session_id[:8])
    finally:
        active_chat_sessions.pop(session_id, None)
        try:
            intent_neo4j_client.close()
        except Exception:
            pass
