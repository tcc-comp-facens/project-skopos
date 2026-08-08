"""Tests for db.query_builder — construção segura de Cypher dinâmica (Fase 1)."""

import pytest

from db.query_builder import (
    DESPESA_DIMENSOES,
    SISTEMA_DIMENSOES,
    DimensaoInvalida,
    build_despesa_cypher,
    build_indicador_cypher,
    dimensoes_validas,
    dimensoes_validas_despesa,
)


class TestDimensoesValidas:
    def test_known_sistema_returns_its_dimensions(self):
        assert dimensoes_validas("sinan") == ["POR_FAIXA_ETARIA", "POR_SEXO"]

    def test_unknown_sistema_returns_empty_list(self):
        assert dimensoes_validas("sistema_inexistente") == []

    def test_returns_a_copy_not_the_internal_list(self):
        """Mutar o retorno não pode afetar SISTEMA_DIMENSOES (allowlist
        compartilhada por todos os agentes)."""
        resultado = dimensoes_validas("sinan")
        resultado.append("HACKED")
        assert "HACKED" not in SISTEMA_DIMENSOES["sinan"]


class TestBuildIndicadorCypherSemDimensao:
    def test_query_and_params_without_dimensao(self):
        query, params = build_indicador_cypher("sinan", ["dengue", "chikungunya"], 2019, 2022)
        assert params == {
            "sistema": "sinan",
            "subtipos": ["dengue", "chikungunya"],
            "dateFrom": 2019,
            "dateTo": 2022,
        }
        assert "IndicadorSaude" in query
        assert "$sistema" in query and "$subtipos" in query
        # Sem dimensão: não deve haver nenhuma referência a relacionamento
        assert "-[r:" not in query

    def test_returns_valor_total_field(self):
        query, _ = build_indicador_cypher("sim", ["mortalidade"], 2019, 2022)
        assert "i.valorTotal AS valor" in query


class TestBuildIndicadorCypherComDimensaoValida:
    def test_valid_dimension_is_interpolated_as_reltype(self):
        query, params = build_indicador_cypher(
            "sinan", ["dengue"], 2019, 2022, dimensao="POR_FAIXA_ETARIA"
        )
        assert "-[r:POR_FAIXA_ETARIA]->" in query
        assert "r.valor AS valor" in query
        assert "dimensao_valor" in query
        # dimensao não vira bind parameter (Neo4j não parametriza reltypes)
        assert "dimensao" not in params

    def test_params_still_fully_bound_for_data_values(self):
        query, params = build_indicador_cypher(
            "sih", ["internacoes"], 2020, 2021, dimensao="POR_CAPITULO_CID"
        )
        assert params["sistema"] == "sih"
        assert params["subtipos"] == ["internacoes"]
        assert params["dateFrom"] == 2020
        assert params["dateTo"] == 2021


class TestBuildIndicadorCypherDimensaoInvalida:
    def test_dimension_not_in_allowlist_for_sistema_raises(self):
        with pytest.raises(DimensaoInvalida):
            build_indicador_cypher("sinan", ["dengue"], 2019, 2022, dimensao="POR_CAPITULO_CID")

    def test_dimension_valid_for_another_sistema_but_not_this_one_raises(self):
        # POR_CAPITULO_CID é válida para sih/sim, não para sipni
        with pytest.raises(DimensaoInvalida):
            build_indicador_cypher(
                "sipni", ["cobertura_vacinal"], 2019, 2022, dimensao="POR_CAPITULO_CID"
            )

    def test_sistema_with_no_dimensions_rejects_any_dimension(self):
        with pytest.raises(DimensaoInvalida):
            build_indicador_cypher("covid", ["casos"], 2019, 2022, dimensao="POR_FAIXA_ETARIA")

    def test_unknown_dimension_string_raises(self):
        with pytest.raises(DimensaoInvalida):
            build_indicador_cypher("sinan", ["dengue"], 2019, 2022, dimensao="DROP TABLE")


# ---------------------------------------------------------------------------
# Fase 3 — build_despesa_cypher (mirror de build_indicador_cypher, lado
# orçamento: DespesaAnual em vez de IndicadorSaude, dimensões não
# particionadas por sistema).
# ---------------------------------------------------------------------------


class TestDimensoesValidasDespesa:
    def test_returns_both_dimensions(self):
        assert dimensoes_validas_despesa() == ["POR_NATUREZA", "POR_APLICACAO"]

    def test_returns_a_copy_not_the_internal_list(self):
        resultado = dimensoes_validas_despesa()
        resultado.append("HACKED")
        assert "HACKED" not in DESPESA_DIMENSOES


class TestBuildDespesaCypherSemDimensao:
    def test_query_and_params_without_dimensao(self):
        query, params = build_despesa_cypher([301, 302], 2019, 2022)
        assert params == {
            "subfuncaoCodigos": [301, 302],
            "dateFrom": 2019,
            "dateTo": 2022,
        }
        assert "DespesaAnual" in query
        assert "$subfuncaoCodigos" in query
        # Sem dimensão: não deve haver nenhuma referência a relacionamento
        assert "-[r:" not in query

    def test_returns_valor_processado_field(self):
        query, _ = build_despesa_cypher([305], 2019, 2022)
        assert "d.valorProcessado AS valor" in query


class TestBuildDespesaCypherComDimensaoValida:
    def test_por_natureza_is_interpolated_as_reltype(self):
        query, params = build_despesa_cypher([305], 2019, 2022, dimensao="POR_NATUREZA")
        assert "-[r:POR_NATUREZA]->" in query
        assert "r.valor AS valor" in query
        assert "dimensao_valor" in query
        # dimensao não vira bind parameter (Neo4j não parametriza reltypes)
        assert "dimensao" not in params

    def test_por_aplicacao_is_interpolated_as_reltype(self):
        query, params = build_despesa_cypher([305], 2019, 2022, dimensao="POR_APLICACAO")
        assert "-[r:POR_APLICACAO]->" in query
        assert "dimensao" not in params

    def test_params_still_fully_bound_for_data_values(self):
        query, params = build_despesa_cypher(
            [122, 301], 2020, 2021, dimensao="POR_NATUREZA"
        )
        assert params["subfuncaoCodigos"] == [122, 301]
        assert params["dateFrom"] == 2020
        assert params["dateTo"] == 2021


class TestBuildDespesaCypherDimensaoInvalida:
    def test_unknown_dimension_string_raises(self):
        with pytest.raises(DimensaoInvalida):
            build_despesa_cypher([305], 2019, 2022, dimensao="DROP TABLE")

    def test_health_side_dimension_is_not_valid_here(self):
        """Cross-contamination: uma dimensão válida do lado saúde não é
        válida do lado despesa — allowlists são independentes."""
        with pytest.raises(DimensaoInvalida):
            build_despesa_cypher([305], 2019, 2022, dimensao="POR_FAIXA_ETARIA")
