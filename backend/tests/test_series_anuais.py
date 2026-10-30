"""Séries anuais entregues ao redator e ao juiz (core/contextos_avaliacao.linhas_series).

Regressão: o redator só via os anos que viraram anomalia. Numa pergunta sobre
dengue de 2020 a 2023 ele escreveu que "não há o número de 2020 e 2022", dados
que existiam, e não conseguia responder "quantos casos".
"""

from queue import Queue
from unittest.mock import MagicMock, patch

from agents.analytical import redacao_ramos
from agents.analytical.analitico import AgenteAnalitico
from agents.analytical.sintetizador import TextSynthesizer
from agents.star.orchestrator import OrquestradorEstrela
from core.contextos_avaliacao import build_contexts, linhas_series
from core.quality_metrics import numeric_fidelity_of

DESPESAS = [
    {"subfuncao": 305, "ano": 2020, "valor": 7629751.43},
    {"subfuncao": 305, "ano": 2021, "valor": 7412168.39},
    {"subfuncao": 305, "ano": 2022, "valor": None},
]
INDICADORES = [
    {"tipo": "dengue", "ano": 2021, "valor": 1229.0},
    {"tipo": "dengue", "ano": 2020, "valor": 2000.0},
    {"tipo": "dengue", "ano": 2020, "valor": 30.0, "dimensao_valor": "0-9"},
    {"tipo": "cobertura_vacinal", "ano": 2020, "valor": 87.5},
]


class TestLinhasSeries:
    def test_uma_linha_por_serie_em_ordem_de_ano(self):
        linhas = linhas_series(DESPESAS, INDICADORES)
        assert linhas == [
            "Gasto anual da subfunção 305 (Vigilância Epidemiológica): "
            "2020: R$ 7,629,751.43; 2021: R$ 7,412,168.39.",
            "Valor anual do indicador dengue: 2020: 2,000; 2021: 1,229.",
            "Valor anual do indicador dengue (0-9): 2020: 30.",
            "Valor anual do indicador cobertura_vacinal: 2020: 87.50.",
        ]

    def test_sem_dados_sem_linhas(self):
        assert linhas_series([], []) == []
        assert linhas_series([1, 2], [3]) == []  # registros que não são dict são ignorados


class TestRedatorRecebeAsSeries:
    def test_prompt_unico_ramo_e_composicao(self):
        series = linhas_series(DESPESAS, INDICADORES)
        s = TextSynthesizer("x")
        prompts = [
            s._build_prompt([], [], {}, series=series),
            s._build_prompt_ramo("SINAN", [], [], {}, None, 2020, 2021, None, series),
            s._build_prompt_composicao([{"sistema": "SINAN", "texto": "t"}], [], None, None,
                                       2020, 2021, None, series),
        ]
        for p in prompts:
            assert "VALORES ANUAIS" in p and "Valor anual do indicador dengue: 2020: 2,000" in p
        assert "nos VALORES ANUAIS" in prompts[2]

    def test_sem_series_o_prompt_nao_ganha_a_secao(self):
        assert "VALORES ANUAIS" not in TextSynthesizer("x")._build_prompt([], [], {})

    def test_ramo_ve_so_o_seu_sistema(self):
        ramo = {"correlacoes": [{"subfuncao": 305, "tipo_indicador": "dengue"}], "anomalias": []}
        linhas = redacao_ramos.series_do_ramo("sinan", ramo, DESPESAS, INDICADORES)
        assert any("dengue" in l for l in linhas)
        assert not any("cobertura_vacinal" in l for l in linhas)
        assert any("subfunção 305" in l for l in linhas)


class TestJuizRecebeAsMesmasSeries:
    def test_build_contexts_inclui_as_linhas(self):
        ctx = build_contexts({"despesas": DESPESAS, "indicadores": INDICADORES})
        for linha in linhas_series(DESPESAS, INDICADORES):
            assert linha in ctx

    def test_numero_da_serie_e_verificado_pela_fidelidade_numerica(self):
        r = numeric_fidelity_of({
            "despesas": DESPESAS, "indicadores": INDICADORES,
            "data_coverage": {"expected_years": [2020, 2021]},
            "texto_analise": "Em 2020 foram 2.000 casos de dengue, e em 2021, 1.229.",
        })
        assert r["nao_verificados"] == [] and r["verificados"] == 4

    def test_contagem_entre_1900_e_2100_com_separador_nao_vira_ano(self):
        """Antes, "2.000 casos" era tratado como o ano 2000 e reprovado."""
        from core.quality_metrics import compute_numeric_fidelity
        ctx = ["Valor anual do indicador dengue: 2020: 2,000."]
        r = compute_numeric_fidelity("Foram 2.000 casos.", ctx, None)
        assert r["verificados"] == 1


class TestLeituraDaCorrelacaoNomeiaOIndicador:
    def test_sem_frase_generica(self):
        dados = [
            {"subfuncao": 305, "subfuncao_nome": "x", "tipo_indicador": "dengue", "ano": a,
             "valor_despesa": d, "valor_indicador": i}
            for a, d, i in ((2019, 100.0, 10.0), (2020, 200.0, 20.0), (2021, 300.0, 30.0))
        ]
        leitura = AgenteAnalitico("t").analisar(dados)["correlacoes"][0]["leitura"]
        assert "casos/óbitos/internações" not in leitura
        assert "valores maiores de dengue" in leitura and "INDESEJÁVEL" in leitura


def test_estrela_entrega_a_serie_ao_redator():
    client = MagicMock()
    client.get_indicadores_por_sistema.side_effect = lambda sistema, subtipos, *a, **k: [
        {"tipo": t, "ano": ano, "valor": 100.0 + ano % 10} for t in subtipos for ano in (2020, 2021, 2022)
    ]
    client.get_despesas_por_subfuncao.side_effect = lambda codigos, *a, **k: [
        {"subfuncao": codigos[0], "ano": ano, "valor": 1000.0 * (ano % 10)} for ano in (2020, 2021, 2022)
    ]
    client.get_variacao_anual.return_value = []
    prompts = []

    def stream(prompt, **k):
        prompts.append(prompt)
        return iter(["texto"])

    with patch("core.llm_client.generate_stream", side_effect=stream):
        OrquestradorEstrela("o", client).run("s1", {
            "date_from": 2020, "date_to": 2022, "health_params": ["dengue"], "use_llm": True,
        }, Queue())
    assert prompts and "Valor anual do indicador dengue: 2020: 100; 2021: 101; 2022: 102." in prompts[-1]
