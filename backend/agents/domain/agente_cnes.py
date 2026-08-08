"""
Agente de Domínio — CNES (Cadastro Nacional de Estabelecimentos de Saúde).

O sistema mais heterogêneo dos 8 (DOCUMENTACAO_ETL_MODELO_DADOS.md §6.4):
12 subtipos, de fontes/formatos de planilha diferentes — 2 fatos de valor
único (leitos, profissionais), 6 com quebra dimensional própria (1
dimensão cada — estabelecimentos_por_tipo/ocupacoes/equipes_saude/
tipo_atendimento/leitos_consultorios/equipamentos) e 4 contagens
independentes sem quebra (estabelecimentos_nivel_atencao/
_servico_classificacao/_habilitacao/_vigilancia_epidemiologica — são 4
grandezas diferentes, não categorias de uma mesma métrica).

Decisão de design: cobre os 12 subtipos num único agente (em vez de um
agente por sub-cubo) — mesmo padrão de "1 agente por Sistema de
Informação" dos demais 7 agentes de saúde, não "1 agente por cubo
TabNet". A deliberação de dimensão reaproveita literalmente o mecanismo
de `AgenteSINAN` (`propose_dimensao_candidatos`/`arbitrar_dimensao`
sobre as 6 dimensões de `SISTEMA_DIMENSOES["cnes"]`) sem nenhum
tratamento especial por subtipo — e isso continua correto mesmo o CNES
tendo uma dimensão por subtipo (não uma dimensão comum a todos, como
POR_FAIXA_ETARIA no SINAN): cada relacionamento dimensional (ex.:
POR_TIPO_ESTABELECIMENTO) só existe de fato nos nós IndicadorSaude do
subtipo correspondente (estabelecimentos_por_tipo), então
`build_indicador_cypher` já filtra implicitamente para esse subtipo ao
escolher aquela dimensão — os outros 11 subtipos simplesmente não têm
linhas para retornar nesse relacionamento, sem precisar de nenhum
código extra aqui. Se nenhuma dimensão for escolhida ("sem quebra"), a
consulta retorna o total anual dos 12 subtipos juntos, como qualquer
outro agente de saúde.

Cobertura nova (Fase 2) — sem agente legado equivalente a substituir.
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

SISTEMA = "cnes"
SUBTIPOS: list[str] = [
    "leitos",
    "profissionais",
    "estabelecimentos_por_tipo",
    "ocupacoes",
    "equipes_saude",
    "tipo_atendimento",
    "leitos_consultorios",
    "equipamentos",
    "estabelecimentos_nivel_atencao",
    "estabelecimentos_servico_classificacao",
    "estabelecimentos_habilitacao",
    "estabelecimentos_vigilancia_epidemiologica",
]


class AgenteCNES(AgenteCoALA):
    """Agente de domínio especializado no CNES — rede assistencial de saúde.

    Implementa deliberação real (mesmo padrão de `AgenteSINAN`):
    `propose_actions` propõe um candidato de consulta por dimensão válida
    (as 6 de `SISTEMA_DIMENSOES["cnes"]`) + "sem quebra";
    `evaluate_and_select` arbitra entre eles via score determinístico +
    LLM opcional. Ver docstring do módulo para por que essa deliberação
    "achata" corretamente mesmo com 1 dimensão por subtipo, em vez de 1
    dimensão comum aos 12.

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
        """Propõe um candidato de consulta por dimensão válida."""
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
        """Arbitra entre os candidatos de dimensão propostos.

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
        """Ação externa (grounding): consulta `IndicadorSaude(sistema="cnes")`
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
                "Agent %s: consultando indicadores (sistema=cnes, subtipos=%s, "
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
        """Consulta indicadores CNES (12 subtipos), sem despesas.

        Método de conveniência chamado pelo orquestrador/supervisor.
        Não retorna "despesas" — orçamento é responsabilidade de
        `AgenteOrcamentoSubfuncao`, consultado separadamente.

        Args:
            analysis_id: ID da análise em andamento.
            date_from: Ano de início do período.
            date_to: Ano de fim do período.
            intent_summary: Resumo da intenção do usuário — insumo da
                deliberação de dimensão.
            health_params: Indicadores solicitados na análise (não usado
                para filtrar SUBTIPOS nesta fase — o agente, uma vez
                ativado, cobre todo o seu domínio, mesmo padrão dos
                demais agentes de saúde).
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
