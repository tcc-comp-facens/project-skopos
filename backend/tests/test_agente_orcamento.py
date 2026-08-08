"""Tests for AgenteOrcamentoSubfuncao — agente de referência de orçamento
(PLANO_NOVO_MODELO_DADOS.md §5).

Classe parametrizada por subfunção — testada aqui instanciada para 305
(Vigilância Epidemiológica), o par usado com AgenteSINAN.

Fase 3: cobre também a deliberação real de dimensão (POR_NATUREZA/
POR_APLICACAO/"sem quebra"), mesmo padrão de dois estágios de
AgenteSINAN, mas particionando candidatos por goal já que
`consultar_despesas` (com deliberação) e `analisar_tendencia` (goal
único) convivem na mesma lista de candidatos.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.domain.agente_orcamento import AgenteOrcamentoSubfuncao


@pytest.fixture
def neo4j_client():
    return MagicMock()


@pytest.fixture
def agente(neo4j_client):
    return AgenteOrcamentoSubfuncao("test-orc-305", neo4j_client, 305, "Vigilância Epidemiológica")


class TestSemanticMemory:
    def test_stores_subfuncao_codigo_e_nome(self, agente):
        assert agente.semantic_memory["subfuncao_codigo"] == 305
        assert agente.semantic_memory["subfuncao_nome"] == "Vigilância Epidemiológica"

    def test_parametrizavel_para_outra_subfuncao(self, neo4j_client):
        outro = AgenteOrcamentoSubfuncao("test-orc-301", neo4j_client, 301, "Atenção Básica")
        assert outro.semantic_memory["subfuncao_codigo"] == 301

    def test_dimensoes_validas_from_query_builder(self, agente):
        assert agente.semantic_memory["dimensoes_validas"] == ["POR_NATUREZA", "POR_APLICACAO"]


class TestProposeActionsGeraCandidatosReais:
    def test_proposes_one_candidate_per_dimension_plus_no_break_plus_tendencia(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
        })
        candidates = agente.propose_actions()
        despesa_candidatos = [c for c in candidates if c["goal"] == "consultar_despesas"]
        dimensoes = {c["dimensao"] for c in despesa_candidatos}
        assert dimensoes == {None, "POR_NATUREZA", "POR_APLICACAO"}

        tendencia_candidatos = [c for c in candidates if c["goal"] == "analisar_tendencia"]
        assert len(tendencia_candidatos) == 1

    def test_no_candidates_without_analysis_params(self, agente):
        assert agente.propose_actions() == []


class TestEvaluateAndSelectSemLlm:
    def test_use_llm_false_picks_highest_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": "quero saber por natureza",
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        despesa_selecionada = [c for c in selected if c["goal"] == "consultar_despesas"]
        assert len(despesa_selecionada) == 1
        assert despesa_selecionada[0]["dimensao"] == "POR_NATUREZA"

    def test_use_llm_false_no_mention_falls_back_to_no_break(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": None,
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        despesa_selecionada = [c for c in selected if c["goal"] == "consultar_despesas"][0]
        assert despesa_selecionada["dimensao"] is None

    def test_analisar_tendencia_survives_alongside_chosen_despesa_candidate(self, agente):
        """Diferente de AgenteSINAN (um goal só): aqui a arbitragem só
        toca consultar_despesas — analisar_tendencia/consultar_variacao_anual
        sempre sobrevivem (ambas goals únicos, sem candidatos concorrentes)."""
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": False, "intent_summary": "por aplicação",
        })
        candidates = agente.propose_actions()
        selected = agente.evaluate_and_select(candidates)
        goals = {c["goal"] for c in selected}
        assert goals == {"consultar_despesas", "analisar_tendencia", "consultar_variacao_anual"}
        assert len(selected) == 3

    def test_empty_candidates_returns_empty(self, agente):
        assert agente.evaluate_and_select([]) == []


class TestEvaluateAndSelectComLlm:
    def test_llm_choice_is_respected(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": "quero por aplicação",
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_APLICACAO"}),
        ) as mock_generate:
            selected = agente.evaluate_and_select(candidates)
        mock_generate.assert_called_once()
        despesa_selecionada = [c for c in selected if c["goal"] == "consultar_despesas"][0]
        assert despesa_selecionada["dimensao"] == "POR_APLICACAO"

    def test_llm_failure_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": "por natureza",
        })
        candidates = agente.propose_actions()
        with patch("core.llm_client.generate", side_effect=Exception("LLM down")):
            selected = agente.evaluate_and_select(candidates)
        despesa_selecionada = [c for c in selected if c["goal"] == "consultar_despesas"][0]
        assert despesa_selecionada["dimensao"] == "POR_NATUREZA"

    def test_llm_choosing_unknown_dimensao_falls_back_to_score(self, agente):
        agente.update_working_memory({
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "use_llm": True, "intent_summary": None,
        })
        candidates = agente.propose_actions()
        with patch(
            "core.llm_client.generate",
            return_value=json.dumps({"dimensao": "POR_FAIXA_ETARIA"}),
        ):
            selected = agente.evaluate_and_select(candidates)
        despesa_selecionada = [c for c in selected if c["goal"] == "consultar_despesas"][0]
        assert despesa_selecionada["dimensao"] is None


class TestConsultarDespesas:
    def test_query_calls_neo4j_with_subfuncao_codigo(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.return_value = [
            {"subfuncao": 305, "subfuncaoNome": "Vigilância Epidemiológica", "ano": 2020, "valor": 100.0},
        ]
        agente.query("a1", 2019, 2021, use_llm=False)
        neo4j_client.get_despesas_por_subfuncao.assert_called_once_with([305], 2019, 2021, None)

    def test_query_calls_neo4j_with_chosen_dimensao(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.return_value = [
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2020, "valor": 50.0,
             "dimensao_valor": "3.3.90.30.00-Material de Consumo"},
        ]
        agente.query("a1", 2019, 2021, use_llm=False, intent_summary="quero saber por natureza")
        neo4j_client.get_despesas_por_subfuncao.assert_called_once_with(
            [305], 2019, 2021, "POR_NATUREZA"
        )

    def test_returns_despesas_and_tendencia(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.return_value = [
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2019, "valor": 100.0},
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2020, "valor": 200.0},
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2021, "valor": 300.0},
        ]
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert len(result["despesas"]) == 3
        assert result["tendencia"]["tendencia"] == "crescimento"

    def test_graceful_degradation_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.side_effect = Exception("Connection refused")
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert result["despesas"] == []
        assert result["tendencia"] == {}

    def test_empty_despesas_returns_empty_tendencia(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.return_value = []
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert result["despesas"] == []
        assert result["tendencia"] == {}


class TestConsultarVariacaoAnual:
    """Fase 3 (PLANO_NOVO_MODELO_DADOS.md §3.1): VARIACAO_ANUAL
    pré-computada no ETL, lida via neo4j_client.get_variacao_anual —
    sempre o mesmo reltype fixo, sem deliberação de dimensão (diferente
    de consultar_despesas)."""

    def test_query_calls_neo4j_get_variacao_anual(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.return_value = []
        neo4j_client.get_variacao_anual.return_value = [
            {"subfuncao": 305, "ano_atual": 2020, "ano_anterior": 2019,
             "percentual": 12.3, "classificacao": "crescimento"},
        ]
        result = agente.query("a1", 2019, 2021, use_llm=False)
        neo4j_client.get_variacao_anual.assert_called_once_with([305], 2019, 2021)
        assert result["variacao_anual"] == [
            {"subfuncao": 305, "ano_atual": 2020, "ano_anterior": 2019,
             "percentual": 12.3, "classificacao": "crescimento"},
        ]

    def test_variacao_anual_independent_of_chosen_despesa_dimensao(self, agente, neo4j_client):
        """Diferente de consultar_despesas, a dimensão escolhida (ou
        "sem quebra") não afeta a chamada a get_variacao_anual — é
        sempre o mesmo reltype fixo, sem parâmetro dimensao."""
        neo4j_client.get_despesas_por_subfuncao.return_value = []
        neo4j_client.get_variacao_anual.return_value = []
        agente.query("a1", 2019, 2021, use_llm=False, intent_summary="quero saber por natureza")
        neo4j_client.get_variacao_anual.assert_called_once_with([305], 2019, 2021)

    def test_graceful_degradation_on_neo4j_failure(self, agente, neo4j_client):
        neo4j_client.get_despesas_por_subfuncao.return_value = []
        neo4j_client.get_variacao_anual.side_effect = Exception("Connection refused")
        result = agente.query("a1", 2019, 2021, use_llm=False)
        assert result["variacao_anual"] == []
        # Falha em variacao_anual não deve impedir despesas/tendencia
        assert "despesas" in result
        assert "tendencia" in result


class TestTendenciaReaproveitaAgenteContextoOrcamentario:
    def test_tendencia_shape_matches_analyze_trends_output(self, agente, neo4j_client):
        """A classificação vem literalmente de
        AgenteContextoOrcamentario.analyze_trends (não duplicada) — o
        shape do resultado precisa bater 1:1."""
        neo4j_client.get_despesas_por_subfuncao.return_value = [
            {"subfuncao": 305, "subfuncaoNome": "VE", "ano": 2019, "valor": 100.0},
        ]
        result = agente.query("a1", 2019, 2021, use_llm=False)
        # Menos de 2 anos de dados -> insuficiente (Req 8.5 herdado)
        assert result["tendencia"]["tendencia"] == "insuficiente"
        assert "variacao_media_percentual" in result["tendencia"]
        assert "anos_analisados" in result["tendencia"]

    def test_tendencia_correct_even_with_dimensao_sliced_rows(self, agente, neo4j_client):
        """Fase 3: quando uma dimensão está ativa, várias linhas
        compartilham o mesmo ano (uma por fatia) — analyze_trends soma
        por ano internamente, então a tendência continua calculada sobre
        o total, não uma fatia isolada."""
        neo4j_client.get_despesas_por_subfuncao.return_value = [
            {"subfuncao": 305, "ano": 2019, "valor": 60.0, "dimensao_valor": "Material de Consumo"},
            {"subfuncao": 305, "ano": 2019, "valor": 40.0, "dimensao_valor": "Vencimentos"},
            {"subfuncao": 305, "ano": 2020, "valor": 150.0, "dimensao_valor": "Material de Consumo"},
            {"subfuncao": 305, "ano": 2020, "valor": 50.0, "dimensao_valor": "Vencimentos"},
        ]
        result = agente.query("a1", 2019, 2021, use_llm=False, intent_summary="por natureza")
        # 100 -> 200: crescimento de 100% (soma das fatias por ano, não fatia isolada)
        assert result["tendencia"]["tendencia"] == "crescimento"
