"""Tests for AgenteCNES — o sistema mais heterogêneo dos 8 (12 subtipos,
6 dimensões, cada dimensão pertence de fato a só 1 subtipo). Cobertura
nova (Fase 2), sem legado equivalente.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.domain.agente_cnes import SISTEMA, SUBTIPOS, AgenteCNES


@pytest.fixture
def neo4j_client():
    return MagicMock()


@pytest.fixture
def agente(neo4j_client):
    return AgenteCNES("test-cnes", neo4j_client)


class TestSemanticMemory:
    def test_sistema_is_cnes(self, agente):
        assert agente.semantic_memory["sistema"] == "cnes"

    def test_subtipos_has_12_subcubos(self, agente):
        assert len(agente.semantic_memory["subtipos"]) == 12
        assert "leitos" in agente.semantic_memory["subtipos"]
        assert "estabelecimentos_vigilancia_epidemiologica" in agente.semantic_memory["subtipos"]

    def test_dimensoes_validas_from_query_builder(self, agente):
        assert agente.semantic_memory["dimensoes_validas"] == [
            "POR_TIPO_ESTABELECIMENTO",
            "POR_OCUPACAO",
            "POR_TIPO_EQUIPE",
            "POR_TIPO_ATENDIMENTO",
            "POR_TIPO_LEITO_CONSULTORIO",
            "POR_TIPO_EQUIPAMENTO",
        ]


class TestProposeActionsGeraCandidatosReais:
    def test_proposes_one_candidate_per_dimension_plus_no_break(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
        })
        candidates = agente.propose_actions()
        dimensoes = {c["dimensao"] for c in candidates}
        assert dimensoes == {
            None,
            "POR_TIPO_ESTABELECIMENTO",
            "POR_OCUPACAO",
            "POR_TIPO_EQUIPE",
            "POR_TIPO_ATENDIMENTO",
            "POR_TIPO_LEITO_CONSULTORIO",
            "POR_TIPO_EQUIPAMENTO",
        }
        assert len(candidates) == 7

    def test_no_candidates_without_analysis_params(self, agente):
        assert agente.propose_actions() == []


class TestEvaluateAndSelectSemLlm:
    def test_use_llm_false_picks_highest_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": "quero saber por tipo equipamento",
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        assert len(selected) == 1
        assert selected[0]["dimensao"] == "POR_TIPO_EQUIPAMENTO"

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
            "use_llm": True, "intent_summary": "quero por tipo estabelecimento",
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_TIPO_ESTABELECIMENTO"}),
        ) as mock_generate:
            selected = agente.evaluate_and_select(candidates)
        mock_generate.assert_called_once()
        assert selected[0]["dimensao"] == "POR_TIPO_ESTABELECIMENTO"

    def test_llm_failure_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": "por tipo equipe",
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["dimensao"] == "POR_TIPO_EQUIPE"

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
    def test_query_calls_neo4j_with_all_12_subtipos(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = [
            {"tipo": "leitos", "ano": 2020, "valor": 300.0},
        ]
        with patch("core.llm_client.generate") as mock_generate:
            result = agente.query("a1", 2019, 2021, use_llm=False)
        mock_generate.assert_not_called()
        neo4j_client.get_indicadores_por_sistema.assert_called_once()
        args = neo4j_client.get_indicadores_por_sistema.call_args[0]
        assert args[0] == SISTEMA
        assert set(args[1]) == set(SUBTIPOS)
        assert "indicadores" in result

    def test_dimensao_escolhida_naturalmente_escopa_a_um_subtipo(self, agente, neo4j_client):
        """POR_TIPO_ESTABELECIMENTO só existe nos nós do subtipo
        estabelecimentos_por_tipo — o mock aqui simula o Neo4j retornando
        só esse subtipo mesmo com os 12 pedidos, comportamento esperado
        (ver docstring do módulo)."""
        neo4j_client.get_indicadores_por_sistema.return_value = [
            {"tipo": "estabelecimentos_por_tipo", "ano": 2020, "valor": 5.0, "dimensao_valor": "UBS"},
        ]
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": "quero por tipo estabelecimento",
        })
        result = agente.query("a1", 2019, 2021, use_llm=False, intent_summary="quero por tipo estabelecimento")
        call_kwargs_dimensao = neo4j_client.get_indicadores_por_sistema.call_args[0][4]
        assert call_kwargs_dimensao == "POR_TIPO_ESTABELECIMENTO"
        assert all(i["tipo"] == "estabelecimentos_por_tipo" for i in result["indicadores"])

    def test_no_despesas_key_in_result(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.return_value = []
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert "despesas" not in result

    def test_graceful_degradation_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_indicadores_por_sistema.side_effect = Exception("Connection refused")
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert result["indicadores"] == []
