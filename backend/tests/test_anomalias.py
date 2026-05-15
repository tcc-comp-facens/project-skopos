"""Tests for AgenteAnomalias.detect()."""

import pytest

from agents.analytical.anomalias import AgenteAnomalias


@pytest.fixture
def agente():
    return AgenteAnomalias("test-anomalias")


def _make_crossed(subfuncao, tipo, points):
    """Helper: build crossed data points from (ano, despesa, indicador) tuples."""
    return [
        {
            "subfuncao": subfuncao,
            "subfuncao_nome": f"Sub {subfuncao}",
            "tipo_indicador": tipo,
            "ano": ano,
            "valor_despesa": desp,
            "valor_indicador": ind,
        }
        for ano, desp, ind in points
    ]


class TestDetectEmpty:
    def test_empty_input_returns_empty_list(self, agente):
        assert agente.detect([]) == []


class TestDetectMinimumPoints:
    def test_single_point_ignored(self, agente):
        """Pairs with < 2 data points produce no anomalies."""
        data = _make_crossed(301, "vacinacao", [(2020, 1000.0, 50.0)])
        result = agente.detect(data)
        assert result == []


class TestDetectAltoGastoBaixoResultado:
    def test_high_spend_bad_outcome_negative_indicator(self, agente):
        """Indicador negativo (internacoes): despesa > mediana E indicador > mediana → ineficiência.

        Gastou muito mas os casos continuam altos = resultado ruim.
        """
        # Median despesa = 200, median indicador = 30
        # Point (2021, 300, 50): despesa > 200, indicador > 30 → ineficiência
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.detect(data)
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "alto_gasto_baixo_resultado" in anomaly_types

    def test_high_spend_bad_outcome_positive_indicator(self, agente):
        """Indicador positivo (vacinacao): despesa > mediana E indicador < mediana → ineficiência.

        Gastou muito mas a cobertura vacinal continua baixa = resultado ruim.
        """
        # Median despesa = 200, median indicador = 30
        # Point (2021, 300, 10): despesa > 200, indicador < 30 → ineficiência
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 50.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 10.0),
        ])
        result = agente.detect(data)
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "alto_gasto_baixo_resultado" in anomaly_types


class TestDetectBaixoGastoAltoResultado:
    def test_low_spend_good_outcome_negative_indicator(self, agente):
        """Indicador negativo (internacoes): despesa < mediana E indicador < mediana → eficiência.

        Gastou pouco e os casos estão baixos = resultado bom.
        """
        # Median despesa = 200, median indicador = 30
        # Point (2019, 100, 10): despesa < 200, indicador < 30 → eficiência
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.detect(data)
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "baixo_gasto_alto_resultado" in anomaly_types

    def test_low_spend_good_outcome_positive_indicator(self, agente):
        """Indicador positivo (vacinacao): despesa < mediana E indicador > mediana → eficiência.

        Gastou pouco mas a cobertura vacinal está alta = resultado bom.
        """
        # Median despesa = 200, median indicador = 30
        # Point (2019, 100, 50): despesa < 200, indicador > 30 → eficiência
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 50.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 10.0),
        ])
        result = agente.detect(data)
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "baixo_gasto_alto_resultado" in anomaly_types


class TestDetectAtMedian:
    def test_values_at_median_produce_no_anomaly(self, agente):
        """When all values are equal (at median), no anomalies detected."""
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 50.0),
            (2020, 100.0, 50.0),
            (2021, 100.0, 50.0),
        ])
        result = agente.detect(data)
        assert result == []


class TestDetectOutputFields:
    def test_output_contains_required_fields(self, agente):
        # Dengue é indicador negativo: high_spend + high_indicator = ineficiência
        data = _make_crossed(305, "dengue", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.detect(data)
        assert len(result) > 0
        required_fields = {"subfuncao", "tipo_indicador", "ano", "tipo_anomalia", "descricao"}
        for anomaly in result:
            assert required_fields.issubset(anomaly.keys())
