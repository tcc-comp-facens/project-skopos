"""
Agente de Domínio — SIA (Sistema de Informações Ambulatoriais).

Especializado em produção ambulatorial. Consulta
`IndicadorSaude(sistema="sia")` no Neo4j via `neo4j_client`.

Cobertura nova (Fase 2) — sem agente legado equivalente a substituir,
decisão de particionamento em PLANO_NOVO_MODELO_DADOS.md §5 (1 agente de
saúde por Sistema de Informação). `sistema="sia"` não tem dimensões
válidas (`SISTEMA_DIMENSOES["sia"] == []`), então este agente não
delibera entre candidatos — sempre consulta sem quebra dimensional,
mesma justificativa documentada em `AgenteCOVID`/`AgenteOrcamentoSubfuncao`.

Replica o padrão de referência de `agente_sinan.py`, sem a deliberação
de dimensão (não aplicável aqui).
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from db.query_builder import dimensoes_validas

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

SISTEMA = "sia"
SUBTIPOS: list[str] = ["producao_ambulatorial"]


class AgenteSIA(AgenteCoALA):
    """Agente de domínio especializado no SIA — produção ambulatorial.

    `sistema="sia"` não tem dimensões válidas no schema atual — não há
    "por qual dimensão consultar" a decidir. `evaluate_and_select` herda
    o passthrough da classe base, mesma justificativa documentada em
    `AgenteOrcamentoSubfuncao`.

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
        actions: list[dict] = []
        if (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("date_from") is not None
        ):
            actions.append({"goal": "consultar_indicadores"})
        return actions

    def _act_consultar_indicadores(self, action: dict) -> None:
        """Ação externa (grounding): consulta `IndicadorSaude(sistema="sia")`
        no Neo4j.

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        subtipos = list(self.semantic_memory["subtipos"])

        try:
            logger.info(
                "Agent %s: consultando indicadores (sistema=sia, subtipos=%s, "
                "periodo=%s-%s)",
                self.agent_id, subtipos, date_from, date_to,
            )
            indicadores = self.neo4j_client.get_indicadores_por_sistema(
                self.semantic_memory["sistema"], subtipos, date_from, date_to,
            )
            self.working_memory["indicadores"] = indicadores
            logger.info(
                "Agent %s: retrieved %d indicadores", self.agent_id, len(indicadores),
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
    ) -> dict[str, Any]:
        """Consulta indicadores SIA (produção ambulatorial), sem despesas.

        Método de conveniência chamado pelo orquestrador/supervisor.
        Não retorna "despesas" — orçamento é responsabilidade de
        `AgenteOrcamentoSubfuncao`, consultado separadamente.

        Args:
            analysis_id: ID da análise em andamento.
            date_from: Ano de início do período.
            date_to: Ano de fim do período.
            intent_summary: Resumo da intenção do usuário (não usado —
                aceito para uniformidade de assinatura com os demais
                agentes de saúde).
            health_params: Indicadores solicitados na análise (idem, não
                usado para filtrar SUBTIPOS nesta fase).

        Returns:
            Dicionário com chave "indicadores". Lista vazia se não houver
            dados ou se a consulta falhar.
        """
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
        })

        self.run_coala_cycle()

        return {"indicadores": self.working_memory.get("indicadores", [])}
