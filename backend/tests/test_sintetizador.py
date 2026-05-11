"""Tests for TextSynthesizer."""

import pytest
from unittest.mock import patch, MagicMock

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
        mock_llm = MagicMock()
        mock_llm.generate_stream.side_effect = RuntimeError("LLM unavailable")
        with patch.dict("sys.modules", {"core.llm_client": mock_llm}):
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
    @patch.dict("sys.modules", {"core.llm_client": MagicMock()})
    def test_yields_tokens_from_llm(self, synth, sample_correlacoes, sample_anomalias, sample_contexto):
        import sys
        mock_llm = sys.modules["core.llm_client"]
        mock_llm.generate_stream.return_value = iter(["Hello", " ", "world"])

        tokens = list(synth.generate_stream(sample_correlacoes, sample_anomalias, sample_contexto))
        assert tokens == ["Hello", " ", "world"]
