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


class TestPeriodoAnalisado:
    """O texto final (LLM ou fallback) sempre declara o período analisado
    quando date_from/date_to são informados — inclusive quando o período
    foi inferido pelo guardrail de intenção, não só quando explicitado
    pelo usuário (AgenteInterpretacaoIntencao)."""

    def test_fallback_mentions_period_when_given(self, synth):
        text = synth.generate_fallback([], [], {}, date_from=2020, date_to=2025)
        assert "2020" in text
        assert "2025" in text
        assert "Período analisado" in text

    def test_fallback_omits_period_line_when_not_given(self, synth):
        text = synth.generate_fallback([], [], {})
        assert "Período analisado" not in text

    def test_llm_prompt_includes_period_instruction_when_given(
        self, synth, sample_correlacoes, sample_anomalias, sample_contexto
    ):
        prompt = synth._build_prompt(
            sample_correlacoes, sample_anomalias, sample_contexto,
            date_from=2020, date_to=2025,
        )
        assert "PERÍODO ANALISADO: 2020 a 2025" in prompt

    def test_llm_prompt_omits_period_instruction_when_not_given(
        self, synth, sample_correlacoes, sample_anomalias, sample_contexto
    ):
        prompt = synth._build_prompt(sample_correlacoes, sample_anomalias, sample_contexto)
        assert "PERÍODO ANALISADO" not in prompt

    def test_generate_without_llm_passes_period_through_to_fallback(self, synth):
        text = synth.generate(
            [], [], {}, use_llm=False, date_from=2015, date_to=2025,
        )
        assert "Período analisado: 2015 a 2025" in text

    def test_old_call_sites_without_period_still_work(self, synth):
        """Chamadas antigas (sem date_from/date_to) continuam funcionando
        sem erro — os novos parâmetros são opcionais."""
        text = synth.generate([], [], {}, use_llm=False)
        assert "Resumo Executivo" in text
        assert "Período analisado" not in text
