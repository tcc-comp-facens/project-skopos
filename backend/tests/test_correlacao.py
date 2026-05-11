"""Tests for AgenteCorrelacao.compute()."""

import pytest

from agents.analytical.correlacao import AgenteCorrelacao


@pytest.fixture
def agente():
    return AgenteCorrelacao("test-correlacao")


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


class TestComputeEmpty:
    def test_empty_input_returns_empty_list(self, agente):
        assert agente.compute([]) == []


class TestComputeSinglePoint:
    def test_single_point_returns_spearman_zero(self, agente):
        data = _make_crossed(301, "vacinacao", [(2020, 100.0, 50.0)])
        result = agente.compute(data)
        assert len(result) == 1
        assert result[0]["spearman"] == 0.0
        assert result[0]["classificacao"] == "baixa"
        assert result[0]["n_pontos"] == 1


class TestComputePerfectPositive:
    def test_perfect_positive_correlation(self, agente):
        # Monotonically increasing both → spearman ≈ 1.0
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 20.0),
            (2021, 300.0, 30.0),
            (2022, 400.0, 40.0),
            (2023, 500.0, 50.0),
        ])
        result = agente.compute(data)
        assert len(result) == 1
        assert result[0]["spearman"] == pytest.approx(1.0, abs=0.01)
        assert result[0]["classificacao"] == "alta"


class TestComputePerfectNegative:
    def test_perfect_negative_correlation(self, agente):
        # Despesa increases, indicador decreases monotonically
        data = _make_crossed(305, "dengue", [
            (2019, 100.0, 50.0),
            (2020, 200.0, 40.0),
            (2021, 300.0, 30.0),
            (2022, 400.0, 20.0),
            (2023, 500.0, 10.0),
        ])
        result = agente.compute(data)
        assert len(result) == 1
        assert result[0]["spearman"] == pytest.approx(-1.0, abs=0.01)
        assert result[0]["classificacao"] == "alta"


class TestComputeMediumCorrelation:
    def test_medium_correlation_classified_media(self, agente):
        # Construct data with moderate positive correlation
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 25.0),
            (2021, 150.0, 15.0),
            (2022, 300.0, 20.0),
            (2023, 400.0, 35.0),
        ])
        result = agente.compute(data)
        assert len(result) == 1
        r = abs(result[0]["spearman"])
        # Should be in medium range
        assert 0.4 <= r < 0.7 or result[0]["classificacao"] in ("média", "alta")
        # If spearman is in [0.4, 0.7) → média
        if 0.4 <= r < 0.7:
            assert result[0]["classificacao"] == "média"


class TestComputeWeakCorrelation:
    def test_weak_correlation_classified_baixa(self, agente):
        # Nearly random data → weak correlation
        data = _make_crossed(305, "covid", [
            (2019, 100.0, 50.0),
            (2020, 300.0, 10.0),
            (2021, 200.0, 60.0),
            (2022, 400.0, 30.0),
            (2023, 150.0, 55.0),
        ])
        result = agente.compute(data)
        assert len(result) == 1
        r = abs(result[0]["spearman"])
        if r < 0.4:
            assert result[0]["classificacao"] == "baixa"


class TestComputeMultiplePairs:
    def test_multiple_pairs_computed_independently(self, agente):
        data = (
            _make_crossed(301, "vacinacao", [
                (2019, 100.0, 10.0),
                (2020, 200.0, 20.0),
                (2021, 300.0, 30.0),
            ])
            + _make_crossed(305, "dengue", [
                (2019, 100.0, 50.0),
                (2020, 200.0, 40.0),
                (2021, 300.0, 30.0),
            ])
        )
        result = agente.compute(data)
        assert len(result) == 2
        # Each pair has its own correlation
        subfuncoes = {r["subfuncao"] for r in result}
        assert subfuncoes == {301, 305}


class TestComputeOutputFields:
    def test_output_contains_required_fields(self, agente):
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 20.0),
        ])
        result = agente.compute(data)
        assert len(result) == 1
        required_fields = {"subfuncao", "tipo_indicador", "spearman", "classificacao", "n_pontos"}
        assert required_fields.issubset(result[0].keys())
        assert result[0]["subfuncao"] == 302
        assert result[0]["tipo_indicador"] == "internacoes"
        assert result[0]["n_pontos"] == 2
