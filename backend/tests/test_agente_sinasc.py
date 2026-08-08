"""Tests for AgenteSINASC — agente de saúde de nascidos vivos (Fase 2),
cobertura nova sem legado equivalente.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.domain.agente_sinasc import SISTEMA, SUBTIPOS, AgenteSINASC


@pytest.fixture
def neo4j_client():
    return MagicMock()


@pytest.fixture
def agente(neo4j_client):
    return AgenteSINASC("test-sinasc", neo4j_client)


class TestSemanticMemory:
    def test_sistema_is_sinasc(self, agente):
        assert agente.semantic_memory["sistema"] == "sinasc"

    def test_subtipos_has_nascidos_vivos(self, agente):
        assert agente.semantic_memory["subtipos"] == ["nascidos_vivos"]

    def test_dimensoes_validas_from_query_builder(self, agente):
        assert agente.semantic_memory["dimensoes_validas"] == [
            "POR_FAIXA_ETARIA", "POR_FAIXA_PESO",
        ]


class TestProposeActionsGeraCandidatosReais:
    def test_proposes_one_candidate_per_dimension_plus_no_break(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
        })
        candidates = agente.propose_actions()
        dimensoes = {c["dimensao"] for c in candidates}
        assert dimensoes == {None, "POR_FAIXA_ETARIA", "POR_FAIXA_PESO"}

    def test_no_candidates_without_analysis_params(self, agente):
        assert agente.propose_actions() == []


class TestEvaluateAndSelectSemLlm:
    def test_use_llm_false_picks_highest_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": "quero por faixa peso",
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] == "POR_FAIXA_PESO"

    def test_use_llm_false_no_mention_falls_back_to_no_break(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": None,
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] is None

    def test_empty_candidates_returns_empty(self, agente):
        assert agente.evaluate_and_select([]) == []


class TestEvaluateAndSelectComLlm:
    def test_llm_choice_is_respected(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": "quero por faixa etária",
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_FAIXA_ETARIA"}),
        ) as mock_generate:
            selected = agente.evaluate_and_select(candidates)
        mock_generate.assert_called_once()
        assert selected[0]["dimensao"] == "POR_FAIXA_ETARIA"

    def test_llm_failure_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": "por faixa peso",
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] == "POR_FAIXA_PESO"

    def test_llm_choosing_unknown_dimensao_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": None,
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_SEXO"}),
        ):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] is None


class TestConsultarIndicadores:
    def test_query_calls_neo4j_with_chosen_dimensao(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = [
            {"tipo": "nascidos_vivos", "ano": 2020, "valor": 30.0},
        ]
        with patch("core.llm_client.generate") as mock_generate:
            result = agente.query("a1", 2019, 2021, use_llm=False)
        mock_generate.assert_not_called()
        neo4j_client.get_indicadores_por_sistema.assert_called_once()
        args = neo4j_client.get_indicadores_por_sistema.call_args[0]
        assert args[0] == SISTEMA
        assert set(args[1]) == set(SUBTIPOS)
        assert "indicadores" in result

    def test_no_despesas_key_in_result(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = []
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert "despesas" not in result

    def test_graceful_degradation_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.side_effect = Exception("Connection refused")
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert result["indicadores"] == []
