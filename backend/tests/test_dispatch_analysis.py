"""Tests for api.dispatch — disparo de análise compartilhado por REST e chat.

Escrito antes/junto da refatoração de routes.py para travar a
compatibilidade retroativa do comportamento de POST /api/analysis
(Req 6.6 do spec realtime-chat-interface).
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


class TestCreateAnalysisEndpointBackwardCompat:
    """Garante que POST /api/analysis mantém o contrato de antes da refatoração."""

    def test_valid_request_returns_analysis_id(self):
        from main import app

        with patch(
            "api.routes.dispatch_analysis", return_value="fixed-analysis-id"
        ) as mock_dispatch:
            with TestClient(app) as client:
                response = client.post(
                    "/api/analysis",
                    json={
                        "dateFrom": 2019,
                        "dateTo": 2022,
                        "healthParams": {"dengue": True},
                        "useLlm": True,
                        "useLlmJudge": False,
                    },
                )

        assert response.status_code == 200
        assert response.json() == {"analysisId": "fixed-analysis-id"}
        mock_dispatch.assert_called_once()
        _, kwargs = mock_dispatch.call_args
        assert kwargs["date_from"] == 2019
        assert kwargs["date_to"] == 2022
        assert kwargs["health_params"] == ["dengue"]
        assert kwargs["interpreted_via"] == "form"

    def test_invalid_date_range_returns_400(self):
        from main import app

        with TestClient(app) as client:
            response = client.post(
                "/api/analysis",
                json={
                    "dateFrom": 2022,
                    "dateTo": 2019,
                    "healthParams": {"dengue": True},
                    "useLlm": True,
                    "useLlmJudge": False,
                },
            )

        assert response.status_code == 400

    def test_no_health_params_returns_400(self):
        from main import app

        with TestClient(app) as client:
            response = client.post(
                "/api/analysis",
                json={
                    "dateFrom": 2019,
                    "dateTo": 2022,
                    "healthParams": {},
                    "useLlm": True,
                    "useLlmJudge": False,
                },
            )

        assert response.status_code == 400


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
