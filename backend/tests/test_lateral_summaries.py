"""Testes da comunicação lateral semântica (Etapa 5 do PLANO_REFATORACAO.md).

Cobre SupervisorSaude/SupervisorOrcamento/SupervisorContexto (geração do
resumo, com fallback determinístico), SupervisorAnalitico (recebimento via
peer_data e uso na priorização) e CoordenadorGeral (repasse dos resumos nas
comunicações laterais que chegam a SupervisorAnalitico) — atualizado na
Fase 1 (PLANO_NOVO_MODELO_DADOS.md §5) para o split
SupervisorOrcamento/SupervisorSaude no lugar do antigo SupervisorDominio.
"""

from __future__ import annotations

from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agents.hierarchical.coordinator import CoordenadorGeral
from agents.hierarchical.supervisors import (
    SupervisorAnalitico,
    SupervisorContexto,
    SupervisorOrcamento,
    SupervisorSaude,
)


def _neo4j_client(despesas=None, indicadores=None):
    client = MagicMock()
    client.get_despesas_por_subfuncao.return_value = despesas or []
    client.get_indicadores_por_sistema.return_value = indicadores or []
    return client


class TestSupervisorSaudeResumo:
    def test_resumo_gerado_via_llm(self):
        """health_params=["mortalidade"] ativa AgenteSIM, que também
        delibera dimensão via LLM (arbitrar_dimensao) — com use_llm=True
        (default), core.llm_client.generate é chamado mais de uma vez.
        Isola a chamada de resumir_para_par pelo `caller` para não
        confundir as duas."""
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
            indicadores=[{"tipo": "mortalidade", "ano": 2020, "valor": 30.0}],
        )
        sup = SupervisorSaude("test-sup-saude", neo4j_client)

        def _generate(prompt, caller):
            if caller.endswith(":resumir_para_par"):
                return "Foram encontrados dados de mortalidade no período."
            return None  # deliberação de dimensão cai no score determinístico

        with patch("core.llm_client.generate", side_effect=_generate) as mock_generate:
            result = sup.run("analysis-1", 2019, 2021, health_params=["mortalidade"])

        resumo_calls = [
            c for c in mock_generate.call_args_list
            if c.kwargs["caller"].endswith(":resumir_para_par")
        ]
        assert len(resumo_calls) == 1
        assert resumo_calls[0].kwargs["caller"] == "test-sup-saude:resumir_para_par"
        assert result["resumo"] == "Foram encontrados dados de mortalidade no período."

    def test_fallback_determinístico_quando_llm_indisponivel(self):
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
            indicadores=[],
        )
        sup = SupervisorSaude("test-sup-saude-2", neo4j_client)
        with patch("core.llm_client.generate", return_value=None):
            result = sup.run("analysis-2", 2019, 2021, health_params=["mortalidade"])

        assert result["resumo"]
        assert "mortalidade" in result["resumo"]

    def test_fallback_quando_llm_lanca_excecao(self):
        neo4j_client = _neo4j_client()
        sup = SupervisorSaude("test-sup-saude-3", neo4j_client)
        with patch("core.llm_client.generate", side_effect=Exception("LLM indisponível")):
            result = sup.run("analysis-3", 2019, 2021, health_params=["mortalidade"])

        # Nunca deve propagar exceção — pipeline continua com resumo de fallback
        assert result["resumo"]
        assert result["despesas"] == []
        assert result["indicadores"] == []

    def test_resumo_nunca_vazio_mesmo_sem_dados(self):
        neo4j_client = _neo4j_client()
        sup = SupervisorSaude("test-sup-saude-4", neo4j_client)
        with patch("core.llm_client.generate", return_value=""):
            result = sup.run("analysis-4", 2019, 2021, health_params=["mortalidade"])

        assert result["resumo"]

    def test_use_llm_false_pula_direto_para_fallback_sem_chamar_llm(self):
        """Regressão: achado ao inspecionar log de execução real — antes
        desta correção, resumir_para_par ignorava a flag use_llm=False da
        análise inteira e gastava tokens de qualquer forma."""
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
            indicadores=[],
        )
        sup = SupervisorSaude("test-sup-saude-5", neo4j_client)
        with patch("core.llm_client.generate") as mock_generate:
            result = sup.run("analysis-5", 2019, 2021, health_params=["mortalidade"], use_llm=False)

        mock_generate.assert_not_called()
        assert result["resumo"]


class TestSupervisorOrcamentoResumo:
    def test_resumo_gerado_via_llm(self):
        """health_params=["dengue"] ativa subfunções cujo
        AgenteOrcamentoSubfuncao também delibera dimensão via LLM
        (arbitrar_dimensao) — com use_llm=True (default),
        core.llm_client.generate é chamado mais de uma vez. Isola a
        chamada de resumir_para_par pelo `caller`, mesmo padrão de
        TestSupervisorSaudeResumo.test_resumo_gerado_via_llm."""
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
        )
        sup = SupervisorOrcamento("test-sup-orcamento", neo4j_client)

        def _generate(prompt, caller):
            if caller.endswith(":resumir_para_par"):
                return "Foram encontradas despesas de vigilância no período."
            return None  # deliberação de dimensão cai no score determinístico

        with patch("core.llm_client.generate", side_effect=_generate) as mock_generate:
            result = sup.run("analysis-1", 2019, 2021, health_params=["dengue"])

        resumo_calls = [
            c for c in mock_generate.call_args_list
            if c.kwargs["caller"].endswith(":resumir_para_par")
        ]
        assert len(resumo_calls) == 1
        assert resumo_calls[0].kwargs["caller"] == "test-sup-orcamento:resumir_para_par"
        assert result["resumo"] == "Foram encontradas despesas de vigilância no período."

    def test_fallback_quando_llm_lanca_excecao(self):
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
        )
        sup = SupervisorOrcamento("test-sup-orcamento-2", neo4j_client)
        with patch("core.llm_client.generate", side_effect=Exception("LLM indisponível")):
            result = sup.run("analysis-2", 2019, 2021, health_params=["dengue"])

        assert result["resumo"]

    def test_use_llm_false_pula_direto_para_fallback_sem_chamar_llm(self):
        neo4j_client = _neo4j_client(
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
        )
        sup = SupervisorOrcamento("test-sup-orcamento-3", neo4j_client)
        with patch("core.llm_client.generate") as mock_generate:
            result = sup.run("analysis-3", 2019, 2021, health_params=["dengue"], use_llm=False)

        mock_generate.assert_not_called()
        assert result["resumo"]

    def test_health_params_fora_do_dominio_305_nao_ativa_nada(self):
        """health_params sem nenhum token do domínio SINAN/covid não deve
        ativar a subfunção 305 nem propor resumir_para_par."""
        neo4j_client = _neo4j_client()
        sup = SupervisorOrcamento("test-sup-orcamento-4", neo4j_client)
        result = sup.run("analysis-4", 2019, 2021, health_params=["vacinacao"])
        assert result["despesas"] == []


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

    def test_use_llm_false_pula_direto_para_fallback_sem_chamar_llm(self):
        sup = SupervisorContexto("test-sup-contexto-3b")
        sup.receive_from_peer({"despesas": [{"subfuncao": 301, "ano": 2020, "valor": 1.0}]})
        trends = {"301": {"tendencia": "crescimento", "variacao_media_percentual": 5.0}}
        with patch(
            "agents.context.contexto_orcamentario.AgenteContextoOrcamentario.analyze_trends",
            return_value=trends,
        ), patch("core.llm_client.generate") as mock_generate:
            result = sup.run(use_llm=False)

        mock_generate.assert_not_called()
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
    def test_comunicar_saude_analitico_repassa_resumo(self):
        coord = CoordenadorGeral("test-coord", MagicMock())
        sup_analitico = MagicMock()
        coord.working_memory.update({
            "_sup_analitico": sup_analitico,
            "_saude_data": {"despesas": [], "indicadores": [], "resumo": "resumo da saude"},
            "_orcamento_data": {"despesas": [], "resumo": "resumo do orcamento"},
            "_despesas_combinadas": [],
            "date_from": 2019, "date_to": 2021, "health_params": ["mortalidade"],
            "intent_summary": "x",
        })
        coord._act_comunicar_saude_analitico({"goal": "comunicar_saude_analitico"})

        sup_analitico.receive_from_peer.assert_called_once()
        payload = sup_analitico.receive_from_peer.call_args[0][0]
        assert "resumo da saude" in payload["resumo_dominio"]
        assert "resumo do orcamento" in payload["resumo_dominio"]

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
        """Degradação graciosa: se _saude_data/_orcamento_data não têm
        'resumo' (ex.: falha upstream), o repasse não quebra — só manda
        string vazia."""
        coord = CoordenadorGeral("test-coord-3", MagicMock())
        sup_analitico = MagicMock()
        coord.working_memory.update({
            "_sup_analitico": sup_analitico,
            "_saude_data": {"despesas": [], "indicadores": []},
            "_orcamento_data": {"despesas": []},
            "_despesas_combinadas": [],
            "date_from": 2019, "date_to": 2021, "health_params": [],
            "intent_summary": None,
        })
        coord._act_comunicar_saude_analitico({"goal": "comunicar_saude_analitico"})

        payload = sup_analitico.receive_from_peer.call_args[0][0]
        assert payload["resumo_dominio"] == ""

    def test_delegar_saude_repassa_use_llm(self):
        """Regressão: use_llm da análise inteira precisa chegar em
        SupervisorSaude.run() para que resumir_para_par (Etapa 5)
        respeite use_llm=False."""
        coord = CoordenadorGeral("test-coord-4", MagicMock())
        sup_saude = MagicMock()
        sup_saude.run.return_value = {"despesas": [], "indicadores": [], "resumo": ""}
        coord.working_memory.update({
            "_sup_saude": sup_saude,
            "_metrics_collectors": [],
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "health_params": [], "intent_summary": None, "use_llm": False,
        })
        coord._act_delegar_saude({"goal": "delegar_saude"})

        sup_saude.run.assert_called_once()
        assert sup_saude.run.call_args.kwargs["use_llm"] is False

    def test_delegar_orcamento_repassa_use_llm(self):
        coord = CoordenadorGeral("test-coord-4b", MagicMock())
        sup_orcamento = MagicMock()
        sup_orcamento.run.return_value = {"despesas": [], "tendencias": {}, "resumo": ""}
        coord.working_memory.update({
            "_sup_orcamento": sup_orcamento,
            "_metrics_collectors": [],
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "health_params": [], "use_llm": False,
        })
        coord._act_delegar_orcamento({"goal": "delegar_orcamento"})

        sup_orcamento.run.assert_called_once()
        assert sup_orcamento.run.call_args.kwargs["use_llm"] is False

    def test_delegar_orcamento_repassa_intent_summary(self):
        """Regressão (Fase 3): intent_summary da análise inteira precisa
        chegar em SupervisorOrcamento.run() para que a deliberação de
        dimensão (POR_NATUREZA/POR_APLICACAO) de cada
        AgenteOrcamentoSubfuncao subordinado tenha insumo real — sem
        isso, a deliberação nunca escolhe uma dimensão diferente de
        "sem quebra" em produção."""
        coord = CoordenadorGeral("test-coord-4c", MagicMock())
        sup_orcamento = MagicMock()
        sup_orcamento.run.return_value = {"despesas": [], "tendencias": {}, "resumo": ""}
        coord.working_memory.update({
            "_sup_orcamento": sup_orcamento,
            "_metrics_collectors": [],
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "health_params": [], "intent_summary": "quero saber por natureza", "use_llm": True,
        })
        coord._act_delegar_orcamento({"goal": "delegar_orcamento"})

        sup_orcamento.run.assert_called_once()
        assert sup_orcamento.run.call_args.kwargs["intent_summary"] == "quero saber por natureza"

    def test_delegar_contexto_repassa_use_llm(self):
        coord = CoordenadorGeral("test-coord-5", MagicMock())
        sup_contexto = MagicMock()
        sup_contexto.run.return_value = {"contexto_orcamentario": {}, "resumo": ""}
        coord.working_memory.update({
            "_sup_contexto": sup_contexto,
            "_metrics_collectors": [],
            "use_llm": False,
        })
        coord._act_delegar_contexto({"goal": "delegar_contexto"})

        sup_contexto.run.assert_called_once_with(use_llm=False)


class TestCoordenadorGeralAgentDataEvent:
    """Etapa nova — evento `agent_data` combinando as queries capturadas
    por SupervisorOrcamento e SupervisorSaude (mesma exposição que a
    topologia estrela, via _act_persistir_metricas)."""

    def test_persistir_metricas_emite_agent_data_combinando_supervisores(self):
        ws_queue = Queue()
        coord = CoordenadorGeral("test-coord-agent-data", MagicMock())
        coord.working_memory.update({
            "analysis_id": "a1",
            "_ws_queue": ws_queue,
            "_metrics_collectors": [],
            "_sup_orcamento": MagicMock(_collectors=[]),
            "_sup_saude": MagicMock(_collectors=[]),
            "_sup_analitico": MagicMock(_collectors=[]),
            "_sup_contexto": MagicMock(_collectors=[]),
            "_orcamento_data": {
                "agent_queries": [{
                    "agentName": "orcamento_subfuncao_301",
                    "agentLabel": "AB (301)",
                    "queries": [{
                        "query": "MATCH (d:DespesaAnual) RETURN d",
                        "params": {},
                        "rowCount": 1,
                        "rows": [{"ano": 2020, "valor": 100.0}],
                    }],
                }],
            },
            "_saude_data": {
                "agent_queries": [{
                    "agentName": "sinan",
                    "agentLabel": "SINAN",
                    "queries": [{
                        "query": "MATCH (i:IndicadorSaude) RETURN i",
                        "params": {},
                        "rowCount": 1,
                        "rows": [{"ano": 2020, "valor": 30.0}],
                    }],
                }],
            },
        })
        coord._act_persistir_metricas({"goal": "persistir_metricas"})

        events = []
        while not ws_queue.empty():
            events.append(ws_queue.get_nowait())

        agent_data_events = [e for e in events if e["type"] == "agent_data"]
        assert len(agent_data_events) == 1
        payload = agent_data_events[0]["payload"]
        assert payload["architecture"] == "hierarchical"
        names = {a["agentName"] for a in payload["agents"]}
        assert names == {"orcamento_subfuncao_301", "sinan"}


class TestPeriodoNoSintetizadorHierarquico:
    """date_from/date_to de peer_data chegam ao TextSynthesizer em
    SupervisorAnalitico — mesma garantia da topologia estrela (ver
    test_orchestrator_star.py::TestPeriodoNoSintetizador)."""

    def _sup_pronto(self, use_llm: bool) -> SupervisorAnalitico:
        sup = SupervisorAnalitico("test-sup-periodo")
        sup.receive_from_peer({
            "despesas": [], "indicadores": [],
            "date_from": 2020, "date_to": 2025,
        })
        sup.working_memory.update({
            "_ws_queue": Queue(),
            "analysis_id": "a1",
            "use_llm": use_llm,
            "correlacoes": [],
            "anomalias": [],
        })
        return sup

    def test_generate_stream_receives_date_from_and_date_to(self):
        sup = self._sup_pronto(use_llm=True)
        with patch("agents.hierarchical.supervisors.TextSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.generate_stream.return_value = iter(["texto"])
            sup._act_sintetizar_texto({"goal": "sintetizar_texto"})

        instance.generate_stream.assert_called_once()
        assert instance.generate_stream.call_args.kwargs["date_from"] == 2020
        assert instance.generate_stream.call_args.kwargs["date_to"] == 2025

    def test_generate_fallback_receives_date_from_and_date_to(self):
        sup = self._sup_pronto(use_llm=False)
        with patch("agents.hierarchical.supervisors.TextSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.generate_fallback.return_value = "texto fallback"
            sup._act_sintetizar_texto({"goal": "sintetizar_texto"})

        instance.generate_fallback.assert_called_once()
        call_args = instance.generate_fallback.call_args.args
        assert call_args[-2:] == (2020, 2025)
