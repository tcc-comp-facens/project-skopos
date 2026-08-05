"""Testes das métricas novas da Etapa 6 do PLANO_REFATORACAO.md.

Cobre especificamente o que foi adicionado/reformulado nesta etapa:
compute_token_cost, compute_communication_volume, compute_faithfulness
(modo claim-based via use_llm=True) e compute_analysis_success. As
métricas pré-existentes (E1/E2/Q1/Q3/R1) não ganham cobertura nova aqui
— não foram alteradas por esta etapa.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from core.quality_metrics import (
    compute_all_quality_metrics,
    compute_analysis_success,
    compute_communication_volume,
    compute_faithfulness,
    compute_token_cost,
)


class TestComputeTokenCost:
    def test_normalizes_snapshot(self):
        snapshot = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "call_count": 3}
        assert compute_token_cost(snapshot) == snapshot

    def test_none_returns_zeros(self):
        assert compute_token_cost(None) == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }

    def test_empty_dict_returns_zeros(self):
        assert compute_token_cost({}) == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }

    def test_missing_keys_default_to_zero(self):
        assert compute_token_cost({"prompt_tokens": 5}) == {
            "prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }


class TestComputeCommunicationVolume:
    def test_star_never_has_lateral_hops(self):
        agent_metrics = [{"agentName": "vigilancia_epidemiologica"}, {"agentName": "correlacao"}]
        result = compute_communication_volume("star", agent_metrics)
        assert result["lateral_hops"] == 0
        assert result["lateral_summaries"] == 0
        assert result["agent_invocations"] == 2
        assert result["message_count"] == 4  # 2 agentes * (1 chamada + 1 retorno)

    def test_hierarchical_always_has_3_lateral_hops(self):
        agent_metrics = [{"agentName": "supervisor_dominio"}, {"agentName": "vigilancia_epidemiologica"}]
        result = compute_communication_volume("hierarchical", agent_metrics)
        assert result["lateral_hops"] == 3
        assert result["lateral_summaries"] == 2
        assert result["message_count"] == 2 * 2 + 3

    def test_payload_records_sums_despesas_e_indicadores(self):
        result = compute_communication_volume(
            "star", [], despesas_count=10, indicadores_count=7
        )
        assert result["payload_records"] == 17

    def test_no_agents_zero_invocations(self):
        result = compute_communication_volume("star", [])
        assert result["agent_invocations"] == 0
        assert result["message_count"] == 0


class TestComputeFaithfulnessClaimBased:
    def test_use_llm_false_keeps_substring_behavior(self):
        """Comportamento default (use_llm=False) precisa continuar
        idêntico ao que já existia antes da Etapa 6 — gratuito, síncrono."""
        correlacoes = [{"subfuncao": 305, "tipo_indicador": "dengue", "classificacao": "alta"}]
        with patch("core.llm_client.generate") as mock_generate:
            result = compute_faithfulness(correlacoes, [], "texto sobre dengue e subfunção 305")
        mock_generate.assert_not_called()
        assert result["score"] == 1.0

    def test_use_llm_true_uses_claim_verifier(self):
        extract_resp = json.dumps({"claims": ["afirmação sobre dengue"]})
        verify_resp = json.dumps({
            "verificacoes": [{"claim": "afirmação sobre dengue", "suportado": True, "justificativa": "bate"}]
        })
        with patch("core.llm_client.generate", side_effect=[extract_resp, verify_resp]) as mock_generate:
            result = compute_faithfulness(
                [{"subfuncao": 305}], [], "texto qualquer", {}, use_llm=True, caller="test",
            )
        assert mock_generate.call_count == 2
        assert result["method"] == "claim_based"
        assert result["score"] == 1.0
        assert result["total_checkpoints"] == 1

    def test_use_llm_true_empty_text_returns_zero_without_calling_llm(self):
        with patch("core.llm_client.generate") as mock_generate:
            result = compute_faithfulness([], [], "", use_llm=True)
        mock_generate.assert_not_called()
        assert result["score"] == 0.0

    def test_use_llm_true_llm_failure_does_not_raise(self):
        with patch("core.llm_client.generate", side_effect=Exception("LLM indisponível")):
            result = compute_faithfulness([{"subfuncao": 305}], [], "texto", use_llm=True)
        assert result["method"] == "claim_based"
        assert result["score"] == 1.0  # nada a penalizar quando não há claims verificadas
        assert result["total_checkpoints"] == 0


class TestComputeAnalysisSuccess:
    def _resultado_completo(self, **overrides):
        base = {
            "despesas": [1], "indicadores": [1], "dados_cruzados": [1],
            "correlacoes": [1], "anomalias": [1], "contexto_orcamentario": {"1": {}},
            "texto_analise": "texto",
        }
        base.update(overrides)
        return base

    def test_success_when_r1_complete_and_within_budget(self):
        result = compute_analysis_success(self._resultado_completo(), wall_clock_ms=1000, time_budget_ms=5000)
        assert result["success"] is True
        assert result["r1_complete"] is True
        assert result["within_time_budget"] is True

    def test_fails_when_r1_incomplete(self):
        result = compute_analysis_success(self._resultado_completo(despesas=[]), wall_clock_ms=1000)
        assert result["success"] is False
        assert result["r1_complete"] is False

    def test_fails_when_over_time_budget(self):
        result = compute_analysis_success(
            self._resultado_completo(), wall_clock_ms=10_000, time_budget_ms=5_000,
        )
        assert result["success"] is False
        assert result["within_time_budget"] is False

    def test_zero_wall_clock_disables_budget_check(self):
        result = compute_analysis_success(self._resultado_completo(), wall_clock_ms=0)
        assert result["within_time_budget"] is True

    def test_self_check_not_run_does_not_penalize(self):
        result = compute_analysis_success(self._resultado_completo())
        assert result["self_check_ok"] is True

    def test_self_check_revisado_counts_as_ok(self):
        resultado = self._resultado_completo(self_check={
            "verificado": True, "revisado": True,
            "claims": [{"claim": "x", "suportado": False}],
        })
        result = compute_analysis_success(resultado)
        assert result["self_check_ok"] is True

    def test_self_check_nao_revisado_com_claim_nao_suportada_falha(self):
        resultado = self._resultado_completo(self_check={
            "verificado": True, "revisado": False,
            "claims": [{"claim": "x", "suportado": False}],
        })
        result = compute_analysis_success(resultado)
        assert result["self_check_ok"] is False
        assert result["success"] is False

    def test_self_check_sem_claims_nao_suportadas_ok(self):
        resultado = self._resultado_completo(self_check={
            "verificado": True, "revisado": False,
            "claims": [{"claim": "x", "suportado": True}],
        })
        result = compute_analysis_success(resultado)
        assert result["self_check_ok"] is True


class TestComputeAllQualityMetricsWiring:
    """Confirma que a agregadora inclui as novas seções (cost, communication,
    outcome) e propaga corretamente os snapshots de token."""

    def _resultado(self):
        return {
            "correlacoes": [], "anomalias": [], "texto_analise": "",
            "contexto_orcamentario": {}, "despesas": [1, 2], "indicadores": [3],
            "dados_cruzados": [], "data_coverage": {},
        }

    def test_includes_cost_communication_outcome_sections(self):
        star_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "call_count": 2}
        metrics = compute_all_quality_metrics(
            star_result=self._resultado(),
            hier_result=self._resultado(),
            star_agent_metrics=[{"agentName": "vigilancia_epidemiologica", "executionTimeMs": 10}],
            hier_agent_metrics=[{"agentName": "supervisor_dominio", "executionTimeMs": 10}],
            star_token_usage=star_usage,
        )
        assert metrics["cost"]["star"] == star_usage
        assert metrics["cost"]["hierarchical"]["total_tokens"] == 0
        assert metrics["communication"]["star"]["lateral_hops"] == 0
        assert metrics["communication"]["hierarchical"]["lateral_hops"] == 3
        assert metrics["communication"]["star"]["payload_records"] == 3
        assert "outcome" in metrics
        assert "success" in metrics["outcome"]["star"]

    def test_faithfulness_claims_only_computed_when_use_llm_judge(self):
        with patch("core.llm_client.generate", return_value=None):
            metrics_off = compute_all_quality_metrics(
                star_result=self._resultado(), hier_result=self._resultado(),
                star_agent_metrics=[], hier_agent_metrics=[],
                use_llm_judge=False, use_llm=True,
            )
        assert "faithfulness_claims" not in metrics_off["quality"]["star"]

        extract_resp = json.dumps({"claims": []})
        with patch("core.llm_client.generate", return_value=extract_resp):
            metrics_on = compute_all_quality_metrics(
                star_result=self._resultado(), hier_result=self._resultado(),
                star_agent_metrics=[], hier_agent_metrics=[],
                use_llm_judge=True, use_llm=True,
            )
        assert "faithfulness_claims" in metrics_on["quality"]["star"]
        assert "faithfulness_claims" in metrics_on["quality"]["hierarchical"]
