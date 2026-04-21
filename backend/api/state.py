"""
Shared in-memory state for active analyses.

Stores queues, threads, results and agent metrics per analysis ID.
Accessed by routes, WebSocket handler and thread runners.
"""

from __future__ import annotations

import threading
from queue import Queue
from typing import Any

from db.neo4j_client import Neo4jClient

# analysisId → shared Queue for WebSocket streaming
active_queues: dict[str, Queue] = {}

# analysisId → [thread_star, thread_hierarchical]
active_threads: dict[str, list[threading.Thread]] = {}

# analysisId → {"star": result, "hierarchical": result, "quality_metrics": ..., "comparative_report": ...}
active_results: dict[str, dict[str, Any]] = {}

# analysisId → {"star": [agent_metrics], "hierarchical": [agent_metrics]}
active_agent_metrics: dict[str, dict[str, list[dict]]] = {}


def get_neo4j_client() -> Neo4jClient:
    """Create a new Neo4j client instance."""
    return Neo4jClient()
