"""Tests for AgenteAnalitico.analisar() — correlação + anomalias consolidadas.

Migrado 1:1 de test_correlacao.py + test_anomalias.py (agentes agora
consolidados em AgenteAnalitico — PLANO_NOVO_MODELO_DADOS.md §7 item 6).
"""

import pytest

from agents.analytical.analitico import AgenteAnalitico


@pytest.fixture
def agente():
    return AgenteAnalitico("test-analitico")


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


# -- Correlação (migrado de test_correlacao.py) -----------------------------


class TestCorrelacaoEmpty:
    def test_empty_input_returns_empty_list(self, agente):
        assert agente.analisar([])["correlacoes"] == []


class TestCorrelacaoSinglePoint:
    def test_single_point_returns_spearman_zero(self, agente):
        data = _make_crossed(301, "vacinacao", [(2020, 100.0, 50.0)])
        result = agente.analisar(data)["correlacoes"]
        assert len(result) == 1
        assert result[0]["spearman"] == 0.0
        assert result[0]["classificacao"] == "baixa"
        assert result[0]["n_pontos"] == 1


class TestCorrelacaoPerfectPositive:
    def test_perfect_positive_correlation(self, agente):
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 20.0),
            (2021, 300.0, 30.0),
            (2022, 400.0, 40.0),
            (2023, 500.0, 50.0),
        ])
        result = agente.analisar(data)["correlacoes"]
        assert len(result) == 1
        assert result[0]["spearman"] == pytest.approx(1.0, abs=0.01)
        assert result[0]["classificacao"] == "alta"


class TestCorrelacaoPerfectNegative:
    def test_perfect_negative_correlation(self, agente):
        data = _make_crossed(305, "dengue", [
            (2019, 100.0, 50.0),
            (2020, 200.0, 40.0),
            (2021, 300.0, 30.0),
            (2022, 400.0, 20.0),
            (2023, 500.0, 10.0),
        ])
        result = agente.analisar(data)["correlacoes"]
        assert len(result) == 1
        assert result[0]["spearman"] == pytest.approx(-1.0, abs=0.01)
        assert result[0]["classificacao"] == "alta"


class TestCorrelacaoMultiplePairs:
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
        result = agente.analisar(data)["correlacoes"]
        assert len(result) == 2
        subfuncoes = {r["subfuncao"] for r in result}
        assert subfuncoes == {301, 305}


class TestCorrelacaoOutputFields:
    def test_output_contains_required_fields(self, agente):
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 20.0),
        ])
        result = agente.analisar(data)["correlacoes"]
        assert len(result) == 1
        required_fields = {"subfuncao", "tipo_indicador", "spearman", "classificacao", "n_pontos"}
        assert required_fields.issubset(result[0].keys())
        assert result[0]["subfuncao"] == 302
        assert result[0]["tipo_indicador"] == "internacoes"
        assert result[0]["n_pontos"] == 2


# -- Anomalias (migrado de test_anomalias.py) --------------------------------


class TestAnomaliasEmpty:
    def test_empty_input_returns_empty_list(self, agente):
        assert agente.analisar([])["anomalias"] == []


class TestAnomaliasMinimumPoints:
    def test_single_point_ignored(self, agente):
        data = _make_crossed(301, "vacinacao", [(2020, 1000.0, 50.0)])
        result = agente.analisar(data)["anomalias"]
        assert result == []


class TestAnomaliasAltoGastoBaixoResultado:
    def test_high_spend_bad_outcome_negative_indicator(self, agente):
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.analisar(data)["anomalias"]
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "alto_gasto_baixo_resultado" in anomaly_types

    def test_high_spend_bad_outcome_positive_indicator(self, agente):
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 50.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 10.0),
        ])
        result = agente.analisar(data)["anomalias"]
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "alto_gasto_baixo_resultado" in anomaly_types


class TestAnomaliasBaixoGastoAltoResultado:
    def test_low_spend_good_outcome_negative_indicator(self, agente):
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.analisar(data)["anomalias"]
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "baixo_gasto_alto_resultado" in anomaly_types

    def test_low_spend_good_outcome_positive_indicator(self, agente):
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 50.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 10.0),
        ])
        result = agente.analisar(data)["anomalias"]
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "baixo_gasto_alto_resultado" in anomaly_types


class TestAnomaliasAtMedian:
    def test_values_at_median_produce_no_anomaly(self, agente):
        data = _make_crossed(301, "vacinacao", [
            (2019, 100.0, 50.0),
            (2020, 100.0, 50.0),
            (2021, 100.0, 50.0),
        ])
        result = agente.analisar(data)["anomalias"]
        assert result == []


class TestAnomaliasOutputFields:
    def test_output_contains_required_fields(self, agente):
        data = _make_crossed(305, "dengue", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.analisar(data)["anomalias"]
        assert len(result) > 0
        required_fields = {"subfuncao", "tipo_indicador", "ano", "tipo_anomalia", "descricao"}
        for anomaly in result:
            assert required_fields.issubset(anomaly.keys())


class TestAnomaliasSinanIndicadoresNegativos:
    def test_sinan_disease_subtype_treated_as_negative_indicator(self, agente):
        """Os 8 subtipos SINAN adicionais (AgenteSINAN, Fase 1) devem ter a
        mesma polaridade de dengue/covid — mais casos = pior."""
        data = _make_crossed(305, "chikungunya", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.analisar(data)["anomalias"]
        anomaly_types = [a["tipo_anomalia"] for a in result]
        assert "alto_gasto_baixo_resultado" in anomaly_types


# -- Consolidação: as duas ferramentas rodam juntas --------------------------


class TestAnalisarConsolidado:
    def test_analisar_returns_both_correlacoes_and_anomalias(self, agente):
        data = _make_crossed(302, "internacoes", [
            (2019, 100.0, 10.0),
            (2020, 200.0, 30.0),
            (2021, 300.0, 50.0),
        ])
        result = agente.analisar(data)
        assert "correlacoes" in result
        assert "anomalias" in result
        assert len(result["correlacoes"]) == 1
        assert len(result["anomalias"]) >= 1

    def test_propose_actions_proposes_both_goals_when_data_present(self, agente):
        data = _make_crossed(302, "internacoes", [(2019, 100.0, 10.0), (2020, 200.0, 30.0)])
        agente.update_working_memory({"dados_cruzados": data})
        candidates = agente.propose_actions()
        goals = [c["goal"] for c in candidates]
        assert goals == ["calcular_correlacao", "detectar_anomalia"]

    def test_propose_actions_empty_when_no_data(self, agente):
        agente.update_working_memory({"dados_cruzados": []})
        assert agente.propose_actions() == []
