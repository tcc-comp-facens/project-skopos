"""
Shared in-memory state for active analyses.

Stores queues, threads and results per analysis ID.
Accessed by routes, WebSocket handler and thread runners.
"""

from __future__ import annotations

import threading
from queue import Queue
from typing import Any

from db.neo4j_client import Neo4jClient

# analysisId → shared Queue for WebSocket streaming
active_queues: dict[str, Queue] = {}

# analysisId → geração da conexão WS que tem permissão de consumir
# active_queues[analysisId] agora. Incrementado a cada nova conexão
# aceita para o mesmo analysisId — usado por api.websocket para uma
# conexão mais antiga (ex.: a conexão "canário" do double-connect de
# desenvolvimento do React StrictMode, mount->cleanup->mount) perceber
# que foi substituída e parar de consumir, em vez de competir pelos
# mesmos itens da fila com a conexão nova e descartar silenciosamente
# os que ela "vencer" na corrida (o socket dela já pode estar fechado
# do lado do cliente nesse ponto, então esses itens nunca chegariam a
# lugar nenhum).
active_ws_generation: dict[str, int] = {}

# analysisId → [thread_star, thread_hierarchical]
active_threads: dict[str, list[threading.Thread]] = {}

# analysisId → {"star": result, "hierarchical": result, "quality_metrics": ..., "comparative_report": ...}
active_results: dict[str, dict[str, Any]] = {}

# chat sessionId → True enquanto uma rodada de chat está em andamento
# nessa sessão (garante uma rodada por vez mesmo se o frontend não
# desabilitar o input a tempo).
active_chat_sessions: dict[str, bool] = {}


def get_neo4j_client() -> Neo4jClient:
    """Create a new Neo4j client instance."""
    return Neo4jClient()
