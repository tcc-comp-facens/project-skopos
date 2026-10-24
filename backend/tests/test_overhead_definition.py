"""Definição única de overhead de coordenação nas duas topologias."""

from __future__ import annotations

import time
from queue import Queue
from unittest.mock import MagicMock

from agents.hierarchical.coordinator import CoordenadorGeral
from core.quality_metrics import compute_coordination_overhead, compute_latency_breakdown


def _m(nome: str, ms: float) -> dict:
    return {"agentName": nome, "executionTimeMs": ms}


class TestComputeCoordinationOverhead:
    def test_hierarquica_ignora_supervisores_que_ja_contem_os_subordinados(self):
        metrics = [
            _m("supervisor_saude", 100), _m("sipni", 100),
            _m("supervisor_orcamento", 50), _m("orcamento_subfuncao", 50),
            _m("coordenador_geral", 5),
        ]
        r = compute_coordination_overhead(metrics)
        assert r["supervisor_time_ms"] == 5
        assert r["worker_time_ms"] == 150
        assert r["overhead_percent"] == round(5 / 155 * 100, 2)

    def test_estrela_e_hierarquica_usam_a_mesma_definicao(self):
        star = [_m("sipni", 100), _m("orquestrador_estrela", 5)]
        hier = [_m("supervisor_saude", 100), _m("sipni", 100), _m("coordenador_geral", 5)]
        assert (
            compute_coordination_overhead(star)["overhead_percent"]
            == compute_coordination_overhead(hier)["overhead_percent"]
        )

    def test_sem_agentes_nao_divide_por_zero(self):
        assert compute_coordination_overhead([])["overhead_percent"] == 0.0


class TestLatencyBreakdown:
    def test_fase_supervisores_so_conta_o_no_raiz(self):
        metrics = [
            _m("supervisor_saude", 100), _m("sipni", 100), _m("coordenador_geral", 5),
        ]
        b = compute_latency_breakdown(metrics)
        assert b["supervisores"]["time_ms"] == 5
        assert b["dominio"]["time_ms"] == 100


class TestRelogioDaHierarquica:
    def test_gravar_metricas_nao_entra_no_relogio(self):
        """A gravação das métricas (lenta com Neo4j real) acontece depois do
        fim da parte determinística e não pode virar overhead."""
        coord = CoordenadorGeral("test-coord-relogio", MagicMock())
        inicio = time.time() - 10.0
        coletor_lento = MagicMock()
        coletor_lento.persist.side_effect = lambda *a, **k: time.sleep(0.2)
        coletor_lento.collect.return_value = {
            "agentType": "supervisor_saude", "executionTimeMs": 1.0, "cpuPercent": 0.0,
        }
        ws_queue: Queue = Queue()
        coord.working_memory.update({
            "analysis_id": "a1",
            "_ws_queue": ws_queue,
            "_coord_start": inicio,
            "_metrics_collectors": [coletor_lento],
            "_sup_orcamento": MagicMock(_collectors=[]),
            "_sup_saude": MagicMock(_collectors=[]),
            "_sup_analitico": MagicMock(_collectors=[], _coala_leaf_end_time=inicio + 0.5),
            "_sup_contexto": MagicMock(_collectors=[]),
        })

        coord._act_persistir_metricas({"goal": "persistir_metricas"})

        evento = ws_queue.get_nowait()
        assert evento["type"] == "metric"
        assert evento["payload"]["totalExecutionTimeMs"] == 500.0
        assert evento["payload"]["overheadTimeMs"] == 500.0
