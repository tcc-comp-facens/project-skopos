"""
Disparo de análise — lógica compartilhada entre POST /api/analysis e o
WebSocket de chat (/ws/chat/{session_id}).

Extraído de api/routes.py para que ambos os caminhos de entrada (formulário
REST legado e chat) usem exatamente a mesma persistência Neo4j e o mesmo
disparo de threads, evitando divergência entre os dois fluxos.

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
) -> str:
    """Persiste a análise no Neo4j e dispara as threads star + hierarchical.

    Reaproveitado por routes.py::create_analysis (formulário REST) e por
    api/chat_runner.py (chat). `source_question`/`interpreted_via` são
    opcionais e só preenchidos quando a análise vem do chat — permitem
    auditar depois a qualidade da interpretação de intenção.

    `intent_summary` (opcional, só vem do chat) é o resumo de intenção
    produzido pelo AgenteInterpretacaoIntencao (Etapa 1 do plano de
    refatoração) — repassado no dict `params` para as duas arquiteturas,
    que é a camada de entrada estruturada compartilhada por ambas.

    Requisitos: 9.1, 10.4
    """
    analysis_id = str(uuid.uuid4())
    logger.info(
        "Analysis [%s]: disparando análise (periodo=%s-%s, health_params=%s, "
        "use_llm=%s, use_llm_judge=%s, interpreted_via=%s, intent_summary=%r)",
        analysis_id[:8], date_from, date_to, health_params,
        use_llm, use_llm_judge, interpreted_via, intent_summary,
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

        with neo4j_client._driver.session() as session:
            session.run(
                """
                MATCH (a:Analise {id: $id}), (d:DespesaSIOPS)
                WHERE d.ano >= $dateFrom AND d.ano <= $dateTo
                MERGE (a)-[:POSSUI_DESPESA]->(d)
                """,
                id=analysis_id,
                dateFrom=date_from,
                dateTo=date_to,
            )
            session.run(
                """
                MATCH (a:Analise {id: $id}), (i:IndicadorDataSUS)
                WHERE i.ano >= $dateFrom AND i.ano <= $dateTo
                  AND i.tipo IN $healthParams
                MERGE (a)-[:POSSUI_INDICADOR]->(i)
                """,
                id=analysis_id,
                dateFrom=date_from,
                dateTo=date_to,
                healthParams=health_params,
            )
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
    active_results[analysis_id] = {"use_llm_judge": use_llm_judge, "use_llm": use_llm}
    t_star.start()
    t_hier.start()
    logger.info(
        "Analysis [%s]: threads star e hierarchical iniciadas", analysis_id[:8]
    )

    return analysis_id
