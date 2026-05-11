"""Tests for domain agent (AgenteVigilanciaEpidemiologica as representative)."""

import pytest
from unittest.mock import MagicMock

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
