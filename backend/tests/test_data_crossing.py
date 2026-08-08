"""Tests for data_crossing module: cross_domain_data, deduplicate_despesas, detect_data_gaps."""

import pytest

from agents.data_crossing import (
    SUBFUNCAO_INDICADOR_MAP,
    cross_domain_data,
    deduplicate_despesas,
    detect_data_gaps,
)


# ============================================================
# cross_domain_data
# ============================================================


class TestCrossDomainDataEmpty:
    def test_empty_despesas_returns_empty(self):
        assert cross_domain_data([], [{"tipo": "dengue", "ano": 2020, "valor": 10}]) == []

    def test_empty_indicadores_returns_empty(self):
        assert cross_domain_data([{"subfuncao": 305, "ano": 2020, "valor": 100}], []) == []


class TestCrossDomainDataMapping:
    def test_301_maps_to_cobertura_vacinal_and_doses_aplicadas(self):
        despesas = [{"subfuncao": 301, "subfuncaoNome": "Atenção Básica", "ano": 2020, "valor": 100.0}]
        indicadores = [
            {"tipo": "cobertura_vacinal", "ano": 2020, "valor": 80.0},
            {"tipo": "doses_aplicadas", "ano": 2020, "valor": 500.0},
        ]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 2
        tipos = {r["tipo_indicador"] for r in result}
        assert tipos == {"cobertura_vacinal", "doses_aplicadas"}
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
            {"tipo": "casos", "ano": 2020, "valor": 40.0},
            {"tipo": "obitos", "ano": 2020, "valor": 5.0},
        ]
        result = cross_domain_data(despesas, indicadores)
        tipos = {r["tipo_indicador"] for r in result}
        assert tipos == {"dengue", "casos", "obitos"}

    def test_302_maps_to_producao_ambulatorial(self):
        despesas = [{"subfuncao": 302, "subfuncaoNome": "AH", "ano": 2021, "valor": 500.0}]
        indicadores = [{"tipo": "producao_ambulatorial", "ano": 2021, "valor": 300.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        assert result[0]["tipo_indicador"] == "producao_ambulatorial"

    def test_301_maps_to_nascidos_vivos(self):
        despesas = [{"subfuncao": 301, "subfuncaoNome": "Atenção Básica", "ano": 2021, "valor": 500.0}]
        indicadores = [{"tipo": "nascidos_vivos", "ano": 2021, "valor": 300.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        assert result[0]["tipo_indicador"] == "nascidos_vivos"

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


class TestCrossDomainDataDimensaoSlices:
    """Fase 3: quando uma dimensão está ativa dos dois lados (mesmo
    vocabulário), múltiplas fatias do mesmo ano devem gerar múltiplas
    linhas cruzadas — não uma só sobrescrita silenciosamente (bug
    corrigido: chave composta (ano, dimensao_valor) em vez de só ano)."""

    def test_multiple_slices_same_year_produce_multiple_crossed_rows(self):
        despesas = [
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2020, "valor": 60.0,
             "dimensao_valor": "faixa_a"},
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2020, "valor": 40.0,
             "dimensao_valor": "faixa_b"},
        ]
        indicadores = [
            {"tipo": "dengue", "ano": 2020, "valor": 10.0, "dimensao_valor": "faixa_a"},
            {"tipo": "dengue", "ano": 2020, "valor": 5.0, "dimensao_valor": "faixa_b"},
        ]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 2
        por_fatia = {r["dimensao_valor"]: r for r in result}
        assert por_fatia["faixa_a"]["valor_despesa"] == 60.0
        assert por_fatia["faixa_a"]["valor_indicador"] == 10.0
        assert por_fatia["faixa_b"]["valor_despesa"] == 40.0
        assert por_fatia["faixa_b"]["valor_indicador"] == 5.0

    def test_mismatched_dimension_vocabularies_produce_no_crossing(self):
        """Despesa quebrada por natureza × indicador quebrado por faixa
        etária — vocabulários diferentes, nenhuma fatia bate, não deve
        cruzar arbitrariamente."""
        despesas = [
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2020, "valor": 60.0,
             "dimensao_valor": "Material de Consumo"},
        ]
        indicadores = [
            {"tipo": "dengue", "ano": 2020, "valor": 10.0, "dimensao_valor": "0-9 anos"},
        ]
        result = cross_domain_data(despesas, indicadores)
        assert result == []

    def test_no_dimensao_behaves_as_before(self):
        """Sem dimensao_valor em nenhum lado (comportamento pré-Fase-3):
        uma linha por (subfuncao/tipo, ano), dimensao_valor None."""
        despesas = [{"subfuncao": 302, "subfuncaoNome": "AH", "ano": 2021, "valor": 500.0}]
        indicadores = [{"tipo": "internacoes", "ano": 2021, "valor": 1000.0}]
        result = cross_domain_data(despesas, indicadores)
        assert len(result) == 1
        assert result[0]["dimensao_valor"] is None


class TestCrossDomainDataYearMatching:
    def test_only_matching_years_produce_crossed_points(self):
        despesas = [
            {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2019, "valor": 100.0},
            {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 200.0},
        ]
        indicadores = [{"tipo": "cobertura_vacinal", "ano": 2020, "valor": 80.0}]
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
            for sf in [122, 301, 302, 303, 304, 305, 306]
            for ano in [2019, 2020, 2021]
        ]
        # Todos os tipos de indicador do mapeamento atual (inclui os 9
        # subtipos SINAN sob 303/304/305 e os 12 do CNES sob
        # 122/304/306, Fase 2) + mortalidade (transversal).
        todos_tipos = {t for tipos in SUBFUNCAO_INDICADOR_MAP.values() for t in tipos}
        todos_tipos.add("mortalidade")
        indicadores = [
            {"tipo": tipo, "ano": ano, "valor": 50.0}
            for tipo in todos_tipos
            for ano in [2019, 2020, 2021]
        ]
        result = detect_data_gaps(despesas, indicadores, 2019, 2021)
        assert result["summary"]["has_gaps"] is False

    def test_missing_years_detected(self):
        # Only 2019 data, but period is 2019-2021
        despesas = [{"subfuncao": 301, "ano": 2019, "valor": 100.0}]
        indicadores = [{"tipo": "cobertura_vacinal", "ano": 2019, "valor": 50.0}]
        result = detect_data_gaps(despesas, indicadores, 2019, 2021)
        assert result["summary"]["has_gaps"] is True
        assert len(result["gaps"]) > 0

    def test_null_valor_indicador_counts_as_missing_not_covered(self):
        """Regressão: CNES 'tipo_atendimento'/'leitos_consultorios' não
        têm total válido na fonte (planilha sem coluna "Total") — a
        linha (tipo, ano) sempre existe no retorno do Neo4j, mas com
        valor=None. Sem checar valor is not None, isso aparecia como
        "100% completo" no relatório de cobertura."""
        despesas = [
            {"subfuncao": 122, "ano": 2022, "valor": 100.0},
            {"subfuncao": 122, "ano": 2023, "valor": 100.0},
        ]
        indicadores = [
            {"tipo": "tipo_atendimento", "ano": 2022, "valor": None},
            {"tipo": "tipo_atendimento", "ano": 2023, "valor": None},
        ]
        result = detect_data_gaps(
            despesas, indicadores, 2022, 2023, health_params=["tipo_atendimento"]
        )
        coverage = result["indicadores_coverage"]["tipo_atendimento"]
        assert coverage["present"] == []
        assert coverage["missing"] == [2022, 2023]
        assert coverage["coverage"] == 0.0
        assert result["summary"]["has_gaps"] is True

    def test_null_valor_despesa_counts_as_missing_not_covered(self):
        despesas = [{"subfuncao": 301, "ano": 2020, "valor": None}]
        indicadores = [{"tipo": "cobertura_vacinal", "ano": 2020, "valor": 50.0}]
        result = detect_data_gaps(
            despesas, indicadores, 2020, 2020, health_params=["cobertura_vacinal"]
        )
        coverage = result["despesas_coverage"][301]
        assert coverage["present"] == []
        assert coverage["missing"] == [2020]
