"""
Supervisores da arquitetura hierárquica (Nível 1).

Três supervisores especializados coordenam os 8 agentes de nível 2:

- **SupervisorDominio** coordena 4 agentes de domínio (Req 10.2).
- **SupervisorAnalitico** coordena 3 agentes analíticos (Req 10.3).
- **SupervisorContexto** coordena AgenteContextoOrcamentario (Req 10.4).

Todos implementam ``receive_from_peer`` para comunicação lateral
direta entre supervisores do mesmo nível, sem intermediação do
CoordenadorGeral (Reqs 10.5, 10.6) — tratada como uma ação externa de
comunicação que escreve numa região nomeada da working memory
(``peer_data``), sem passar pelo ciclo `propose_actions`/`execute` de
quem recebe (é o par que empurra o dado, não o receptor que o busca).

Cada supervisor executa de fato pelo ciclo CoALA: `run()` configura a
working memory e delega a `run_coala_cycle()`, que percorre as
macro-ações registradas em `procedural_memory` na ordem proposta por
`propose_actions` — a mesma degradação graciosa e coleta de métricas por
agente subordinado que o pipeline imperativo anterior já tinha.

Requisitos: 10.2, 10.3, 10.4, 10.5, 10.6, 10.7
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
INDICADOR_TO_AGENT: dict[str, str] = {
    "dengue": "vigilancia_epidemiologica",
    "covid": "vigilancia_epidemiologica",
    "internacoes": "saude_hospitalar",
    "vacinacao": "atencao_primaria",
    "mortalidade": "mortalidade",
}

# Ordem fixa de avaliação dos agentes de domínio
_DOMAIN_AGENT_KEY_ORDER: list[str] = ["vigilancia", "hospitalar", "primaria", "mortalidade"]
_AGENT_KEY_TO_TYPE: dict[str, str] = {
    "vigilancia": "vigilancia_epidemiologica",
    "hospitalar": "saude_hospitalar",
    "primaria": "atencao_primaria",
    "mortalidade": "mortalidade",
}


class SupervisorDominio(AgenteCoALA):
    """Supervisor de domínio — nível 1 da topologia hierárquica (Req 10.2).

    Coordena 4 agentes de domínio (nível 2):
    AgenteVigilanciaEpidemiologica, AgenteSaudeHospitalar,
    AgenteAtencaoPrimaria e AgenteMortalidade.

    Executa os agentes ativos (conforme health_params) em sequência via
    macro-ações registradas em `procedural_memory`, agrega resultados de
    despesas e indicadores (Req 10.7), e disponibiliza os dados para
    comunicação lateral via ``receive_from_peer`` (Req 10.5).

    Attributes:
        neo4j_client: Cliente Neo4j repassado aos agentes de domínio.
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.peer_data: dict[str, Any] = {}
        self.semantic_memory = {"indicador_to_agent": INDICADOR_TO_AGENT}
        self.procedural_memory = {
            "consultar_dominio": [self._act_consultar_dominio],
            "agregar_resultados": [self._act_agregar_resultados],
        }
        self._collectors: list[MetricsCollector] = []

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe parâmetros da análise a partir da working memory."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe consultar os agentes de domínio ativos e agregar o resultado.

        Ativa apenas os agentes de domínio cujos indicadores estão
        presentes em health_params (retrieval de `semantic_memory`). Se
        health_params for None ou vazio, ativa todos (comportamento legado).
        """
        actions: list[dict] = []
        health_params = self.working_memory.get("health_params")
        indicador_to_agent = self.semantic_memory["indicador_to_agent"]

        if health_params:
            active_agent_types: set[str] = set()
            for hp in health_params:
                agent_type = indicador_to_agent.get(hp)
                if agent_type:
                    active_agent_types.add(agent_type)
            keys = [k for k in _DOMAIN_AGENT_KEY_ORDER if _AGENT_KEY_TO_TYPE[k] in active_agent_types]
        else:
            keys = list(_DOMAIN_AGENT_KEY_ORDER)

        for key in keys:
            actions.append({"goal": "consultar_dominio", "agent_key": key})

        logger.info(
            "SupervisorDominio %s: activating %d/%d domain agents for health_params=%s",
            self.agent_id,
            len(keys),
            len(_DOMAIN_AGENT_KEY_ORDER),
            health_params,
        )

        actions.append({"goal": "agregar_resultados"})
        return actions

    # -- Ações ------------------------------------------------------------

    def _act_consultar_dominio(self, action: dict) -> None:
        """Ação externa: consulta um agente de domínio subordinado (Req 10.2).

        Resolve a classe do agente pelo nome global no momento da chamada,
        para permitir substituição via `unittest.mock.patch` em testes.
        """
        specs = {
            "vigilancia": ("vigilancia_epidemiologica", AgenteVigilanciaEpidemiologica, "hier-vigilancia"),
            "hospitalar": ("saude_hospitalar", AgenteSaudeHospitalar, "hier-hospitalar"),
            "primaria": ("atencao_primaria", AgenteAtencaoPrimaria, "hier-primaria"),
            "mortalidade": ("mortalidade", AgenteMortalidade, "hier-mortalidade"),
        }
        agent_type, agent_cls, id_prefix = specs[action["agent_key"]]
        agent_id_str = f"{id_prefix}-{uuid.uuid4().hex[:8]}"
        agent = agent_cls(agent_id_str, self.neo4j_client)

        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]

        mc = MetricsCollector(agent_id_str, agent_type)
        mc.start()
        try:
            result = agent.query(analysis_id, date_from, date_to)
            mc.stop()
            self.working_memory.setdefault("despesas", []).extend(result.get("despesas", []))
            self.working_memory.setdefault("indicadores", []).extend(result.get("indicadores", []))
            logger.info(
                "SupervisorDominio %s: %s returned %d despesas, %d indicadores",
                self.agent_id,
                agent_type,
                len(result.get("despesas", [])),
                len(result.get("indicadores", [])),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            # Graceful degradation: exclude failed agent, continue
            logger.error(
                "SupervisorDominio %s: %s failed — %s", self.agent_id, agent_type, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_agregar_resultados(self, action: dict) -> None:
        """Ação interna (reasoning): deduplica despesas e agrega o resultado (Req 10.7)."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            unique_despesas = deduplicate_despesas(despesas)

            aggregated = {"despesas": unique_despesas, "indicadores": indicadores}
            self.working_memory["despesas"] = unique_despesas
            self.working_memory["aggregated"] = aggregated
            logger.info(
                "SupervisorDominio %s: aggregated %d despesas, %d indicadores",
                self.agent_id,
                len(unique_despesas),
                len(indicadores),
            )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    # -- Comunicação lateral (Req 10.5) ------------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação).

        Args:
            data: Dicionário com dados enviados pelo supervisor par.
        """
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorDominio %s: received peer data with keys %s",
            self.agent_id,
            list(data.keys()),
        )

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        date_from: int | None,
        date_to: int | None,
        health_params: list[str] | None = None,
        intent_summary: str | None = None,
    ) -> dict[str, Any]:
        """Executa o pipeline de domínio via agentes subordinados.

        Configura a working memory e delega ao ciclo CoALA, que percorre
        as macro-ações registradas em `procedural_memory` (Req 10.2, 10.7).

        Args:
            analysis_id: UUID da análise.
            date_from: Ano inicial do período.
            date_to: Ano final do período.
            health_params: Lista de tipos de indicador selecionados pelo usuário.
            intent_summary: Resumo da intenção do usuário (AgenteInterpretacaoIntencao,
                Etapa 1 do plano de refatoração) — insumo para a construção
                de queries via LLM dos agentes de domínio (Etapa 2, ainda
                não implementada; armazenado aqui para uso futuro).

        Returns:
            Dicionário com "despesas" e "indicadores" agregados.
        """
        self._collectors = []
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
            "health_params": health_params,
            "intent_summary": intent_summary,
            "despesas": [],
            "indicadores": [],
        })

        self.run_coala_cycle()

        return self.working_memory.get("aggregated", {"despesas": [], "indicadores": []})


class SupervisorAnalitico(AgenteCoALA):
    """Supervisor analítico — nível 1 da topologia hierárquica (Req 10.3).

    Coordena 3 agentes analíticos (nível 2): AgenteCorrelacao,
    AgenteAnomalias e TextSynthesizer.

    Recebe dados de domínio e contexto orçamentário via
    ``receive_from_peer`` (Reqs 10.5, 10.6), cruza dados usando
    ``cross_domain_data()``, e executa o pipeline analítico via macro-ações
    registradas em `procedural_memory`: cruzamento → gaps → correlação →
    anomalias → sintetizador.

    Attributes:
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.peer_data: dict[str, Any] = {}
        self.procedural_memory = {
            "cruzar_dados": [self._act_cruzar_dados],
            "detectar_gaps": [self._act_detectar_gaps],
            "calcular_correlacoes": [self._act_calcular_correlacoes],
            "detectar_anomalias": [self._act_detectar_anomalias],
            "capturar_wallclock": [self._act_capturar_wallclock],
            "sintetizar_texto": [self._act_sintetizar_texto],
        }
        self._collectors: list[MetricsCollector] = []
        # Marca o fim da parte determinística (antes do sintetizador/LLM) —
        # usado pelo CoordenadorGeral para excluir o tempo do LLM do overhead.
        self._coala_leaf_end_time: float | None = None

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe dados disponíveis (recebidos dos peers ou já na working memory)."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "despesas": self.peer_data.get("despesas", []),
            "indicadores": self.peer_data.get("indicadores", []),
            "contexto_orcamentario": self.peer_data.get("contexto_orcamentario", {}),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe o pipeline analítico completo, se há análise e fila WS configuradas."""
        if not (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("_ws_queue") is not None
        ):
            return []
        return [
            {"goal": "cruzar_dados"},
            {"goal": "detectar_gaps"},
            {"goal": "calcular_correlacoes"},
            {"goal": "detectar_anomalias"},
            {"goal": "capturar_wallclock"},
            {"goal": "sintetizar_texto"},
        ]

    # -- Comunicação lateral (Reqs 10.5, 10.6) ------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação).

        Tipicamente chamado pelo CoordenadorGeral para repassar dados
        do SupervisorDominio (despesas, indicadores) e do
        SupervisorContexto (contexto_orcamentario).

        Args:
            data: Dicionário com dados enviados pelo supervisor par.
        """
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorAnalitico %s: received peer data with keys %s",
            self.agent_id,
            list(data.keys()),
        )

    # -- Ações --------------------------------------------------------------

    def _act_cruzar_dados(self, action: dict) -> None:
        """Ação interna (reasoning): cruza dados de domínio (Req 10.5)."""
        try:
            despesas = self.peer_data.get("despesas", [])
            indicadores = self.peer_data.get("indicadores", [])
            dados_cruzados = cross_domain_data(despesas, indicadores)
            self.working_memory["dados_cruzados"] = dados_cruzados
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_gaps(self, action: dict) -> None:
        """Ação interna (reasoning): detecta lacunas de dados para transparência."""
        try:
            despesas = self.peer_data.get("despesas", [])
            indicadores = self.peer_data.get("indicadores", [])
            date_from = self.peer_data.get("date_from")
            date_to = self.peer_data.get("date_to")
            health_params = self.peer_data.get("health_params")
            data_coverage: dict = {}
            if date_from is not None and date_to is not None:
                data_coverage = detect_data_gaps(
                    despesas, indicadores, date_from, date_to, health_params
                )
                if data_coverage.get("summary", {}).get("has_gaps"):
                    logger.warning(
                        "SupervisorAnalitico %s: %d data gaps detected",
                        self.agent_id,
                        data_coverage["summary"]["total_gaps"],
                    )
            self.working_memory["data_coverage"] = data_coverage
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_calcular_correlacoes(self, action: dict) -> None:
        """Ação externa: delega a AgenteCorrelacao."""
        corr_id = f"hier-correlacao-{uuid.uuid4().hex[:8]}"
        agente_correlacao = AgenteCorrelacao(corr_id)
        mc = MetricsCollector(corr_id, "correlacao")
        mc.start()
        try:
            dados_cruzados = self.working_memory.get("dados_cruzados", [])
            correlacoes = agente_correlacao.compute(dados_cruzados)
            mc.stop()
            self.working_memory["correlacoes"] = correlacoes
            logger.info(
                "SupervisorAnalitico %s: computed %d correlações",
                self.agent_id,
                len(correlacoes),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorAnalitico %s: correlacao failed — %s", self.agent_id, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_anomalias(self, action: dict) -> None:
        """Ação externa: delega a AgenteAnomalias."""
        anom_id = f"hier-anomalias-{uuid.uuid4().hex[:8]}"
        agente_anomalias = AgenteAnomalias(anom_id)
        mc = MetricsCollector(anom_id, "anomalias")
        mc.start()
        try:
            dados_cruzados = self.working_memory.get("dados_cruzados", [])
            anomalias = agente_anomalias.detect(dados_cruzados)
            mc.stop()
            self.working_memory["anomalias"] = anomalias
            logger.info(
                "SupervisorAnalitico %s: detected %d anomalias",
                self.agent_id,
                len(anomalias),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorAnalitico %s: anomalias failed — %s", self.agent_id, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_capturar_wallclock(self, action: dict) -> None:
        """Ação interna (bookkeeping): marca o fim da parte determinística (antes do LLM)."""
        self._coala_leaf_end_time = time.time()

    def _act_sintetizar_texto(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): gera o texto via TextSynthesizer."""
        sint_id = f"hier-sintetizador-{uuid.uuid4().hex[:8]}"
        sintetizador = TextSynthesizer(sint_id)
        mc = MetricsCollector(sint_id, "sintetizador")
        mc.start()
        texto_analise = ""
        try:
            ws_queue = self.working_memory["_ws_queue"]
            analysis_id = self.working_memory["analysis_id"]
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.peer_data.get("contexto_orcamentario", {})
            data_coverage = self.working_memory.get("data_coverage")
            use_llm = self.working_memory.get("use_llm", True)

            adapter = StreamingAdapter(ws_queue, analysis_id, "hierarchical")

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
                        texto_analise = sintetizador.generate_fallback(
                            correlacoes, anomalias, contexto_orcamentario, data_coverage
                        )
                        adapter.stream_text(texto_analise)
                except Exception:
                    logger.warning(
                        "SupervisorAnalitico %s: LLM streaming failed, using fallback",
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
                "SupervisorAnalitico %s: synthesis complete (%d chars)",
                self.agent_id,
                len(texto_analise),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorAnalitico %s: sintetizador failed — %s", self.agent_id, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        ws_queue: Queue,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Executa o pipeline analítico via 3 agentes subordinados.

        Espera que ``receive_from_peer`` já tenha sido chamado com
        despesas, indicadores e contexto_orcamentario. Configura a working
        memory e delega ao ciclo CoALA (Req 10.3).

        Args:
            analysis_id: UUID da análise em andamento.
            ws_queue: Fila para streaming de eventos WebSocket.
            use_llm: Se True, tenta gerar texto via LLM antes do fallback.

        Returns:
            Dicionário com "correlacoes", "anomalias", "texto_analise",
            "data_coverage" e "dados_cruzados".
        """
        self._collectors = []
        self._coala_leaf_end_time = None
        self.update_working_memory({
            "analysis_id": analysis_id,
            "_ws_queue": ws_queue,
            "use_llm": use_llm,
        })

        self.run_coala_cycle()

        result = {
            "correlacoes": self.working_memory.get("correlacoes", []),
            "anomalias": self.working_memory.get("anomalias", []),
            "texto_analise": self.working_memory.get("texto_analise", ""),
            "data_coverage": self.working_memory.get("data_coverage", {}),
            "dados_cruzados": self.working_memory.get("dados_cruzados", []),
        }

        self.working_memory["aggregated"] = result
        logger.info(
            "SupervisorAnalitico %s: pipeline complete — %d correlacoes, %d anomalias",
            self.agent_id,
            len(result["correlacoes"]),
            len(result["anomalias"]),
        )

        return result


class SupervisorContexto(AgenteCoALA):
    """Supervisor de contexto — nível 1 da topologia hierárquica (Req 10.4).

    Coordena o AgenteContextoOrcamentario (nível 2) para análise de
    tendências temporais de gasto orçamentário.

    Recebe despesas do SupervisorDominio via ``receive_from_peer``
    (Req 10.6) e executa a análise de tendências via ciclo CoALA.

    Attributes:
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.peer_data: dict[str, Any] = {}
        self.procedural_memory = {
            "executar_contexto_orcamentario": [self._act_executar_contexto_orcamentario],
        }
        self._collectors: list[MetricsCollector] = []

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe dados disponíveis (recebidos dos peers)."""
        return {
            "despesas": self.peer_data.get("despesas", []),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe executar a análise de tendências se há despesas disponíveis."""
        if self.peer_data.get("despesas"):
            return [{"goal": "executar_contexto_orcamentario"}]
        return []

    # -- Comunicação lateral (Req 10.6) ------------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação).

        Tipicamente chamado pelo CoordenadorGeral para repassar
        despesas do SupervisorDominio.

        Args:
            data: Dicionário com "despesas" do supervisor par.
        """
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorContexto %s: received peer data with keys %s",
            self.agent_id,
            list(data.keys()),
        )

    # -- Ações --------------------------------------------------------------

    def _act_executar_contexto_orcamentario(self, action: dict) -> None:
        """Ação externa: delega a AgenteContextoOrcamentario."""
        ctx_id = f"hier-contexto-{uuid.uuid4().hex[:8]}"
        agente_contexto = AgenteContextoOrcamentario(ctx_id)
        mc = MetricsCollector(ctx_id, "contexto_orcamentario")
        mc.start()
        try:
            despesas = self.peer_data.get("despesas", [])
            contexto_orcamentario = agente_contexto.analyze_trends(despesas)
            mc.stop()
            self.working_memory["contexto_orcamentario"] = contexto_orcamentario
            logger.info(
                "SupervisorContexto %s: computed trends for %d subfunções",
                self.agent_id,
                len(contexto_orcamentario),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorContexto %s: contexto_orcamentario failed — %s",
                self.agent_id,
                exc,
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(self) -> dict[str, Any]:
        """Executa a análise de contexto orçamentário via subordinado.

        Espera que ``receive_from_peer`` já tenha sido chamado com
        as despesas do SupervisorDominio. Delega ao ciclo CoALA (Req 10.4).

        Returns:
            Dicionário com "contexto_orcamentario".
        """
        self._collectors = []
        self.run_coala_cycle()

        result = {
            "contexto_orcamentario": self.working_memory.get("contexto_orcamentario", {}),
        }

        self.working_memory["aggregated"] = result
        logger.info("SupervisorContexto %s: pipeline complete", self.agent_id)

        return result
