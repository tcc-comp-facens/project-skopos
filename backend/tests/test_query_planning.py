"""Testes do módulo compartilhado agents.domain.query_planning (Etapa 2)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agents.domain import query_planning


@pytest.fixture(autouse=True)
def _clean_cache_and_env(monkeypatch):
    """Cada teste começa com cache vazio e a flag desligada (default)."""
    query_planning.reset_cache()
    monkeypatch.delenv("USE_LLM_QUERY_PLANNING", raising=False)
    yield
    query_planning.reset_cache()


class TestFlag:
    def test_disabled_by_default(self):
        assert query_planning.llm_query_planning_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "True", "YES"])
    def test_enabled_values(self, monkeypatch, value):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", value)
        assert query_planning.llm_query_planning_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", ""])
    def test_disabled_values(self, monkeypatch, value):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", value)
        assert query_planning.llm_query_planning_enabled() is False


class TestFastPath:
    def test_trivial_mapping_never_calls_llm_even_with_flag_on(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue", "covid"]}
        with patch("core.llm_client.generate") as mock_generate:
            plan, origem = query_planning.plan_query(
                agent_id="test-agent",
                agent_type="vigilancia_epidemiologica",
                static_plan=static_plan,
                is_trivial=True,
                intent_summary="comparar dengue",
                health_params=["dengue"],
            )
        assert origem == "fast_path"
        assert plan == static_plan
        mock_generate.assert_not_called()

    def test_flag_off_never_calls_llm_even_when_non_trivial(self):
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue", "covid"]}
        with patch("core.llm_client.generate") as mock_generate:
            plan, origem = query_planning.plan_query(
                agent_id="test-agent",
                agent_type="vigilancia_epidemiologica",
                static_plan=static_plan,
                is_trivial=False,
                intent_summary="algo novo",
                health_params=["algo_novo"],
            )
        assert origem == "fast_path"
        assert plan == static_plan
        mock_generate.assert_not_called()


class TestLlmPath:
    def test_llm_called_when_non_trivial_and_flag_on(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue", "covid"]}
        llm_response = json.dumps({"subfuncoes": [305], "tipos_indicador": ["dengue", "covid", "zika"]})
        with patch("core.llm_client.generate", return_value=llm_response) as mock_generate:
            plan, origem = query_planning.plan_query(
                agent_id="test-agent",
                agent_type="vigilancia_epidemiologica",
                static_plan=static_plan,
                is_trivial=False,
                intent_summary="e quanto a zika?",
                health_params=["dengue", "zika"],
            )
        assert origem == "llm"
        assert plan == {"subfuncoes": [305], "tipos_indicador": ["dengue", "covid", "zika"]}
        mock_generate.assert_called_once()
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test-agent:planejar_consulta"

    def test_cache_avoids_second_llm_call(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue"]}
        llm_response = json.dumps({"subfuncoes": [305], "tipos_indicador": ["dengue", "zika"]})
        kwargs = dict(
            agent_id="test-agent",
            agent_type="vigilancia_epidemiologica",
            static_plan=static_plan,
            is_trivial=False,
            intent_summary="e quanto a zika?",
            health_params=["dengue", "zika"],
        )
        with patch("core.llm_client.generate", return_value=llm_response) as mock_generate:
            plan1, origem1 = query_planning.plan_query(**kwargs)
            plan2, origem2 = query_planning.plan_query(**kwargs)

        assert origem1 == "llm"
        assert origem2 == "cache"
        assert plan1 == plan2
        mock_generate.assert_called_once()

    def test_different_intent_summary_is_a_different_cache_key(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue"]}
        llm_response = json.dumps({"subfuncoes": [305], "tipos_indicador": ["dengue"]})
        with patch("core.llm_client.generate", return_value=llm_response) as mock_generate:
            query_planning.plan_query(
                agent_id="a", agent_type="vigilancia_epidemiologica",
                static_plan=static_plan, is_trivial=False,
                intent_summary="pergunta 1", health_params=["dengue"],
            )
            query_planning.plan_query(
                agent_id="a", agent_type="vigilancia_epidemiologica",
                static_plan=static_plan, is_trivial=False,
                intent_summary="pergunta 2", health_params=["dengue"],
            )
        assert mock_generate.call_count == 2

    def test_llm_failure_falls_back_to_static_plan(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue"]}
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            plan, origem = query_planning.plan_query(
                agent_id="test-agent", agent_type="vigilancia_epidemiologica",
                static_plan=static_plan, is_trivial=False,
                intent_summary="x", health_params=["dengue"],
            )
        assert origem == "llm_failed_fallback"
        assert plan == static_plan

    def test_malformed_llm_json_falls_back_to_static_plan(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue"]}
        with patch("core.llm_client.generate", return_value="isto não é JSON"):
            plan, origem = query_planning.plan_query(
                agent_id="test-agent", agent_type="vigilancia_epidemiologica",
                static_plan=static_plan, is_trivial=False,
                intent_summary="x", health_params=["dengue"],
            )
        assert origem == "llm_failed_fallback"
        assert plan == static_plan

    def test_empty_llm_response_falls_back_to_static_plan(self, monkeypatch):
        monkeypatch.setenv("USE_LLM_QUERY_PLANNING", "true")
        static_plan = {"subfuncoes": [305], "tipos_indicador": ["dengue"]}
        with patch("core.llm_client.generate", return_value=None):
            plan, origem = query_planning.plan_query(
                agent_id="test-agent", agent_type="vigilancia_epidemiologica",
                static_plan=static_plan, is_trivial=False,
                intent_summary="x", health_params=["dengue"],
            )
        assert origem == "llm_failed_fallback"
        assert plan == static_plan


class TestParsePlan:
    def test_parses_plain_json(self):
        raw = json.dumps({"subfuncoes": [301, 302], "tipos_indicador": ["vacinacao"]})
        plan = query_planning._parse_plan(raw)
        assert plan == {"subfuncoes": [301, 302], "tipos_indicador": ["vacinacao"]}

    def test_parses_json_wrapped_in_markdown_fence(self):
        raw = '```json\n{"subfuncoes": [301], "tipos_indicador": ["vacinacao"]}\n```'
        plan = query_planning._parse_plan(raw)
        assert plan == {"subfuncoes": [301], "tipos_indicador": ["vacinacao"]}

    def test_rejects_missing_keys(self):
        with pytest.raises(ValueError):
            query_planning._parse_plan(json.dumps({"subfuncoes": [301]}))

    def test_rejects_non_int_subfuncoes(self):
        with pytest.raises(ValueError):
            query_planning._parse_plan(
                json.dumps({"subfuncoes": ["301"], "tipos_indicador": ["vacinacao"]})
            )

    def test_rejects_non_string_tipos(self):
        with pytest.raises(ValueError):
            query_planning._parse_plan(
                json.dumps({"subfuncoes": [301], "tipos_indicador": [1, 2]})
            )
