"""Testes unitários do ciclo cognitivo CoALA na classe base (AgenteCoALA)."""

import pytest

from agents.base import ActionFailure, AgenteCoALA


class _AgenteDeTeste(AgenteCoALA):
    """Subclasse mínima para exercitar o ciclo CoALA de forma isolada."""

    def __init__(self, agent_id: str, fail_primary: bool = False, fail_all: bool = False):
        super().__init__(agent_id)
        self.semantic_memory = {"limiar": 10}
        self.calls: list[str] = []
        self._fail_primary = fail_primary
        self._fail_all = fail_all
        self.procedural_memory = {
            "somar": [self._act_somar_primaria, self._act_somar_fallback],
        }

    def perceive(self) -> dict:
        return {"valor": self.working_memory.get("valor", 0)}

    def propose_actions(self) -> list[dict]:
        if self.working_memory.get("valor") is None:
            return []
        return [{"goal": "somar"}]

    def _act_somar_primaria(self, action: dict) -> None:
        self.calls.append("primaria")
        if self._fail_primary or self._fail_all:
            raise ActionFailure(action, "falha simulada na estratégia primária")
        self.working_memory["resultado"] = self.working_memory["valor"] + self.semantic_memory["limiar"]

    def _act_somar_fallback(self, action: dict) -> None:
        self.calls.append("fallback")
        if self._fail_all:
            raise ActionFailure(action, "falha simulada no fallback")
        self.working_memory["resultado"] = 0


def test_run_coala_cycle_executes_full_sequence_and_updates_working_memory():
    agente = _AgenteDeTeste("test-1")
    agente.update_working_memory({"valor": 5})

    agente.run_coala_cycle()

    assert agente.working_memory["resultado"] == 15
    assert agente.calls == ["primaria"]


def test_update_working_memory_merges_without_discarding_existing_keys():
    agente = _AgenteDeTeste("test-2")
    agente.update_working_memory({"valor": 1})
    agente.update_working_memory({"outro": "x"})

    assert agente.working_memory == {"valor": 1, "outro": "x"}


def test_propose_actions_reads_from_working_memory():
    agente = _AgenteDeTeste("test-3")
    assert agente.propose_actions() == []

    agente.update_working_memory({"valor": 0})
    assert agente.propose_actions() == [{"goal": "somar"}]


def test_evaluate_and_select_marks_candidates_as_pending():
    agente = _AgenteDeTeste("test-4")
    candidates = [{"goal": "somar"}]

    selected = agente.evaluate_and_select(candidates)

    assert selected == [{"goal": "somar", "status": "pending"}]


def test_execute_falls_back_to_second_strategy_when_primary_fails():
    agente = _AgenteDeTeste("test-5", fail_primary=True)
    agente.update_working_memory({"valor": 5})

    agente.run_coala_cycle()

    assert agente.calls == ["primaria", "fallback"]
    assert agente.working_memory["resultado"] == 0


def test_execute_marks_action_failed_when_all_strategies_fail():
    agente = _AgenteDeTeste("test-6", fail_all=True)
    agente.update_working_memory({"valor": 5})

    agente.run_coala_cycle()

    assert "resultado" not in agente.working_memory
    assert len(agente._failed_actions) == 2  # primária + fallback, ambas registradas


def test_execute_marks_failed_when_no_strategy_registered_for_goal():
    agente = _AgenteDeTeste("test-7")
    actions = [{"goal": "goal_inexistente", "status": "pending"}]

    agente.execute(actions)

    assert actions[0]["status"] == "failed"
    assert agente._failed_actions == [{"goal": "goal_inexistente", "status": "failed"}]


def test_episodic_memory_records_one_entry_per_attempt_success_and_failure():
    agente = _AgenteDeTeste("test-8", fail_primary=True)
    agente.update_working_memory({"valor": 5})

    agente.run_coala_cycle()

    assert [e["status"] for e in agente.episodic_memory] == ["failed", "completed"]
    assert agente.episodic_memory[0]["action"] == "somar"
    assert agente.episodic_memory[0]["detail"] == "falha simulada na estratégia primária"
    assert all("timestamp" in e for e in agente.episodic_memory)


def test_semantic_memory_is_populated_at_init_and_readable():
    agente = _AgenteDeTeste("test-9")
    assert agente.semantic_memory == {"limiar": 10}


def test_action_failure_carries_action_and_reason():
    action = {"goal": "x"}
    exc = ActionFailure(action, "motivo")

    assert exc.action is action
    assert exc.reason == "motivo"
    assert "motivo" in str(exc)
