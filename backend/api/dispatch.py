"""
Disparo de análise — lógica usada pelo WebSocket de chat (/ws/chat/{session_id}).

Extraído de api/routes.py originalmente para compartilhar entre o
formulário REST legado (POST /api/analysis, removido — a entrada por
botão-por-categoria foi substituída pelo chat) e o chat. Mantido como
módulo próprio por continuar sendo a persistência Neo4j + disparo de
threads compartilhado por api/chat_runner.py.

Requisitos: 9.1, 10.4, 6.1, 6.5 (spec realtime-chat-interface)
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from queue import Queue
from typing import Any

from api.runners import run_star, run_hierarchical
from api.state import active_queues, active_results, active_threads, get_neo4j_client

logger = logging.getLogger(__name__)


def get_available_year_range() -> tuple[int, int] | None:
    """Retorna (ano_min, ano_max) dos dados carregados no Neo4j, ou None."""
    neo4j_client = get_neo4j_client()
    try:
        return neo4j_client.get_year_range()
    finally:
        neo4j_client.close()


def dispatch_analysis(
    date_from: int,
    date_to: int,
    health_params: list[str],
    use_llm: bool,
    use_llm_judge: bool,
    source_question: str | None = None,
    interpreted_via: str | None = None,
    intent_summary: str | None = None,
    use_self_check: bool = False,
    intent_token_usage: dict[str, Any] | None = None,
) -> str:
    """Persiste a análise no Neo4j e dispara as threads star + hierarchical.

    Chamado por api/chat_runner.py (chat) — único caminho de entrada hoje.
    `source_question`/`interpreted_via` são opcionais e permitem auditar
    depois a qualidade da interpretação de intenção.

    `intent_summary` (opcional, só vem do chat) é o resumo de intenção
    produzido pelo AgenteInterpretacaoIntencao (Etapa 1 do plano de
    refatoração) — repassado no dict `params` para as duas arquiteturas,
    que é a camada de entrada estruturada compartilhada por ambas.

    `use_self_check` (opcional, default False — mesmo padrão de
    `use_llm_judge`) habilita a verificação pós-síntese via LLM (Etapa 4
    do plano de refatoração) em ambas as arquiteturas.

    `intent_token_usage` (opcional, só vem do chat) é o snapshot de custo
    de tokens da interpretação de intenção (Etapa 6) — persistido em
    `active_results` para ser reportado separado do custo por topologia
    no payload de `/api/analysis/{id}/quality`.

    Requisitos: 9.1, 10.4
    """
    analysis_id = str(uuid.uuid4())
    logger.info(
        "Analysis [%s]: disparando análise (periodo=%s-%s, health_params=%s, "
        "use_llm=%s, use_llm_judge=%s, use_self_check=%s, interpreted_via=%s, "
        "intent_summary=%r)",
        analysis_id[:8], date_from, date_to, health_params,
        use_llm, use_llm_judge, use_self_check, interpreted_via, intent_summary,
    )

    neo4j_client = get_neo4j_client()
    try:
        neo4j_client.save_analise({
            "id": analysis_id,
            "dateFrom": date_from,
            "dateTo": date_to,
            "healthParams": {p: True for p in health_params},
            "starStatus": "pending",
            "hierStatus": "pending",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceQuestion": source_question,
            "interpretedVia": interpreted_via,
        })
    finally:
        neo4j_client.close()

    ws_queue: Queue = Queue()
    active_queues[analysis_id] = ws_queue

    params: dict[str, Any] = {
        "date_from": date_from,
        "date_to": date_to,
        "health_params": health_params,
        "use_llm": use_llm,
        "use_llm_judge": use_llm_judge,
        "intent_summary": intent_summary,
        "use_self_check": use_self_check,
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
    active_results[analysis_id] = {
        "use_llm_judge": use_llm_judge,
        "use_llm": use_llm,
        "intent_token_usage": intent_token_usage,
    }
    t_star.start()
    t_hier.start()
    logger.info(
        "Analysis [%s]: threads star e hierarchical iniciadas", analysis_id[:8]
    )

    return analysis_id
