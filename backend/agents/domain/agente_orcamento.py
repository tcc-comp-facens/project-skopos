"""
Agente de Domínio — Orçamento por Subfunção.

Classe parametrizada por subfunção (`subfuncao_codigo`/`subfuncao_nome`)
— base reutilizável para os 7 agentes orçamentários alvo de
PLANO_NOVO_MODELO_DADOS.md §5 (1 por subfunção: 122, 301-306).

Consulta `DespesaAnual(subfuncaoCodigo=<código>)` no Neo4j via
`neo4j_client.get_despesas_por_subfuncao` (campo `valorProcessado`).
Absorve o cálculo de tendência ano-a-ano — decisão tomada em
PLANO_NOVO_MODELO_DADOS.md §5 ("`AgenteContextoOrcamentario` eliminado
como agente separado — cada agente orçamentário absorve a
responsabilidade de calcular sua própria tendência") — reaproveitando
`AgenteContextoOrcamentario.analyze_trends` em vez de duplicar a lógica
de classificação (crescimento/corte/estagnação/insuficiente).

Deliberação real de dimensão (Fase 3): `DespesaAnual` ganhou quebra
dimensional por natureza de despesa (o que foi comprado — medicamentos,
equipamentos, salários) e por aplicação (bloco de financiamento SUS) —
ver `db/query_builder.py` (`DESPESA_DIMENSOES`) e
`etl/orcamento_loader.py` (`POR_NATUREZA`/`POR_APLICACAO`). Este agente
delibera entre "sem quebra"/`POR_NATUREZA`/`POR_APLICACAO`, mesmo padrão
de dois estágios (score determinístico + LLM opcional) de
`AgenteSINAN`, via `agents.domain.query_planning`. Diferente de
`AgenteSINAN`, cujo `propose_actions` só propõe um goal
(`consultar_indicadores`), este agente propõe três goals na mesma lista
de candidatos (`consultar_despesas` — com deliberação —,
`analisar_tendencia` e `consultar_variacao_anual` — ambas sempre
únicas) — `evaluate_and_select` precisa particionar por goal antes de
arbitrar, só o primeiro grupo é candidato a arbitragem real.

`consultar_variacao_anual` (Fase 3, PLANO_NOVO_MODELO_DADOS.md §3.1) lê
`VARIACAO_ANUAL` — a variação percentual ano-a-ano pré-computada uma
vez no ETL (`etl/orcamento_loader.py`, reaproveitando literalmente
`agents.context.contexto_orcamentario.compute_yoy_variation`/
`classify_trend`, mesma fórmula/limiares de `analisar_tendencia`).
Informativo, não substitui `tendencia` — as duas coexistem porque têm
semânticas diferentes: `tendencia` resume o período pedido inteiro
(`date_from`-`date_to`) num único veredito; `variacao_anual` é a lista
crua de transições ano-a-ano pré-computadas globalmente no ETL
(insensível à janela pedida além do filtro final por ano).
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from agents.context.contexto_orcamentario import AgenteContextoOrcamentario
from agents.domain.query_planning import arbitrar_dimensao, propose_dimensao_candidatos
from db.query_builder import dimensoes_validas_despesa

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class AgenteOrcamentoSubfuncao(AgenteCoALA):
    """Agente de domínio orçamentário parametrizado por subfunção.

    Delibera entre candidatos concorrentes de dimensão para
    `consultar_despesas` (Fase 3) — `POR_NATUREZA`, `POR_APLICACAO` ou
    "sem quebra" — mesmo padrão de `AgenteSINAN`. `analisar_tendencia`
    continua uma ação única, sem deliberação (a classificação de
    tendência opera sobre o total anual, não sobre uma fatia
    dimensional — ver `_act_analisar_tendencia`).

    Attributes:
        neo4j_client: Cliente Neo4j para queries Cypher.
    """

    def __init__(
        self,
        agent_id: str,
        neo4j_client: "Neo4jClient",
        subfuncao_codigo: int,
        subfuncao_nome: str,
    ) -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.semantic_memory = {
            "subfuncao_codigo": subfuncao_codigo,
            "subfuncao_nome": subfuncao_nome,
            "dimensoes_validas": dimensoes_validas_despesa(),
        }
        self.procedural_memory = {
            "consultar_despesas": [
                self._act_consultar_despesas,
                self._act_fallback_despesas,
            ],
            "analisar_tendencia": [self._act_analisar_tendencia],
            "consultar_variacao_anual": [
                self._act_consultar_variacao_anual,
                self._act_fallback_variacao_anual,
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
        """Propõe um candidato de consulta por dimensão válida (goal
        `consultar_despesas`) mais a ação única `analisar_tendencia`."""
        actions: list[dict] = []
        if not (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("date_from") is not None
        ):
            return actions

        candidatos_dimensao = propose_dimensao_candidatos(
            self.semantic_memory["dimensoes_validas"]
        )
        actions.extend(
            {
                "goal": "consultar_despesas",
                "dimensao": c["dimensao"],
                "descricao": c["descricao"],
            }
            for c in candidatos_dimensao
        )
        actions.append({"goal": "analisar_tendencia"})
        actions.append({"goal": "consultar_variacao_anual"})
        return actions

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Arbitra só os candidatos de `consultar_despesas` — diferente
        de `AgenteSINAN`, esta lista mistura dois goals, então
        `analisar_tendencia` (goal único) passa direto, sem arbitragem.
        """
        if not candidates:
            return []

        despesa_candidatos = [c for c in candidates if c["goal"] == "consultar_despesas"]
        outros = [c for c in candidates if c["goal"] != "consultar_despesas"]

        selected: list[dict] = []
        if despesa_candidatos:
            escolhido, origem = arbitrar_dimensao(
                agent_id=self.agent_id,
                candidatos=despesa_candidatos,
                intent_summary=self.working_memory.get("intent_summary"),
                use_llm=self.working_memory.get("use_llm", True),
            )
            logger.info(
                "Agent %s: dimensão de despesas escolhida via %s: %s",
                self.agent_id, origem, escolhido["dimensao"],
            )
            selected.append(dict(escolhido, status="pending"))
        selected.extend(dict(c, status="pending") for c in outros)
        return selected

    def _act_consultar_despesas(self, action: dict) -> None:
        """Ação externa (grounding): consulta `DespesaAnual` no Neo4j
        para a subfunção deste agente, com a dimensão escolhida por
        `evaluate_and_select`.

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        codigo = self.semantic_memory["subfuncao_codigo"]
        dimensao = action.get("dimensao")

        try:
            logger.info(
                "Agent %s: consultando despesas (subfuncao=%s, dimensao=%s, periodo=%s-%s)",
                self.agent_id, codigo, dimensao, date_from, date_to,
            )
            despesas = self.neo4j_client.get_despesas_por_subfuncao(
                [codigo], date_from, date_to, dimensao
            )
            self.working_memory["despesas"] = despesas
            self.working_memory["dimensao_escolhida"] = dimensao
            logger.info(
                "Agent %s: retrieved %d despesas (dimensao=%s)",
                self.agent_id, len(despesas), dimensao,
            )
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_fallback_despesas(self, action: dict) -> None:
        """Estratégia de fallback: grava lista vazia em working memory."""
        self.working_memory["despesas"] = []
        logger.warning("Agent %s: fallback — returning empty despesas", self.agent_id)

    def _act_analisar_tendencia(self, action: dict) -> None:
        """Ação interna (reasoning): classifica a tendência ano-a-ano do
        próprio orçamento, reaproveitando
        `AgenteContextoOrcamentario.analyze_trends` (não duplica a lógica
        de classificação).

        Seguro mesmo quando `consultar_despesas` escolheu uma dimensão
        (Fase 3): `analyze_trends` soma `valor` por ano internamente
        (`year_values[ano] += item["valor"]`), então múltiplas linhas de
        fatia dimensional para o mesmo ano se recombinam corretamente no
        total antes da classificação — a tendência nunca é calculada
        sobre uma fatia isolada.

        Sem fallback registrado — se falhar, o orquestrador/supervisor
        segue com as despesas mesmo assim (`tendencia` ausente do
        resultado)."""
        try:
            despesas = self.working_memory.get("despesas", [])
            if not despesas:
                self.working_memory["tendencia"] = {}
                return
            ctx_id = f"{self.agent_id}-tendencia"
            agente_contexto = AgenteContextoOrcamentario(ctx_id)
            tendencias = agente_contexto.analyze_trends(despesas)
            codigo = self.semantic_memory["subfuncao_codigo"]
            self.working_memory["tendencia"] = tendencias.get(codigo, {})
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_consultar_variacao_anual(self, action: dict) -> None:
        """Ação externa (grounding): lê `VARIACAO_ANUAL` pré-computada no
        ETL para a subfunção deste agente (Fase 3).

        Não depende da dimensão escolhida por `consultar_despesas` — é
        sempre o mesmo reltype fixo, sem deliberação (ver docstring do
        módulo).

        Raises:
            ActionFailure: Se a consulta ao Neo4j falhar.
        """
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        codigo = self.semantic_memory["subfuncao_codigo"]

        try:
            variacao_anual = self.neo4j_client.get_variacao_anual(
                [codigo], date_from, date_to
            )
            self.working_memory["variacao_anual"] = variacao_anual
            logger.info(
                "Agent %s: retrieved %d variacao_anual", self.agent_id, len(variacao_anual)
            )
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_fallback_variacao_anual(self, action: dict) -> None:
        """Estratégia de fallback: grava lista vazia em working memory."""
        self.working_memory["variacao_anual"] = []
        logger.warning("Agent %s: fallback — returning empty variacao_anual", self.agent_id)

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
        """Consulta despesas da subfunção e classifica a tendência.

        Args:
            analysis_id: ID da análise em andamento.
            date_from: Ano de início do período.
            date_to: Ano de fim do período.
            intent_summary: Resumo da intenção do usuário — insumo da
                deliberação de dimensão (Fase 3).
            health_params: Não usado por este agente — aceito só por
                simetria de assinatura com os agentes de saúde
                (`AgenteSINAN.query`).
            use_llm: Se False, a deliberação de dimensão decide só pelo
                score determinístico — sem chamada LLM.

        Returns:
            Dict com "despesas" (shape legado: subfuncao, subfuncaoNome,
            ano, valor — mais "dimensao_valor" quando uma dimensão foi
            escolhida), "tendencia" (tendencia/variacao_media_percentual/
            anos_analisados, ou {} se dados insuficientes/consulta falhou)
            e "variacao_anual" (lista de transições ano-a-ano
            pré-computadas no ETL — subfuncao/ano_atual/ano_anterior/
            percentual/classificacao, ou [] se não houver dados/consulta
            falhar).
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

        return {
            "despesas": self.working_memory.get("despesas", []),
            "tendencia": self.working_memory.get("tendencia", {}),
            "variacao_anual": self.working_memory.get("variacao_anual", []),
        }
