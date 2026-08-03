"""
Coordenador geral da arquitetura hierárquica (Nível 0).

Delega para 3 supervisores especializados de nível 1:
- SupervisorDominio (4 agentes de domínio)
- SupervisorAnalitico (3 agentes analíticos)
- SupervisorContexto (1 agente de contexto orçamentário)

Implementa comunicação lateral entre supervisores (Reqs 10.5, 10.6),
degradação graciosa em falha de supervisor (Req 10.9), e contagem
de mensagens para comparação quantitativa (Reqs 11.1, 11.2).

Nota arquitetural (CoALA): assim como o OrquestradorEstrela, o
CoordenadorGeral executa de fato pelo ciclo CoALA — cada etapa de
delegação e cada comunicação lateral entre supervisores é uma ação
nomeada em `procedural_memory`, proposta em ordem fixa por
`propose_actions()` (a ordem reflete as dependências de dados entre
supervisores: domínio → comunicação lateral → contexto → comunicação
lateral → analítico). `evaluate_and_select` colapsa num passthrough
pelo mesmo motivo que no orquestrador estrela — não há candidatos
concorrentes a arbitrar neste nível.

Requisitos: 10.1, 10.5, 10.6, 10.8, 10.9, 11.1, 11.2
"""

from __future__ import annotations

import logging
import time
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from agents.hierarchical.supervisors import (
    SupervisorDominio,
    SupervisorAnalitico,
    SupervisorContexto,
)
from core.metrics import MetricsCollector

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class CoordenadorGeral(AgenteCoALA):
    """Nível 0 da topologia hierárquica com 3 supervisores (Req 10).

    Delega para SupervisorDominio, SupervisorAnalitico e
    SupervisorContexto. Os supervisores trocam dados lateralmente
    via ``receive_from_peer`` (Reqs 10.5, 10.6). Agrega resultados,
    registra métricas por agente e supervisor (Req 10.8), e trata
    falhas com degradação graciosa (Req 10.9).

    Herda de AgenteCoALA e executa de fato pelo ciclo CoALA — cada etapa
    de delegação/comunicação lateral é uma ação registrada em
    `procedural_memory`, proposta em ordem fixa por `propose_actions()`.

    Attributes:
        neo4j_client: Cliente Neo4j para queries e persistência.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.procedural_memory = {
            "delegar_dominio": [self._act_delegar_dominio],
            "comunicar_dominio_analitico": [self._act_comunicar_dominio_analitico],
            "comunicar_dominio_contexto": [self._act_comunicar_dominio_contexto],
            "delegar_contexto": [self._act_delegar_contexto],
            "comunicar_contexto_analitico": [self._act_comunicar_contexto_analitico],
            "delegar_analitico": [self._act_delegar_analitico],
            "persistir_metricas": [self._act_persistir_metricas],
        }

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe parâmetros da análise a partir da working memory."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
            "health_params": self.working_memory.get("health_params", []),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe as macro-ações do pipeline hierárquico, em ordem de dependência.

        A ordem reflete as dependências de dados entre supervisores
        (domínio → comunicação lateral → contexto → comunicação lateral →
        analítico → persistência), não uma arbitragem entre candidatos
        concorrentes.
        """
        if not self.working_memory.get("analysis_id"):
            return []
        return [
            {"goal": "delegar_dominio"},
            {"goal": "comunicar_dominio_analitico"},
            {"goal": "comunicar_dominio_contexto"},
            {"goal": "delegar_contexto"},
            {"goal": "comunicar_contexto_analitico"},
            {"goal": "delegar_analitico"},
            {"goal": "persistir_metricas"},
        ]

    # -- Ações --------------------------------------------------------------

    def _act_delegar_dominio(self, action: dict) -> None:
        """Ação externa: delega para SupervisorDominio.run() (Req 10.1)."""
        sup_dominio = self.working_memory["_sup_dominio"]
        mc = MetricsCollector(sup_dominio.agent_id, "supervisor_dominio")
        mc.start()
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        health_params = self.working_memory.get("health_params", [])
        intent_summary = self.working_memory.get("intent_summary")
        try:
            dominio_data = sup_dominio.run(
                analysis_id=analysis_id,
                date_from=date_from,
                date_to=date_to,
                health_params=health_params,
                intent_summary=intent_summary,
            )
            mc.stop()
            logger.info(
                "CoordenadorGeral %s: SupervisorDominio completed — %d despesas, %d indicadores",
                self.agent_id,
                len(dominio_data.get("despesas", [])),
                len(dominio_data.get("indicadores", [])),
            )
        except Exception as exc:
            mc.stop()
            # Req 10.9: degradação graciosa — enviar erro e continuar
            logger.error(
                "CoordenadorGeral %s: SupervisorDominio failed — %s", self.agent_id, exc
            )
            self._send_error(f"SupervisorDominio falhou: {exc}")
            dominio_data = {"despesas": [], "indicadores": []}

        self.working_memory["_dominio_data"] = dominio_data
        self.working_memory["_metrics_collectors"].append(mc)

    def _act_comunicar_dominio_analitico(self, action: dict) -> None:
        """Comunicação lateral: SupervisorDominio → SupervisorAnalitico (Req 10.5).

        Repassa despesas e indicadores para o pipeline analítico.
        """
        sup_analitico = self.working_memory["_sup_analitico"]
        dominio_data = self.working_memory.get("_dominio_data", {})
        logger.info(
            "[%s] CoordenadorGeral: comunicação lateral Dominio -> Analitico "
            "(%d despesas, %d indicadores)",
            self.agent_id,
            len(dominio_data.get("despesas", [])),
            len(dominio_data.get("indicadores", [])),
        )
        sup_analitico.receive_from_peer({
            "despesas": dominio_data.get("despesas", []),
            "indicadores": dominio_data.get("indicadores", []),
            "date_from": self.working_memory["date_from"],
            "date_to": self.working_memory["date_to"],
            "health_params": self.working_memory.get("health_params", []),
            "intent_summary": self.working_memory.get("intent_summary"),
        })

    def _act_comunicar_dominio_contexto(self, action: dict) -> None:
        """Comunicação lateral: SupervisorDominio → SupervisorContexto (Req 10.5).

        Repassa despesas para análise de tendências orçamentárias.
        """
        sup_contexto = self.working_memory["_sup_contexto"]
        dominio_data = self.working_memory.get("_dominio_data", {})
        logger.info(
            "[%s] CoordenadorGeral: comunicação lateral Dominio -> Contexto (%d despesas)",
            self.agent_id, len(dominio_data.get("despesas", [])),
        )
        sup_contexto.receive_from_peer({"despesas": dominio_data.get("despesas", [])})

    def _act_delegar_contexto(self, action: dict) -> None:
        """Ação externa: delega para SupervisorContexto.run() (Req 10.4)."""
        sup_contexto = self.working_memory["_sup_contexto"]
        mc = MetricsCollector(sup_contexto.agent_id, "supervisor_contexto")
        mc.start()
        try:
            contexto_data = sup_contexto.run()
            mc.stop()
            logger.info("CoordenadorGeral %s: SupervisorContexto completed", self.agent_id)
        except Exception as exc:
            mc.stop()
            # Req 10.9: degradação graciosa — enviar erro e continuar
            logger.error(
                "CoordenadorGeral %s: SupervisorContexto failed — %s", self.agent_id, exc
            )
            self._send_error(f"SupervisorContexto falhou: {exc}")
            contexto_data = {"contexto_orcamentario": {}}

        self.working_memory["_contexto_data"] = contexto_data
        self.working_memory["_metrics_collectors"].append(mc)

    def _act_comunicar_contexto_analitico(self, action: dict) -> None:
        """Comunicação lateral: SupervisorContexto → SupervisorAnalitico (Req 10.6).

        Repassa contexto orçamentário para enriquecer a síntese textual.
        """
        sup_analitico = self.working_memory["_sup_analitico"]
        contexto_data = self.working_memory.get("_contexto_data", {})
        logger.info(
            "[%s] CoordenadorGeral: comunicação lateral Contexto -> Analitico (%d subfunções)",
            self.agent_id, len(contexto_data.get("contexto_orcamentario", {})),
        )
        sup_analitico.receive_from_peer({
            "contexto_orcamentario": contexto_data.get("contexto_orcamentario", {}),
        })

    def _act_delegar_analitico(self, action: dict) -> None:
        """Ação externa: delega para SupervisorAnalitico.run() (Req 10.3)."""
        sup_analitico = self.working_memory["_sup_analitico"]
        mc = MetricsCollector(sup_analitico.agent_id, "supervisor_analitico")
        mc.start()
        analysis_id = self.working_memory["analysis_id"]
        ws_queue = self.working_memory["_ws_queue"]
        use_llm = self.working_memory.get("use_llm", True)
        try:
            analitico_data = sup_analitico.run(
                analysis_id=analysis_id, ws_queue=ws_queue, use_llm=use_llm
            )
            # Usa o timestamp de fim da parte determinística (exclui tempo
            # do sintetizador/LLM) — mais preciso que mc.stop() aqui.
            leaf_end = getattr(sup_analitico, "_coala_leaf_end_time", None)
            if leaf_end and mc._start_time:
                mc._end_time = leaf_end
            else:
                mc.stop()
            logger.info(
                "CoordenadorGeral %s: SupervisorAnalitico completed — %d correlacoes, %d anomalias",
                self.agent_id,
                len(analitico_data.get("correlacoes", [])),
                len(analitico_data.get("anomalias", [])),
            )
            self.working_memory["_analitico_data"] = analitico_data
        except Exception as exc:
            mc.stop()
            # Req 10.9: degradação graciosa — enviar erro
            logger.error(
                "CoordenadorGeral %s: SupervisorAnalitico failed — %s", self.agent_id, exc
            )
            self._send_error(f"SupervisorAnalitico falhou: {exc}")
            self.working_memory["_analitico_data"] = {
                "correlacoes": [],
                "anomalias": [],
                "texto_analise": "",
            }

        self.working_memory["_metrics_collectors"].append(mc)

    def _act_persistir_metricas(self, action: dict) -> None:
        """Persiste métricas de 8 agentes + 3 supervisores e emite evento `metric` (Req 10.8, 11.2).

        Overhead = wall_clock - soma dos agentes FOLHA (nível 2).
        Supervisores NÃO entram em workers_time porque seu tempo já
        engloba o dos subordinados — somar ambos causaria dupla contagem.
        O overhead captura: tempo dos supervisores fora dos subordinados
        + comunicação lateral (receive_from_peer) + instanciação.
        """
        analysis_id = self.working_memory["analysis_id"]
        metrics_collectors: list[MetricsCollector] = self.working_memory.get(
            "_metrics_collectors", []
        )
        sup_dominio = self.working_memory["_sup_dominio"]
        sup_analitico = self.working_memory["_sup_analitico"]
        sup_contexto = self.working_memory["_sup_contexto"]

        # Persiste métricas dos supervisores
        for mc in metrics_collectors:
            try:
                mc.persist(self.neo4j_client, analysis_id, "hierarchical")
            except Exception as exc:
                logger.error(
                    "CoordenadorGeral %s: failed to persist supervisor metrics — %s",
                    self.agent_id,
                    exc,
                )

        # Persiste métricas dos agentes subordinados de cada supervisor
        for supervisor in (sup_dominio, sup_analitico, sup_contexto):
            for agent_mc in getattr(supervisor, "_collectors", []):
                try:
                    agent_mc.persist(self.neo4j_client, analysis_id, "hierarchical")
                except Exception as exc:
                    logger.error(
                        "CoordenadorGeral %s: failed to persist agent metrics — %s",
                        self.agent_id,
                        exc,
                    )

        agent_metrics = []
        workers_time_ms = 0.0

        # Supervisor metrics (para exibição, NÃO somados em workers)
        for mc in metrics_collectors:
            try:
                m = mc.collect()
                agent_metrics.append({
                    "agentName": m["agentType"],
                    "executionTimeMs": m["executionTimeMs"],
                    "cpuPercent": m["cpuPercent"],
                })
            except Exception:
                pass

        # Subordinate agent metrics (agentes folha — somados em workers)
        for supervisor in (sup_dominio, sup_analitico, sup_contexto):
            for agent_mc in getattr(supervisor, "_collectors", []):
                try:
                    m = agent_mc.collect()
                    # Exclui sintetizador — é um serviço LLM, não agente CoALA
                    if m.get("agentType") == "sintetizador":
                        continue
                    agent_metrics.append({
                        "agentName": m["agentType"],
                        "executionTimeMs": m["executionTimeMs"],
                        "cpuPercent": m["cpuPercent"],
                    })
                    workers_time_ms += m["executionTimeMs"]
                except Exception:
                    pass

        # Wall-clock total time — exclui tempo do sintetizador (LLM)
        # porque depende da disponibilidade da API do LLM, não da arquitetura.
        coord_start = self.working_memory.get("_coord_start", time.time())
        coord_end = time.time()
        wall_clock_ms_raw = round((coord_end - coord_start) * 1000, 2)

        # Subtrair tempo do sintetizador (último collector do SupervisorAnalitico)
        sint_time_ms = 0.0
        for agent_mc in getattr(sup_analitico, "_collectors", []):
            try:
                m = agent_mc.collect()
                if m.get("agentType") == "sintetizador":
                    sint_time_ms = m.get("executionTimeMs", 0)
            except Exception:
                pass

        wall_clock_ms = round(max(0, wall_clock_ms_raw - sint_time_ms), 2)
        overhead_ms = round(max(0, wall_clock_ms - workers_time_ms), 2)

        agent_metrics.append({
            "agentName": "coordenador_geral",
            "executionTimeMs": overhead_ms,
            "cpuPercent": 0.0,
        })

        ws_queue = self.working_memory["_ws_queue"]
        ws_queue.put({
            "analysisId": analysis_id,
            "architecture": "hierarchical",
            "type": "metric",
            "payload": {
                "architecture": "hierarchical",
                "totalExecutionTimeMs": wall_clock_ms,
                "workersTimeMs": round(workers_time_ms, 2),
                "overheadTimeMs": overhead_ms,
                "agentMetrics": agent_metrics,
            },
        })

    # -- Helpers --------------------------------------------------------

    def _send_error(self, message: str) -> None:
        """Envia evento de erro via ws_queue (Req 10.9), se disponível."""
        ws_queue = self.working_memory.get("_ws_queue")
        if ws_queue is None:
            return
        ws_queue.put({
            "analysisId": self.working_memory.get("analysis_id"),
            "architecture": "hierarchical",
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
        """Executa o pipeline completo da arquitetura hierárquica.

        Instancia os 3 supervisores com IDs únicos (Req 10.1), configura
        a working memory e delega ao ciclo CoALA (`run_coala_cycle`), que
        percorre as macro-ações registradas em `procedural_memory` na
        ordem proposta por `propose_actions`.

        Args:
            analysis_id: UUID da análise.
            params: Dicionário com date_from, date_to, health_params.
            ws_queue: Fila para streaming de eventos WebSocket.

        Returns:
            Dicionário com resultado completo da análise (possivelmente parcial).
        """
        sup_dominio_id = f"hier-sup-dominio-{uuid.uuid4().hex[:8]}"
        sup_analitico_id = f"hier-sup-analitico-{uuid.uuid4().hex[:8]}"
        sup_contexto_id = f"hier-sup-contexto-{uuid.uuid4().hex[:8]}"

        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "health_params": params.get("health_params", []),
            "use_llm": params.get("use_llm", True),
            "intent_summary": params.get("intent_summary"),
            "_ws_queue": ws_queue,
            "_sup_dominio": SupervisorDominio(sup_dominio_id, self.neo4j_client),
            "_sup_analitico": SupervisorAnalitico(sup_analitico_id),
            "_sup_contexto": SupervisorContexto(sup_contexto_id),
            "_metrics_collectors": [],
            "_coord_start": time.time(),
        })

        self.run_coala_cycle()

        dominio_data = self.working_memory.get("_dominio_data", {})
        contexto_data = self.working_memory.get("_contexto_data", {})
        analitico_data = self.working_memory.get("_analitico_data", {})

        result: dict[str, Any] = dict(analitico_data)
        result.setdefault("correlacoes", [])
        result.setdefault("anomalias", [])
        result.setdefault("texto_analise", "")
        result["despesas"] = dominio_data.get("despesas", [])
        result["indicadores"] = dominio_data.get("indicadores", [])
        result["contexto_orcamentario"] = contexto_data.get("contexto_orcamentario", {})

        self.working_memory["result"] = result
        logger.info("CoordenadorGeral %s: pipeline complete", self.agent_id)

        return result
