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

# Re-export shared state and models so existing imports from "main" still work.
# This keeps backward compatibility with tests that do `from main import ...`.
from api.state import active_queues, active_threads, active_results, active_agent_metrics  # noqa: F401
from api.models import (  # noqa: F401
    AnalysisRequest,
    AnalysisResponse,
    HealthParams,
    health_params_to_list as _health_params_to_list,
    validate_analysis_params as _validate_analysis_params,
)
from api.state import get_neo4j_client as _get_neo4j_client  # noqa: F401

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
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
