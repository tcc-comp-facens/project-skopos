"""Tests for data_crossing module: cross_domain_data, deduplicate_despesas, detect_data_gaps."""

import pytest

from agents.data_crossing import cross_domain_data, deduplicate_despesas, detect_data_gaps


# ============================================================
# cross_domain_data
# ============================================================


class TestCrossDomainDataEmpty:
    def test_empty_despesas_returns_empty(self):
        assert cross_domain_data([], [{"tipo": "dengue", "ano": 2020, "valor": 10}]) == []

    def test_empty_indicadores_returns_empty(self):
        assert cross_domain_data([{"subfuncao": 305, "ano": 2020, "valor": 100}], []) == []


class TestCrossDomainDataMapping:
    def test_301_maps_to_vacinacao(self):
        despesas = [{"subfuncao": 301, "subfuncaoNome": "Atenção Básica", "ano": 2020, "valor": 100.0}]
        indicadores = [{"tipo": "vacinacao", "ano": 2020, "valor": 80.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        assert result[0]["tipo_indicador"] == "vacinacao"
        assert result[0]["subfuncao"] == 301

    def test_302_maps_to_internacoes(self):
        despesas = [{"subfuncao": 302, "subfuncaoNome": "Assistência Hospitalar", "ano": 2021, "valor": 200.0}]
        indicadores = [{"tipo": "internacoes", "ano": 2021, "valor": 500.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        assert result[0]["tipo_indicador"] == "internacoes"

    def test_305_maps_to_dengue_and_covid(self):
        despesas = [{"subfuncao": 305, "subfuncaoNome": "Vigilância", "ano": 2020, "valor": 150.0}]
        indicadores = [
            {"tipo": "dengue", "ano": 2020, "valor": 30.0},
            {"tipo": "covid", "ano": 2020, "valor": 40.0},
        ]
        result = cross_domain_data(despesas, indicadores)
        tipos = {r["tipo_indicador"] for r in result}
        assert tipos == {"dengue", "covid"}

    def test_mortalidade_crosses_with_all_subfuncoes(self):
        despesas = [
            {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 100.0},
            {"subfuncao": 302, "subfuncaoNome": "AH", "ano": 2020, "valor": 200.0},
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2020, "valor": 300.0},
        ]
        indicadores = [{"tipo": "mortalidade", "ano": 2020, "valor": 50.0}]
        result = cross_domain_data(despesas, indicadores)
        subfuncoes = {r["subfuncao"] for r in result}
        # mortalidade crosses with 301, 302, 303, 305 — but only those with data
        assert 301 in subfuncoes
        assert 302 in subfuncoes
        assert 305 in subfuncoes


class TestCrossDomainDataYearMatching:
    def test_only_matching_years_produce_crossed_points(self):
        despesas = [
            {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2019, "valor": 100.0},
            {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 200.0},
        ]
        indicadores = [{"tipo": "vacinacao", "ano": 2020, "valor": 80.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        assert result[0]["ano"] == 2020


class TestCrossDomainDataOutputFields:
    def test_output_has_required_fields(self):
        despesas = [{"subfuncao": 302, "subfuncaoNome": "AH", "ano": 2021, "valor": 500.0}]
        indicadores = [{"tipo": "internacoes", "ano": 2021, "valor": 1000.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        required = {"subfuncao", "subfuncao_nome", "tipo_indicador", "ano", "valor_despesa", "valor_indicador"}
        assert required.issubset(result[0].keys())


# ============================================================
# deduplicate_despesas
# ============================================================


class TestDeduplicateDespesas:
    def test_empty_list_returns_empty(self):
        assert deduplicate_despesas([]) == []

    def test_no_duplicates_returns_same_list(self):
        despesas = [
            {"subfuncao": 301, "ano": 2019, "valor": 100.0},
            {"subfuncao": 301, "ano": 2020, "valor": 200.0},
            {"subfuncao": 302, "ano": 2019, "valor": 300.0},
        ]
        result = deduplicate_despesas(despesas)
        assert len(result) == 3

    def test_duplicates_removed_first_kept(self):
        despesas = [
            {"subfuncao": 301, "ano": 2020, "valor": 100.0},
            {"subfuncao": 301, "ano": 2020, "valor": 999.0},  # duplicate
        ]
        result = deduplicate_despesas(despesas)
        assert len(result) == 1
        assert result[0]["valor"] == 100.0


# ============================================================
# detect_data_gaps
# ============================================================


class TestDetectDataGaps:
    def test_full_coverage_returns_no_gaps(self):
        despesas = [
            {"subfuncao": sf, "ano": ano, "valor": 100.0}
            for sf in [301, 302, 303, 305]
            for ano in [2019, 2020, 2021]
        ]
        indicadores = [
            {"tipo": tipo, "ano": ano, "valor": 50.0}
            for tipo in ["vacinacao", "internacoes", "dengue", "covid", "mortalidade"]
            for ano in [2019, 2020, 2021]
        ]
        result = detect_data_gaps(despesas, indicadores, 2019, 2021)
        assert result["summary"]["has_gaps"] is False

    def test_missing_years_detected(self):
        # Only 2019 data, but period is 2019-2021
        despesas = [{"subfuncao": 301, "ano": 2019, "valor": 100.0}]
        indicadores = [{"tipo": "vacinacao", "ano": 2019, "valor": 50.0}]
        result = detect_data_gaps(despesas, indicadores, 2019, 2021)
        assert result["summary"]["has_gaps"] is True
        assert len(result["gaps"]) > 0
