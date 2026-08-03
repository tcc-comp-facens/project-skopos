"""Tests for TextSynthesizer."""

import pytest
from unittest.mock import patch

from agents.analytical.sintetizador import TextSynthesizer


@pytest.fixture
def synth():
    return TextSynthesizer("test-synth")


@pytest.fixture
def sample_correlacoes():
    return [
        {
            "subfuncao": 301,
            "subfuncao_nome": "Atenção Básica",
            "tipo_indicador": "vacinacao",
            "spearman": 0.85,
            "pearson": 0.80,
            "kendall": 0.75,
            "classificacao": "alta",
            "n_pontos": 5,
        }
    ]


@pytest.fixture
def sample_anomalias():
    return [
        {
            "subfuncao": 302,
            "tipo_indicador": "internacoes",
            "ano": 2021,
            "tipo_anomalia": "alto_gasto_baixo_resultado",
            "descricao": "Subfunção 302 em 2021: gasto acima da mediana com indicador abaixo",
        }
    ]


@pytest.fixture
def sample_contexto():
    return {
        301: {
            "subfuncao": 301,
            "tendencia": "crescimento",
            "variacao_media_percentual": 15.0,
            "anos_analisados": [2019, 2020, 2021],
        }
    }


class TestGenerateWithoutLlm:
    def test_returns_structured_text_with_all_sections(self, synth, sample_correlacoes, sample_anomalias, sample_contexto):
        text = synth.generate(sample_correlacoes, sample_anomalias, sample_contexto, use_llm=False)
        assert "Resumo Executivo" in text
        assert "Correlações" in text
        assert "Anomalias" in text
        assert "Contexto Orçamentário" in text


class TestGenerateFallbackOnLlmFailure:
    def test_falls_back_when_llm_raises(self, synth, sample_correlacoes, sample_anomalias, sample_contexto):
        # patch no atributo (não em sys.modules) — robusto à ordem de execução
        # dos testes, diferente de patch.dict("sys.modules", ...), que só
        # funciona se core.llm_client nunca tiver sido importado de verdade
        # antes neste processo (ver import a.b as c vs sys.modules["a.b"]).
        with patch("core.llm_client.generate_stream", side_effect=RuntimeError("LLM unavailable")):
            text = synth.generate(sample_correlacoes, sample_anomalias, sample_contexto, use_llm=True)
            assert "Resumo Executivo" in text


class TestGenerateFallback:
    def test_includes_all_sections(self, synth):
        text = synth.generate_fallback([], [], {})
        assert "Resumo Executivo" in text
        assert "Correlações" in text
        assert "Anomalias" in text
        assert "Contexto Orçamentário" in text

    def test_references_correlation_data(self, synth, sample_correlacoes):
        text = synth.generate_fallback(sample_correlacoes, [], {})
        assert "vacinacao" in text

    def test_references_anomaly_descriptions(self, synth, sample_anomalias):
        text = synth.generate_fallback([], sample_anomalias, {})
        assert "gasto acima da mediana" in text


class TestGenerateStream:
    def test_yields_tokens_from_llm(self, synth, sample_correlacoes, sample_anomalias, sample_contexto):
        with patch("core.llm_client.generate_stream", return_value=iter(["Hello", " ", "world"])):
            tokens = list(synth.generate_stream(sample_correlacoes, sample_anomalias, sample_contexto))
        assert tokens == ["Hello", " ", "world"]
