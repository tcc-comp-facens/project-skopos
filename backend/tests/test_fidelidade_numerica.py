"""Testes da fidelidade numérica (Q4, core/quality_metrics.py)."""

from core.quality_metrics import compute_numeric_fidelity, numeric_fidelity_of

CONTEXTOS = [
    "Período analisado: a análise cobre os anos de 2016 a 2025.",
    "Correlação: subfunção 305 (Vigilância Epidemiológica) × casos — coeficiente de Spearman "
    "-0.8, classificação alta, calculada sobre 4 pontos.",
    "Anomalia em 2022: subfunção 305 × casos — tipo alto_gasto_baixo_resultado. gasto acima da "
    "mediana (R$ 24,409,337.18) com indicador acima da mediana (31375.0 casos).",
    "Contexto orçamentário da subfunção 305: tendência crescimento, variação média 67.66%.",
    "Cobertura dos dados: completude das despesas 100%, completude dos indicadores 40%.",
]


def _q4(texto, anos=(2016, 2025)):
    return compute_numeric_fidelity(texto, CONTEXTOS, anos)


class TestFormatos:
    def test_milhar_brasileiro_e_valor_com_escala(self):
        r = _q4("Foram 31.375 casos, com gasto de cerca de R$ 24,4 milhões.")
        assert r["numeros_no_texto"] == 2 and r["verificados"] == 2 and r["score"] == 1.0

    def test_percentual_e_coeficiente(self):
        r = _q4("A variação média foi de 67,66%, com coeficiente de 0,8 e completude de 40%.")
        assert r["nao_verificados"] == [] and r["score"] == 1.0

    def test_numero_inventado_nao_verifica(self):
        r = _q4("Houve 211 casos e o gasto foi de R$ 24,4 milhões.")
        assert r["nao_verificados"] == ["211"]
        assert r["score"] == 0.5

    def test_arredondamento_declarado_pelo_texto(self):
        assert _q4("cerca de 31 mil casos")["score"] == 1.0   # ±2% do valor
        assert _q4("cerca de 24 milhões")["score"] == 1.0     # ±500 mil
        assert _q4("cerca de 20 milhões")["score"] == 0.0


class TestAnosEExclusoes:
    def test_ano_dentro_do_periodo_verifica(self):
        r = _q4("Entre 2020 e 2023 os casos caíram.")
        assert r["verificados"] == 2

    def test_ano_fora_do_periodo_nao_verifica(self):
        assert _q4("Em 2012 houve um surto.")["nao_verificados"] == ["2012"]

    def test_digitos_colados_a_letras_e_inteiros_pequenos_ficam_de_fora(self):
        r = _q4("A COVID-19 afetou dois anos; há 3 exemplos e 4 pontos.")
        assert r["numeros_no_texto"] == 0 and r["score"] is None

    def test_texto_vazio(self):
        assert compute_numeric_fidelity("", CONTEXTOS)["score"] is None


class TestComResultado:
    def test_monta_o_material_do_resultado(self):
        resultado = {
            "texto_analise": "A correlação usou 4 pontos e a variação média foi de 12,5%.",
            "correlacoes": [],
            "anomalias": [],
            "contexto_orcamentario": {
                "301": {"tendencia": "crescimento", "variacao_media_percentual": 12.5},
            },
            "data_coverage": {"expected_years": [2019, 2020, 2021]},
        }
        r = numeric_fidelity_of(resultado)
        assert r["score"] == 1.0 and r["numeros_no_texto"] == 1
