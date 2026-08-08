"""
Coordenador geral da arquitetura hierárquica (Nível 0).

Delega para 4 supervisores especializados de nível 1:
- SupervisorOrcamento (agentes AgenteOrcamentoSubfuncao — 1 por
  subfunção, 7 no total)
- SupervisorSaude (5 agentes de saúde — SINAN, SIH, SIM, SI-PNI, COVID)
- SupervisorAnalitico (agentes analíticos)
- SupervisorContexto (1 agente de contexto orçamentário)

SupervisorOrcamento e SupervisorSaude substituem o antigo
SupervisorDominio (Fase 1 — PLANO_NOVO_MODELO_DADOS.md §5 e decisão
tomada com o usuário: separar orçamento e saúde em dois supervisores
distintos em vez de um único supervisor de domínio combinado). Desde a
decomposição completa (Fase 2), nenhum agente de SupervisorSaude
consulta despesas — só SupervisorOrcamento produz despesas — então a
sobreposição estrutural que motivou `_act_combinar_despesas` não existe
mais. A combinação/dedupe permanece como salvaguarda (custa pouco,
protege contra qualquer regressão futura), não porque haja overlap real
hoje.

Implementa comunicação lateral entre supervisores (Reqs 10.5, 10.6),
degradação graciosa em falha de supervisor (Req 10.9), e contagem
de mensagens para comparação quantitativa (Reqs 11.1, 11.2).

Nota arquitetural (CoALA): assim como o OrquestradorEstrela, o
CoordenadorGeral executa de fato pelo ciclo CoALA — cada etapa de
delegação e cada comunicação lateral entre supervisores é uma ação
nomeada em `procedural_memory`, proposta em ordem fixa por
`propose_actions()` (a ordem reflete as dependências de dados entre
supervisores: orçamento + saúde → combinar despesas → comunicação
lateral → contexto → comunicação lateral → analítico).
`evaluate_and_select` colapsa num passthrough pelo mesmo motivo que no
orquestrador estrela — não há candidatos concorrentes a arbitrar neste
nível (a deliberação real acontece dentro de AgenteSINAN, nível 2).

Requisitos: 10.1, 10.5, 10.6, 10.8, 10.9, 11.1, 11.2
"""

from __future__ import annotations

import logging
import time
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from agents.data_crossing import deduplicate_despesas
from agents.hierarchical.supervisors import (
    SupervisorOrcamento,
    SupervisorSaude,
    SupervisorAnalitico,
    SupervisorContexto,
)
from core.metrics import MetricsCollector

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class CoordenadorGeral(AgenteCoALA):
    """Nível 0 da topologia hierárquica com 4 supervisores (Req 10).

    Delega para SupervisorOrcamento, SupervisorSaude, SupervisorAnalitico
    e SupervisorContexto. Os supervisores trocam dados lateralmente via
    ``receive_from_peer`` (Reqs 10.5, 10.6). Agrega resultados, registra
    métricas por agente e supervisor (Req 10.8), e trata falhas com
    degradação graciosa (Req 10.9).

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
            "delegar_orcamento": [self._act_delegar_orcamento],
            "delegar_saude": [self._act_delegar_saude],
            "combinar_despesas": [self._act_combinar_despesas],
            "comunicar_saude_analitico": [self._act_comunicar_saude_analitico],
            "comunicar_orcamento_contexto": [self._act_comunicar_orcamento_contexto],
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
        (orçamento + saúde → combinar despesas → comunicação lateral →
        contexto → comunicação lateral → analítico → persistência), não
        uma arbitragem entre candidatos concorrentes.
        """
        if not self.working_memory.get("analysis_id"):
            return []
        return [
            {"goal": "delegar_orcamento"},
            {"goal": "delegar_saude"},
            {"goal": "combinar_despesas"},
            {"goal": "comunicar_saude_analitico"},
            {"goal": "comunicar_orcamento_contexto"},
            {"goal": "delegar_contexto"},
            {"goal": "comunicar_contexto_analitico"},
            {"goal": "delegar_analitico"},
            {"goal": "persistir_metricas"},
        ]

    # -- Ações --------------------------------------------------------------

    def _act_delegar_orcamento(self, action: dict) -> None:
        """Ação externa: delega para SupervisorOrcamento.run() (Fase 1)."""
        sup_orcamento = self.working_memory["_sup_orcamento"]
        mc = MetricsCollector(sup_orcamento.agent_id, "supervisor_orcamento")
        mc.start()
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        health_params = self.working_memory.get("health_params", [])
        use_llm = self.working_memory.get("use_llm", True)
        intent_summary = self.working_memory.get("intent_summary")
        try:
            orcamento_data = sup_orcamento.run(
                analysis_id=analysis_id,
                date_from=date_from,
                date_to=date_to,
                health_params=health_params,
                intent_summary=intent_summary,
                use_llm=use_llm,
            )
            mc.stop()
            logger.info(
                "CoordenadorGeral %s: SupervisorOrcamento completed — %d despesas",
                self.agent_id, len(orcamento_data.get("despesas", [])),
            )
        except Exception as exc:
            mc.stop()
            logger.error(
                "CoordenadorGeral %s: SupervisorOrcamento failed — %s", self.agent_id, exc
            )
            self._send_error(f"SupervisorOrcamento falhou: {exc}")
            orcamento_data = {"despesas": [], "tendencias": {}}

        self.working_memory["_orcamento_data"] = orcamento_data
        self.working_memory["_metrics_collectors"].append(mc)

    def _act_delegar_saude(self, action: dict) -> None:
        """Ação externa: delega para SupervisorSaude.run()."""
        sup_saude = self.working_memory["_sup_saude"]
        mc = MetricsCollector(sup_saude.agent_id, "supervisor_saude")
        mc.start()
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        health_params = self.working_memory.get("health_params", [])
        intent_summary = self.working_memory.get("intent_summary")
        use_llm = self.working_memory.get("use_llm", True)
        try:
            saude_data = sup_saude.run(
                analysis_id=analysis_id,
                date_from=date_from,
                date_to=date_to,
                health_params=health_params,
                intent_summary=intent_summary,
                use_llm=use_llm,
            )
            mc.stop()
            logger.info(
                "CoordenadorGeral %s: SupervisorSaude completed — %d despesas, %d indicadores",
                self.agent_id,
                len(saude_data.get("despesas", [])),
                len(saude_data.get("indicadores", [])),
            )
        except Exception as exc:
            mc.stop()
            logger.error(
                "CoordenadorGeral %s: SupervisorSaude failed — %s", self.agent_id, exc
            )
            self._send_error(f"SupervisorSaude falhou: {exc}")
            saude_data = {"despesas": [], "indicadores": []}

        self.working_memory["_saude_data"] = saude_data
        self.working_memory["_metrics_collectors"].append(mc)

    def _act_combinar_despesas(self, action: dict) -> None:
        """Ação interna (reasoning): combina despesas de SupervisorOrcamento
        e SupervisorSaude.

        Desde a decomposição completa (Fase 2), SupervisorSaude nunca
        retorna despesas — a combinação/dedupe por (subfuncao, ano)
        permanece como salvaguarda estrutural, não porque haja overlap
        real esperado.
        """
        try:
            orcamento_data = self.working_memory.get("_orcamento_data", {})
            saude_data = self.working_memory.get("_saude_data", {})
            combinadas = deduplicate_despesas(
                list(orcamento_data.get("despesas", [])) + list(saude_data.get("despesas", []))
            )
            self.working_memory["_despesas_combinadas"] = combinadas
            logger.info(
                "[%s] CoordenadorGeral: %d despesas combinadas (orcamento + saúde)",
                self.agent_id, len(combinadas),
            )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_comunicar_saude_analitico(self, action: dict) -> None:
        """Comunicação lateral: SupervisorOrcamento + SupervisorSaude →
        SupervisorAnalitico (Req 10.5).

        Repassa despesas combinadas e indicadores para o pipeline
        analítico, acompanhados dos resumos textuais gerados por ambos
        os supervisores de origem (Etapa 5) — dá conteúdo semântico à
        comunicação lateral, não só transporte de dados brutos (D4 do
        PLANO_REFATORACAO.md).
        """
        sup_analitico = self.working_memory["_sup_analitico"]
        orcamento_data = self.working_memory.get("_orcamento_data", {})
        saude_data = self.working_memory.get("_saude_data", {})
        despesas = self.working_memory.get("_despesas_combinadas", [])
        resumo_orcamento = orcamento_data.get("resumo", "")
        resumo_saude = saude_data.get("resumo", "")
        resumo_dominio = " ".join(p for p in (resumo_orcamento, resumo_saude) if p)
        logger.info(
            "[%s] CoordenadorGeral: comunicação lateral Orcamento+Saude -> Analitico "
            "(%d despesas, %d indicadores, resumo=%r)",
            self.agent_id,
            len(despesas),
            len(saude_data.get("indicadores", [])),
            resumo_dominio,
        )
        sup_analitico.receive_from_peer({
            "despesas": despesas,
            "indicadores": saude_data.get("indicadores", []),
            "date_from": self.working_memory["date_from"],
            "date_to": self.working_memory["date_to"],
            "health_params": self.working_memory.get("health_params", []),
            "intent_summary": self.working_memory.get("intent_summary"),
            "resumo_dominio": resumo_dominio,
        })

    def _act_comunicar_orcamento_contexto(self, action: dict) -> None:
        """Comunicação lateral: despesas combinadas → SupervisorContexto (Req 10.5).

        Repassa despesas (orçamento + saúde combinadas) para análise de
        tendências orçamentárias — cobre todas as subfunções presentes,
        não só as que SupervisorOrcamento já instancia nesta fase.
        """
        sup_contexto = self.working_memory["_sup_contexto"]
        despesas = self.working_memory.get("_despesas_combinadas", [])
        logger.info(
            "[%s] CoordenadorGeral: comunicação lateral Orcamento+Saude -> Contexto (%d despesas)",
            self.agent_id, len(despesas),
        )
        sup_contexto.receive_from_peer({"despesas": despesas})

    def _act_delegar_contexto(self, action: dict) -> None:
        """Ação externa: delega para SupervisorContexto.run() (Req 10.4)."""
        sup_contexto = self.working_memory["_sup_contexto"]
        mc = MetricsCollector(sup_contexto.agent_id, "supervisor_contexto")
        mc.start()
        use_llm = self.working_memory.get("use_llm", True)
        try:
            contexto_data = sup_contexto.run(use_llm=use_llm)
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

        Repassa contexto orçamentário para enriquecer a síntese textual,
        acompanhado do resumo textual gerado por SupervisorContexto
        (Etapa 5) — ver docstring de `_act_comunicar_saude_analitico`.
        """
        sup_analitico = self.working_memory["_sup_analitico"]
        contexto_data = self.working_memory.get("_contexto_data", {})
        resumo_contexto = contexto_data.get("resumo", "")
        logger.info(
            "[%s] CoordenadorGeral: comunicação lateral Contexto -> Analitico "
            "(%d subfunções, resumo=%r)",
            self.agent_id, len(contexto_data.get("contexto_orcamentario", {})), resumo_contexto,
        )
        sup_analitico.receive_from_peer({
            "contexto_orcamentario": contexto_data.get("contexto_orcamentario", {}),
            "resumo_contexto": resumo_contexto,
        })

    def _act_delegar_analitico(self, action: dict) -> None:
        """Ação externa: delega para SupervisorAnalitico.run() (Req 10.3)."""
        sup_analitico = self.working_memory["_sup_analitico"]
        mc = MetricsCollector(sup_analitico.agent_id, "supervisor_analitico")
        mc.start()
        analysis_id = self.working_memory["analysis_id"]
        ws_queue = self.working_memory["_ws_queue"]
        use_llm = self.working_memory.get("use_llm", True)
        use_self_check = self.working_memory.get("use_self_check", False)
        try:
            analitico_data = sup_analitico.run(
                analysis_id=analysis_id, ws_queue=ws_queue, use_llm=use_llm,
                use_self_check=use_self_check,
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
        """Persiste métricas dos agentes + 4 supervisores e emite evento
        `metric` (Req 10.8, 11.2).

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
        sup_orcamento = self.working_memory["_sup_orcamento"]
        sup_saude = self.working_memory["_sup_saude"]
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
        for supervisor in (sup_orcamento, sup_saude, sup_analitico, sup_contexto):
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
        for supervisor in (sup_orcamento, sup_saude, sup_analitico, sup_contexto):
            for agent_mc in getattr(supervisor, "_collectors", []):
                try:
                    m = agent_mc.collect()
                    # Exclui sintetizador (serviço LLM, não agente CoALA),
                    # priorizacao (Etapa 3) e verificacao (Etapa 4) — todos
                    # rodam depois de capturar_wallclock e incluem chamada
                    # LLM, mesma lógica de exclusão.
                    if m.get("agentType") in ("sintetizador", "priorizacao", "verificacao"):
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

        # Subtrair tempo do sintetizador, da priorização (Etapa 3) e da
        # verificação pós-síntese (Etapa 4) — todos os collectors do
        # SupervisorAnalitico, todos incluem chamada LLM e rodam depois de
        # capturar_wallclock.
        sint_time_ms = 0.0
        prior_time_ms = 0.0
        verif_time_ms = 0.0
        for agent_mc in getattr(sup_analitico, "_collectors", []):
            try:
                m = agent_mc.collect()
                if m.get("agentType") == "sintetizador":
                    sint_time_ms = m.get("executionTimeMs", 0)
                elif m.get("agentType") == "priorizacao":
                    prior_time_ms = m.get("executionTimeMs", 0)
                elif m.get("agentType") == "verificacao":
                    verif_time_ms = m.get("executionTimeMs", 0)
            except Exception:
                pass

        wall_clock_ms = round(
            max(0, wall_clock_ms_raw - sint_time_ms - prior_time_ms - verif_time_ms), 2
        )
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

        Instancia os 4 supervisores com IDs únicos (Req 10.1), configura
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
        sup_orcamento_id = f"hier-sup-orcamento-{uuid.uuid4().hex[:8]}"
        sup_saude_id = f"hier-sup-saude-{uuid.uuid4().hex[:8]}"
        sup_analitico_id = f"hier-sup-analitico-{uuid.uuid4().hex[:8]}"
        sup_contexto_id = f"hier-sup-contexto-{uuid.uuid4().hex[:8]}"

        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "health_params": params.get("health_params", []),
            "use_llm": params.get("use_llm", True),
            "use_self_check": params.get("use_self_check", False),
            "intent_summary": params.get("intent_summary"),
            "_ws_queue": ws_queue,
            "_sup_orcamento": SupervisorOrcamento(sup_orcamento_id, self.neo4j_client),
            "_sup_saude": SupervisorSaude(sup_saude_id, self.neo4j_client),
            "_sup_analitico": SupervisorAnalitico(sup_analitico_id),
            "_sup_contexto": SupervisorContexto(sup_contexto_id),
            "_metrics_collectors": [],
            "_coord_start": time.time(),
        })

        self.run_coala_cycle()

        orcamento_data = self.working_memory.get("_orcamento_data", {})
        saude_data = self.working_memory.get("_saude_data", {})
        contexto_data = self.working_memory.get("_contexto_data", {})
        analitico_data = self.working_memory.get("_analitico_data", {})

        result: dict[str, Any] = dict(analitico_data)
        result.setdefault("correlacoes", [])
        result.setdefault("anomalias", [])
        result.setdefault("texto_analise", "")
        result["despesas"] = self.working_memory.get(
            "_despesas_combinadas", saude_data.get("despesas", [])
        )
        result["indicadores"] = saude_data.get("indicadores", [])
        result["contexto_orcamentario"] = contexto_data.get("contexto_orcamentario", {})

        self.working_memory["result"] = result
        logger.info("CoordenadorGeral %s: pipeline complete", self.agent_id)

        return result
