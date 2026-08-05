"""Tests for domain agent (AgenteVigilanciaEpidemiologica as representative)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.domain import query_planning
from agents.domain.vigilancia_epidemiologica import (
    AgenteVigilanciaEpidemiologica,
    SUBFUNCAO,
    TIPOS_INDICADOR,
)


@pytest.fixture
def neo4j_client():
    return MagicMock()


@pytest.fixture
def agente(neo4j_client):
    return AgenteVigilanciaEpidemiologica("test-vigilancia", neo4j_client)


class TestQueryReturnStructure:
    def test_returns_dict_with_despesas_and_indicadores(self, agente, neo4j_client):
        neo4j_client.get_despesas.return_value = [
            {"subfuncao": 305, "ano": 2020, "valor": 100.0},
        ]
        neo4j_client.get_indicadores.return_value = [
            {"tipo": "dengue", "ano": 2020, "valor": 30.0},
        ]
        result = agente.query("analysis-1", 2019, 2021)
        assert "despesas" in result
        assert "indicadores" in result


class TestQueryFiltersSubfuncao:
    def test_filters_despesas_by_subfuncao_305(self, agente, neo4j_client):
        neo4j_client.get_despesas.return_value = [
            {"subfuncao": 301, "ano": 2020, "valor": 50.0},
            {"subfuncao": 305, "ano": 2020, "valor": 100.0},
            {"subfuncao": 302, "ano": 2020, "valor": 75.0},
        ]
        neo4j_client.get_indicadores.return_value = []
        result = agente.query("analysis-2", 2019, 2021)
        # Only subfuncao 305 should remain
        assert all(d["subfuncao"] == 305 for d in result["despesas"])
        assert len(result["despesas"]) == 1


class TestQueryUsesCorrectTipos:
    def test_uses_correct_tipos_indicador(self, agente, neo4j_client):
        neo4j_client.get_despesas.return_value = []
        neo4j_client.get_indicadores.return_value = []
        agente.query("analysis-3", 2019, 2021)
        # get_indicadores should be called with TIPOS_INDICADOR = ["dengue", "covid"]
        neo4j_client.get_indicadores.assert_called_once_with(
            "analysis-3", 2019, 2021, TIPOS_INDICADOR
        )


class TestQueryGracefulDegradation:
    def test_returns_empty_lists_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_despesas.side_effect = Exception("Connection refused")
        neo4j_client.get_indicadores.side_effect = Exception("Connection refused")
        result = agente.query("analysis-4", 2019, 2021)
        assert result["despesas"] == []
        assert result["indicadores"] == []


class TestSubfuncaoConstant:
    def test_subfuncao_is_305(self):
        assert SUBFUNCAO == 305

    def test_tipos_indicador_are_dengue_covid(self):
        assert set(TIPOS_INDICADOR) == {"dengue", "covid"}


# ---------------------------------------------------------------------
# Etapa 2 (PLANO_REFATORACAO.md) — planejamento de consulta via LLM
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_query_planning(monkeypatch):
    """Cache limpo e flag desligada (default) antes/depois de cada teste."""
    query_planning.reset_cache()
    monkeypatch.delenv("USE_LLM_QUERY_PLANNING", raising=False)
    yield
    query_planning.reset_cache()


class TestFastPathIsDefaultBehavior:
    def test_no_llm_call_in_todays_trivial_scenario(self, agente, neo4j_client):
        """Cenário de hoje: mapeamento estático, flag desligada — o LLM
        nunca deveria ser chamado, nem se USE_LLM_QUERY_PLANNING estivesse
        ligada por engano em algum teste anterior."""
        neo4j_client.get_despesas.return_value = []
        neo4j_client.get_indicadores.return_value = []
        with patch("core.llm_client.generate") as mock_generate:
            agente.query("analysis-fastpath", 2019, 2021)
        mock_generate.assert_not_called()

    def test_query_plan_matches_static_semantic_memory(self, agente, neo4j_client):
        neo4j_client.get_despesas.return_value = []
        neo4j_client.get_indicadores.return_value = []
        agente.query("analysis-fastpath-2", 2019, 2021)
        assert agente.working_memory["query_plan"] == {
            "subfuncoes": [SUBFUNCAO],
            "tipos_indicador": list(TIPOS_INDICADOR),
        }

    def test_flag_on_but_mapping_still_trivial_does_not_call_llm(self, agente, neo4j_client):
        """Mesmo com a flag ligada, se semantic_memory não foi estendido
        (mapeamento continua trivial), o fast-path é usado."""
        neo4j_client.get_despesas.return_value = []
        neo4j_client.get_indicadores.return_value = []
        with patch.dict("os.environ", {"USE_LLM_QUERY_PLANNING": "true"}):
            with patch("core.llm_client.generate") as mock_generate:
                agente.query("analysis-fastpath-3", 2019, 2021)
        mock_generate.assert_not_called()


class TestLlmQueryPlanningWhenExtended:
    def test_llm_invoked_when_semantic_memory_extended_and_flag_on(
        self, agente, neo4j_client
    ):
        """Simula o cenário de crescimento futuro da base: um novo
        indicador ('zika') é adicionado ao mapeamento estático deste
        agente — deixa de ser trivial, e com a flag ligada o LLM decide
        o plano de consulta."""
        agente.semantic_memory["tipos_indicador"] = ["dengue", "covid", "zika"]
        neo4j_client.get_despesas.return_value = [
            {"subfuncao": 305, "ano": 2020, "valor": 100.0},
        ]
        neo4j_client.get_indicadores.return_value = [
            {"tipo": "zika", "ano": 2020, "valor": 5.0},
        ]
        llm_response = json.dumps(
            {"subfuncoes": [305], "tipos_indicador": ["dengue", "covid", "zika"]}
        )
        with patch.dict("os.environ", {"USE_LLM_QUERY_PLANNING": "true"}):
            with patch("core.llm_client.generate", return_value=llm_response) as mock_generate:
                result = agente.query(
                    "analysis-llm-1", 2019, 2021,
                    intent_summary="e quanto a zika?",
                    health_params=["dengue", "zika"],
                )

        mock_generate.assert_called_once()
        neo4j_client.get_indicadores.assert_called_once_with(
            "analysis-llm-1", 2019, 2021, ["dengue", "covid", "zika"]
        )
        assert result["indicadores"] == [
            {"tipo": "zika", "ano": 2020, "valor": 5.0},
        ]

    def test_cache_reused_across_two_agent_instances(self, neo4j_client):
        """O cache é compartilhado no módulo query_planning (não por
        instância de agente) — duas instâncias com o mesmo contexto não
        deveriam pagar 2 chamadas LLM."""
        neo4j_client.get_despesas.return_value = []
        neo4j_client.get_indicadores.return_value = []
        llm_response = json.dumps(
            {"subfuncoes": [305], "tipos_indicador": ["dengue", "covid", "zika"]}
        )
        with patch.dict("os.environ", {"USE_LLM_QUERY_PLANNING": "true"}):
            with patch("core.llm_client.generate", return_value=llm_response) as mock_generate:
                agente1 = AgenteVigilanciaEpidemiologica("a1", neo4j_client)
                agente1.semantic_memory["tipos_indicador"] = ["dengue", "covid", "zika"]
                agente1.query(
                    "analysis-cache-1", 2019, 2021,
                    intent_summary="mesma pergunta", health_params=["zika"],
                )

                agente2 = AgenteVigilanciaEpidemiologica("a2", neo4j_client)
                agente2.semantic_memory["tipos_indicador"] = ["dengue", "covid", "zika"]
                agente2.query(
                    "analysis-cache-2", 2019, 2021,
                    intent_summary="mesma pergunta", health_params=["zika"],
                )

        mock_generate.assert_called_once()

    def test_llm_failure_does_not_break_the_pipeline(self, agente, neo4j_client):
        agente.semantic_memory["tipos_indicador"] = ["dengue", "covid", "zika"]
        neo4j_client.get_despesas.return_value = []
        neo4j_client.get_indicadores.return_value = []
        with patch.dict("os.environ", {"USE_LLM_QUERY_PLANNING": "true"}):
            with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
                result = agente.query(
                    "analysis-llm-fail", 2019, 2021,
                    intent_summary="e quanto a zika?", health_params=["zika"],
                )
        # Cai no plano estático (semantic_memory já estendido) em vez de falhar
        assert result["despesas"] == []
        assert result["indicadores"] == []
        assert agente.working_memory["query_plan"]["tipos_indicador"] == [
            "dengue", "covid", "zika",
        ]
