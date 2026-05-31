"""
Coordenador geral da arquitetura hierárquica (Nível 0).

Delega para 3 supervisores especializados de nível 1:
- SupervisorDominio (4 agentes de domínio)
- SupervisorAnalitico (3 agentes analíticos)
- SupervisorContexto (1 agente de contexto orçamentário)

Implementa comunicação lateral entre supervisores (Reqs 10.5, 10.6),
degradação graciosa em falha de supervisor (Req 10.9), e contagem
de mensagens para comparação quantitativa (Reqs 11.1, 11.2).

Requisitos: 10.1, 10.5, 10.6, 10.8, 10.9, 11.1, 11.2
"""

from __future__ import annotations

import logging
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents.base import AgenteBDI
from agents.hierarchical.supervisors import (
    SupervisorDominio,
    SupervisorAnalitico,
    SupervisorContexto,
)
from core.metrics import MetricsCollector

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class CoordenadorGeral(AgenteBDI):
    """Nível 0 da topologia hierárquica com 3 supervisores (Req 10).

    Delega para SupervisorDominio, SupervisorAnalitico e
    SupervisorContexto. Os supervisores trocam dados lateralmente
    via ``receive_from_peer`` (Reqs 10.5, 10.6). Agrega resultados,
    registra métricas por agente e supervisor (Req 10.8), e trata
    falhas com degradação graciosa (Req 10.9).

    Attributes:
        neo4j_client: Cliente Neo4j para queries e persistência.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client

    # -- BDI overrides --------------------------------------------------

    def perceive(self) -> dict:
        """Percebe parâmetros da análise a partir das crenças."""
        return {
            "analysis_id": self.beliefs.get("analysis_id"),
            "date_from": self.beliefs.get("date_from"),
            "date_to": self.beliefs.get("date_to"),
            "health_params": self.beliefs.get("health_params", []),
        }

    def deliberate(self) -> list[dict]:
        """Define desejos: delegar para supervisores e persistir métricas."""
        desires: list[dict] = []
        if self.beliefs.get("analysis_id"):
            desires.append({"goal": "delegar_dominio"})
            desires.append({"goal": "delegar_contexto"})
            desires.append({"goal": "delegar_analitico"})
            desires.append({"goal": "persistir_metricas"})
        self.desires = desires
        return desires

    def plan(self, desires: list[dict]) -> list[dict]:
        return [{"desire": d, "status": "pending"} for d in desires]

    # -- Public API -----------------------------------------------------

    def run(
        self,
        analysis_id: str,
        params: dict[str, Any],
        ws_queue: Queue,
    ) -> dict[str, Any]:
        """Executa o pipeline completo da arquitetura hierárquica.

        Pipeline:
        1. Instancia 3 supervisores com IDs únicos (Req 10.1).
        2. Delega para SupervisorDominio.run() (Req 10.1).
        4. Comunicação lateral: SupervisorDominio → SupervisorAnalitico
           via receive_from_peer (despesas + indicadores) (Req 10.5).
        5. Comunicação lateral: SupervisorDominio → SupervisorContexto
           via receive_from_peer (despesas) (Req 10.5).
        6. Delega para SupervisorContexto.run() (Req 10.4).
        7. Comunicação lateral: SupervisorContexto → SupervisorAnalitico
           via receive_from_peer (contexto_orcamentario) (Req 10.6).
        8. Delega para SupervisorAnalitico.run() (Req 10.3).
        9. Persiste métricas para 8 agentes + 3 supervisores (Req 10.8).
        10. Trata falhas de supervisor com degradação graciosa (Req 10.9).
        11. Envia contagem de mensagens como evento metric (Req 11.2).

        Args:
            analysis_id: UUID da análise.
            params: Dicionário com date_from, date_to, health_params.
            ws_queue: Fila para streaming de eventos WebSocket.

        Returns:
            Dicionário com resultado completo da análise (possivelmente parcial).
        """
        self.update_beliefs({
            "analysis_id": analysis_id,
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "health_params": params.get("health_params", []),
            "ws_queue": ws_queue,
        })

        date_from = params.get("date_from")
        date_to = params.get("date_to")

        # Wall-clock timing for fair comparison
        import time as _time
        _coord_start = _time.time()

        result: dict[str, Any] = {}
        dominio_data: dict[str, Any] = {}
        contexto_data: dict[str, Any] = {}
        metrics_collectors: list[MetricsCollector] = []

        # -- 2. Instanciar 3 supervisores com IDs únicos (Req 10.1) --
        sup_dominio_id = f"hier-sup-dominio-{uuid.uuid4().hex[:8]}"
        sup_analitico_id = f"hier-sup-analitico-{uuid.uuid4().hex[:8]}"
        sup_contexto_id = f"hier-sup-contexto-{uuid.uuid4().hex[:8]}"

        sup_dominio = SupervisorDominio(sup_dominio_id, self.neo4j_client)
        sup_analitico = SupervisorAnalitico(sup_analitico_id)
        sup_contexto = SupervisorContexto(sup_contexto_id)

        # -- 3. Delegar para SupervisorDominio (Req 10.1) --
        mc_dominio = MetricsCollector(sup_dominio_id, "supervisor_dominio")
        mc_dominio.start()
        try:
            dominio_data = sup_dominio.run(
                analysis_id=analysis_id,
                date_from=date_from,
                date_to=date_to,
                health_params=params.get("health_params", []),
            )
            mc_dominio.stop()
            logger.info(
                "CoordenadorGeral %s: SupervisorDominio completed — %d despesas, %d indicadores",
                self.agent_id,
                len(dominio_data.get("despesas", [])),
                len(dominio_data.get("indicadores", [])),
            )
        except Exception as exc:
            mc_dominio.stop()
            # Req 10.9: degradação graciosa — enviar erro e continuar
            logger.error(
                "CoordenadorGeral %s: SupervisorDominio failed — %s",
                self.agent_id,
                exc,
            )
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": "hierarchical",
                "type": "error",
                "payload": f"SupervisorDominio falhou: {exc}",
            })
            dominio_data = {"despesas": [], "indicadores": []}
        metrics_collectors.append(mc_dominio)

        # -- 4. Comunicação lateral: SupervisorDominio → SupervisorAnalitico (Req 10.5) --
        #    Repassa despesas e indicadores para o pipeline analítico.
        sup_analitico.receive_from_peer({
            "despesas": dominio_data.get("despesas", []),
            "indicadores": dominio_data.get("indicadores", []),
            "date_from": date_from,
            "date_to": date_to,
            "health_params": params.get("health_params", []),
        })

        # -- 5. Comunicação lateral: SupervisorDominio → SupervisorContexto (Req 10.5) --
        #    Repassa despesas para análise de tendências orçamentárias.
        sup_contexto.receive_from_peer({
            "despesas": dominio_data.get("despesas", []),
        })

        # -- 6. Delegar para SupervisorContexto (Req 10.4) --
        mc_contexto = MetricsCollector(sup_contexto_id, "supervisor_contexto")
        mc_contexto.start()
        try:
            contexto_data = sup_contexto.run()
            mc_contexto.stop()
            logger.info(
                "CoordenadorGeral %s: SupervisorContexto completed",
                self.agent_id,
            )
        except Exception as exc:
            mc_contexto.stop()
            # Req 10.9: degradação graciosa — enviar erro e continuar
            logger.error(
                "CoordenadorGeral %s: SupervisorContexto failed — %s",
                self.agent_id,
                exc,
            )
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": "hierarchical",
                "type": "error",
                "payload": f"SupervisorContexto falhou: {exc}",
            })
            contexto_data = {"contexto_orcamentario": {}}
        metrics_collectors.append(mc_contexto)

        # -- 7. Comunicação lateral: SupervisorContexto → SupervisorAnalitico (Req 10.6) --
        #    Repassa contexto orçamentário para enriquecer a síntese textual.
        sup_analitico.receive_from_peer({
            "contexto_orcamentario": contexto_data.get("contexto_orcamentario", {}),
        })

        # -- 8. Delegar para SupervisorAnalitico (Req 10.3) --
        mc_analitico = MetricsCollector(sup_analitico_id, "supervisor_analitico")
        mc_analitico.start()
        try:
            analitico_data = sup_analitico.run(
                analysis_id=analysis_id,
                ws_queue=ws_queue,
                use_llm=params.get("use_llm", True),
            )
            # Stop usando o timestamp BDI (exclui tempo do sintetizador/LLM)
            bdi_end = getattr(sup_analitico, "_bdi_end_time", None)
            if bdi_end and mc_analitico._start_time:
                mc_analitico._end_time = bdi_end
            else:
                mc_analitico.stop()
            result.update(analitico_data)
            logger.info(
                "CoordenadorGeral %s: SupervisorAnalitico completed — %d correlacoes, %d anomalias",
                self.agent_id,
                len(analitico_data.get("correlacoes", [])),
                len(analitico_data.get("anomalias", [])),
            )
        except Exception as exc:
            mc_analitico.stop()
            # Req 10.9: degradação graciosa — enviar erro
            logger.error(
                "CoordenadorGeral %s: SupervisorAnalitico failed — %s",
                self.agent_id,
                exc,
            )
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": "hierarchical",
                "type": "error",
                "payload": f"SupervisorAnalitico falhou: {exc}",
            })
            result.setdefault("correlacoes", [])
            result.setdefault("anomalias", [])
            result.setdefault("texto_analise", "")
        metrics_collectors.append(mc_analitico)

        # -- 9. Persistir métricas para 8 agentes + 3 supervisores (Req 10.8) --
        # First persist supervisor metrics
        for mc in metrics_collectors:
            try:
                mc.persist(self.neo4j_client, analysis_id, "hierarchical")
            except Exception as exc:
                logger.error(
                    "CoordenadorGeral %s: failed to persist supervisor metrics — %s",
                    self.agent_id,
                    exc,
                )

        # Then persist subordinate agent metrics from each supervisor
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

        # -- 10. Merge domain and context data into result --
        result["despesas"] = dominio_data.get("despesas", [])
        result["indicadores"] = dominio_data.get("indicadores", [])
        result["contexto_orcamentario"] = contexto_data.get("contexto_orcamentario", {})

        # -- 11. Send benchmark metrics event (Req 11.2) --
        # Overhead = wall_clock - soma dos agentes FOLHA (nível 2).
        # Supervisores NÃO entram em workers_time porque seu tempo já
        # engloba o dos subordinados — somar ambos causaria dupla contagem.
        # O overhead captura: tempo dos supervisores fora dos subordinados
        # + comunicação lateral (receive_from_peer) + instanciação.
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
                    # Exclui sintetizador — é um serviço LLM, não agente BDI
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
        # porque depende da disponibilidade da API Groq, não da arquitetura.
        _coord_end = _time.time()
        wall_clock_ms_raw = round((_coord_end - _coord_start) * 1000, 2)

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

        self.beliefs["result"] = result
        logger.info(
            "CoordenadorGeral %s: pipeline complete",
            self.agent_id,
        )

        return result
