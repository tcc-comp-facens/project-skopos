"""
Coordenador geral da arquitetura hierárquica (Nível 0).

Delega para 4 supervisores especializados de nível 1:
- SupervisorOrcamento (agentes AgenteOrcamentoSubfuncao — 1 por
  subfunção, 7 no total)
- SupervisorSaude (8 agentes de saúde — SINAN, SIH, SIM, SI-PNI, COVID,
  SINASC, SIA, CNES)
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

Árvore estrita, sem canal lateral entre supervisores: um design anterior
tinha os supervisores trocando dados diretamente entre si (mediado pela
raiz, mas ainda uma troca par-a-par — `receive_from_peer`/`peer_data`,
inclusive com resumos textuais via LLM). Removido por decisão explícita:
isso fazia a "hierárquica" deste projeto não ser uma árvore no sentido
estrito. Hoje o `CoordenadorGeral` colhe o retorno de cada supervisor
(`SupervisorOrcamento`, `SupervisorSaude`, `SupervisorContexto`) e repassa
o que for necessário como **parâmetro direto** na chamada seguinte —
nenhum supervisor conhece ou escreve no estado de outro.

Degradação graciosa em falha de supervisor (Req 10.9), e contagem
de mensagens para comparação quantitativa (Reqs 11.1, 11.2).

Nota arquitetural (CoALA): o CoordenadorGeral executa pelo ciclo CoALA.
Cada etapa de delegação é uma ação nomeada em `procedural_memory`, e a
ordem reflete as dependências de dados entre supervisores (orçamento e
saúde, depois combinar despesas, contexto e analítico). Quais ramos
acionar é decisão do coordenador: `evaluate_and_select()` submete os
ramos ao LLM, que também escreve um subobjetivo em linguagem natural para
os ramos de orçamento e de saúde (`agents/planejamento.py`). Cada um
desses supervisores escolhe os seus subordinados a partir do próprio
subobjetivo. É o planejamento decomposto pelos níveis da árvore.

Requisitos: 10.1, 10.8, 10.9, 11.1, 11.2
"""

from __future__ import annotations

import logging
import time
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents import planejamento, revisao_entregas
from agents.base import ActionFailure, AgenteCoALA
from agents.data_crossing import deduplicate_despesas
from agents.hierarchical.supervisors import (
    SupervisorOrcamento,
    SupervisorSaude,
    SupervisorAnalitico,
    SupervisorContexto,
)
from core.metrics import MetricsCollector
from core.quality_metrics import papel_de

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


def _deliberacao_de(no: Any) -> float:
    """Tempo de deliberação por LLM de um supervisor (0 se não for número)."""
    valor = getattr(no, "deliberacao_ms", 0.0)
    return float(valor) if isinstance(valor, (int, float)) else 0.0


class CoordenadorGeral(AgenteCoALA):
    """Nível 0 da topologia hierárquica com 4 supervisores (Req 10).

    Delega para SupervisorOrcamento, SupervisorSaude, SupervisorAnalitico
    e SupervisorContexto. Coleta o retorno de cada supervisor e repassa o
    que for necessário como parâmetro direto ao próximo — árvore estrita,
    sem canal lateral entre supervisores. Agrega resultados, registra
    métricas por agente e supervisor (Req 10.8), e trata falhas com
    degradação graciosa (Req 10.9).

    Herda de AgenteCoALA e executa de fato pelo ciclo CoALA — cada etapa
    de delegação é uma ação registrada em `procedural_memory`, proposta em
    ordem fixa por `propose_actions()`.

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
            "delegar_contexto": [self._act_delegar_contexto],
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
        (orçamento + saúde → combinar despesas → contexto → analítico →
        persistência), não uma arbitragem entre candidatos concorrentes.
        """
        if not self.working_memory.get("analysis_id"):
            return []
        return [
            {"goal": "delegar_orcamento"},
            {"goal": "delegar_saude"},
            {"goal": "combinar_despesas"},
            {"goal": "delegar_contexto"},
            {"goal": "delegar_analitico"},
            {"goal": "persistir_metricas"},
        ]

    _RAMOS = [
        {"id": "ramo:orcamento", "descricao": "SupervisorOrcamento, que coordena um agente "
         "de despesas por subfunção orçamentária"},
        {"id": "ramo:saude", "descricao": "SupervisorSaude, que coordena um agente por "
         "sistema de informação em saúde"},
        {"id": "ramo:contexto", "descricao": "SupervisorContexto, que descreve o contexto "
         "orçamentário das despesas levantadas (depende do ramo de orçamento)"},
    ]
    _GOAL_DO_RAMO = {
        "delegar_orcamento": "ramo:orcamento",
        "delegar_saude": "ramo:saude",
        "delegar_contexto": "ramo:contexto",
    }

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Planeja com o LLM quais ramos acionar e o subobjetivo de cada um.

        A proposta são todos os ramos de dados. O LLM pode retirar um ramo,
        com justificativa, e escreve os subobjetivos dos ramos de orçamento
        e de saúde. O ramo analítico, que calcula e redige, é sempre
        acionado.
        """
        if not candidates:
            return []
        wm = self.working_memory
        indicadores = ", ".join(wm.get("health_params") or []) or "todos os disponíveis"
        periodo = f"{wm.get('date_from')} a {wm.get('date_to')}"
        plano = self._deliberar(
            planejamento.planejar,
            agent_id=self.agent_id,
            papel="o coordenador geral, que delega a supervisores de ramo",
            objetivo=planejamento.objetivo_da_analise(
                wm.get("intent_summary"), wm.get("health_params"),
                wm.get("date_from"), wm.get("date_to"),
            ),
            candidatos=self._RAMOS,
            proposta=[self._GOAL_DO_RAMO[c["goal"]] for c in candidates
                      if c["goal"] in self._GOAL_DO_RAMO],
            use_llm=wm.get("use_llm", True),
            subobjetivos_padrao={
                "ramo:orcamento": f"Levantar as despesas das subfunções ligadas aos "
                                  f"indicadores {indicadores}, de {periodo}.",
                "ramo:saude": f"Levantar os indicadores {indicadores}, de {periodo}.",
            },
            observacoes=planejamento.OBS_CRUZAMENTO + " O ramo analítico, que calcula "
            "e redige a resposta, é sempre acionado e não está na lista.",
        )
        wm["_plano_coordenador"] = plano
        return [
            dict(c, status="pending") for c in candidates
            if c["goal"] not in self._GOAL_DO_RAMO
            or self._GOAL_DO_RAMO[c["goal"]] in plano["escolhidos"]
        ]

    def _subobjetivo(self, ramo: str) -> str | None:
        return (self.working_memory.get("_plano_coordenador") or {}).get("subobjetivos", {}).get(ramo)

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
        objetivo = self._subobjetivo("ramo:orcamento")
        try:
            orcamento_data = self._delegar(
                "supervisor_orcamento", sup_orcamento.agent_id,
                lambda: sup_orcamento.run(
                analysis_id=analysis_id,
                date_from=date_from,
                date_to=date_to,
                health_params=health_params,
                intent_summary=intent_summary,
                use_llm=use_llm,
                objetivo=objetivo,
            ),
                pedido=objetivo or {"periodo": [date_from, date_to], "indicadores": health_params},
                natureza_pedido="texto" if objetivo else "dados",
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
        objetivo = self._subobjetivo("ramo:saude")
        try:
            saude_data = self._delegar(
                "supervisor_saude", sup_saude.agent_id,
                lambda: sup_saude.run(
                analysis_id=analysis_id,
                date_from=date_from,
                date_to=date_to,
                health_params=health_params,
                intent_summary=intent_summary,
                use_llm=use_llm,
                objetivo=objetivo,
            ),
                pedido=objetivo or {"periodo": [date_from, date_to], "indicadores": health_params},
                natureza_pedido="texto" if objetivo else "dados",
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

    def _act_delegar_contexto(self, action: dict) -> None:
        """Ação externa: delega para SupervisorContexto.run() (Req 10.4).

        Repassa as despesas combinadas (orçamento + saúde) como parâmetro
        direto — o coordenador colheu esse dado de `_act_combinar_despesas`,
        não há canal lateral entre supervisores.
        """
        sup_contexto = self.working_memory["_sup_contexto"]
        mc = MetricsCollector(sup_contexto.agent_id, "supervisor_contexto")
        mc.start()
        despesas = self.working_memory.get("_despesas_combinadas", [])
        use_llm = self.working_memory.get("use_llm", True)
        try:
            contexto_data = self._delegar(
                "supervisor_contexto", sup_contexto.agent_id,
                lambda: sup_contexto.run(despesas=despesas, use_llm=use_llm),
                pedido={"despesas": despesas}, natureza_resposta="dados", extrair=None,
            )
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

    def _act_delegar_analitico(self, action: dict) -> None:
        """Ação externa: delega para SupervisorAnalitico.run() (Req 10.3).

        Repassa despesas combinadas, indicadores (de SupervisorSaude) e
        contexto orçamentário (de SupervisorContexto) como parâmetros
        diretos — o coordenador colheu tudo do retorno dos supervisores de
        origem, sem canal lateral entre eles.
        """
        sup_analitico = self.working_memory["_sup_analitico"]
        mc = MetricsCollector(sup_analitico.agent_id, "supervisor_analitico")
        mc.start()
        analysis_id = self.working_memory["analysis_id"]
        ws_queue = self.working_memory["_ws_queue"]
        use_llm = self.working_memory.get("use_llm", True)
        use_self_check = self.working_memory.get("use_self_check", False)
        despesas = self.working_memory.get("_despesas_combinadas", [])
        saude_data = self.working_memory.get("_saude_data", {})
        contexto_data = self.working_memory.get("_contexto_data", {})
        try:
            analitico_data = self._delegar(
                "supervisor_analitico", sup_analitico.agent_id,
                lambda: sup_analitico.run(
                analysis_id=analysis_id,
                ws_queue=ws_queue,
                despesas=despesas,
                indicadores=saude_data.get("indicadores", []),
                contexto_orcamentario=contexto_data.get("contexto_orcamentario", {}),
                date_from=self.working_memory.get("date_from"),
                date_to=self.working_memory.get("date_to"),
                health_params=self.working_memory.get("health_params", []),
                intent_summary=self.working_memory.get("intent_summary"),
                use_llm=use_llm,
                use_self_check=use_self_check,
            ),
                pedido={"despesas": despesas, "indicadores": saude_data.get("indicadores", [])}, natureza_resposta="texto", extrair=lambda r: r.get("texto_analise"),
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

        Overhead = wall_clock determinístico - soma dos agentes FOLHA (nível 2),
        a mesma definição da estrela. Supervisores NÃO entram em workers_time
        porque seu tempo já engloba o dos subordinados — somar ambos causaria
        dupla contagem; aparecem em `agent_metrics` só para exibição. O
        overhead captura: tempo dos supervisores fora dos subordinados +
        instanciação (não há mais tempo de comunicação lateral a capturar —
        a árvore agora é estrita).
        """
        persist_entry = time.time()  # fallback do relógio se o analítico não chegou a fechá-lo
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
                    "papel": papel_de(m["agentType"]),
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
                    if m.get("agentType") in ("sintetizador", "priorizacao", "verificacao", "redator_ramo"):
                        continue
                    agent_metrics.append({
                        "agentName": m["agentType"],
                        "papel": papel_de(m["agentType"]),
                        "executionTimeMs": m["executionTimeMs"],
                        "cpuPercent": m["cpuPercent"],
                    })
                    workers_time_ms += m["executionTimeMs"]
                except Exception:
                    pass

        # Wall-clock determinístico: do início do coordenador até o fim da parte
        # determinística do SupervisorAnalitico (`capturar_wallclock`) — o mesmo
        # ponto em que a estrela fecha o relógio (`_orch_end`). Fica de fora
        # tudo o que vem depois: priorização, síntese e verificação (LLM) e a
        # própria gravação das métricas no Neo4j (que antes entrava no relógio
        # só da hierárquica e aparecia como "overhead").
        coord_start = self.working_memory.get("_coord_start", time.time())
        leaf_end = getattr(sup_analitico, "_coala_leaf_end_time", None)
        has_leaf_end = isinstance(leaf_end, (int, float)) and leaf_end >= coord_start
        coord_end = leaf_end if has_leaf_end else persist_entry
        wall_clock_ms = round(max(0, (coord_end - coord_start) * 1000), 2)
        overhead_ms = round(max(0, wall_clock_ms - workers_time_ms), 2)
        # Parte do overhead gasta esperando o LLM: o planejamento do
        # coordenador e o planejamento e a revisão dos supervisores de dados.
        # O restante é custo estrutural.
        deliberacao_ms = round(min(
            self.deliberacao_ms
            + _deliberacao_de(self.working_memory.get("_sup_orcamento"))
            + _deliberacao_de(self.working_memory.get("_sup_saude")),
            overhead_ms,
        ), 2)

        agent_metrics.append({
            "agentName": "coordenador_geral",
            "papel": "agente",
            "executionTimeMs": overhead_ms,
            "deliberacaoMs": deliberacao_ms,
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
                "deliberationTimeMs": deliberacao_ms,
                "agentMetrics": agent_metrics,
            },
        })

        orcamento_data = self.working_memory.get("_orcamento_data", {})
        saude_data = self.working_memory.get("_saude_data", {})
        agent_queries = orcamento_data.get("agent_queries", []) + saude_data.get(
            "agent_queries", []
        )
        ws_queue.put({
            "analysisId": analysis_id,
            "architecture": "hierarchical",
            "type": "agent_data",
            "payload": {"architecture": "hierarchical", "agents": agent_queries},
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
            "_plano_coordenador": None,
        })

        # Registro das trocas desta topologia (FIPA-ACL), compartilhado com
        # os supervisores: cada nó registra as mensagens que troca.
        self.registro_mensagens = []
        self.deliberacao_ms = 0.0
        for chave in ("_sup_orcamento", "_sup_saude", "_sup_analitico", "_sup_contexto"):
            self.working_memory[chave].registro_mensagens = self.registro_mensagens
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
        result["agent_queries"] = orcamento_data.get("agent_queries", []) + saude_data.get(
            "agent_queries", []
        )
        # Cada supervisor de dados revisou o próprio ramo; o coordenador não
        # recebe entregas de agentes de dados e só soma os dois resumos.
        result["revisao"] = revisao_entregas.combinar_resumos(
            [orcamento_data.get("revisao"), saude_data.get("revisao")]
        )
        # Do início do run() até o texto final, redação incluída — o tempo
        # de parede do evento `metric` para antes da redação.
        result["mensagens"] = list(self.registro_mensagens)
        result["plano"] = {
            no: plano for no, plano in (
                ("coordenador", self.working_memory.get("_plano_coordenador")),
                ("supervisor_orcamento", orcamento_data.get("plano")),
                ("supervisor_saude", saude_data.get("plano")),
            ) if plano
        }
        result["tempo_total_ms"] = round(
            (time.time() - self.working_memory["_coord_start"]) * 1000, 1
        )

        self.working_memory["result"] = result
        logger.info("CoordenadorGeral %s: pipeline complete", self.agent_id)

        return result
