"""Testes da comunicação lateral semântica (Etapa 5 do PLANO_REFATORACAO.md).

Cobre SupervisorDominio/SupervisorContexto (geração do resumo, com fallback
determinístico), SupervisorAnalitico (recebimento via peer_data e uso na
priorização) e CoordenadorGeral (repasse dos resumos nas 2 comunicações
laterais que chegam a SupervisorAnalitico).
"""

from __future__ import annotations

from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agents.hierarchical.coordinator import CoordenadorGeral
from agents.hierarchical.supervisors import SupervisorAnalitico, SupervisorContexto, SupervisorDominio


def _neo4j_client(despesas=None, indicadores=None):
    client = MagicMock()
    client.get_despesas.return_value = despesas or []
    client.get_indicadores.return_value = indicadores or []
    return client


class TestSupervisorDominioResumo:
    def test_resumo_gerado_via_llm(self):
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "ano": 2020, "valor": 100.0}],
            indicadores=[{"tipo": "dengue", "ano": 2020, "valor": 30.0}],
        )
        sup = SupervisorDominio("test-sup-dominio", neo4j_client)
        with patch(
            "core.llm_client.generate", return_value="Foram encontrados dados de dengue no período."
        ) as mock_generate:
            result = sup.run("analysis-1", 2019, 2021, health_params=["dengue"])

        mock_generate.assert_called_once()
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test-sup-dominio:resumir_para_par"
        assert result["resumo"] == "Foram encontrados dados de dengue no período."

    def test_fallback_determinístico_quando_llm_indisponivel(self):
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "ano": 2020, "valor": 100.0}],
            indicadores=[],
        )
        sup = SupervisorDominio("test-sup-dominio-2", neo4j_client)
        with patch("core.llm_client.generate", return_value=None):
            result = sup.run("analysis-2", 2019, 2021, health_params=["dengue"])

        assert result["resumo"]
        assert "dengue" in result["resumo"]

    def test_fallback_quando_llm_lanca_excecao(self):
        neo4j_client = _neo4j_client()
        sup = SupervisorDominio("test-sup-dominio-3", neo4j_client)
        with patch("core.llm_client.generate", side_effect=Exception("LLM indisponível")):
            result = sup.run("analysis-3", 2019, 2021, health_params=["dengue"])

        # Nunca deve propagar exceção — pipeline continua com resumo de fallback
        assert result["resumo"]
        assert result["despesas"] == []
        assert result["indicadores"] == []

    def test_resumo_nunca_vazio_mesmo_sem_dados(self):
        neo4j_client = _neo4j_client()
        sup = SupervisorDominio("test-sup-dominio-4", neo4j_client)
        with patch("core.llm_client.generate", return_value=""):
            result = sup.run("analysis-4", 2019, 2021, health_params=["dengue"])

        assert result["resumo"]


class TestSupervisorContextoResumo:
    def test_resumo_gerado_via_llm(self):
        sup = SupervisorContexto("test-sup-contexto")
        sup.receive_from_peer({"despesas": [{"subfuncao": 305, "ano": 2020, "valor": 100.0}]})
        with patch(
            "core.llm_client.generate",
            return_value="O gasto em Vigilância Epidemiológica caiu nos últimos anos.",
        ) as mock_generate:
            result = sup.run()

        mock_generate.assert_called_once()
        _, kwargs = mock_generate.call_args
        assert kwargs["caller"] == "test-sup-contexto:resumir_para_par"
        assert result["resumo"] == "O gasto em Vigilância Epidemiológica caiu nos últimos anos."

    def test_fallback_determinístico_escolhe_maior_variacao(self):
        sup = SupervisorContexto("test-sup-contexto-2")
        sup.working_memory["contexto_orcamentario"] = {
            "301": {"tendencia": "crescimento", "variacao_media_percentual": 5.0},
            "305": {"tendencia": "corte", "variacao_media_percentual": -30.0},
        }
        sup._act_resumir_fallback({"goal": "resumir_para_par"})

        assert "305" in sup.working_memory["resumo"]
        assert "-30.0" in sup.working_memory["resumo"]

    def test_fallback_acionado_quando_llm_falha(self):
        sup = SupervisorContexto("test-sup-contexto-2b")
        sup.receive_from_peer({"despesas": [{"subfuncao": 305, "ano": 2020, "valor": 100.0}]})
        with patch("core.llm_client.generate", side_effect=Exception("LLM indisponível")):
            result = sup.run()

        assert result["resumo"]

    def test_no_despesas_no_resumir_para_par_proposto(self):
        sup = SupervisorContexto("test-sup-contexto-3")
        assert sup.propose_actions() == []

    def test_resumo_vazio_quando_sem_contexto_calculado(self):
        """Se despesas existem mas o contexto ficou vazio, resumo é string vazia (sem erro)."""
        sup = SupervisorContexto("test-sup-contexto-4")
        sup.receive_from_peer({"despesas": [{"subfuncao": 999, "ano": 2020, "valor": 1.0}]})
        with patch("agents.context.contexto_orcamentario.AgenteContextoOrcamentario.analyze_trends", return_value={}):
            result = sup.run()
        assert result["resumo"] == ""


class TestSupervisorAnaliticoRecebeResumos:
    def _sup_analitico_pronto(self) -> SupervisorAnalitico:
        sup = SupervisorAnalitico("test-sup-analitico")
        sup.receive_from_peer({
            "despesas": [{"subfuncao": 305, "ano": 2020, "valor": 100.0}],
            "indicadores": [{"tipo": "dengue", "ano": 2020, "valor": 30.0}],
            "intent_summary": "Comparar dengue e vacinação.",
            "resumo_dominio": "Foram encontrados dados de dengue.",
        })
        sup.receive_from_peer({
            "contexto_orcamentario": {"305": {"tendencia": "corte", "variacao_media_percentual": -10.0}},
            "resumo_contexto": "Gasto em Vigilância caiu 10%.",
        })
        return sup

    def test_peer_data_contem_ambos_resumos(self):
        sup = self._sup_analitico_pronto()
        assert sup.peer_data["resumo_dominio"] == "Foram encontrados dados de dengue."
        assert sup.peer_data["resumo_contexto"] == "Gasto em Vigilância caiu 10%."

    def test_compor_intent_summary_concatena_tudo(self):
        sup = self._sup_analitico_pronto()
        composto = sup._compor_intent_summary()
        assert "Comparar dengue e vacinação." in composto
        assert "Foram encontrados dados de dengue." in composto
        assert "Gasto em Vigilância caiu 10%." in composto

    def test_compor_intent_summary_ignora_partes_vazias(self):
        sup = SupervisorAnalitico("test-sup-analitico-2")
        sup.receive_from_peer({"intent_summary": "Só a intenção."})
        assert sup._compor_intent_summary() == "Só a intenção."

    def test_compor_intent_summary_none_quando_tudo_vazio(self):
        sup = SupervisorAnalitico("test-sup-analitico-3")
        assert sup._compor_intent_summary() is None

    def test_priorizar_achados_usa_intent_summary_enriquecido(self):
        sup = self._sup_analitico_pronto()
        sup.working_memory["correlacoes"] = [
            {"subfuncao": 305, "tipo_indicador": "dengue", "spearman": -0.3, "classificacao": "baixa"}
        ]
        sup.working_memory["anomalias"] = []
        sup.working_memory["use_llm"] = True

        with patch(
            "agents.analytical.priorizacao.AgentePriorizacaoAnalitica.prioritize"
        ) as mock_prioritize:
            mock_prioritize.return_value = {
                "angulo": "intencao_usuario", "descricao_angulo": "x",
                "correlacoes": [], "anomalias": [], "contexto_orcamentario": {},
            }
            sup._act_priorizar_achados({"goal": "priorizar_achados"})

        mock_prioritize.assert_called_once()
        args, kwargs = mock_prioritize.call_args
        intent_summary_passado = args[3] if len(args) > 3 else kwargs.get("intent_summary")
        assert "Foram encontrados dados de dengue." in intent_summary_passado
        assert "Gasto em Vigilância caiu 10%." in intent_summary_passado


class TestCoordenadorGeralRepasseDeResumos:
    def test_comunicar_dominio_analitico_repassa_resumo(self):
        coord = CoordenadorGeral("test-coord", MagicMock())
        sup_analitico = MagicMock()
        coord.working_memory.update({
            "_sup_analitico": sup_analitico,
            "_dominio_data": {"despesas": [], "indicadores": [], "resumo": "resumo do domínio"},
            "date_from": 2019, "date_to": 2021, "health_params": ["dengue"],
            "intent_summary": "x",
        })
        coord._act_comunicar_dominio_analitico({"goal": "comunicar_dominio_analitico"})

        sup_analitico.receive_from_peer.assert_called_once()
        payload = sup_analitico.receive_from_peer.call_args[0][0]
        assert payload["resumo_dominio"] == "resumo do domínio"

    def test_comunicar_contexto_analitico_repassa_resumo(self):
        coord = CoordenadorGeral("test-coord-2", MagicMock())
        sup_analitico = MagicMock()
        coord.working_memory.update({
            "_sup_analitico": sup_analitico,
            "_contexto_data": {"contexto_orcamentario": {}, "resumo": "resumo do contexto"},
        })
        coord._act_comunicar_contexto_analitico({"goal": "comunicar_contexto_analitico"})

        sup_analitico.receive_from_peer.assert_called_once()
        payload = sup_analitico.receive_from_peer.call_args[0][0]
        assert payload["resumo_contexto"] == "resumo do contexto"

    def test_repasse_funciona_com_resumo_ausente(self):
        """Degradação graciosa: se _dominio_data não tem 'resumo' (ex.: falha
        upstream), o repasse não quebra — só manda string vazia."""
        coord = CoordenadorGeral("test-coord-3", MagicMock())
        sup_analitico = MagicMock()
        coord.working_memory.update({
            "_sup_analitico": sup_analitico,
            "_dominio_data": {"despesas": [], "indicadores": []},
            "date_from": 2019, "date_to": 2021, "health_params": [],
            "intent_summary": None,
        })
        coord._act_comunicar_dominio_analitico({"goal": "comunicar_dominio_analitico"})

        payload = sup_analitico.receive_from_peer.call_args[0][0]
        assert payload["resumo_dominio"] == ""
