"""Tests for AgenteCOVID — agente de saúde dedicado a COVID-19 (Fase 2),
substitui a metade "covid" de AgenteVigilanciaEpidemiologica (que só
consultava "casos", nunca "obitos").
"""

from unittest.mock import MagicMock

import pytest

from agents.domain.agente_covid import SISTEMA, SUBTIPOS, AgenteCOVID


@pytest.fixture
def neo4j_client():
    return MagicMock()


@pytest.fixture
def agente(neo4j_client):
    return AgenteCOVID("test-covid", neo4j_client)


class TestSemanticMemory:
    def test_sistema_is_covid(self, agente):
        assert agente.semantic_memory["sistema"] == "covid"

    def test_subtipos_has_casos_e_obitos(self, agente):
        assert agente.semantic_memory["subtipos"] == ["casos", "obitos"]

    def test_dimensoes_validas_is_empty(self, agente):
        """covid não tem dimensões válidas no schema atual."""
        assert agente.semantic_memory["dimensoes_validas"] == []


class TestProposeActions:
    def test_proposes_single_action_no_dimensao_deliberation(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
        })
        candidates = agente.propose_actions()
        assert candidates == [{"goal": "consultar_indicadores"}]

    def test_no_candidates_without_analysis_params(self, agente):
        assert agente.propose_actions() == []


class TestConsultarIndicadores:
    def test_query_calls_neo4j_with_casos_e_obitos(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = [
            {"tipo": "casos", "ano": 2020, "valor": 30.0},
            {"tipo": "obitos", "ano": 2020, "valor": 1.0},
        ]
        result = agente.query("a1", 2019, 2021)
        neo4j_client.get_indicadores_por_sistema.assert_called_once()
        args = neo4j_client.get_indicadores_por_sistema.call_args[0]
        assert args[0] == SISTEMA
        assert set(args[1]) == set(SUBTIPOS)
        assert len(result["indicadores"]) == 2

    def test_no_despesas_key_in_result(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = []
        result = agente.query("a1", 2019, 2021)
        assert "despesas" not in result

    def test_graceful_degradation_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.side_effect = Exception("Connection refused")
        result = agente.query("a1", 2019, 2021)
        assert result["indicadores"] == []
