"""Testes da revisão das entregas (agents/revisao_entregas.py) e da sua
integração nas duas topologias.

Nenhum teste chama o LLM real: `core.llm_client.generate` é substituído por
um revisor falso que lê o prompt.
"""

import json
import re
from contextlib import ExitStack
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agents import revisao_entregas as rev
from agents.domain.agente_sim import AgenteSIM
from agents.hierarchical.coordinator import CoordenadorGeral
from agents.hierarchical.supervisors import SupervisorOrcamento, SupervisorSaude
from agents.star.orchestrator import OrquestradorEstrela
from scripts.falhas_semanticas import InjetorCorrupcao

MORTALIDADE_LIMPA = {2016: 4100, 2017: 4200, 2018: 4150, 2019: 4300, 2020: 5600, 2021: 6100}


def _indicadores():
    return [{"tipo": "mortalidade", "ano": a, "valor": v} for a, v in MORTALIDADE_LIMPA.items()]


class _Agente:
    """Agente falso: devolve, a cada reenvio, a próxima lista da fila."""

    def __init__(self, reenvios):
        self._reenvios = list(reenvios)
        self.chamadas = 0

    def reenviar(self):
        self.chamadas += 1
        item = self._reenvios.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"indicadores": item}


def _entrega(registros, instancia=None, agente="sim"):
    return {"agente": agente, "campo": "indicadores", "instancia": instancia, "registros": registros}


def _sinal(indice):
    return {"entrega": 0, "indice": indice, "chave": [], "serie": "S1", "rotulo": "x", "ano": 0, "valor": 0}


# ---------------------------------------------------------------------------
# Séries, prompt e leitura da resposta
# ---------------------------------------------------------------------------


class TestSeriesEPrompt:
    def test_agrupa_por_serie_e_ordena_por_ano(self):
        regs = [
            {"tipo": "dengue", "ano": 2018, "valor": 10},
            {"tipo": "dengue", "ano": 2016, "valor": 7},
            {"tipo": "dengue", "ano": 2017, "valor": 8, "dimensao_valor": "0-4"},
        ]
        series = rev.montar_series([_entrega(regs, agente="sinan")])
        assert [s["id"] for s in series] == ["S1", "S2"]
        assert series[0]["rotulo"] == "sinan/dengue"
        assert [p["ano"] for p in series[0]["pontos"]] == [2016, 2018]
        assert series[1]["rotulo"] == "sinan/dengue [0-4]"

    def test_despesas_viram_series_de_subfuncao(self):
        entrega = {"agente": "orcamento_subfuncao", "campo": "despesas", "instancia": None, "registros": [
            {"subfuncao": 305, "subfuncaoNome": "Vigilância Epidemiológica", "ano": 2020, "valor": 1.5},
        ]}
        prompt = rev.montar_prompt(rev.montar_series([entrega]), "dengue x gasto", 2019, 2021)
        assert "S1: orçamento/subfunção 305 (Vigilância Epidemiológica) → 2020=1.50" in prompt
        assert "de 2019 a 2021" in prompt
        assert '{"suspeitos": []}' in prompt

    def test_valores_inteiros_sem_casas_decimais(self):
        prompt = rev.montar_prompt(rev.montar_series([_entrega(_indicadores())]))
        assert "2016=4100; 2017=4200" in prompt


class TestRevisarEntregas:
    def test_sinal_volta_ao_registro_pela_serie_e_ano(self):
        resposta = json.dumps({"suspeitos": [{"serie": "S1", "ano": 2019, "motivo": "salto"}]})
        with patch("core.llm_client.generate", return_value=resposta) as gen:
            sinais, resumo = rev.revisar_entregas([_entrega(_indicadores())], caller="t")
        gen.assert_called_once()
        assert resumo["chamadas"] == 1 and resumo["falhou"] is False
        assert len(sinais) == 1
        assert sinais[0]["indice"] == 3 and sinais[0]["chave"] == ["mortalidade", 2019, None, None]

    def test_resposta_em_bloco_markdown_e_serie_inexistente(self):
        resposta = '```json\n{"suspeitos": [{"serie": "S9", "ano": 2019}, {"serie": "S1", "ano": "2020"}]}\n```'
        with patch("core.llm_client.generate", return_value=resposta):
            sinais, _ = rev.revisar_entregas([_entrega(_indicadores())], caller="t")
        assert [s["ano"] for s in sinais] == [2020]

    @pytest.mark.parametrize("resposta", [None, "não é json", '{"outra": 1}'])
    def test_falha_do_llm_nao_sinaliza_nada(self, resposta):
        with patch("core.llm_client.generate", return_value=resposta):
            sinais, resumo = rev.revisar_entregas([_entrega(_indicadores())], caller="t")
        assert sinais == [] and resumo["falhou"] is True and resumo["erro"]

    def test_sem_chave_de_api_a_revisao_falha_sem_derrubar(self):
        # conftest remove as chaves: generate() devolve None sem chamar o provedor
        finais, resumo = rev.revisar_e_resolver(
            [_entrega(_indicadores())], revisor="orquestrador_estrela", caller="t"
        )
        assert resumo["falhou"] is True and resumo["revisores"] == ["orquestrador_estrela"]
        assert finais[0] == _indicadores()

    def test_sem_registros_nao_chama_o_llm(self):
        with patch("core.llm_client.generate") as gen:
            sinais, resumo = rev.revisar_entregas([], caller="t")
        gen.assert_not_called()
        assert resumo["chamadas"] == 0 and sinais == []


# ---------------------------------------------------------------------------
# Contestação por reenvio
# ---------------------------------------------------------------------------


class TestResolverSuspeitos:
    def test_mesmo_valor_no_reenvio_confirma(self):
        regs = _indicadores()
        agente = _Agente([_indicadores()])
        resumo = rev.resumo_vazio()
        finais = rev.resolver_suspeitos([_entrega(regs, agente)], [_sinal(2)], resumo)
        assert finais[0] == regs
        assert resumo["confirmados"] == 1 and resumo["reenvios"] == 1 and agente.chamadas == 1

    def test_valor_diferente_que_se_repete_corrige(self):
        regs = _indicadores()
        regs[2] = dict(regs[2], valor=41500)  # corrompido em trânsito
        agente = _Agente([_indicadores(), _indicadores()])
        resumo = rev.resumo_vazio()
        finais = rev.resolver_suspeitos([_entrega(regs, agente)], [_sinal(2)], resumo)
        assert finais[0][2]["valor"] == MORTALIDADE_LIMPA[2018]
        assert regs[2]["valor"] == 41500  # o original não é alterado
        assert resumo["corrigidos"] == 1 and resumo["reenvios"] == 2
        assert resumo["sinalizados"][0]["decisao"] == "corrigido"

    def test_sem_maioria_descarta(self):
        regs = _indicadores()
        v1, v2 = _indicadores(), _indicadores()
        v1[2]["valor"], v2[2]["valor"] = 1, 2
        resumo = rev.resumo_vazio()
        finais = rev.resolver_suspeitos([_entrega(regs, _Agente([v1, v2]))], [_sinal(2)], resumo)
        assert len(finais[0]) == len(regs) - 1
        assert resumo["descartados"] == 1

    def test_reenvio_que_falha_mantem_o_original(self):
        regs = _indicadores()
        resumo = rev.resumo_vazio()
        finais = rev.resolver_suspeitos(
            [_entrega(regs, _Agente([RuntimeError("fora")]))], [_sinal(1)], resumo
        )
        assert finais[0] == regs and resumo["nao_verificados"] == 1

    def test_aplicar_na_memoria_reconstroi_as_listas(self):
        e1 = _entrega([{"tipo": "a", "ano": 1, "valor": 1}], agente="sim")
        e2 = {"agente": "orcamento_subfuncao", "campo": "despesas", "instancia": None,
              "registros": [{"subfuncao": 301, "ano": 1, "valor": 9}]}
        wm = {"indicadores": "antigo", "despesas": "antigo"}
        rev.aplicar_na_memoria(wm, [e1, e2], [[{"tipo": "a", "ano": 1, "valor": 2}], []])
        assert wm["indicadores"] == [{"tipo": "a", "ano": 1, "valor": 2}] and wm["despesas"] == []

    def test_combinar_resumos_soma_os_revisores(self):
        a = dict(rev.resumo_vazio(["supervisor_saude"]), chamadas=1, corrigidos=2,
                 sinalizados=[{"x": 1}])
        b = dict(rev.resumo_vazio(["supervisor_orcamento"]), chamadas=1, falhou=True, erro="e")
        total = rev.combinar_resumos([b, None, a])
        assert total["revisores"] == ["supervisor_orcamento", "supervisor_saude"]
        assert total["chamadas"] == 2 and total["corrigidos"] == 2 and total["falhou"] is True
        assert total["sinalizados"] == [{"x": 1}]


# ---------------------------------------------------------------------------
# Integração nas duas topologias
# ---------------------------------------------------------------------------


def _revisor_perfeito(chamadas):
    """LLM falso que sinaliza todo valor de mortalidade diferente do limpo."""

    def generate(prompt, *args, caller="?", **kwargs):
        chamadas.append(caller)
        suspeitos = []
        for linha in prompt.splitlines():
            m = re.match(r"(S\d+): sim/mortalidade → (.*)$", linha)
            if not m:
                continue
            for par in m.group(2).split("; "):
                ano, valor = par.split("=")
                if float(valor) != MORTALIDADE_LIMPA[int(ano)]:
                    suspeitos.append({"serie": m.group(1), "ano": int(ano), "motivo": "salto"})
        return json.dumps({"suspeitos": suspeitos})

    return generate


@pytest.fixture
def neo4j_client():
    client = MagicMock()
    client.get_indicadores_por_sistema.side_effect = lambda *a, **k: _indicadores()
    client.get_despesas_por_subfuncao.side_effect = lambda codigos, *a, **k: [
        {"subfuncao": codigos[0], "subfuncaoNome": "x", "ano": ano, "valor": 1000.0 + ano}
        for ano in MORTALIDADE_LIMPA
    ]
    client.get_variacao_anual.return_value = []
    return client


PARAMS = {"date_from": 2016, "date_to": 2021, "health_params": ["mortalidade"], "use_llm": False}


class TestIntegracao:
    def _rodar(self, topologia_cls, neo4j_client, analysis_id):
        chamadas = []
        injetor = InjetorCorrupcao("sim")
        with ExitStack() as stack:
            injetor.aplicar(stack, (AgenteSIM,))
            stack.enter_context(patch("core.llm_client.generate", _revisor_perfeito(chamadas)))
            prefixo = "star-orch" if topologia_cls is OrquestradorEstrela else "hier-coord"
            resultado = topologia_cls(prefixo, neo4j_client).run(analysis_id, dict(PARAMS), Queue())
        return resultado, chamadas, injetor.gabarito(analysis_id)

    def test_estrela_revisa_tudo_numa_chamada_e_corrige(self, neo4j_client):
        resultado, chamadas, gabarito = self._rodar(OrquestradorEstrela, neo4j_client, "a-star")
        corrompidos = gabarito["star"]["entregas"][0]["corrompidos"]
        assert corrompidos, "a injeção deveria ter corrompido algum registro"
        assert len(chamadas) == 1 and chamadas[0].endswith(":revisar_entregas")
        assert {r["ano"]: r["valor"] for r in resultado["indicadores"]} == MORTALIDADE_LIMPA
        assert resultado["revisao"]["corrigidos"] == len(corrompidos)
        assert resultado["revisao"]["revisores"] == ["orquestrador_estrela"]

    def test_hierarquica_revisa_por_ramo_e_corrige(self, neo4j_client):
        resultado, chamadas, gabarito = self._rodar(CoordenadorGeral, neo4j_client, "a-hier")
        corrompidos = gabarito["hierarchical"]["entregas"][0]["corrompidos"]
        assert corrompidos
        assert len(chamadas) == 2  # supervisor de orçamento e supervisor de saúde
        assert {r["ano"]: r["valor"] for r in resultado["indicadores"]} == MORTALIDADE_LIMPA
        assert resultado["revisao"]["corrigidos"] == len(corrompidos)
        assert sorted(resultado["revisao"]["revisores"]) == ["supervisor_orcamento", "supervisor_saude"]

    def test_mesma_corrupcao_nas_duas_topologias_da_mesma_analise(self, neo4j_client):
        _, _, g_star = self._rodar(OrquestradorEstrela, neo4j_client, "mesma")
        # Contadores do injetor são por agente; um injetor novo com o mesmo
        # analysis_id reproduz a corrupção na outra topologia.
        _, _, g_hier = self._rodar(CoordenadorGeral, neo4j_client, "mesma")
        assert g_star["star"]["entregas"][0]["corrompidos"] == g_hier["hierarchical"]["entregas"][0]["corrompidos"]


class TestProposta:
    def test_estrela_propoe_revisao_mesmo_sem_llm(self, neo4j_client):
        orq = OrquestradorEstrela("o", neo4j_client)
        orq.working_memory.update({"health_params": ["dengue"], "use_llm": False})
        goals = [a["goal"] for a in orq.propose_actions()]
        assert goals.index("revisar_entregas") < goals.index("cruzar_dados")
        assert goals.index("revisar_entregas") > goals.index("consultar_orcamento")

    def test_estrela_sem_agentes_de_dados_nao_revisa(self, neo4j_client):
        orq = OrquestradorEstrela("o", neo4j_client)
        orq.working_memory.update({"health_params": [], "use_llm": True})
        assert "revisar_entregas" not in [a["goal"] for a in orq.propose_actions()]

    def test_supervisores_propoem_revisao_antes_de_agregar(self, neo4j_client):
        saude = SupervisorSaude("s", neo4j_client)
        saude.working_memory.update({"health_params": ["dengue"]})
        orc = SupervisorOrcamento("o", neo4j_client)
        orc.working_memory.update({"health_params": ["dengue"]})
        for sup in (saude, orc):
            goals = [a["goal"] for a in sup.propose_actions()]
            assert goals[-2:] == ["revisar_entregas", "agregar_resultados"]
