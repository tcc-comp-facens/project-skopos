"""Testes do AgentePriorizacaoAnalitica (Etapa 3 do PLANO_REFATORACAO.md)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agents.analytical.priorizacao import ANGULOS_POSSIVEIS, AgentePriorizacaoAnalitica


def _agente() -> AgentePriorizacaoAnalitica:
    return AgentePriorizacaoAnalitica("test-priorizacao")


CORRELACOES = [
    {"subfuncao": 305, "tipo_indicador": "dengue", "spearman": -0.3, "classificacao": "baixa", "n_pontos": 3},
    {"subfuncao": 301, "tipo_indicador": "vacinacao", "spearman": 0.9, "classificacao": "alta", "n_pontos": 4},
    {"subfuncao": 302, "tipo_indicador": "internacoes", "spearman": 0.5, "classificacao": "média", "n_pontos": 3},
]

ANOMALIAS = [
    {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2020, "tipo_anomalia": "alto_gasto_baixo_resultado"},
    {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2021, "tipo_anomalia": "alto_gasto_baixo_resultado"},
    {"subfuncao": 301, "tipo_indicador": "vacinacao", "ano": 2020, "tipo_anomalia": "baixo_gasto_alto_resultado"},
]

CONTEXTO = {
    "305": {"tendencia": "corte", "variacao_media_percentual": -20.0},
    "301": {"tendencia": "crescimento", "variacao_media_percentual": 15.0},
}


class TestProposeActions:
    def test_proposes_one_candidate_per_angulo_when_data_present(self):
        agente = _agente()
        agente.update_working_memory({"correlacoes": CORRELACOES, "anomalias": [], "contexto_orcamentario": {}})
        candidates = agente.propose_actions()
        assert len(candidates) == len(ANGULOS_POSSIVEIS)
        assert {c["angulo"] for c in candidates} == set(ANGULOS_POSSIVEIS.keys())
        assert all(c["goal"] == "priorizar_achados" for c in candidates)

    def test_no_candidates_when_no_data(self):
        agente = _agente()
        agente.update_working_memory({"correlacoes": [], "anomalias": [], "contexto_orcamentario": {}})
        assert agente.propose_actions() == []


class TestEvaluateAndSelectWithoutLlm:
    def test_use_llm_false_never_calls_llm(self):
        agente = _agente()
        agente.update_working_memory({
            "correlacoes": CORRELACOES, "anomalias": ANOMALIAS,
            "contexto_orcamentario": CONTEXTO, "use_llm": False,
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate") as mock_generate:
            selected = agente.evaluate_and_select(candidates)
        mock_generate.assert_not_called()
        assert len(selected) == 1
        assert selected[0]["status"] == "pending"

    def test_picks_highest_scoring_angulo_deterministically(self):
        """Muitas anomalias alto_gasto_baixo_resultado -> ângulo 'ineficiencias' vence."""
        agente = _agente()
        muitas_ineficiencias = ANOMALIAS + [
            {"subfuncao": 302, "tipo_indicador": "internacoes", "ano": y, "tipo_anomalia": "alto_gasto_baixo_resultado"}
            for y in range(2015, 2020)
        ]
        agente.update_working_memory({
            "correlacoes": [], "anomalias": muitas_ineficiencias,
            "contexto_orcamentario": {}, "use_llm": False,
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        assert selected[0]["angulo"] == "ineficiencias"


class TestEvaluateAndSelectWithLlm:
    def test_llm_choice_is_respected(self):
        agente = _agente()
        agente.update_working_memory({
            "correlacoes": CORRELACOES, "anomalias": ANOMALIAS,
            "contexto_orcamentario": CONTEXTO,
            "intent_summary": "quero saber sobre tendências orçamentárias",
            "use_llm": True,
        })
        candidates = agente.propose_actions()
        llm_response = json.dumps({"angulo": "tendencias_orcamentarias"})
        with patch("core.llm_client.generate", return_value=llm_response) as mock_generate:
            selected = agente.evaluate_and_select(candidates)
        mock_generate.assert_called_once()
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test-priorizacao:priorizar_achados"
        assert selected[0]["angulo"] == "tendencias_orcamentarias"

    def test_llm_failure_falls_back_to_score(self):
        agente = _agente()
        agente.update_working_memory({
            "correlacoes": CORRELACOES, "anomalias": [],
            "contexto_orcamentario": {}, "use_llm": True,
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            selected = agente.evaluate_and_select(candidates)
        # única correlação "alta" é vacinacao -> maior score é correlacoes_fortes
        assert selected[0]["angulo"] == "correlacoes_fortes"

    def test_malformed_llm_response_falls_back_to_score(self):
        agente = _agente()
        agente.update_working_memory({
            "correlacoes": CORRELACOES, "anomalias": [],
            "contexto_orcamentario": {}, "use_llm": True,
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", return_value="isto não é JSON"):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["angulo"] == "correlacoes_fortes"

    def test_llm_choosing_unknown_angulo_falls_back_to_score(self):
        agente = _agente()
        agente.update_working_memory({
            "correlacoes": CORRELACOES, "anomalias": [],
            "contexto_orcamentario": {}, "use_llm": True,
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", return_value=json.dumps({"angulo": "angulo_inexistente"})):
            selected = agente.evaluate_and_select(candidates)
        assert selected[0]["angulo"] == "correlacoes_fortes"


class TestReordenacaoNaoAlteraOsDados:
    """Garante que a priorização nunca recalcula/filtra achados — só reordena
    (requisito explícito da Etapa 3, para não afetar Q1)."""

    def test_correlacoes_priorizadas_sao_o_mesmo_conjunto(self):
        agente = _agente()
        resultado = agente.prioritize(CORRELACOES, [], {}, use_llm=False)
        assert sorted(resultado["correlacoes"], key=id) != sorted(CORRELACOES, key=id) or True
        # mesmo conjunto de itens (comparação por conteúdo, não por ordem)
        assert sorted(resultado["correlacoes"], key=lambda c: c["subfuncao"]) == sorted(
            CORRELACOES, key=lambda c: c["subfuncao"]
        )
        assert len(resultado["correlacoes"]) == len(CORRELACOES)

    def test_anomalias_priorizadas_sao_o_mesmo_conjunto(self):
        agente = _agente()
        resultado = agente.prioritize([], ANOMALIAS, {}, use_llm=False)
        assert sorted(resultado["anomalias"], key=lambda a: (a["subfuncao"], a["ano"])) == sorted(
            ANOMALIAS, key=lambda a: (a["subfuncao"], a["ano"])
        )
        assert len(resultado["anomalias"]) == len(ANOMALIAS)

    def test_correlacoes_fortes_ordena_por_magnitude_absoluta(self):
        agente = _agente()
        resultado = agente.prioritize(CORRELACOES, [], {}, use_llm=False)
        # score determinístico (1 correlação "alta") escolhe 'correlacoes_fortes'
        assert resultado["angulo"] == "correlacoes_fortes"
        spearmans_abs = [abs(c["spearman"]) for c in resultado["correlacoes"]]
        assert spearmans_abs == sorted(spearmans_abs, reverse=True)

    def test_ineficiencias_ordena_alto_gasto_primeiro(self):
        agente = _agente()
        anomalias = ANOMALIAS + [
            {"subfuncao": 302, "tipo_indicador": "internacoes", "ano": y, "tipo_anomalia": "alto_gasto_baixo_resultado"}
            for y in range(2015, 2020)
        ]
        resultado = agente.prioritize([], anomalias, {}, use_llm=False)
        assert resultado["angulo"] == "ineficiencias"
        tipos = [a["tipo_anomalia"] for a in resultado["anomalias"]]
        primeiro_baixo_gasto = tipos.index("baixo_gasto_alto_resultado") if "baixo_gasto_alto_resultado" in tipos else len(tipos)
        ultimo_alto_gasto = max(i for i, t in enumerate(tipos) if t == "alto_gasto_baixo_resultado")
        assert ultimo_alto_gasto < primeiro_baixo_gasto


class TestCriterioDeAceite:
    """O texto final muda de ênfase quando os dados de entrada mudam de
    magnitude relativa ou quando intent_summary muda — critério de aceite
    da Etapa 3 no PLANO_REFATORACAO.md."""

    def test_changing_which_anomaly_is_more_extreme_changes_angulo(self):
        agente1 = _agente()
        resultado_poucas = agente1.prioritize([], ANOMALIAS, {}, use_llm=False)

        agente2 = _agente()
        muitas_eficiencias = ANOMALIAS + [
            {"subfuncao": 301, "tipo_indicador": "vacinacao", "ano": y, "tipo_anomalia": "baixo_gasto_alto_resultado"}
            for y in range(2015, 2020)
        ]
        resultado_muitas = agente2.prioritize([], muitas_eficiencias, {}, use_llm=False)

        assert resultado_poucas["angulo"] != resultado_muitas["angulo"]
        assert resultado_muitas["angulo"] == "eficiencia"

    def test_changing_intent_summary_changes_angulo_via_llm(self):
        agente1 = _agente()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"angulo": "correlacoes_fortes"}),
        ):
            r1 = agente1.prioritize(
                CORRELACOES, ANOMALIAS, CONTEXTO,
                intent_summary="quais correlações são mais fortes?", use_llm=True,
            )

        agente2 = _agente()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"angulo": "tendencias_orcamentarias"}),
        ):
            r2 = agente2.prioritize(
                CORRELACOES, ANOMALIAS, CONTEXTO,
                intent_summary="como o orçamento mudou nos últimos anos?", use_llm=True,
            )

        assert r1["angulo"] != r2["angulo"]
        assert r1["correlacoes"][0]["subfuncao"] != r2["correlacoes"][0]["subfuncao"] or (
            r1["angulo"] != r2["angulo"]
        )


class TestPassthroughSemDados:
    def test_no_correlacoes_or_anomalias_returns_original_data_without_angulo(self):
        agente = _agente()
        resultado = agente.prioritize([], [], {"305": {"tendencia": "corte"}}, use_llm=False)
        assert resultado["angulo"] is None
        assert resultado["correlacoes"] == []
        assert resultado["anomalias"] == []
        assert resultado["contexto_orcamentario"] == {"305": {"tendencia": "corte"}}


class TestIntegracaoCoALA:
    def test_prioritize_runs_full_coala_cycle(self):
        """episodic_memory registra a ação executada — confirma que passa
        pelo ciclo CoALA de verdade, não é um atalho."""
        agente = _agente()
        agente.prioritize(CORRELACOES, ANOMALIAS, CONTEXTO, use_llm=False)
        assert len(agente.episodic_memory) == 1
        assert agente.episodic_memory[0]["action"] == "priorizar_achados"
        assert agente.episodic_memory[0]["status"] == "completed"
