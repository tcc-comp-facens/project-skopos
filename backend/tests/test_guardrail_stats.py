"""Testes de core/guardrail_stats.py (Etapa 6 do PLANO_REFATORACAO.md)."""

from __future__ import annotations

import pytest

from core import guardrail_stats


@pytest.fixture(autouse=True)
def _reset():
    guardrail_stats.reset_guardrail_counts()
    yield
    guardrail_stats.reset_guardrail_counts()


def test_no_decisions_yet_rate_is_zero():
    result = guardrail_stats.compute_guardrail_rejection_rate()
    assert result == {"total_messages": 0, "rejected": 0, "rejection_rate": 0.0}


def test_records_in_scope_decision():
    guardrail_stats.record_guardrail_decision(out_of_scope=False)
    result = guardrail_stats.compute_guardrail_rejection_rate()
    assert result == {"total_messages": 1, "rejected": 0, "rejection_rate": 0.0}


def test_records_out_of_scope_decision():
    guardrail_stats.record_guardrail_decision(out_of_scope=True)
    result = guardrail_stats.compute_guardrail_rejection_rate()
    assert result == {"total_messages": 1, "rejected": 1, "rejection_rate": 1.0}


def test_computes_rate_across_multiple_decisions():
    guardrail_stats.record_guardrail_decision(out_of_scope=True)
    guardrail_stats.record_guardrail_decision(out_of_scope=False)
    guardrail_stats.record_guardrail_decision(out_of_scope=False)
    guardrail_stats.record_guardrail_decision(out_of_scope=False)
    result = guardrail_stats.compute_guardrail_rejection_rate()
    assert result["total_messages"] == 4
    assert result["rejected"] == 1
    assert result["rejection_rate"] == 0.25


def test_reset_clears_counters():
    guardrail_stats.record_guardrail_decision(out_of_scope=True)
    guardrail_stats.reset_guardrail_counts()
    result = guardrail_stats.compute_guardrail_rejection_rate()
    assert result["total_messages"] == 0
