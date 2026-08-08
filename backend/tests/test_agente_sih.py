"""Tests for AgenteSIH — segundo agente de saúde da Fase 2, confirma que
o padrão de AgenteSINAN generaliza fora do caso "9 subtipos".

Cobre a deliberação real de dimensão (propose_actions/evaluate_and_select
com candidatos concorrentes de verdade) e a consulta a
IndicadorSaude(sistema="sih") via neo4j_client.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.domain.agente_sih import SISTEMA, SUBTIPOS, AgenteSIH


@pytest.fixture
def neo4j_client():
    return MagicMock()


@pytest.fixture
def agente(neo4j_client):
    return AgenteSIH("test-sih", neo4j_client)


class TestSemanticMemory:
    def test_sistema_is_sih(self, agente):
        assert agente.semantic_memory["sistema"] == "sih"

    def test_subtipos_has_internacoes(self, agente):
        assert agente.semantic_memory["subtipos"] == ["internacoes"]

    def test_dimensoes_validas_from_query_builder(self, agente):
        assert agente.semantic_memory["dimensoes_validas"] == ["POR_CAPITULO_CID"]


class TestProposeActionsGeraCandidatosReais:
    def test_proposes_one_candidate_per_dimension_plus_no_break(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
        })
        candidates = agente.propose_actions()
        dimensoes = {c["dimensao"] for c in candidates}
        assert dimensoes == {None, "POR_CAPITULO_CID"}
        assert all(c["goal"] == "consultar_indicadores" for c in candidates)

    def test_no_candidates_without_analysis_params(self, agente):
        assert agente.propose_actions() == []


class TestEvaluateAndSelectSemLlm:
    def test_use_llm_false_picks_highest_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": "quero saber por capítulo cid",
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        assert len(selected) == 1
        assert selected[0]["dimensao"] == "POR_CAPITULO_CID"

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
            "use_llm": True, "intent_summary": "quero por capítulo cid",
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_CAPITULO_CID"}),
        ) as mock_generate:
            selected = agente.evaluate_and_select(candidates)
        mock_generate.assert_called_once()
        assert selected[0]["dimensao"] == "POR_CAPITULO_CID"

    def test_llm_failure_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": "por capítulo cid",
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] == "POR_CAPITULO_CID"

    def test_llm_choosing_unknown_dimensao_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": None,
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_FAIXA_ETARIA"}),
        ):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] is None


class TestConsultarIndicadores:
    def test_query_calls_neo4j_with_chosen_dimensao(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = [
            {"tipo": "internacoes", "ano": 2020, "valor": 30.0, "dimensao_valor": "Cap. IX"},
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
        """Diferente dos agentes legados, AgenteSIH não consulta despesas
        — isso é responsabilidade de AgenteOrcamentoSubfuncao."""
        neo4j_client.get_indicadores_por_sistema.return_value = []
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert "despesas" not in result

    def test_graceful_degradation_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.side_effect = Exception("Connection refused")
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert result["indicadores"] == []
