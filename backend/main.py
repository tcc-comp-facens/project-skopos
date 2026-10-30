"""
FastAPI application entry point.

Creates the app, configures CORS, and registers API routers.
All endpoint logic lives in api/routes.py and api/websocket.py.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from api.websocket import router as ws_router
from api.chat_websocket import router as chat_ws_router
from core import llm_client

# Re-export shared state and models so existing imports from "main" still work.
# This keeps backward compatibility with tests that do `from main import ...`.
from api.state import active_queues, active_threads, active_results  # noqa: F401
from api.state import get_neo4j_client as _get_neo4j_client  # noqa: F401

load_dotenv()

# LOG_LEVEL=DEBUG expõe o conteúdo completo dos prompts enviados ao LLM
# (core/llm_client.py) — INFO (default) só mostra um preview truncado.
# %(threadName)s permite distinguir execuções concorrentes de estrela e
# hierárquica (cada análise dispara as duas em threads daemon separadas).
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
)

# Modelos fixos e presença da chave, logo no startup. A chave nunca é
# logada, só se ela existe (o sistema cai em modo determinístico/fallback
# sem ela).
logging.getLogger(__name__).info(
    "LLM: análises=%s, juiz RAGAS=gpt-5-mini, api_key=%s",
    llm_client.MODELO_ANALISE,
    "configurada" if llm_client.has_api_key() else f"AUSENTE ({llm_client.API_KEY_ENV})",
)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Garante as constraints dos nós gravados pela aplicação (Analise/MetricaExecucao).

    Melhor esforço: o banco indisponível no startup não impede a API de subir.
    """
    try:
        client = _get_neo4j_client()
        try:
            client.ensure_schema()
        finally:
            client.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("Neo4j: esquema não verificado no startup — %s", exc)
    yield


app = FastAPI(title="Multiagent Architecture Comparison", lifespan=_lifespan)

# CORS (Req 10.4)
origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(api_router)
app.include_router(ws_router)
app.include_router(chat_ws_router)
