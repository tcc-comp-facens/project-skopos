"""Tests for AgenteContextoOrcamentario.analyze_trends()."""

import pytest

from agents.context.contexto_orcamentario import AgenteContextoOrcamentario


@pytest.fixture
def agente():
    return AgenteContextoOrcamentario("test-contexto")


def _make_despesas(subfuncao, year_values):
    """Helper: build despesa records from {ano: valor} dict."""
    return [
        {"subfuncao": subfuncao, "subfuncaoNome": f"Sub {subfuncao}", "ano": ano, "valor": valor}
        for ano, valor in year_values.items()
    ]


class TestAnalyzeTrendsEmpty:
    def test_empty_input_returns_empty_dict(self, agente):
        assert agente.analyze_trends([]) == {}


class TestAnalyzeTrendsSingleYear:
    def test_single_year_returns_insuficiente(self, agente):
        despesas = _make_despesas(301, {2020: 1000.0})
        result = agente.analyze_trends(despesas)
        assert 301 in result
        assert result[301]["tendencia"] == "insuficiente"


class TestAnalyzeTrendsCrescimento:
    def test_consecutive_growth_classified_crescimento(self, agente):
        """Growth > 5% for 2+ consecutive years → crescimento."""
        despesas = _make_despesas(302, {
            2019: 100.0,
            2020: 120.0,  # +20%
            2021: 150.0,  # +25%
            2022: 200.0,  # +33%
        })
        result = agente.analyze_trends(despesas)
        assert result[302]["tendencia"] == "crescimento"


class TestAnalyzeTrendsCorte:
    def test_consecutive_cuts_classified_corte(self, agente):
        """Negative variation for 2+ consecutive years → corte."""
        despesas = _make_despesas(305, {
            2019: 200.0,
            2020: 150.0,  # -25%
            2021: 100.0,  # -33%
            2022: 70.0,   # -30%
        })
        result = agente.analyze_trends(despesas)
        assert result[305]["tendencia"] == "corte"


class TestAnalyzeTrendsEstagnacao:
    def test_small_variations_classified_estagnacao(self, agente):
        """All |variation| < 5% → estagnacao."""
        despesas = _make_despesas(301, {
            2019: 100.0,
            2020: 102.0,  # +2%
            2021: 101.0,  # -0.98%
            2022: 103.0,  # +1.98%
        })
        result = agente.analyze_trends(despesas)
        assert result[301]["tendencia"] == "estagnacao"


class TestAnalyzeTrendsMultipleSubfuncoes:
    def test_multiple_subfuncoes_computed_independently(self, agente):
        despesas = (
            _make_despesas(301, {2019: 100.0, 2020: 200.0, 2021: 300.0})
            + _make_despesas(302, {2019: 300.0, 2020: 200.0, 2021: 100.0})
        )
        result = agente.analyze_trends(despesas)
        assert 301 in result
        assert 302 in result
        assert result[301]["tendencia"] == "crescimento"
        assert result[302]["tendencia"] == "corte"
