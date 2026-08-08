"""Tests for api.dispatch — disparo de análise usado pelo chat.

O antigo POST /api/analysis (formulário com um botão por categoria de
indicador) foi removido — dispatch_analysis hoje só é chamado pelo chat
(api/chat_runner.py), mas continua genérico o suficiente para aceitar
qualquer `interpreted_via`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import dispatch
from api.state import active_queues, active_results, active_threads


@pytest.fixture
def neo4j_client():
    client = MagicMock()
    client._driver.session.return_value.__enter__.return_value = MagicMock()
    client.get_year_range.return_value = (2015, 2025)
    return client


@pytest.fixture(autouse=True)
def _patch_dispatch(neo4j_client):
    with patch("api.dispatch.get_neo4j_client", return_value=neo4j_client), \
         patch("api.dispatch.run_star"), \
         patch("api.dispatch.run_hierarchical"):
        yield


class TestDispatchAnalysis:
    def test_returns_id_and_registers_shared_state(self):
        analysis_id = dispatch.dispatch_analysis(
            date_from=2019,
            date_to=2022,
            health_params=["dengue"],
            use_llm=True,
            use_llm_judge=False,
        )

        assert analysis_id in active_queues
        assert analysis_id in active_threads
        assert analysis_id in active_results
        assert active_results[analysis_id]["use_llm"] is True
        assert active_results[analysis_id]["use_llm_judge"] is False

    def test_persists_source_question_and_interpreted_via(self, neo4j_client):
        dispatch.dispatch_analysis(
            date_from=2019,
            date_to=2022,
            health_params=["dengue"],
            use_llm=True,
            use_llm_judge=False,
            source_question="compare dengue de 2019 a 2022",
            interpreted_via="regex",
        )

        saved = neo4j_client.save_analise.call_args[0][0]
        assert saved["sourceQuestion"] == "compare dengue de 2019 a 2022"
        assert saved["interpretedVia"] == "regex"

    def test_form_dispatch_has_no_source_question(self, neo4j_client):
        dispatch.dispatch_analysis(
            date_from=2019,
            date_to=2022,
            health_params=["dengue"],
            use_llm=True,
            use_llm_judge=False,
            interpreted_via="form",
        )

        saved = neo4j_client.save_analise.call_args[0][0]
        assert saved["sourceQuestion"] is None
        assert saved["interpretedVia"] == "form"


class TestGetAvailableYearRange:
    def test_returns_range_from_neo4j(self, neo4j_client):
        result = dispatch.get_available_year_range()

        assert result == (2015, 2025)
        neo4j_client.close.assert_called_once()

    def test_returns_none_when_no_data(self, neo4j_client):
        neo4j_client.get_year_range.return_value = None

        result = dispatch.get_available_year_range()

        assert result is None


class TestDataRangeEndpoint:
    def test_returns_min_and_max_year(self):
        from main import app

        with patch("api.routes.get_available_year_range", return_value=(2015, 2025)):
            with TestClient(app) as client:
                response = client.get("/api/data-range")

        assert response.status_code == 200
        assert response.json() == {"minYear": 2015, "maxYear": 2025}

    def test_returns_nulls_when_no_data(self):
        from main import app

        with patch("api.routes.get_available_year_range", return_value=None):
            with TestClient(app) as client:
                response = client.get("/api/data-range")

        assert response.status_code == 200
        assert response.json() == {"minYear": None, "maxYear": None}
