"""
Classe base AgenteCoALA — framework CoALA (Cognitive Architectures for Language Agents).

Todos os agentes do sistema (estrela e hierárquico) herdam desta classe.
Implementa o ciclo cognitivo CoALA completo:

    perceive → update_working_memory → propose_actions → evaluate_and_select
    → execute (que já embute observe/learn por ação)

Memória (Sumers et al., 2023):
    - working_memory: buffer ativo de curto prazo (equivalente às antigas "beliefs").
    - semantic_memory: fatos/regras declarativas de domínio (ex: mapeamento
      subfunção→indicador, limiares de classificação) — lidos via *retrieval*
      dentro de propose_actions, nunca hardcoded direto do global no meio da
      lógica de decisão.
    - episodic_memory: histórico de ações executadas nesta instância (sucesso
      e falha), gravado via _observe_and_learn — só em memória, descartado ao
      fim do processo.
    - procedural_memory: "como fazer" — lista ordenada de estratégias
      (callables) por goal; a primeira é a estratégia primária, as demais são
      fallbacks tentados em ordem se a anterior levantar ActionFailure.

Espaço de ações:
    - Internas (reasoning/retrieval/learning): processamento sobre a working
      memory, leitura de semantic_memory, gravação em episodic_memory.
    - Externas (grounding): qualquer interação com o ambiente fora do
      processo do agente (Neo4j, LLM, WebSocket, comunicação lateral entre
      agentes) — implementadas dentro das estratégias de procedural_memory.
"""

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ActionFailure(Exception):
    """Levantada quando uma ação falha durante a execução."""

    def __init__(self, action: dict, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"Action failed: {reason}")


class AgenteCoALA:
    """Classe base para todos os agentes CoALA.

    Attributes:
        agent_id: Identificador único do agente.
        working_memory: Buffer de curto prazo com o estado ativo do agente.
        semantic_memory: Fatos/regras de domínio, populados no __init__ das
            subclasses a partir de constantes de módulo.
        episodic_memory: Histórico de ações executadas (sucesso/falha), só
            em memória.
        procedural_memory: Estratégias (callables) registradas por goal,
            em ordem de prioridade (primária → fallbacks).
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.working_memory: dict[str, Any] = {}
        self.semantic_memory: dict[str, Any] = {}
        self.episodic_memory: list[dict] = []
        self.procedural_memory: dict[str, list[Callable[[dict], None]]] = {}
        self._failed_actions: list[dict] = []

    def perceive(self) -> dict:
        """Percebe o ambiente (consulta Neo4j ou recebe dados do orquestrador).

        Subclasses devem sobrescrever este método para implementar
        a percepção específica do agente.

        Returns:
            Dicionário com as percepções do ambiente.
        """
        return {}

    def update_working_memory(self, perception: dict) -> None:
        """Incorpora a percepção à memória de trabalho.

        Args:
            perception: Dicionário com novas informações do ambiente.
        """
        self.working_memory.update(perception)

    def propose_actions(self) -> list[dict]:
        """Propõe candidatos de ação (goals) dado o estado da working memory.

        Subclasses devem sobrescrever para implementar a lógica de proposta
        específica, fazendo *retrieval* explícito de `semantic_memory` onde
        for relevante. A implementação base não propõe nenhuma ação.

        Returns:
            Lista de candidatos de ação, cada um pelo menos com a chave "goal".
        """
        return []

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Avalia e seleciona os candidatos de ação a executar.

        Neste sistema não há candidatos concorrentes com score real a
        arbitrar — evaluate e select colapsam num único passo por decisão
        intencional (documentado aqui, não por omissão). A implementação
        base seleciona todos os candidatos, na ordem propostas.

        Args:
            candidates: Candidatos de ação retornados por `propose_actions`.

        Returns:
            Lista de ações selecionadas para execução (cada uma com "status": "pending").
        """
        return [dict(c, status="pending") for c in candidates]

    def execute(self, actions: list[dict]) -> None:
        """Executa as ações selecionadas, percorrendo a memória procedural.

        Para cada ação, tenta as estratégias registradas em
        `procedural_memory[goal]` em ordem (primária → fallbacks). Cada
        tentativa (sucesso ou falha) é registrada via `_observe_and_learn`.
        Se todas as estratégias falharem, a ação é marcada como "failed" e o
        laço segue para a próxima ação — essa propriedade sustenta a
        degradação graciosa em todos os níveis (agentes-folha e, após a
        reescrita, também os orquestradores).

        Args:
            actions: Lista de ações a executar.
        """
        classe = type(self).__name__
        for action in actions:
            goal = action.get("goal")
            strategies = self.procedural_memory.get(goal, [])

            if not strategies:
                action["status"] = "failed"
                self._failed_actions.append(dict(action))
                logger.error(
                    "Agent %s (%s): nenhuma estratégia procedural registrada para o goal '%s'",
                    self.agent_id, classe, goal,
                )
                continue

            for i, strategy in enumerate(strategies):
                estrategia_nome = getattr(strategy, "__name__", repr(strategy))
                logger.info(
                    "Agent %s (%s): iniciando ação '%s' (estratégia %d/%d: %s)",
                    self.agent_id, classe, goal, i + 1, len(strategies), estrategia_nome,
                )
                inicio = time.time()
                try:
                    strategy(action)
                except ActionFailure as exc:
                    elapsed_ms = (time.time() - inicio) * 1000
                    action["status"] = "failed"
                    self._failed_actions.append(dict(action, reason=exc.reason))
                    self._observe_and_learn(action, "failed", detail=exc.reason)
                    logger.warning(
                        "Agent %s (%s): ação '%s' falhou após %.1fms (estratégia %d/%d) — %s",
                        self.agent_id, classe, goal, elapsed_ms, i + 1, len(strategies), exc.reason,
                    )
                    continue
                else:
                    elapsed_ms = (time.time() - inicio) * 1000
                    action["status"] = "completed"
                    self._observe_and_learn(action, "completed")
                    logger.info(
                        "Agent %s (%s): ação '%s' concluída em %.1fms",
                        self.agent_id, classe, goal, elapsed_ms,
                    )
                    break
            else:
                logger.error(
                    "Agent %s (%s): todas as estratégias esgotadas para o goal '%s', reportando falha",
                    self.agent_id, classe, goal,
                )

    def _observe_and_learn(self, action: dict, status: str, detail: Any = None) -> None:
        """Ação interna de *learning*: registra o episódio em episodic_memory.

        Chamada ao final de cada tentativa de execução (sucesso ou falha).
        Só em memória — descartada ao fim do processo (sem persistência).

        Args:
            action: A ação executada.
            status: "completed" ou "failed".
            detail: Informação adicional (ex: motivo da falha).
        """
        self.episodic_memory.append(
            {
                "action": action.get("goal"),
                "status": status,
                "detail": detail,
                "timestamp": time.time(),
            }
        )

    def run_coala_cycle(self) -> None:
        """Ciclo CoALA completo.

        perceive → update_working_memory → propose_actions →
        evaluate_and_select → execute (que já embute observe/learn por ação).
        """
        classe = type(self).__name__
        inicio = time.time()
        logger.info("Agent %s (%s): iniciando ciclo CoALA", self.agent_id, classe)

        perception = self.perceive()
        self.update_working_memory(perception)
        candidates = self.propose_actions()
        goals = [c.get("goal") for c in candidates]
        logger.info(
            "Agent %s (%s): %d ação(ões) propostas: %s",
            self.agent_id, classe, len(candidates), goals,
        )
        actions = self.evaluate_and_select(candidates)

        falhas_antes = len(self._failed_actions)
        self.execute(actions)
        falhas_depois = len(self._failed_actions)

        elapsed_ms = (time.time() - inicio) * 1000
        logger.info(
            "Agent %s (%s): ciclo CoALA concluído em %.1fms (%d ações, %d falha(s) nesta rodada)",
            self.agent_id, classe, elapsed_ms, len(actions), falhas_depois - falhas_antes,
        )
