"""Testes de core/claim_verifier.py (Etapa 4 do PLANO_REFATORACAO.md)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.claim_verifier import extract_claims, revise_text, self_check, verify_claims

DADOS = {
    "correlacoes": [
        {"subfuncao": 305, "tipo_indicador": "dengue", "spearman": -0.3, "classificacao": "baixa"},
    ],
    "anomalias": [],
    "contexto_orcamentario": {},
}


class TestExtractClaims:
    def test_empty_text_returns_empty_list(self):
        assert extract_claims("") == []
        assert extract_claims("   ") == []

    def test_parses_claims_from_llm_response(self):
        resposta = json.dumps({"claims": ["A correlação em Vigilância é fraca", "O gasto caiu 20%"]})
        with patch("core.llm_client.generate", return_value=resposta) as mock_generate:
            claims = extract_claims("algum texto", caller="test")
        mock_generate.assert_called_once()
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test:extract_claims"
        assert claims == ["A correlação em Vigilância é fraca", "O gasto caiu 20%"]

    def test_llm_unavailable_returns_empty_list(self):
        with patch("core.llm_client.generate", return_value=None):
            assert extract_claims("algum texto") == []

    def test_malformed_json_returns_empty_list(self):
        with patch("core.llm_client.generate", return_value="isto não é JSON"):
            assert extract_claims("algum texto") == []

    def test_truncates_to_max_claims(self):
        muitas = [f"claim {i}" for i in range(30)]
        resposta = json.dumps({"claims": muitas})
        with patch("core.llm_client.generate", return_value=resposta):
            claims = extract_claims("texto longo")
        assert len(claims) == 15

    def test_strips_markdown_fences(self):
        resposta = "```json\n" + json.dumps({"claims": ["x"]}) + "\n```"
        with patch("core.llm_client.generate", return_value=resposta):
            assert extract_claims("texto") == ["x"]


class TestVerifyClaims:
    def test_empty_claims_returns_empty_list(self):
        assert verify_claims([], DADOS) == []

    def test_parses_verifications_in_order(self):
        claims = ["afirmação suportada", "afirmação inventada"]
        resposta = json.dumps({
            "verificacoes": [
                {"claim": claims[0], "suportado": True, "justificativa": "bate com os dados"},
                {"claim": claims[1], "suportado": False, "justificativa": "não aparece nos dados"},
            ]
        })
        with patch("core.llm_client.generate", return_value=resposta) as mock_generate:
            result = verify_claims(claims, DADOS, caller="test")
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test:verify_claims"
        assert result[0] == {"claim": claims[0], "suportado": True, "justificativa": "bate com os dados"}
        assert result[1] == {"claim": claims[1], "suportado": False, "justificativa": "não aparece nos dados"}

    def test_llm_unavailable_assumes_supported(self):
        claims = ["afirmação qualquer"]
        with patch("core.llm_client.generate", return_value=None):
            result = verify_claims(claims, DADOS)
        assert result[0]["suportado"] is True

    def test_malformed_response_assumes_supported(self):
        claims = ["afirmação qualquer"]
        with patch("core.llm_client.generate", return_value="não é JSON"):
            result = verify_claims(claims, DADOS)
        assert result[0]["suportado"] is True

    def test_missing_entries_default_to_supported(self):
        """Resposta do LLM com menos entradas que claims não deve quebrar."""
        claims = ["claim 1", "claim 2", "claim 3"]
        resposta = json.dumps({"verificacoes": [{"claim": "claim 1", "suportado": False, "justificativa": "x"}]})
        with patch("core.llm_client.generate", return_value=resposta):
            result = verify_claims(claims, DADOS)
        assert len(result) == 3
        assert result[0]["suportado"] is False
        assert result[1]["suportado"] is True
        assert result[2]["suportado"] is True


class TestReviseText:
    def test_no_unsupported_claims_returns_none(self):
        assert revise_text("texto original", [], DADOS) is None

    def test_returns_revised_text_from_llm(self):
        nao_suportadas = [{"claim": "afirmação inventada", "suportado": False, "justificativa": "não bate"}]
        with patch("core.llm_client.generate", return_value="texto corrigido") as mock_generate:
            revisado = revise_text("texto original", nao_suportadas, DADOS, caller="test")
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test:revise_text"
        assert revisado == "texto corrigido"

    def test_llm_unavailable_returns_none(self):
        nao_suportadas = [{"claim": "x", "suportado": False, "justificativa": "y"}]
        with patch("core.llm_client.generate", return_value=None):
            assert revise_text("texto original", nao_suportadas, DADOS) is None


class TestSelfCheckOrchestration:
    """Testa o fluxo completo self_check() — critério de aceite da Etapa 4."""

    def test_no_claims_extracted_keeps_text_unchanged(self):
        with patch("core.llm_client.generate", return_value=json.dumps({"claims": []})):
            resultado = self_check("texto sem afirmações", [], [], {})
        assert resultado["texto_final"] == "texto sem afirmações"
        assert resultado["revisado"] is False
        assert resultado["verificado"] is True
        assert resultado["claims"] == []

    def test_all_claims_supported_keeps_text_unchanged(self):
        texto = "O gasto em Vigilância caiu 20% no período."
        extract_resp = json.dumps({"claims": ["O gasto em Vigilância caiu 20%"]})
        verify_resp = json.dumps({
            "verificacoes": [{"claim": "O gasto em Vigilância caiu 20%", "suportado": True, "justificativa": "bate"}]
        })
        with patch("core.llm_client.generate", side_effect=[extract_resp, verify_resp]):
            resultado = self_check(texto, DADOS["correlacoes"], [], {})
        assert resultado["texto_final"] == texto
        assert resultado["revisado"] is False
        assert len(resultado["claims"]) == 1

    def test_hallucination_detected_and_corrected(self):
        """Critério de aceite: injeta uma alucinação, verify_claims marca
        como não suportada, e a passada de correção remove/corrige a
        afirmação no texto final."""
        texto = (
            "A correlação entre gastos em Vigilância e casos de dengue é fraca. "
            "O investimento em saúde já erradicou completamente a dengue em Sorocaba."
        )
        claim_real = "A correlação entre gastos em Vigilância e casos de dengue é fraca"
        claim_alucinada = "O investimento em saúde já erradicou completamente a dengue em Sorocaba"

        extract_resp = json.dumps({"claims": [claim_real, claim_alucinada]})
        verify_resp = json.dumps({
            "verificacoes": [
                {"claim": claim_real, "suportado": True, "justificativa": "spearman=-0.3, classificação baixa"},
                {"claim": claim_alucinada, "suportado": False, "justificativa": "dados não mostram erradicação"},
            ]
        })
        texto_corrigido = (
            "A correlação entre gastos em Vigilância e casos de dengue é fraca."
        )

        with patch(
            "core.llm_client.generate",
            side_effect=[extract_resp, verify_resp, texto_corrigido],
        ) as mock_generate:
            resultado = self_check(texto, DADOS["correlacoes"], [], {}, caller="test")

        assert mock_generate.call_count == 3
        assert resultado["revisado"] is True
        assert resultado["texto_final"] == texto_corrigido
        assert claim_alucinada not in resultado["texto_final"]
        nao_suportadas = [c for c in resultado["claims"] if not c["suportado"]]
        assert len(nao_suportadas) == 1
        assert nao_suportadas[0]["claim"] == claim_alucinada

    def test_revision_fails_keeps_original_text(self):
        texto = "texto com alucinação"
        extract_resp = json.dumps({"claims": ["afirmação inventada"]})
        verify_resp = json.dumps({
            "verificacoes": [{"claim": "afirmação inventada", "suportado": False, "justificativa": "não bate"}]
        })
        with patch("core.llm_client.generate", side_effect=[extract_resp, verify_resp, None]):
            resultado = self_check(texto, [], [], {})
        assert resultado["revisado"] is False
        assert resultado["texto_final"] == texto
        assert resultado["verificado"] is True

    def test_extract_failure_returns_original_text_not_verified(self):
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            resultado = self_check("texto qualquer", [], [], {})
        assert resultado["texto_final"] == "texto qualquer"
        assert resultado["revisado"] is False
        assert resultado["verificado"] is False
        assert resultado["claims"] == []

    def test_never_raises_on_verify_failure(self):
        extract_resp = json.dumps({"claims": ["alguma afirmação"]})
        with patch("core.llm_client.generate", side_effect=[extract_resp, Exception("boom")]):
            resultado = self_check("texto", [], [], {})
        assert resultado["texto_final"] == "texto"
        assert resultado["verificado"] is False
