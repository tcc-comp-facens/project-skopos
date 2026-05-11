"""Tests for OrquestradorEstrela.run() with mocked agents."""

import pytest
from queue import Queue
from unittest.mock import MagicMock, patch

from agents.star.orchestrator import OrquestradorEstrela


@pytest.fixture
def neo4j_client():
    client = MagicMock()
    client.get_despesas.return_value = [
        {"subfuncao": 305, "subfuncaoNome": "Vigilância", "ano": 2020, "valor": 100.0},
        {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 200.0},
    ]
    client.get_indicadores.return_value = [
        {"tipo": "dengue", "ano": 2020, "valor": 30.0},
        {"tipo": "vacinacao", "ano": 2020, "valor": 80.0},
    ]
    client.save_metrica = MagicMock()
    return client


@pytest.fixture
def ws_queue():
    return Queue()


@pytest.fixture
def orchestrator(neo4j_client):
    return OrquestradorEstrela("test-orch", neo4j_client)


class TestAllAgentsCalled:
    def test_all_domain_agents_called_when_all_params(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue", "covid", "internacoes", "vacinacao", "mortalidade"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-1", params, ws_queue)
        # All 4 domain agents should have been called
        # get_despesas called once per domain agent
        assert neo4j_client.get_despesas.call_count == 4


class TestSubsetAgents:
    def test_only_relevant_agents_called_for_dengue(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-2", params, ws_queue)
        # Only vigilancia_epidemiologica should be called (1 agent)
        assert neo4j_client.get_despesas.call_count == 1


class TestGracefulDegradation:
    def test_pipeline_continues_when_agent_fails(self, neo4j_client, ws_queue):
        # Make get_despesas fail on first call, succeed on second
        neo4j_client.get_despesas.side_effect = [
            Exception("Neo4j timeout"),
            [{"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 200.0}],
        ]
        neo4j_client.get_indicadores.side_effect = [
            Exception("Neo4j timeout"),
            [{"tipo": "vacinacao", "ano": 2020, "valor": 80.0}],
        ]
        orch = OrquestradorEstrela("test-orch-fail", neo4j_client)
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue", "vacinacao"],
            "use_llm": False,
        }
        # Should not raise
        result = orch.run("analysis-3", params, ws_queue)
        assert "correlacoes" in result
        assert "anomalias" in result


class TestMessageCount:
    def test_message_count_equals_two_times_agent_calls(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-4", params, ws_queue)
        # 1 domain agent + contexto + correlacao + anomalias + sintetizador = 5 calls × 2
        assert result["message_count"] == 10


class TestResultKeys:
    def test_result_contains_expected_keys(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-5", params, ws_queue)
        expected_keys = {
            "despesas", "indicadores", "dados_cruzados",
            "contexto_orcamentario", "correlacoes", "anomalias",
            "texto_analise", "message_count", "data_coverage",
        }
        assert expected_keys.issubset(result.keys())


class TestErrorEventsOnFailure:
    def test_error_events_sent_to_ws_queue(self, ws_queue):
        """When agent.query() raises at orchestrator level, error event is sent."""
        mock_client = MagicMock()
        mock_client.save_metrica = MagicMock()
        orch = OrquestradorEstrela("test-orch-err", mock_client)
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "use_llm": False,
        }
        # Patch the domain agent's query method to raise at orchestrator level
        with patch(
            "agents.star.orchestrator.AgenteVigilanciaEpidemiologica"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.query.side_effect = Exception("Agent crashed")
            orch.run("analysis-6", params, ws_queue)

        # Collect error events
        errors = []
        while not ws_queue.empty():
            event = ws_queue.get()
            if event.get("type") == "error":
                errors.append(event)
        assert len(errors) >= 1
