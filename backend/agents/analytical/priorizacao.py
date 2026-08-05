"""
Agente Analítico — Priorização de Achados (Etapa 3 do PLANO_REFATORACAO.md).

Recebe as correlações, anomalias e o contexto orçamentário já calculados
(dados determinísticos, inalterados por este agente) e decide qual ângulo
de ênfase usar e em que ordem apresentar os achados ao TextSynthesizer —
antes deste agente, essa decisão ficava implícita e invisível dentro do
prompt do sintetizador.

É o primeiro lugar do sistema onde o loop CoALA `propose_actions ->
evaluate_and_select` faz trabalho de verdade: `propose_actions` gera um
candidato por ângulo de ênfase possível (mais de um candidato real, ao
contrário de todo outro agente do sistema), e `evaluate_and_select`
combina um score determinístico simples (magnitude/contagem dos achados
brutos) com uma chamada LLM que escolhe entre eles, informada por
`intent_summary` (Etapa 1).

Importante: este agente NUNCA recalcula nem filtra `correlacoes`/
`anomalias` — só decide ordem/destaque. Os dados brutos usados na métrica
de consistência determinística Q1 (`compute_deterministic_consistency`)
não são tocados.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import ActionFailure, AgenteCoALA

logger = logging.getLogger(__name__)

# Ângulos de ênfase possíveis — memória semântica (fatos de domínio deste
# agente), não hardcoded direto no meio da lógica de decisão.
ANGULOS_POSSIVEIS: dict[str, str] = {
    "ineficiencias": (
        "focar nos casos de alto gasto com baixo resultado (possíveis "
        "ineficiências na aplicação dos recursos)"
    ),
    "eficiencia": (
        "focar nos casos de baixo gasto com bom resultado (possíveis boas "
        "práticas ou uso eficiente dos recursos)"
    ),
    "correlacoes_fortes": (
        "focar nas correlações estatisticamente mais fortes entre gasto e "
        "indicador de saúde"
    ),
    "tendencias_orcamentarias": (
        "focar em como o orçamento de cada área mudou ao longo do tempo "
        "(cortes ou crescimento)"
    ),
    "intencao_usuario": (
        "focar diretamente no que o usuário perguntou, mesmo que outros "
        "achados sejam estatisticamente mais fortes"
    ),
}

# Prioridade de tipo de anomalia por ângulo — usado para reordenar (nunca
# filtrar) a lista de anomalias na ação de execução.
_PRIORIDADE_ANOMALIA: dict[str, dict[str, int]] = {
    "ineficiencias": {"alto_gasto_baixo_resultado": 0, "baixo_gasto_alto_resultado": 1},
    "eficiencia": {"baixo_gasto_alto_resultado": 0, "alto_gasto_baixo_resultado": 1},
}

_ORDEM_CLASSIFICACAO_PADRAO: dict[str, int] = {"alta": 0, "média": 1, "baixa": 2}


class AgentePriorizacaoAnalitica(AgenteCoALA):
    """Agente que decide ênfase/ordem dos achados antes da síntese textual.

    Attributes:
        semantic_memory: `angulos_possiveis` — mapeamento nome→descrição
            dos candidatos de ênfase narrativa.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.semantic_memory = {"angulos_possiveis": dict(ANGULOS_POSSIVEIS)}
        self.procedural_memory = {
            "priorizar_achados": [self._act_priorizar_achados],
        }

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        return {
            "correlacoes": self.working_memory.get("correlacoes", []),
            "anomalias": self.working_memory.get("anomalias", []),
            "contexto_orcamentario": self.working_memory.get("contexto_orcamentario", {}),
            "intent_summary": self.working_memory.get("intent_summary"),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe um candidato por ângulo de ênfase possível (Etapa 3).

        Primeiro lugar do sistema onde `propose_actions` gera mais de um
        candidato de verdade — `evaluate_and_select` é quem de fato
        escolhe entre eles.
        """
        if not (
            self.working_memory.get("correlacoes") or self.working_memory.get("anomalias")
        ):
            return []
        angulos = self.semantic_memory["angulos_possiveis"]
        return [
            {"goal": "priorizar_achados", "angulo": nome, "descricao": descricao}
            for nome, descricao in angulos.items()
        ]

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Escolhe UM candidato entre os propostos (Etapa 3).

        Primeiro uso real de `evaluate_and_select` no sistema — combina
        (a) um score determinístico simples por ângulo (contagem/magnitude
        dos achados brutos) com (b) uma chamada LLM que escolhe entre os
        candidatos, informada por `intent_summary`. Se o LLM falhar ou
        devolver algo inválido, cai no candidato de maior score
        determinístico — nunca quebra o pipeline.

        Respeita `working_memory["use_llm"]` (default True): quando False
        (análise com `useLlm=false`), nem tenta a chamada LLM — vai direto
        para o score determinístico. Evita chamada de rede surpresa quando
        o usuário desligou LLM para a análise inteira.
        """
        if not candidates:
            return []

        scored = [(c, self._score_candidato(c)) for c in candidates]

        if not self.working_memory.get("use_llm", True):
            escolhido, melhor_score = max(scored, key=lambda item: item[1])
            logger.info(
                "Agent %s: use_llm=False — ângulo escolhido via score determinístico: "
                "'%s' (score=%.1f)",
                self.agent_id, escolhido["angulo"], melhor_score,
            )
            return [dict(escolhido, status="pending")]

        escolhido = self._escolher_via_llm(candidates, scored)
        return [dict(escolhido, status="pending")]

    def _score_candidato(self, candidate: dict) -> float:
        """Score determinístico por ângulo — magnitude/contagem dos dados brutos."""
        angulo = candidate["angulo"]
        correlacoes = self.working_memory.get("correlacoes", [])
        anomalias = self.working_memory.get("anomalias", [])
        contexto = self.working_memory.get("contexto_orcamentario", {})

        if angulo == "ineficiencias":
            return float(
                sum(1 for a in anomalias if a.get("tipo_anomalia") == "alto_gasto_baixo_resultado")
            )
        if angulo == "eficiencia":
            return float(
                sum(1 for a in anomalias if a.get("tipo_anomalia") == "baixo_gasto_alto_resultado")
            )
        if angulo == "correlacoes_fortes":
            return float(sum(1 for c in correlacoes if c.get("classificacao") == "alta"))
        if angulo == "tendencias_orcamentarias":
            return float(
                sum(
                    1
                    for v in contexto.values()
                    if isinstance(v, dict) and v.get("tendencia") in ("crescimento", "corte")
                )
            )
        if angulo == "intencao_usuario":
            return 1.0 if self.working_memory.get("intent_summary") else 0.0
        return 0.0

    def _escolher_via_llm(
        self, candidates: list[dict], scored: list[tuple[dict, float]]
    ) -> dict:
        try:
            import core.llm_client as llm_client

            prompt = self._build_prompt(scored)
            raw = llm_client.generate(
                prompt, caller=f"{self.agent_id}:priorizar_achados"
            )
            escolha = self._parse_escolha(raw, candidates)
            if escolha is not None:
                logger.info(
                    "Agent %s: ângulo escolhido via LLM: '%s'",
                    self.agent_id, escolha["angulo"],
                )
                return escolha
        except Exception as exc:
            logger.warning(
                "Agent %s: escolha de ângulo via LLM falhou (%s) — usando maior score",
                self.agent_id, exc,
            )

        melhor, melhor_score = max(scored, key=lambda item: item[1])
        logger.info(
            "Agent %s: ângulo escolhido via score determinístico: '%s' (score=%.1f)",
            self.agent_id, melhor["angulo"], melhor_score,
        )
        return melhor

    def _build_prompt(self, scored: list[tuple[dict, float]]) -> str:
        linhas = [
            f'- "{c["angulo"]}": {c["descricao"]} (score determinístico: {score:.1f})'
            for c, score in scored
        ]
        intent_summary = self.working_memory.get("intent_summary")
        return (
            "Você ajuda a decidir qual ângulo de ênfase usar ao escrever uma "
            "análise de gastos públicos de saúde vs. indicadores de saúde de "
            "Sorocaba-SP.\n\n"
            f"Intenção do usuário nesta análise: {intent_summary or '(não informada)'}\n\n"
            "Candidatos de ênfase (com um score determinístico de apoio, "
            "baseado na magnitude/contagem dos achados brutos já calculados "
            "— você não deve recalcular nem contestar esses números):\n"
            + "\n".join(linhas)
            + "\n\n"
            "Escolha o ângulo mais adequado — priorize a intenção do usuário "
            "quando ela estiver informada; caso contrário, priorize o score "
            "determinístico mais alto. Responda SOMENTE com um objeto JSON, "
            "sem texto antes ou depois, sem markdown: "
            '{"angulo": "<nome exato de um dos candidatos acima>"}'
        )

    def _parse_escolha(self, raw: str | None, candidates: list[dict]) -> dict | None:
        if not raw:
            return None
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                cleaned = cleaned.split("\n", 1)[1]
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        nome = data.get("angulo")
        for c in candidates:
            if c["angulo"] == nome:
                return c
        return None

    # -- Ação de execução ---------------------------------------------------

    def _act_priorizar_achados(self, action: dict) -> None:
        """Ação interna (reasoning): reordena achados conforme o ângulo escolhido.

        Nunca recalcula nem filtra `correlacoes`/`anomalias` — só decide a
        ordem em que aparecem e anota a descrição do ângulo escolhido, para
        o `TextSynthesizer` usar como instrução de ênfase no prompt.
        """
        try:
            angulo = action.get("angulo", "")
            descricao = action.get("descricao", "")
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})

            self.working_memory["achados_priorizados"] = {
                "angulo": angulo,
                "descricao_angulo": descricao,
                "correlacoes": self._ordenar_correlacoes(correlacoes, angulo),
                "anomalias": self._ordenar_anomalias(anomalias, angulo),
                "contexto_orcamentario": contexto_orcamentario,
            }
            logger.info(
                "Agent %s: achados priorizados via ângulo '%s' (%d correlações, %d anomalias)",
                self.agent_id, angulo, len(correlacoes), len(anomalias),
            )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    @staticmethod
    def _ordenar_correlacoes(correlacoes: list[dict], angulo: str) -> list[dict]:
        if angulo == "correlacoes_fortes":
            return sorted(correlacoes, key=lambda c: abs(c.get("spearman", 0)), reverse=True)
        return sorted(
            correlacoes,
            key=lambda c: _ORDEM_CLASSIFICACAO_PADRAO.get(c.get("classificacao"), 3),
        )

    @staticmethod
    def _ordenar_anomalias(anomalias: list[dict], angulo: str) -> list[dict]:
        prioridade = _PRIORIDADE_ANOMALIA.get(angulo)
        if prioridade is None:
            return list(anomalias)
        return sorted(anomalias, key=lambda a: prioridade.get(a.get("tipo_anomalia"), 2))

    # -- Interface pública --------------------------------------------------

    def prioritize(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        intent_summary: str | None = None,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Decide ênfase/ordem dos achados. Método de conveniência chamado
        pelo orquestrador/supervisor.

        Args:
            correlacoes: Lista de correlações já calculadas (inalteradas).
            anomalias: Lista de anomalias já detectadas (inalteradas).
            contexto_orcamentario: Tendências orçamentárias já calculadas.
            intent_summary: Resumo da intenção do usuário (Etapa 1).
            use_llm: Se False, escolhe o ângulo só via score determinístico
                (mesma flag `useLlm` do resto da análise) — sem chamada LLM.

        Returns:
            Dict com `angulo`, `descricao_angulo`, `correlacoes` (reordenada),
            `anomalias` (reordenada) e `contexto_orcamentario`. Se não houver
            achados para priorizar, devolve os dados originais sem ângulo.
        """
        self.update_working_memory({
            "correlacoes": correlacoes,
            "anomalias": anomalias,
            "contexto_orcamentario": contexto_orcamentario,
            "intent_summary": intent_summary,
            "use_llm": use_llm,
        })
        self.run_coala_cycle()
        return self.working_memory.get("achados_priorizados", {
            "angulo": None,
            "descricao_angulo": None,
            "correlacoes": correlacoes,
            "anomalias": anomalias,
            "contexto_orcamentario": contexto_orcamentario,
        })
