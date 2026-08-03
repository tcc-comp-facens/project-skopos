"""
Orquestrador central da arquitetura estrela.

Hub central que instancia e coordena 8 agentes especializados:
4 de domínio, 3 analíticos e 1 de contexto. Intermedia toda
comunicação entre agentes — nenhum agente periférico chama outro
diretamente.

Pipeline (cada etapa é uma ação nomeada em `procedural_memory`,
executada pelo ciclo CoALA da classe base):
  1. Fase de Domínio: até 4 agentes consultam Neo4j em sequência
     (ativação condicional conforme health_params)
  2. Cruzamento de dados: despesas × indicadores por subfunção
  3. Detecção de lacunas de dados
  4. Fase Analítica: contexto orçamentário, correlação, anomalias, síntese
  5. Persistência de métricas

Nota arquitetural (CoALA): diferente dos agentes-folha (domínio,
analíticos, contexto), aqui `propose_actions()` não arbitra entre
candidatos concorrentes — a ordem das macro-ações é imposta por
dependência de dados (cruzamento só pode ocorrer depois do domínio,
síntese só depois de correlação/anomalias/contexto). `evaluate_and_select`
portanto colapsa num passthrough, por decisão intencional documentada
aqui, não por omissão. O `run()` público delega inteiramente ao ciclo
`run_coala_cycle()` da base — cada etapa do pipeline hoje vive como um
método `_act_*` registrado em `procedural_memory`, o que preserva
exatamente a mesma degradação graciosa, coleta de métricas por agente e
eventos WebSocket de erro que o pipeline imperativo anterior já tinha,
só que percorridos pelo laço genérico de `execute()` em vez de um
try/except manual por etapa.

Requisitos: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 11.1, 11.2
"""

from __future__ import annotations

import logging
import time
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from agents.data_crossing import cross_domain_data, deduplicate_despesas, detect_data_gaps
from agents.domain.vigilancia_epidemiologica import AgenteVigilanciaEpidemiologica
from agents.domain.saude_hospitalar import AgenteSaudeHospitalar
from agents.domain.atencao_primaria import AgenteAtencaoPrimaria
from agents.domain.mortalidade import AgenteMortalidade
from agents.analytical.correlacao import AgenteCorrelacao
from agents.analytical.anomalias import AgenteAnomalias
from agents.analytical.sintetizador import TextSynthesizer
from agents.context.contexto_orcamentario import AgenteContextoOrcamentario
from core.metrics import MetricsCollector
from core.streaming_adapter import StreamingAdapter

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Mapeamento: tipo de indicador → agente de domínio responsável
# Usado para ativar apenas os agentes relevantes aos health_params do usuário
INDICADOR_TO_AGENT: dict[str, str] = {
    "dengue": "vigilancia_epidemiologica",
    "covid": "vigilancia_epidemiologica",
    "internacoes": "saude_hospitalar",
    "vacinacao": "atencao_primaria",
    "mortalidade": "mortalidade",
}

# Ordem fixa de avaliação dos agentes de domínio (preserva a sequência
# histórica Vigilância → Hospitalar → Primária → Mortalidade)
_DOMAIN_AGENT_KEY_ORDER: list[str] = ["vigilancia", "hospitalar", "primaria", "mortalidade"]

# Apenas strings (usadas para filtrar por health_params em propose_actions) —
# a resolução para classe real acontece em _act_consultar_dominio, lendo o
# nome global no momento da chamada (permite mock/patch em testes).
_AGENT_KEY_TO_TYPE: dict[str, str] = {
    "vigilancia": "vigilancia_epidemiologica",
    "hospitalar": "saude_hospitalar",
    "primaria": "atencao_primaria",
    "mortalidade": "mortalidade",
}


class OrquestradorEstrela(AgenteCoALA):
    """Hub central da topologia estrela coordenando 8 agentes especializados (Req 9).

    Toda comunicação entre agentes periféricos passa por este
    orquestrador (Req 9.3). Distribui tarefas via chamadas de
    método Python, registra métricas de tempo de execução por agente
    (Req 9.7), e trata falhas com resultados parciais (Req 9.8).

    Herda de AgenteCoALA e executa de fato pelo ciclo CoALA — cada etapa
    do pipeline é uma ação registrada em `procedural_memory`, proposta em
    ordem fixa por `propose_actions()` (a ordem já reflete as dependências
    de dados entre etapas).

    Attributes:
        neo4j_client: Cliente Neo4j para queries e persistência.
    """

    def __init__(self, agent_id: str, neo4j_client: Neo4jClient) -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.semantic_memory = {"indicador_to_agent": INDICADOR_TO_AGENT}
        self.procedural_memory = {
            "consultar_dominio": [self._act_consultar_dominio],
            "cruzar_dados": [self._act_cruzar_dados],
            "detectar_gaps": [self._act_detectar_gaps],
            "analisar_contexto": [self._act_analisar_contexto],
            "calcular_correlacoes": [self._act_calcular_correlacoes],
            "detectar_anomalias": [self._act_detectar_anomalias],
            "capturar_wallclock": [self._act_capturar_wallclock],
            "sintetizar_texto": [self._act_sintetizar_texto],
            "persistir_metricas": [self._act_persistir_metricas],
        }

    # -- Ciclo CoALA --------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe parâmetros da análise a partir da working memory."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
            "health_params": self.working_memory.get("health_params", []),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe as macro-ações do pipeline, em ordem de dependência de dados.

        A ativação dos agentes de domínio é condicional aos health_params
        (retrieval de `semantic_memory["indicador_to_agent"]`) — só os
        agentes relevantes à seleção do usuário são propostos (Req 9.1).
        """
        actions: list[dict] = []
        health_params = self.working_memory.get("health_params", [])
        indicador_to_agent = self.semantic_memory["indicador_to_agent"]

        active_agent_types: set[str] = set()
        for hp in health_params:
            agent_type = indicador_to_agent.get(hp)
            if agent_type:
                active_agent_types.add(agent_type)

        for key in _DOMAIN_AGENT_KEY_ORDER:
            if _AGENT_KEY_TO_TYPE[key] in active_agent_types:
                actions.append({"goal": "consultar_dominio", "agent_key": key})

        logger.info(
            "[%s] OrquestradorEstrela: activating %d/%d domain agents for health_params=%s",
            self.agent_id,
            len(actions),
            len(_DOMAIN_AGENT_KEY_ORDER),
            health_params,
        )

        actions.append({"goal": "cruzar_dados"})
        actions.append({"goal": "detectar_gaps"})
        actions.append({"goal": "analisar_contexto"})
        actions.append({"goal": "calcular_correlacoes"})
        actions.append({"goal": "detectar_anomalias"})
        actions.append({"goal": "capturar_wallclock"})
        actions.append({"goal": "sintetizar_texto"})
        actions.append({"goal": "persistir_metricas"})
        return actions

    # -- Ações (grounding/reasoning) -----------------------------------------

    def _act_consultar_dominio(self, action: dict) -> None:
        """Ação externa: consulta um agente de domínio (Req 9.2, 9.3).

        Resolve a classe do agente pelo nome global no momento da chamada
        (não via dict pré-construído), para que os testes possam
        substituir a classe (`unittest.mock.patch`) normalmente.
        """
        specs = {
            "vigilancia": ("vigilancia_epidemiologica", AgenteVigilanciaEpidemiologica, "star-vigilancia"),
            "hospitalar": ("saude_hospitalar", AgenteSaudeHospitalar, "star-hospitalar"),
            "primaria": ("atencao_primaria", AgenteAtencaoPrimaria, "star-primaria"),
            "mortalidade": ("mortalidade", AgenteMortalidade, "star-mortalidade"),
        }
        agent_type, agent_cls, id_prefix = specs[action["agent_key"]]
        agent_id_str = f"{id_prefix}-{uuid.uuid4().hex[:8]}"
        agent = agent_cls(agent_id_str, self.neo4j_client)

        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]

        logger.info(
            "[%s] OrquestradorEstrela: delegando a %s (agent_id=%s)",
            self.agent_id, agent_type, agent_id_str,
        )
        mc = MetricsCollector(agent_id_str, agent_type)
        mc.start()
        try:
            result = agent.query(analysis_id, date_from, date_to)
            mc.stop()
            self.working_memory.setdefault("despesas", []).extend(result.get("despesas", []))
            self.working_memory.setdefault("indicadores", []).extend(result.get("indicadores", []))
            logger.info(
                "[%s] OrquestradorEstrela: %s (agent_id=%s) returned %d despesas, %d indicadores",
                self.agent_id,
                agent_type,
                agent_id_str,
                len(result.get("despesas", [])),
                len(result.get("indicadores", [])),
            )
            self.working_memory.setdefault("_collectors", []).append((agent_id_str, agent_type, mc))
        except Exception as exc:
            mc.stop()
            logger.error(
                "[%s] OrquestradorEstrela: %s (agent_id=%s) failed — %s",
                self.agent_id, agent_type, agent_id_str, exc,
            )
            self._send_error(f"Agente {agent_type} falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((agent_id_str, agent_type, mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_cruzar_dados(self, action: dict) -> None:
        """Ação interna (reasoning): deduplica e cruza despesas × indicadores (Req 9.4)."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            unique_despesas = deduplicate_despesas(despesas)
            dados_cruzados = cross_domain_data(unique_despesas, indicadores)
            self.working_memory["despesas"] = unique_despesas
            self.working_memory["dados_cruzados"] = dados_cruzados
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_gaps(self, action: dict) -> None:
        """Ação interna (reasoning): detecta lacunas de dados para transparência."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            date_from = self.working_memory["date_from"]
            date_to = self.working_memory["date_to"]
            health_params = self.working_memory.get("health_params", [])

            data_coverage = detect_data_gaps(
                despesas, indicadores, date_from, date_to, health_params
            )
            self.working_memory["data_coverage"] = data_coverage
            if data_coverage["summary"]["has_gaps"]:
                logger.warning(
                    "[%s] OrquestradorEstrela: %d data gaps detected",
                    self.agent_id,
                    data_coverage["summary"]["total_gaps"],
                )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_analisar_contexto(self, action: dict) -> None:
        """Ação externa: delega a AgenteContextoOrcamentario (Req 9.4)."""
        ctx_id = f"star-contexto-{uuid.uuid4().hex[:8]}"
        agente_contexto = AgenteContextoOrcamentario(ctx_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando a contexto_orcamentario (agent_id=%s)",
            self.agent_id, ctx_id,
        )
        mc = MetricsCollector(ctx_id, "contexto_orcamentario")
        mc.start()
        try:
            despesas = self.working_memory.get("despesas", [])
            contexto_orcamentario = agente_contexto.analyze_trends(despesas)
            mc.stop()
            self.working_memory["contexto_orcamentario"] = contexto_orcamentario
            logger.info(
                "[%s] OrquestradorEstrela: contexto_orcamentario computed for %d subfunções",
                self.agent_id,
                len(contexto_orcamentario),
            )
            self.working_memory.setdefault("_collectors", []).append(
                (ctx_id, "contexto_orcamentario", mc)
            )
        except Exception as exc:
            mc.stop()
            logger.error(
                "[%s] OrquestradorEstrela: contexto_orcamentario failed — %s", self.agent_id, exc
            )
            self._send_error(f"Agente contexto_orcamentario falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append(
                (ctx_id, "contexto_orcamentario", mc)
            )
            raise ActionFailure(action, str(exc)) from exc

    def _act_calcular_correlacoes(self, action: dict) -> None:
        """Ação externa: delega a AgenteCorrelacao (Req 9.4)."""
        corr_id = f"star-correlacao-{uuid.uuid4().hex[:8]}"
        agente_correlacao = AgenteCorrelacao(corr_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando a correlacao (agent_id=%s)", self.agent_id, corr_id
        )
        mc = MetricsCollector(corr_id, "correlacao")
        mc.start()
        try:
            dados_cruzados = self.working_memory.get("dados_cruzados", [])
            correlacoes = agente_correlacao.compute(dados_cruzados)
            mc.stop()
            self.working_memory["correlacoes"] = correlacoes
            logger.info(
                "[%s] OrquestradorEstrela: computed %d correlações", self.agent_id, len(correlacoes)
            )
            self.working_memory.setdefault("_collectors", []).append((corr_id, "correlacao", mc))
        except Exception as exc:
            mc.stop()
            logger.error("[%s] OrquestradorEstrela: correlacao failed — %s", self.agent_id, exc)
            self._send_error(f"Agente correlacao falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((corr_id, "correlacao", mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_anomalias(self, action: dict) -> None:
        """Ação externa: delega a AgenteAnomalias (Req 9.5)."""
        anom_id = f"star-anomalias-{uuid.uuid4().hex[:8]}"
        agente_anomalias = AgenteAnomalias(anom_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando a anomalias (agent_id=%s)", self.agent_id, anom_id
        )
        mc = MetricsCollector(anom_id, "anomalias")
        mc.start()
        try:
            dados_cruzados = self.working_memory.get("dados_cruzados", [])
            anomalias = agente_anomalias.detect(dados_cruzados)
            mc.stop()
            self.working_memory["anomalias"] = anomalias
            logger.info(
                "[%s] OrquestradorEstrela: detected %d anomalias", self.agent_id, len(anomalias)
            )
            self.working_memory.setdefault("_collectors", []).append((anom_id, "anomalias", mc))
        except Exception as exc:
            mc.stop()
            logger.error("[%s] OrquestradorEstrela: anomalias failed — %s", self.agent_id, exc)
            self._send_error(f"Agente anomalias falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((anom_id, "anomalias", mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_capturar_wallclock(self, action: dict) -> None:
        """Ação interna (bookkeeping): marca o fim do pipeline determinístico.

        Wall-clock mede apenas o pipeline CoALA (domínio → cruzamento →
        contexto → correlação → anomalias), excluindo o sintetizador — a
        geração de texto depende da disponibilidade da API do LLM, não
        reflete a eficiência da arquitetura multiagente em si.
        """
        self.working_memory["_orch_end"] = time.time()

    def _act_sintetizar_texto(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): gera o texto via TextSynthesizer (Req 9.6)."""
        sint_id = f"star-sintetizador-{uuid.uuid4().hex[:8]}"
        sintetizador = TextSynthesizer(sint_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando síntese textual (sintetizador_id=%s, use_llm=%s)",
            self.agent_id, sint_id, self.working_memory.get("use_llm", True),
        )
        mc = MetricsCollector(sint_id, "sintetizador")
        mc.start()
        texto_analise = ""
        try:
            ws_queue = self.working_memory["_ws_queue"]
            analysis_id = self.working_memory["analysis_id"]
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})
            data_coverage = self.working_memory.get("data_coverage")
            use_llm = self.working_memory.get("use_llm", True)

            adapter = StreamingAdapter(ws_queue, analysis_id, "star")

            if use_llm:
                try:
                    token_gen = sintetizador.generate_stream(
                        correlacoes=correlacoes,
                        anomalias=anomalias,
                        contexto_orcamentario=contexto_orcamentario,
                        data_coverage=data_coverage,
                    )
                    texto_analise = adapter.stream_tokens(token_gen)
                    if not texto_analise:
                        # LLM returned empty — use fallback
                        texto_analise = sintetizador.generate_fallback(
                            correlacoes, anomalias, contexto_orcamentario, data_coverage
                        )
                        adapter.stream_text(texto_analise)
                except Exception:
                    logger.warning(
                        "[%s] OrquestradorEstrela: LLM streaming failed, using fallback",
                        self.agent_id,
                    )
                    texto_analise = sintetizador.generate_fallback(
                        correlacoes, anomalias, contexto_orcamentario, data_coverage
                    )
                    adapter.stream_text(texto_analise)
            else:
                texto_analise = sintetizador.generate_fallback(
                    correlacoes, anomalias, contexto_orcamentario, data_coverage
                )
                adapter.stream_text(texto_analise)

            mc.stop()
            self.working_memory["texto_analise"] = texto_analise
            logger.info(
                "[%s] OrquestradorEstrela: synthesis complete (%d chars)",
                self.agent_id, len(texto_analise),
            )
            self.working_memory.setdefault("_collectors", []).append((sint_id, "sintetizador", mc))
        except Exception as exc:
            mc.stop()
            logger.error("[%s] OrquestradorEstrela: sintetizador failed — %s", self.agent_id, exc)
            self._send_error(f"Sintetizador falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((sint_id, "sintetizador", mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_persistir_metricas(self, action: dict) -> None:
        """Ação externa: persiste métricas de cada agente e emite evento `metric` (Req 9.7, 11.2)."""
        try:
            analysis_id = self.working_memory["analysis_id"]
            collectors: list[tuple[str, str, MetricsCollector]] = self.working_memory.get(
                "_collectors", []
            )

            for agent_id_str, agent_type, mc in collectors:
                try:
                    mc.persist(self.neo4j_client, analysis_id, "star")
                except Exception as exc:
                    logger.error(
                        "[%s] OrquestradorEstrela: failed to persist metrics for %s — %s",
                        self.agent_id,
                        agent_id_str,
                        exc,
                    )

            agent_metrics = []
            workers_time_ms = 0.0
            for _, agent_type, mc in collectors:
                # Exclui sintetizador — é um serviço LLM, não agente CoALA
                if agent_type == "sintetizador":
                    continue
                try:
                    m = mc.collect()
                    agent_metrics.append({
                        "agentName": agent_type,
                        "executionTimeMs": m["executionTimeMs"],
                        "cpuPercent": m["cpuPercent"],
                    })
                    workers_time_ms += m["executionTimeMs"]
                except Exception:
                    pass

            orch_start = self.working_memory.get("_orch_start", time.time())
            orch_end = self.working_memory.get("_orch_end", time.time())
            wall_clock_ms = round((orch_end - orch_start) * 1000, 2)
            overhead_ms = round(max(0, wall_clock_ms - workers_time_ms), 2)

            agent_metrics.append({
                "agentName": "orquestrador_estrela",
                "executionTimeMs": overhead_ms,
                "cpuPercent": 0.0,
            })

            ws_queue = self.working_memory["_ws_queue"]
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": "star",
                "type": "metric",
                "payload": {
                    "architecture": "star",
                    "totalExecutionTimeMs": wall_clock_ms,
                    "workersTimeMs": round(workers_time_ms, 2),
                    "overheadTimeMs": overhead_ms,
                    "agentMetrics": agent_metrics,
                },
            })
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    # -- Helpers --------------------------------------------------------

    def _send_error(self, message: str) -> None:
        """Envia evento de erro via ws_queue (Req 9.8), se disponível."""
        ws_queue = self.working_memory.get("_ws_queue")
        if ws_queue is None:
            return
        ws_queue.put({
            "analysisId": self.working_memory.get("analysis_id"),
            "architecture": "star",
            "type": "error",
            "payload": message,
        })

    # -- Interface pública ------------------------------------------------

    def run(
        self,
        analysis_id: str,
        params: dict[str, Any],
        ws_queue: Queue,
    ) -> dict[str, Any]:
        """Executa o pipeline completo da arquitetura estrela com 8 agentes.

        Configura a working memory com os parâmetros da análise e delega
        ao ciclo CoALA (`run_coala_cycle`), que percorre as macro-ações
        registradas em `procedural_memory` na ordem proposta por
        `propose_actions` (Req 9.1-9.8).

        Args:
            analysis_id: UUID da análise.
            params: Dicionário com date_from, date_to, health_params.
            ws_queue: Fila para streaming de eventos WebSocket.

        Returns:
            Dicionário com resultado completo da análise.
        """
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "health_params": params.get("health_params", []),
            "use_llm": params.get("use_llm", True),
            "intent_summary": params.get("intent_summary"),
            "_ws_queue": ws_queue,
            "_orch_start": time.time(),
            "despesas": [],
            "indicadores": [],
            "_collectors": [],
        })

        self.run_coala_cycle()

        result = {
            "despesas": self.working_memory.get("despesas", []),
            "indicadores": self.working_memory.get("indicadores", []),
            "dados_cruzados": self.working_memory.get("dados_cruzados", []),
            "contexto_orcamentario": self.working_memory.get("contexto_orcamentario", {}),
            "correlacoes": self.working_memory.get("correlacoes", []),
            "anomalias": self.working_memory.get("anomalias", []),
            "texto_analise": self.working_memory.get("texto_analise", ""),
            "data_coverage": self.working_memory.get("data_coverage", {}),
        }

        self.working_memory["result"] = result
        logger.info("OrquestradorEstrela %s: pipeline complete", self.agent_id)

        return result
