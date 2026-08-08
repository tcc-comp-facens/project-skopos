"""
Agente de Domínio — SINAN (Sistema de Informação de Agravos de Notificação).

Especializado nos 9 agravos de notificação compulsória cobertos pelo
SINAN (dengue, chikungunya, sífilis adquirida/gestante/congênita,
coqueluche, hepatites virais, tuberculose, hanseníase). Consulta
`IndicadorSaude(sistema="sinan")` no Neo4j via `neo4j_client`.

Substitui a metade "dengue" do antigo `AgenteVigilanciaEpidemiologica`
(a metade "covid" migra separadamente — ver
`agents/domain/vigilancia_epidemiologica.py`) — decisão de particionamento
em PLANO_NOVO_MODELO_DADOS.md §5 (1 agente de saúde por Sistema de
Informação).

Agente de referência da Fase 1: implementa o padrão completo de
deliberação real que os outros 7 agentes de saúde ainda a construir
devem replicar — `propose_actions`/`evaluate_and_select` escolhem de
fato, entre candidatos concorrentes, por qual dimensão quebrar a
consulta (sem quebra / por faixa etária / por sexo), em vez do
passthrough que a maioria dos agentes do sistema ainda usa. Isso *é* a
"consulta dinâmica a partir do input do chat" pedida — o LLM decide o
quê filtrar (dimensão), nunca escreve Cypher (ver `db/query_builder.py`).

Diferente dos agentes de domínio legados, este agente NÃO consulta
despesas — orçamento é responsabilidade de `AgenteOrcamentoSubfuncao`
(1 agente por subfunção), consultado separadamente pelo
orquestrador/supervisor.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from agents.domain.query_planning import arbitrar_dimensao, propose_dimensao_candidatos
from db.query_builder import dimensoes_validas

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

SISTEMA = "sinan"
SUBTIPOS: list[str] = [
    "dengue",
    "chikungunya",
    "sifilis_adquirida",
    "sifilis_gestante",
    "sifilis_congenita",
    "coqueluche",
    "hepatites_virais",
    "tuberculose",
    "hanseniase",
]


class AgenteSINAN(AgenteCoALA):
    """Agente de domínio especializado no SINAN — 9 doenças de notificação
    compulsória.

    Implementa deliberação real (Fase 1 — reforço de rigor CoALA):
    `propose_actions` propõe um candidato de consulta por dimensão válida
    (`POR_FAIXA_ETARIA`, `POR_SEXO`) + "sem quebra"; `evaluate_and_select`
    arbitra entre eles via score determinístico + LLM opcional
    (`agents.domain.query_planning.arbitrar_dimensao`), mesmo padrão de
    dois estágios de `AgentePriorizacaoAnalitica`.

    Attributes:
        neo4j_client: Cliente Neo4j para queries Cypher.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.semantic_memory = {
            "sistema": SISTEMA,
            "subtipos": list(SUBTIPOS),
            "dimensoes_validas": dimensoes_validas(SISTEMA),
        }
        self.procedural_memory = {
            "consultar_indicadores": [
                self._act_consultar_indicadores,
                self._act_fallback_indicadores,
            ],
        }

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe um candidato de consulta por dimensão válida — mais de
        um candidato real (diferente da maioria dos agentes do sistema,
        cujo `propose_actions` só monta um pipeline fixo), de fato
        arbitrado por `evaluate_and_select`."""
        if not (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("date_from") is not None
        ):
            return []
        candidatos_dimensao = propose_dimensao_candidatos(
            self.semantic_memory["dimensoes_validas"]
        )
        return [
            {
                "goal": "consultar_indicadores",
                "dimensao": c["dimensao"],
                "descricao": c["descricao"],
            }
            for c in candidatos_dimensao
        ]

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Arbitra entre os candidatos de dimensão propostos (Fase 1).

        Só um candidato é de fato executado — evita N consultas ao Neo4j
        por análise. Delega a arbitragem (score determinístico + LLM
        opcional, com fallback ao maior score) a
        `agents.domain.query_planning.arbitrar_dimensao`.
        """
        if not candidates:
            return []
        escolhido, origem = arbitrar_dimensao(
            agent_id=self.agent_id,
            candidatos=candidates,
            intent_summary=self.working_memory.get("intent_summary"),
            use_llm=self.working_memory.get("use_llm", True),
        )
        logger.info(
            "Agent %s: dimensão de consulta escolhida via %s: %s",
            self.agent_id, origem, escolhido["dimensao"],
        )
        return [dict(escolhido, status="pending")]

    def _act_consultar_indicadores(self, action: dict) -> None:
        """Ação externa (grounding): consulta `IndicadorSaude(sistema="sinan")`
        no Neo4j, com a dimensão escolhida por `evaluate_and_select`.

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        dimensao = action.get("dimensao")
        subtipos = list(self.semantic_memory["subtipos"])

        try:
            logger.info(
                "Agent %s: consultando indicadores (sistema=sinan, subtipos=%s, "
                "dimensao=%s, periodo=%s-%s)",
                self.agent_id, subtipos, dimensao, date_from, date_to,
            )
            indicadores = self.neo4j_client.get_indicadores_por_sistema(
                self.semantic_memory["sistema"], subtipos, date_from, date_to, dimensao,
            )
            self.working_memory["indicadores"] = indicadores
            self.working_memory["dimensao_escolhida"] = dimensao
            logger.info(
                "Agent %s: retrieved %d indicadores (dimensao=%s)",
                self.agent_id, len(indicadores), dimensao,
            )
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_fallback_indicadores(self, action: dict) -> None:
        """Estratégia de fallback: grava lista vazia em working memory.

        Permite que o orquestrador/supervisor continue com dados parciais
        em vez de propagar a falha da consulta ao Neo4j.
        """
        self.working_memory["indicadores"] = []
        logger.warning("Agent %s: fallback — returning empty indicadores", self.agent_id)

    # -- Interface pública --------------------------------------------------

    def query(
        self,
        analysis_id: str,
        date_from: int,
        date_to: int,
        intent_summary: str | None = None,
        health_params: list[str] | None = None,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Consulta indicadores SINAN (9 doenças), sem despesas.

        Método de conveniência chamado pelo orquestrador/supervisor.
        Diferente dos agentes de domínio legados, não retorna "despesas"
        — orçamento é responsabilidade de `AgenteOrcamentoSubfuncao`,
        consultado separadamente.

        Args:
            analysis_id: ID da análise em andamento.
            date_from: Ano de início do período.
            date_to: Ano de fim do período.
            intent_summary: Resumo da intenção do usuário — insumo da
                deliberação de dimensão.
            health_params: Indicadores solicitados na análise (não usado
                para filtrar SUBTIPOS nesta fase — o agente, uma vez
                ativado, cobre todo o seu domínio, mesmo padrão dos
                agentes legados).
            use_llm: Se False, a deliberação de dimensão decide só pelo
                score determinístico — sem chamada LLM.

        Returns:
            Dicionário com chave "indicadores". Lista vazia se não houver
            dados ou se a consulta falhar.
        """
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
            "intent_summary": intent_summary,
            "health_params": health_params,
            "use_llm": use_llm,
        })

        self.run_coala_cycle()

        return {"indicadores": self.working_memory.get("indicadores", [])}
