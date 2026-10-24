"""Testes da árvore estrita da hierárquica (substitui test_lateral_summaries.py).

A comunicação lateral entre supervisores (Etapa 5 do docs/historico/PLANO_REFATORACAO.md
— `receive_from_peer`/`peer_data`, com resumos textuais via LLM) foi
removida: o modelo de hierárquica deste projeto precisa ser uma árvore
estrita, sem par-a-par nem mediado pela raiz. Hoje `CoordenadorGeral`
colhe o retorno de cada supervisor e repassa o que for necessário como
parâmetro direto na chamada seguinte — cobrimos aqui exatamente essa
propagação, além dos regressions que sobreviveram à remoção (uso de
use_llm/intent_summary, evento agent_data, date_from/date_to no
sintetizador).
"""

from __future__ import annotations

from queue import Queue
from unittest.mock import MagicMock, patch

from agents.hierarchical.coordinator import CoordenadorGeral
from agents.hierarchical.supervisors import SupervisorAnalitico, SupervisorContexto


def _neo4j_client(despesas=None, indicadores=None):
    client = MagicMock()
    client.get_despesas_por_subfuncao.return_value = despesas or []
    client.get_indicadores_por_sistema.return_value = indicadores or []
    return client


class TestSupervisorContextoRecebeDespesasDireto:
    def test_run_aceita_despesas_como_parametro(self):
        """Sem receive_from_peer/peer_data: despesas chegam direto em run()."""
        sup = SupervisorContexto("test-sup-contexto")
        with patch(
            "agents.context.contexto_orcamentario.AgenteContextoOrcamentario.analyze_trends",
            return_value={"305": {"tendencia": "corte", "variacao_media_percentual": -10.0}},
        ):
            result = sup.run(despesas=[{"subfuncao": 305, "ano": 2020, "valor": 100.0}])

        assert result["contexto_orcamentario"]["305"]["tendencia"] == "corte"
        assert not hasattr(sup, "peer_data")

    def test_sem_despesas_nao_propoe_nada(self):
        sup = SupervisorContexto("test-sup-contexto-2")
        assert sup.propose_actions() == []
        result = sup.run(despesas=[])
        assert result["contexto_orcamentario"] == {}


class TestSupervisorAnaliticoRecebeDadosDireto:
    def _sup_pronto(self, use_llm: bool) -> SupervisorAnalitico:
        sup = SupervisorAnalitico("test-sup-analitico")
        sup.update_working_memory({
            "_ws_queue": Queue(),
            "analysis_id": "a1",
            "despesas": [],
            "indicadores": [],
            "contexto_orcamentario": {},
            "date_from": 2020,
            "date_to": 2025,
            "use_llm": use_llm,
            "correlacoes": [],
            "anomalias": [],
        })
        return sup

    def test_run_aceita_dados_como_parametros_diretos(self):
        """Sem receive_from_peer/peer_data: despesas, indicadores e
        contexto_orcamentario chegam direto em run()."""
        sup = SupervisorAnalitico("test-sup-analitico-2")
        assert not hasattr(sup, "peer_data")
        result = sup.run(
            analysis_id="a1",
            ws_queue=Queue(),
            despesas=[{"subfuncao": 305, "subfuncaoNome": "x", "ano": 2020, "valor": 100.0}],
            indicadores=[{"tipo": "dengue", "ano": 2020, "valor": 30.0}],
            contexto_orcamentario={"305": {"tendencia": "corte", "variacao_media_percentual": -10.0}},
            date_from=2019,
            date_to=2021,
            use_llm=False,
        )
        assert result["dados_cruzados"]

    def test_priorizar_achados_usa_intent_summary_direto(self):
        """intent_summary chega direto (sem mais concatenar resumos
        laterais — Etapa 5 removida)."""
        sup = self._sup_pronto(use_llm=True)
        sup.working_memory["intent_summary"] = "Comparar dengue e vacinação."
        sup.working_memory["correlacoes"] = [
            {"subfuncao": 305, "tipo_indicador": "dengue", "spearman": -0.3, "classificacao": "baixa"}
        ]

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
        assert intent_summary_passado == "Comparar dengue e vacinação."

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


class TestCoordenadorGeralRepasseDireto:
    def test_delegar_saude_repassa_use_llm(self):
        coord = CoordenadorGeral("test-coord-4", MagicMock())
        sup_saude = MagicMock()
        sup_saude.run.return_value = {"despesas": [], "indicadores": []}
        coord.working_memory.update({
            "_sup_saude": sup_saude,
            "_metrics_collectors": [],
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "health_params": [], "intent_summary": None, "use_llm": False,
        })
        coord._act_delegar_saude({"goal": "delegar_saude"})

        sup_saude.run.assert_called_once()
        assert sup_saude.run.call_args.kwargs["use_llm"] is False

    def test_delegar_orcamento_repassa_use_llm_e_intent_summary(self):
        """Regressão (Fase 3): intent_summary da análise inteira precisa
        chegar em SupervisorOrcamento.run() para que a deliberação de
        dimensão (POR_NATUREZA/POR_APLICACAO) de cada
        AgenteOrcamentoSubfuncao subordinado tenha insumo real."""
        coord = CoordenadorGeral("test-coord-4b", MagicMock())
        sup_orcamento = MagicMock()
        sup_orcamento.run.return_value = {"despesas": [], "tendencias": {}}
        coord.working_memory.update({
            "_sup_orcamento": sup_orcamento,
            "_metrics_collectors": [],
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "health_params": [], "intent_summary": "quero saber por natureza", "use_llm": True,
        })
        coord._act_delegar_orcamento({"goal": "delegar_orcamento"})

        sup_orcamento.run.assert_called_once()
        assert sup_orcamento.run.call_args.kwargs["intent_summary"] == "quero saber por natureza"

    def test_delegar_contexto_repassa_despesas_combinadas(self):
        """Sem canal lateral: o coordenador repassa as despesas já
        combinadas (SupervisorOrcamento + SupervisorSaude) direto para
        SupervisorContexto.run(), como parâmetro."""
        coord = CoordenadorGeral("test-coord-5", MagicMock())
        sup_contexto = MagicMock()
        sup_contexto.run.return_value = {"contexto_orcamentario": {}}
        coord.working_memory.update({
            "_sup_contexto": sup_contexto,
            "_metrics_collectors": [],
            "_despesas_combinadas": [{"subfuncao": 305, "ano": 2020, "valor": 100.0}],
            "use_llm": False,
        })
        coord._act_delegar_contexto({"goal": "delegar_contexto"})

        sup_contexto.run.assert_called_once_with(
            despesas=[{"subfuncao": 305, "ano": 2020, "valor": 100.0}], use_llm=False,
        )

    def test_delegar_analitico_repassa_dados_combinados_direto(self):
        """Sem canal lateral: o coordenador repassa despesas combinadas,
        indicadores (de SupervisorSaude) e contexto_orcamentario (de
        SupervisorContexto) direto para SupervisorAnalitico.run()."""
        coord = CoordenadorGeral("test-coord-6", MagicMock())
        sup_analitico = MagicMock()
        sup_analitico.run.return_value = {"correlacoes": [], "anomalias": [], "texto_analise": ""}
        sup_analitico._coala_leaf_end_time = None
        ws_queue = Queue()
        coord.working_memory.update({
            "_sup_analitico": sup_analitico,
            "_metrics_collectors": [],
            "_ws_queue": ws_queue,
            "_despesas_combinadas": [{"subfuncao": 305, "ano": 2020, "valor": 100.0}],
            "_saude_data": {"indicadores": [{"tipo_indicador": "dengue", "ano": 2020, "valor": 30.0}]},
            "_contexto_data": {"contexto_orcamentario": {"305": {"tendencia": "corte"}}},
            "analysis_id": "a1", "date_from": 2019, "date_to": 2021,
            "health_params": ["dengue"], "intent_summary": "x", "use_llm": True,
            "use_self_check": False,
        })
        coord._act_delegar_analitico({"goal": "delegar_analitico"})

        sup_analitico.run.assert_called_once()
        kwargs = sup_analitico.run.call_args.kwargs
        assert kwargs["despesas"] == [{"subfuncao": 305, "ano": 2020, "valor": 100.0}]
        assert kwargs["indicadores"] == [{"tipo_indicador": "dengue", "ano": 2020, "valor": 30.0}]
        assert kwargs["contexto_orcamentario"] == {"305": {"tendencia": "corte"}}
        assert kwargs["intent_summary"] == "x"

    def test_persistir_metricas_emite_agent_data_combinando_supervisores(self):
        """Evento `agent_data` combinando as queries capturadas por
        SupervisorOrcamento e SupervisorSaude (mesma exposição que a
        topologia estrela)."""
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

    def test_nenhum_supervisor_tem_receive_from_peer(self):
        """Árvore estrita: nenhum supervisor expõe mais o canal lateral."""
        from agents.hierarchical.supervisors import (
            SupervisorOrcamento, SupervisorSaude, SupervisorAnalitico, SupervisorContexto,
        )

        for cls in (SupervisorOrcamento, SupervisorSaude, SupervisorAnalitico, SupervisorContexto):
            assert not hasattr(cls, "receive_from_peer")
