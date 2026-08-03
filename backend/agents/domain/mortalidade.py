"""
Agente de Domínio — Mortalidade.

Especializado em dados de mortalidade SIM (transversal a todas as subfunções).
Consulta nós IndicadorDataSUS (tipo="mortalidade") e DespesaSIOPS de TODAS
as subfunções (301, 302, 303, 305) no Neo4j, filtrando por período e análise.

Diferente dos demais agentes de domínio, este agente NÃO filtra despesas por
uma única subfunção — ele retorna despesas de todas as subfunções porque dados
de mortalidade cruzam com todas as categorias de gasto.

Requisitos: 4.1, 4.2, 4.3, 4.4, 4.5
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Configuração de domínio deste agente
SUBFUNCOES: list[int] = [301, 302, 303, 305]  # All subfunções (transversal)
TIPOS_INDICADOR: list[str] = ["mortalidade"]


class AgenteMortalidade(AgenteCoALA):
    """Agente de domínio especializado em mortalidade (visão transversal).

    Consulta DespesaSIOPS de TODAS as subfunções (301, 302, 303, 305) e
    IndicadorDataSUS com tipo="mortalidade" no Neo4j via neo4j_client,
    filtrando por período (date_from/date_to) e análise (analysis_id).

    Diferente dos demais agentes de domínio que filtram por uma única
    subfunção, este agente mantém despesas de todas as subfunções porque
    dados de mortalidade são transversais a todas as categorias de gasto.

    Herda de AgenteCoALA e implementa o ciclo CoALA completo:
    perceive → propose_actions → evaluate_and_select → execute (Req 4.4).
    A lista de subfunções e os tipos de indicador são fatos de domínio
    expostos via `semantic_memory` — lidos por *retrieval* dentro de
    `_act_*`, não hardcoded direto do módulo no meio da execução.

    Attributes:
        neo4j_client: Cliente Neo4j para queries Cypher.
    """

    def __init__(self, agent_id: str, neo4j_client: Neo4jClient) -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.semantic_memory = {
            "subfuncoes": SUBFUNCOES,
            "tipos_indicador": TIPOS_INDICADOR,
        }
        self.procedural_memory = {
            "consultar_despesas": [
                self._act_consultar_despesas,
                self._act_fallback_despesas,
            ],
            "consultar_indicadores": [
                self._act_consultar_indicadores,
                self._act_fallback_indicadores,
            ],
        }

    # ------------------------------------------------------------------
    # Ciclo CoALA
    # ------------------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe o ambiente a partir da working memory já definida.

        O orquestrador/supervisor chama update_working_memory com os
        parâmetros da consulta antes de disparar o ciclo. A percepção
        retorna esses parâmetros.

        Returns:
            Dicionário com analysis_id, date_from e date_to.
        """
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe ações com base na working memory atual.

        Se os parâmetros de consulta estão presentes, propõe consultar
        despesas (todas as subfunções) e indicadores (mortalidade).

        Returns:
            Lista de candidatos de ação.
        """
        actions: list[dict] = []
        if (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("date_from") is not None
        ):
            actions.append({"goal": "consultar_despesas"})
            actions.append({"goal": "consultar_indicadores"})
        return actions

    def _act_consultar_despesas(self, action: dict) -> None:
        """Ação externa (grounding): consulta DespesaSIOPS no Neo4j.

        Mantém despesas de TODAS as subfunções (transversal) (Req 4.2),
        lida via retrieval de `semantic_memory["subfuncoes"]`.

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        subfuncoes = self.semantic_memory["subfuncoes"]

        try:
            logger.info(
                "Agent %s: consultando despesas (subfuncoes=%s, periodo=%s-%s)",
                self.agent_id, subfuncoes, date_from, date_to,
            )
            all_despesas = self.neo4j_client.get_despesas(
                analysis_id, date_from, date_to
            )
            despesas = [d for d in all_despesas if d.get("subfuncao") in subfuncoes]
            self.working_memory["despesas"] = despesas
            logger.info(
                "Agent %s: retrieved %d despesas (subfuncoes=%s)",
                self.agent_id,
                len(despesas),
                subfuncoes,
            )
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_consultar_indicadores(self, action: dict) -> None:
        """Ação externa (grounding): consulta IndicadorDataSUS no Neo4j.

        Busca tipos definidos em `semantic_memory["tipos_indicador"]`
        (Req 4.1).

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        tipos_indicador = self.semantic_memory["tipos_indicador"]

        try:
            logger.info(
                "Agent %s: consultando indicadores (tipos=%s, periodo=%s-%s)",
                self.agent_id, tipos_indicador, date_from, date_to,
            )
            indicadores = self.neo4j_client.get_indicadores(
                analysis_id, date_from, date_to, tipos_indicador
            )
            self.working_memory["indicadores"] = indicadores
            logger.info(
                "Agent %s: retrieved %d indicadores (tipos=%s)",
                self.agent_id,
                len(indicadores),
                tipos_indicador,
            )
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_fallback_despesas(self, action: dict) -> None:
        """Estratégia de fallback (Req 4.5): grava lista vazia em working memory.

        Permite que o orquestrador/supervisor continue com dados parciais
        em vez de propagar a falha da consulta ao Neo4j.
        """
        self.working_memory["despesas"] = []
        logger.warning("Agent %s: fallback — returning empty despesas", self.agent_id)

    def _act_fallback_indicadores(self, action: dict) -> None:
        """Estratégia de fallback (Req 4.5): grava lista vazia em working memory."""
        self.working_memory["indicadores"] = []
        logger.warning(
            "Agent %s: fallback — returning empty indicadores", self.agent_id
        )

    # ------------------------------------------------------------------
    # Interface pública
    # ------------------------------------------------------------------

    def query(
        self,
        analysis_id: str,
        date_from: int,
        date_to: int,
    ) -> dict[str, Any]:
        """Consulta despesas (todas as subfunções) e indicadores (mortalidade).

        Método de conveniência chamado pelo orquestrador/supervisor.
        Configura a working memory, executa o ciclo CoALA e retorna os dados.

        Diferente dos demais agentes de domínio, este agente retorna
        despesas de TODAS as subfunções (301, 302, 303, 305) porque
        mortalidade é transversal a todas as categorias de gasto.

        Args:
            analysis_id: ID da análise em andamento.
            date_from: Ano de início do período.
            date_to: Ano de fim do período.

        Returns:
            Dicionário com chaves "despesas" e "indicadores", cada uma
            contendo lista de registros do Neo4j. Retorna listas vazias
            se não houver dados (Req 4.5).
        """
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
        })

        self.run_coala_cycle()

        return {
            "despesas": self.working_memory.get("despesas", []),
            "indicadores": self.working_memory.get("indicadores", []),
        }
