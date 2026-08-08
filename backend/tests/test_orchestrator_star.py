"""Tests for OrquestradorEstrela.run() with mocked agents.

Fase 2 (PLANO_NOVO_MODELO_DADOS.md §5): decomposição completa — os 5
agentes de saúde (SINAN, SIH, SIM, SI-PNI, COVID) nunca consultam
despesas, só indicadores; despesas vêm exclusivamente de
AgenteOrcamentoSubfuncao (1 por subfunção, até 7), cada uma ativada
condicionalmente pelos tokens do seu domínio correspondente (ver
_SUBFUNCAO_TOKENS em agents/star/orchestrator.py).
"""

import pytest
from queue import Queue
from unittest.mock import MagicMock, patch

from agents.star.orchestrator import OrquestradorEstrela, _SUBFUNCAO_TOKENS


@pytest.fixture
def neo4j_client():
    client = MagicMock()
    client.get_despesas_por_subfuncao.return_value = [
        {"subfuncao": 305, "subfuncaoNome": "Vigilância", "ano": 2020, "valor": 100.0},
        {"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 200.0},
    ]
    client.get_indicadores_por_sistema.return_value = [
        {"tipo": "dengue", "ano": 2020, "valor": 30.0},
        {"tipo": "vacinacao", "ano": 2020, "valor": 80.0},
    ]
    client.save_metrica = MagicMock()
    return client


@pytest.fixture
def ws_queue():
    return Queue()


@pytest.fixture
def orchestrator(neo4j_client):
    return OrquestradorEstrela("test-orch", neo4j_client)


class TestAllAgentsCalled:
    def test_all_domain_agents_called_when_all_params(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue", "covid", "internacoes", "vacinacao", "mortalidade"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-1", params, ws_queue)
        # 5 agentes de saúde ativados (covid, sih, sipni, sim, sinan) —
        # nenhum deles consulta despesas. Despesas vêm só dos agentes
        # orçamentários ativados: 301 (vacinacao->sipni), 302
        # (internacoes->sih), 303/304/305 (dengue->sinan, mais 305 também
        # via covid) = 5 subfunções ativadas = 5 chamadas a
        # get_despesas_por_subfuncao.
        assert neo4j_client.get_despesas_por_subfuncao.call_count == 5


class TestSubsetAgents:
    def test_only_relevant_agents_called_for_dengue(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-2", params, ws_queue)
        # AgenteSINAN (indicadores) + 3 agentes orçamentários ativados por
        # qualquer agravo do SINAN (303 Suporte Profilático, 304
        # Vigilância Sanitária, 305 Vigilância Epidemiológica) —
        # AgenteSINAN não consulta despesas.
        assert neo4j_client.get_despesas_por_subfuncao.call_count == 3


class TestSubfuncao304GatingRefinado:
    """Regressão: 304 (Vigilância Sanitária) só ativa com um subtipo do
    CNES ("estabelecimentos_vigilancia_epidemiologica" — único com par
    temático real), não com qualquer subtipo do CNES como 122/306
    (decisão explícita tomada com o usuário)."""

    def test_304_only_activates_with_vigilancia_epidemiologica_cnes_subtipo(self):
        assert _SUBFUNCAO_TOKENS[304] & {"leitos", "profissionais", "ocupacoes"} == set()
        assert "estabelecimentos_vigilancia_epidemiologica" in _SUBFUNCAO_TOKENS[304]

    def test_122_e_306_still_activate_with_any_cnes_subtipo(self):
        assert "leitos" in _SUBFUNCAO_TOKENS[122]
        assert "leitos" in _SUBFUNCAO_TOKENS[306]


class TestOrcamentoIntentSummaryWiring:
    """Regressão (Fase 3): intent_summary/use_llm da análise inteira
    precisam chegar em AgenteOrcamentoSubfuncao.query() — sem isso, a
    deliberação de dimensão (POR_NATUREZA/POR_APLICACAO) nunca escolhe
    algo diferente de "sem quebra" em produção, mesmo quando o usuário
    pede explicitamente."""

    def test_intent_summary_reaches_orcamento_dimensao_deliberation(
        self, orchestrator, neo4j_client, ws_queue
    ):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "intent_summary": "quero saber por natureza",
            "use_llm": False,
        }
        orchestrator.run("analysis-intent-orc", params, ws_queue)

        dimensoes_pedidas = {
            call.args[3] if len(call.args) > 3 else call.kwargs.get("dimensao")
            for call in neo4j_client.get_despesas_por_subfuncao.call_args_list
        }
        assert "POR_NATUREZA" in dimensoes_pedidas


class TestGracefulDegradation:
    def test_pipeline_continues_when_agent_fails(self, neo4j_client, ws_queue):
        neo4j_client.get_despesas_por_subfuncao.side_effect = [
            Exception("Neo4j timeout"),
            [{"subfuncao": 301, "subfuncaoNome": "AB", "ano": 2020, "valor": 200.0}],
        ]
        neo4j_client.get_indicadores_por_sistema.side_effect = [
            Exception("Neo4j timeout"),
            [{"tipo": "vacinacao", "ano": 2020, "valor": 80.0}],
        ]
        orch = OrquestradorEstrela("test-orch-fail", neo4j_client)
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue", "vacinacao"],
            "use_llm": False,
        }
        # Should not raise
        result = orch.run("analysis-3", params, ws_queue)
        assert "correlacoes" in result
        assert "anomalias" in result


class TestResultKeys:
    def test_result_contains_expected_keys(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["dengue"],
            "use_llm": False,
        }
        result = orchestrator.run("analysis-5", params, ws_queue)
        expected_keys = {
            "despesas", "indicadores", "dados_cruzados",
            "contexto_orcamentario", "correlacoes", "anomalias",
            "texto_analise", "data_coverage",
        }
        assert expected_keys.issubset(result.keys())


class TestErrorEventsOnFailure:
    def test_error_events_sent_to_ws_queue(self, ws_queue):
        """When agent.query() raises at orchestrator level, error event is sent."""
        mock_client = MagicMock()
        mock_client.save_metrica = MagicMock()
        orch = OrquestradorEstrela("test-orch-err", mock_client)
        params = {
            "date_from": 2019,
            "date_to": 2021,
            "health_params": ["covid"],
            "use_llm": False,
        }
        # Patch the domain agent's query method to raise at orchestrator level
        with patch(
            "agents.star.orchestrator.AgenteCOVID"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.query.side_effect = Exception("Agent crashed")
            orch.run("analysis-6", params, ws_queue)

        # Collect error events
        errors = []
        while not ws_queue.empty():
            event = ws_queue.get()
            if event.get("type") == "error":
                errors.append(event)
        assert len(errors) >= 1


class TestSelfCheckWiring:
    """Etapa 4 — verificação pós-síntese: gating por flag e por use_llm."""

    def test_disabled_by_default_no_call(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019, "date_to": 2021,
            "health_params": ["covid"], "use_llm": False,
        }
        with patch("agents.star.orchestrator.claim_verifier.self_check") as mock_self_check:
            result = orchestrator.run("analysis-sc-1", params, ws_queue)
        mock_self_check.assert_not_called()
        assert result.get("self_check") is None

    def test_skipped_when_use_llm_false_even_if_flag_on(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019, "date_to": 2021,
            "health_params": ["covid"], "use_llm": False, "use_self_check": True,
        }
        with patch("agents.star.orchestrator.claim_verifier.self_check") as mock_self_check:
            orchestrator.run("analysis-sc-2", params, ws_queue)
        mock_self_check.assert_not_called()

    def test_runs_and_revises_text_when_enabled(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019, "date_to": 2021,
            "health_params": ["covid"], "use_llm": True, "use_self_check": True,
        }
        with patch("agents.star.orchestrator.TextSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.generate_stream.return_value = iter(["texto ", "gerado"])
            with patch("agents.star.orchestrator.claim_verifier.self_check") as mock_self_check:
                mock_self_check.return_value = {
                    "texto_final": "texto corrigido",
                    "claims": [{"claim": "x", "suportado": False, "justificativa": "y"}],
                    "revisado": True,
                    "verificado": True,
                }
                result = orchestrator.run("analysis-sc-3", params, ws_queue)

        mock_self_check.assert_called_once()
        assert result["texto_analise"] == "texto corrigido"
        assert result["self_check"]["revisado"] is True

    def test_no_correction_keeps_synthesized_text(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019, "date_to": 2021,
            "health_params": ["covid"], "use_llm": True, "use_self_check": True,
        }
        with patch("agents.star.orchestrator.TextSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.generate_stream.return_value = iter(["texto original"])
            with patch("agents.star.orchestrator.claim_verifier.self_check") as mock_self_check:
                mock_self_check.return_value = {
                    "texto_final": "texto original", "claims": [], "revisado": False, "verificado": True,
                }
                result = orchestrator.run("analysis-sc-4", params, ws_queue)

        assert result["texto_analise"] == "texto original"
        assert result["self_check"]["revisado"] is False

    def test_verificacao_excluded_from_agent_metrics(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019, "date_to": 2021,
            "health_params": ["covid"], "use_llm": True, "use_self_check": True,
        }
        with patch("agents.star.orchestrator.TextSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.generate_stream.return_value = iter(["texto"])
            with patch("agents.star.orchestrator.claim_verifier.self_check") as mock_self_check:
                mock_self_check.return_value = {
                    "texto_final": "texto", "claims": [], "revisado": False, "verificado": True,
                }
                orchestrator.run("analysis-sc-5", params, ws_queue)

        metric_events = []
        while not ws_queue.empty():
            event = ws_queue.get()
            if event.get("type") == "metric":
                metric_events.append(event)
        assert metric_events
        agent_names = {m["agentName"] for m in metric_events[0]["payload"]["agentMetrics"]}
        assert "verificacao" not in agent_names

    def test_self_check_failure_does_not_break_pipeline(self, orchestrator, neo4j_client, ws_queue):
        params = {
            "date_from": 2019, "date_to": 2021,
            "health_params": ["covid"], "use_llm": True, "use_self_check": True,
        }
        with patch("agents.star.orchestrator.TextSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.generate_stream.return_value = iter(["texto original"])
            with patch(
                "agents.star.orchestrator.claim_verifier.self_check",
                side_effect=Exception("LLM indisponível"),
            ):
                result = orchestrator.run("analysis-sc-6", params, ws_queue)

        assert result["texto_analise"] == "texto original"
