"""Harness de escalabilidade — Etapa 6 do PLANO_REFATORACAO.md.

Roda a comparação estrela-vs-hierárquica variando artificialmente o
volume de dados (nº de anos / registros de despesa e indicador) e mede
como E1 (overhead de coordenação), E2 (breakdown de latência) e o volume
de comunicação (Etapa 6) evoluem com o tamanho de N. `use_llm=False` em
todo o harness (determinístico, sem custo/latência de rede) — o objetivo
aqui é medir como a *estrutura* das duas topologias escala com o volume
de dados, não a latência de uma API externa (essa parte, variação de
custo de tokens com N, exigiria chamadas reais e fica fora do escopo de
um harness automatizado em CI; ver nota no PLANO_REFATORACAO.md).

Produz ao menos 2 pontos de comparação (N pequeno vs. N maior), como
pede o critério de aceite da Etapa 6. Sem thresholds fixos/arbitrários
(mesma lógica de D12 — nenhuma fonte prescreve limiar de overhead
"aceitável"): o harness reporta os números via log e valida invariantes
estruturais (nenhuma exceção, todos os dados processados, Q1 continua
consistente), não compara contra um corte artificial.
"""

from __future__ import annotations

import logging
from queue import Queue
from unittest.mock import MagicMock

import pytest

from agents.hierarchical.coordinator import CoordenadorGeral
from agents.star.orchestrator import OrquestradorEstrela
from core.quality_metrics import (
    compute_coordination_overhead,
    compute_communication_volume,
    compute_deterministic_consistency,
    compute_latency_breakdown,
)

logger = logging.getLogger(__name__)

ALL_HEALTH_PARAMS = ["dengue", "covid", "internacoes", "vacinacao", "mortalidade"]
# Fase 2 (PLANO_NOVO_MODELO_DADOS.md §5): decomposição completa — nenhum
# agente de saúde consulta despesas, só AgenteOrcamentoSubfuncao. Com
# ALL_HEALTH_PARAMS, 5 subfunções são ativadas: 301 (vacinacao->sipni),
# 302 (internacoes->sih), 303/304 (dengue->sinan, "qualquer agravo
# ativa"), 305 (dengue/covid->sinan+covid) — ver _SUBFUNCAO_TOKENS em
# agents/star/orchestrator.py.
SUBFUNCOES = [301, 302, 303, 304, 305]

# ALL_HEALTH_PARAMS ativa 5 agentes de saúde (covid, sih, sipni, sim,
# sinan) — cada um consulta um nº fixo de subtipos por ano. sinan
# consulta os 9 subtipos SINAN de uma vez, covid os 2 (casos+obitos),
# sipni os 2 (cobertura_vacinal+doses_aplicadas) — nenhum é filtrável
# por health_params nesta fase (ver agents/domain/agente_*.py).
INDICADORES_POR_ANO = 1 + 2 + 2 + 1 + 9  # sih+covid+sipni+sim(mortalidade)+9 SINAN


def _synthetic_neo4j_client(n_years: int) -> MagicMock:
    """Gera um cliente Neo4j mockado com despesas/indicadores sintéticos
    escalando linearmente com n_years — usado para simular o crescimento
    da base de dados mencionado como motivação da Etapa 2.

    `get_despesas_por_subfuncao`/`get_indicadores_por_sistema` (schema
    novo, Fase 1) ecoam de volta exatamente o que foi pedido (códigos de
    subfunção / subtipos), 1 linha por ano — suficiente para o harness
    estrutural, sem tentar replicar o filtro real de período."""
    anos = list(range(2010, 2010 + n_years))

    def _despesas_por_subfuncao(subfuncao_codigos, date_from, date_to, dimensao=None):
        return [
            {"subfuncao": sf, "subfuncaoNome": f"Subfuncao {sf}", "ano": ano, "valor": 1000.0 * (i + 1)}
            for i, sf in enumerate(subfuncao_codigos)
            for ano in anos
        ]

    def _indicadores_por_sistema(sistema, subtipos, date_from, date_to, dimensao=None):
        return [
            {"tipo": subtipo, "ano": ano, "valor": 10.0 + i}
            for i, subtipo in enumerate(subtipos)
            for ano in anos
        ]

    client = MagicMock()
    client.get_despesas_por_subfuncao.side_effect = _despesas_por_subfuncao
    client.get_indicadores_por_sistema.side_effect = _indicadores_por_sistema
    client.save_metrica = MagicMock()
    return client


def _run_star(n_years: int) -> dict:
    neo4j_client = _synthetic_neo4j_client(n_years)
    orchestrator = OrquestradorEstrela(f"bench-star-{n_years}", neo4j_client)
    ws_queue: Queue = Queue()
    params = {
        "date_from": 2010, "date_to": 2010 + n_years,
        "health_params": ALL_HEALTH_PARAMS, "use_llm": False,
    }
    result = orchestrator.run(f"bench-analysis-star-{n_years}", params, ws_queue)

    agent_metrics = []
    while not ws_queue.empty():
        event = ws_queue.get()
        if event.get("type") == "metric":
            agent_metrics = event["payload"]["agentMetrics"]
    return {"result": result, "agent_metrics": agent_metrics}


def _run_hierarchical(n_years: int) -> dict:
    neo4j_client = _synthetic_neo4j_client(n_years)
    coordinator = CoordenadorGeral(f"bench-hier-{n_years}", neo4j_client)
    ws_queue: Queue = Queue()
    params = {
        "date_from": 2010, "date_to": 2010 + n_years,
        "health_params": ALL_HEALTH_PARAMS, "use_llm": False,
    }
    result = coordinator.run(f"bench-analysis-hier-{n_years}", params, ws_queue)

    agent_metrics = []
    while not ws_queue.empty():
        event = ws_queue.get()
        if event.get("type") == "metric":
            agent_metrics = event["payload"]["agentMetrics"]
    return {"result": result, "agent_metrics": agent_metrics}


def _benchmark_point(n_years: int) -> dict:
    star = _run_star(n_years)
    hier = _run_hierarchical(n_years)

    star_e1 = compute_coordination_overhead(star["agent_metrics"])
    hier_e1 = compute_coordination_overhead(hier["agent_metrics"])
    star_e2 = compute_latency_breakdown(star["agent_metrics"])
    hier_e2 = compute_latency_breakdown(hier["agent_metrics"])
    star_comm = compute_communication_volume(
        "star", star["agent_metrics"],
        despesas_count=len(star["result"].get("despesas", [])),
        indicadores_count=len(star["result"].get("indicadores", [])),
    )
    hier_comm = compute_communication_volume(
        "hierarchical", hier["agent_metrics"],
        despesas_count=len(hier["result"].get("despesas", [])),
        indicadores_count=len(hier["result"].get("indicadores", [])),
    )
    consistency = compute_deterministic_consistency(star["result"], hier["result"])

    return {
        "n_years": n_years,
        "star": {"e1": star_e1, "e2": star_e2, "communication": star_comm, "result": star["result"]},
        "hierarchical": {"e1": hier_e1, "e2": hier_e2, "communication": hier_comm, "result": hier["result"]},
        "consistency": consistency,
    }


N_SMALL = 3
N_LARGE = 10


class TestScalabilityBenchmark:
    """Critério de aceite da Etapa 6: pelo menos 2 pontos de comparação
    (N pequeno vs. N maior)."""

    def test_produces_two_comparison_points_without_errors(self):
        pequeno = _benchmark_point(N_SMALL)
        grande = _benchmark_point(N_LARGE)

        for ponto, label in [(pequeno, "pequeno"), (grande, "grande")]:
            logger.info(
                "Escalabilidade [N=%s/%s]: estrela overhead=%.1f%% workers=%.1fms | "
                "hierárquica overhead=%.1f%% workers=%.1fms | "
                "comunicação estrela=%d msgs, hierárquica=%d msgs",
                label, ponto["n_years"],
                ponto["star"]["e1"]["overhead_percent"], ponto["star"]["e1"]["worker_time_ms"],
                ponto["hierarchical"]["e1"]["overhead_percent"], ponto["hierarchical"]["e1"]["worker_time_ms"],
                ponto["star"]["communication"]["message_count"],
                ponto["hierarchical"]["communication"]["message_count"],
            )

        assert pequeno["n_years"] == N_SMALL
        assert grande["n_years"] == N_LARGE

    def test_all_domain_data_is_processed_regardless_of_n(self):
        """Invariante estrutural: nenhum dado é descartado silenciosamente
        conforme N cresce — despesas/indicadores no resultado escalam
        exatamente com o volume sintético gerado (4 subfunções x N anos,
        13 subtipos de indicador x N anos — Fase 1)."""
        for n_years in (N_SMALL, N_LARGE):
            ponto = _benchmark_point(n_years)
            expected_despesas = len(SUBFUNCOES) * n_years
            expected_indicadores = INDICADORES_POR_ANO * n_years
            assert len(ponto["star"]["result"]["despesas"]) == expected_despesas
            assert len(ponto["star"]["result"]["indicadores"]) == expected_indicadores
            assert len(ponto["hierarchical"]["result"]["despesas"]) == expected_despesas
            assert len(ponto["hierarchical"]["result"]["indicadores"]) == expected_indicadores

    def test_deterministic_consistency_holds_at_every_scale(self):
        """Q1 não pode quebrar conforme a base cresce — correlações e
        anomalias devem continuar numericamente idênticas entre as duas
        topologias em qualquer N."""
        for n_years in (N_SMALL, N_LARGE):
            ponto = _benchmark_point(n_years)
            assert ponto["consistency"]["all_identical"] is True, (
                f"Q1 divergiu em N={n_years}: {ponto['consistency']['divergences']}"
            )

    def test_hierarchical_communication_grows_by_fixed_lateral_overhead(self):
        """O overhead de comunicação lateral da hierárquica (Etapa 5) é
        fixo (3 hops + 2 resumos) independente de N — não cresce com o
        volume de dados, só o volume de payload cresce. Confirma
        estruturalmente a mitigação de risco registrada na Etapa 5
        ("fixo em nº de supervisores, seguro para escala")."""
        pequeno = _benchmark_point(N_SMALL)
        grande = _benchmark_point(N_LARGE)

        assert pequeno["hierarchical"]["communication"]["lateral_hops"] == 3
        assert grande["hierarchical"]["communication"]["lateral_hops"] == 3
        assert pequeno["star"]["communication"]["lateral_hops"] == 0
        assert grande["star"]["communication"]["lateral_hops"] == 0

        # O payload (nº de registros), ao contrário do overhead lateral,
        # cresce com N — confirma que N=grande de fato move mais dados.
        assert (
            grande["hierarchical"]["communication"]["payload_records"]
            > pequeno["hierarchical"]["communication"]["payload_records"]
        )
