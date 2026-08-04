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
from agents.domain.query_planning import plan_query

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

    Planejamento de consulta (Etapa 2 do PLANO_REFATORACAO.md): antes de
    consultar o Neo4j, o agente propõe `planejar_consulta` — resolve, via
    `agents.domain.query_planning`, quais subfunções/tipos de indicador
    usar como filtro. Por padrão (mapeamento trivial, flag desligada) é
    um fast-path determinístico idêntico ao comportamento anterior; só
    aciona LLM quando `semantic_memory` for estendido no futuro e a flag
    `USE_LLM_QUERY_PLANNING` estiver ligada.

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
            "planejar_consulta": [self._act_planejar_consulta],
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

        Se os parâmetros de consulta estão presentes, propõe planejar a
        consulta e então consultar despesas (todas as subfunções) e
        indicadores (mortalidade).

        Returns:
            Lista de candidatos de ação.
        """
        actions: list[dict] = []
        if (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("date_from") is not None
        ):
            actions.append({"goal": "planejar_consulta"})
            actions.append({"goal": "consultar_despesas"})
            actions.append({"goal": "consultar_indicadores"})
        return actions

    def _act_planejar_consulta(self, action: dict) -> None:
        """Ação interna (reasoning): resolve o plano de consulta (Etapa 2).

        Fast-path determinístico no cenário atual — só toca o LLM se
        `semantic_memory` deixar de ser o mapeamento estático padrão E a
        flag `USE_LLM_QUERY_PLANNING` estiver ligada. Nunca falha: erro no
        LLM cai no plano estático (ver `query_planning.plan_query`).
        """
        static_plan = {
            "subfuncoes": list(self.semantic_memory["subfuncoes"]),
            "tipos_indicador": list(self.semantic_memory["tipos_indicador"]),
        }
        is_trivial = (
            self.semantic_memory.get("subfuncoes") == SUBFUNCOES
            and self.semantic_memory.get("tipos_indicador") == TIPOS_INDICADOR
        )
        plan, origem = plan_query(
            agent_id=self.agent_id,
            agent_type="mortalidade",
            static_plan=static_plan,
            is_trivial=is_trivial,
            intent_summary=self.working_memory.get("intent_summary"),
            health_params=self.working_memory.get("health_params"),
        )
        self.working_memory["query_plan"] = plan
        logger.info(
            "Agent %s: plano de consulta definido via %s: %s",
            self.agent_id, origem, plan,
        )

    def _act_consultar_despesas(self, action: dict) -> None:
        """Ação externa (grounding): consulta DespesaSIOPS no Neo4j.

        Mantém despesas das subfunções do plano de consulta (Req 4.2),
        resolvido por `_act_planejar_consulta` — no caso trivial, TODAS
        as subfunções (transversal).

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        query_plan = self.working_memory.get("query_plan") or {
            "subfuncoes": list(self.semantic_memory["subfuncoes"])
        }
        subfuncoes = query_plan["subfuncoes"]

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

        Busca os tipos definidos no plano de consulta (Req 4.1),
        resolvido por `_act_planejar_consulta`.

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        query_plan = self.working_memory.get("query_plan") or {
            "tipos_indicador": list(self.semantic_memory["tipos_indicador"])
        }
        tipos_indicador = query_plan["tipos_indicador"]

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
        intent_summary: str | None = None,
        health_params: list[str] | None = None,
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
            intent_summary: Resumo da intenção do usuário (opcional, vem
                do AgenteInterpretacaoIntencao — Etapa 1), usado pelo
                planejamento de consulta (Etapa 2).
            health_params: Indicadores solicitados na análise (opcional),
                idem.

        Returns:
            Dicionário com chaves "despesas" e "indicadores", cada uma
            contendo lista de registros do Neo4j. Retorna listas vazias
            se não houver dados (Req 4.5).
        """
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
            "intent_summary": intent_summary,
            "health_params": health_params,
        })

        self.run_coala_cycle()

        return {
            "despesas": self.working_memory.get("despesas", []),
            "indicadores": self.working_memory.get("indicadores", []),
        }
