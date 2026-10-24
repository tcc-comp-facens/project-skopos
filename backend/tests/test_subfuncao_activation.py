"""Consistência entre a regra de ativação das subfunções orçamentárias e o mapa de cruzamento.

Regressão: `nascidos_vivos` (SINASC) e `producao_ambulatorial` (SIA) constavam em
`SUBFUNCAO_INDICADOR_MAP` (301 e 302), mas não em `_SUBFUNCAO_TOKENS`. Resultado: a
despesa nunca era buscada para esses dois indicadores e o cruzamento orçamento ×
indicador ficava sempre vazio (R1 = 0,29).
"""

from __future__ import annotations

from queue import Queue
from unittest.mock import MagicMock

import pytest

import agents.hierarchical.supervisors as supervisors
import agents.star.orchestrator as orchestrator
from agents.data_crossing import SUBFUNCAO_INDICADOR_MAP
from agents.hierarchical.coordinator import CoordenadorGeral
from agents.intent.agente_interpretacao_intencao import VALID_HEALTH_PARAMS
from agents.star.orchestrator import OrquestradorEstrela


def _subfuncoes_ativadas(modulo, health_param: str) -> set[int]:
    return {
        codigo
        for codigo in modulo._SUBFUNCAO_TOKENS
        if modulo._subfuncao_ativa(codigo, {health_param})
    }


class TestTabelasDeRegra:
    def test_estrela_e_hierarquica_usam_as_mesmas_regras(self):
        assert orchestrator._SUBFUNCAO_TOKENS == supervisors._SUBFUNCAO_TOKENS
        assert orchestrator.INDICADOR_TO_AGENT == supervisors.INDICADOR_TO_AGENT
        assert orchestrator._ORCAMENTO_SUBFUNCOES == supervisors._ORCAMENTO_SUBFUNCOES

    @pytest.mark.parametrize("modulo", [orchestrator, supervisors], ids=["estrela", "hierarquica"])
    def test_todo_health_param_ativa_ao_menos_uma_subfuncao(self, modulo):
        sem_orcamento = [
            hp for hp in VALID_HEALTH_PARAMS if not _subfuncoes_ativadas(modulo, hp)
        ]
        assert sem_orcamento == [], (
            f"health_params sem nenhuma despesa associada (cruzamento sempre vazio): {sem_orcamento}"
        )

    @pytest.mark.parametrize("modulo", [orchestrator, supervisors], ids=["estrela", "hierarquica"])
    def test_nascidos_vivos_ativa_atencao_basica(self, modulo):
        assert 301 in _subfuncoes_ativadas(modulo, "nascidos_vivos")
        assert 301 in SUBFUNCAO_INDICADOR_MAP and "nascidos_vivos" in SUBFUNCAO_INDICADOR_MAP[301]

    @pytest.mark.parametrize("modulo", [orchestrator, supervisors], ids=["estrela", "hierarquica"])
    def test_producao_ambulatorial_ativa_assistencia_hospitalar(self, modulo):
        assert 302 in _subfuncoes_ativadas(modulo, "producao_ambulatorial")
        assert 302 in SUBFUNCAO_INDICADOR_MAP and "producao_ambulatorial" in SUBFUNCAO_INDICADOR_MAP[302]

    @pytest.mark.parametrize("modulo", [orchestrator, supervisors], ids=["estrela", "hierarquica"])
    def test_ativacao_nova_nao_vaza_para_outras_subfuncoes(self, modulo):
        assert _subfuncoes_ativadas(modulo, "nascidos_vivos") == {301}
        assert _subfuncoes_ativadas(modulo, "producao_ambulatorial") == {302}

    @pytest.mark.parametrize("modulo", [orchestrator, supervisors], ids=["estrela", "hierarquica"])
    def test_ativacao_anterior_continua_igual(self, modulo):
        assert _subfuncoes_ativadas(modulo, "vacinacao") == {301}
        assert _subfuncoes_ativadas(modulo, "internacoes") == {302}


def _neo4j_simulado() -> MagicMock:
    """Neo4j simulado que ecoa o que foi pedido, um ponto por ano."""
    anos = list(range(2016, 2024))

    def despesas(subfuncao_codigos, date_from, date_to, dimensao=None):
        return [
            {"subfuncao": sf, "subfuncaoNome": f"Subfuncao {sf}", "ano": ano, "valor": 1000.0 * (i + 1)}
            for sf in subfuncao_codigos
            for i, ano in enumerate(anos)
        ]

    def indicadores(sistema, subtipos, date_from, date_to, dimensao=None):
        return [
            {"tipo": subtipo, "ano": ano, "valor": 10.0 + (i * 3) % 7}
            for subtipo in subtipos
            for i, ano in enumerate(anos)
        ]

    client = MagicMock()
    client.get_despesas_por_subfuncao.side_effect = despesas
    client.get_indicadores_por_sistema.side_effect = indicadores
    client.query_log = []
    return client


def _params(health_param: str) -> dict:
    return {
        "date_from": 2016, "date_to": 2023, "health_params": [health_param],
        "use_llm": False, "intent_summary": None, "use_self_check": False,
    }


@pytest.mark.parametrize(
    ("health_param", "subfuncao", "tipo"),
    [("nascidos_vivos", 301, "nascidos_vivos"), ("producao_ambulatorial", 302, "producao_ambulatorial")],
)
class TestFluxoCompleto:
    def test_estrela_busca_a_despesa_e_cruza(self, health_param, subfuncao, tipo):
        client = _neo4j_simulado()
        result = OrquestradorEstrela("t-estrela", client).run("a1", _params(health_param), Queue())

        pedidas = [c.args[0] for c in client.get_despesas_por_subfuncao.call_args_list]
        assert pedidas == [[subfuncao]]
        assert {d["subfuncao"] for d in result["despesas"]} == {subfuncao}
        assert {c["tipo_indicador"] for c in result["dados_cruzados"]} == {tipo}
        assert result["dados_cruzados"]

    def test_hierarquica_busca_a_despesa_e_cruza(self, health_param, subfuncao, tipo):
        client = _neo4j_simulado()
        result = CoordenadorGeral("t-hier", client).run("a2", _params(health_param), Queue())

        pedidas = [c.args[0] for c in client.get_despesas_por_subfuncao.call_args_list]
        assert pedidas == [[subfuncao]]
        assert {d["subfuncao"] for d in result["despesas"]} == {subfuncao}
        assert {c["tipo_indicador"] for c in result["dados_cruzados"]} == {tipo}
        assert result["dados_cruzados"]
