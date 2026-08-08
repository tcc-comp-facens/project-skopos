"""Tests for AgenteContextoOrcamentario.analyze_trends()."""

import math

import pytest

from agents.context.contexto_orcamentario import AgenteContextoOrcamentario, compute_cagr


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


class TestComputeCagr:
    def test_doubling_over_one_year_is_100_percent(self):
        assert compute_cagr(100.0, 200.0, 1) == pytest.approx(100.0)

    def test_doubling_over_two_years_is_root_two_minus_one(self):
        # (200/100)**(1/2) - 1 = ~41.42%, não 50% (que seria média simples)
        assert compute_cagr(100.0, 200.0, 2) == pytest.approx(41.4214, abs=0.01)

    def test_zero_base_positive_final_is_infinite(self):
        assert compute_cagr(0.0, 100.0, 3) == math.inf

    def test_zero_base_zero_final_is_zero(self):
        assert compute_cagr(0.0, 0.0, 3) == 0.0

    def test_same_value_is_zero_growth(self):
        assert compute_cagr(100.0, 100.0, 5) == pytest.approx(0.0)


class TestAnalyzeTrendsCagrNotSkewedByOneYearSpike:
    def test_single_year_spike_does_not_dominate_average(self, agente):
        """Regressão: um único salto de base baixa (ex.: subfunção que
        passa a existir e cresce muito no primeiro ano com dado, como o
        122/Administração Geral real, documentado em
        PLANO_NOVO_MODELO_DADOS.md §7.2) não deve inflar a "variação
        média ao ano" para muito acima do que a série sustenta — a
        média aritmética simples das variações ano-a-ano fazia isso
        (~192%/ano em produção); CAGR ponta a ponta não."""
        despesas = _make_despesas(122, {
            2020: 10.0,
            2021: 1000.0,  # +9900% nesse ano isolado
            2022: 1010.0,  # +1%
            2023: 1020.0,  # +0.99%
            2024: 1030.0,  # +0.98%
        })
        result = agente.analyze_trends(despesas)
        # CAGR ponta a ponta 2020->2024 (10 -> 1030, 4 anos) ~ 173%/ano —
        # bem alto ainda (o salto real é grande), mas não os milhares de
        # % que uma média simples das variações produziria.
        media_simples = ((9900 + 1 + 0.99 + 0.98) / 4)
        assert result[122]["variacao_media_percentual"] < media_simples / 5

    def test_multi_year_steady_growth_matches_cagr_not_simple_mean(self, agente):
        despesas = _make_despesas(301, {
            2019: 100.0,
            2020: 200.0,  # +100%
            2021: 400.0,  # +100%
        })
        result = agente.analyze_trends(despesas)
        # CAGR 100->400 em 2 anos = 100% (média simples também dá 100%
        # aqui, caso "limpo" sem outlier — serve de sanity check).
        assert result[301]["variacao_media_percentual"] == pytest.approx(100.0, abs=0.01)


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
