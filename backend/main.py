"""
FastAPI application entry point.

Creates the app, configures CORS, and registers API routers.
All endpoint logic lives in api/routes.py and api/websocket.py.
"""

from __future__ import annotations

import logging
import os

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

# Qual provedor de LLM está de fato ativo (LLM_PROVIDER, default deepseek)
# logo no startup — a configuração vem de .env/env do container e é fácil
# de errar sem perceber; sem esta linha, só dá para descobrir o provedor
# em uso na primeira chamada ao LLM. A chave nunca é logada, só se ela
# existe (o sistema cai em modo determinístico/fallback sem ela).
_llm_provider = llm_client.get_provider()
logging.getLogger(__name__).info(
    "LLM: provider=%s, model=%s, api_key=%s",
    _llm_provider.name,
    llm_client.resolve_model(_llm_provider),
    "configurada" if llm_client.has_api_key(_llm_provider) else f"AUSENTE ({_llm_provider.api_key_env})",
)

app = FastAPI(title="Multiagent Architecture Comparison")

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
