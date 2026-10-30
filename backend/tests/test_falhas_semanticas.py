"""Testes da falha semântica da bateria (scripts/falhas_semanticas.py) e da
consolidação do experimento de revisão (scripts/analise_revisao.py)."""

import json
from contextlib import ExitStack

import pytest

from agents.base import AgenteCoALA
from scripts import analise_revisao
from scripts.falhas_semanticas import FATORES, InjetorCorrupcao, avaliar_contra_gabarito, corromper


def _registros(n=10, inicio=2016):
    return [{"tipo": "dengue", "ano": inicio + i, "valor": 100 + i} for i in range(n)]


class TestCorromper:
    def test_mesma_semente_mesma_corrupcao(self):
        a = corromper(_registros(), "x|sinan|0")
        b = corromper(_registros(), "x|sinan|0")
        assert a == b

    def test_fracao_e_valores(self):
        regs = _registros()
        copias, corrompidos = corromper(regs, "s")
        assert len(corrompidos) == 2  # 20% de 10
        for c in corrompidos:
            i = c["indice"]
            assert copias[i]["valor"] == c["valor_corrompido"] != regs[i]["valor"]
            assert isinstance(copias[i]["valor"], int)  # inteiro continua inteiro
            assert c["limpo"] == regs[i]
            assert c["chave"] == ["dengue", regs[i]["ano"], None, None]
        assert regs == _registros()  # originais intactos

    def test_ao_menos_um_e_zeros_preservados(self):
        regs = [{"tipo": "t", "ano": 2020, "valor": 0}, {"tipo": "t", "ano": 2021, "valor": 3.5}]
        copias, corrompidos = corromper(regs, "s")
        assert [c["indice"] for c in corrompidos] == [1]
        assert copias[0]["valor"] == 0
        assert copias[1]["valor"] / 3.5 in FATORES

    def test_sem_elegiveis(self):
        assert corromper([{"tipo": "t", "ano": 1, "valor": 0}], "s") == ([{"tipo": "t", "ano": 1, "valor": 0}], [])


class _AgenteSaude(AgenteCoALA):
    def query(self, analysis_id, *args, **kwargs):
        self.working_memory.update({"analysis_id": analysis_id, "indicadores": _registros()})
        return {"indicadores": self.working_memory["indicadores"]}


class TestInjetor:
    def _instalar(self, persistente):
        injetor = InjetorCorrupcao("sinan", persistente=persistente)
        stack = ExitStack()
        injetor.aplicar(stack, (_AgenteSaude,))
        return injetor, stack

    def test_transitoria_reenvio_limpo(self):
        injetor, stack = self._instalar(False)
        with stack:
            agente = _AgenteSaude("star-sinan-1")
            entregue = agente.query("a1")
            reenvio = agente.reenviar()
        assert entregue["indicadores"] != _registros()
        assert reenvio["indicadores"] == _registros()
        assert agente.working_memory["indicadores"] == _registros()  # memória do agente limpa
        g = injetor.gabarito("a1")
        assert list(g) == ["star"] and g["star"]["agente"] == "sinan"
        assert len(g["star"]["entregas"]) == 1

    def test_persistente_corrompe_todo_reenvio(self):
        injetor, stack = self._instalar(True)
        with stack:
            agente = _AgenteSaude("hier-sinan-1")
            agente.query("a1")
            agente.reenviar()
        assert len(injetor.gabarito("a1")["hierarchical"]["entregas"]) == 2

    def test_outro_agente_nao_e_corrompido(self):
        injetor, stack = self._instalar(False)
        with stack:
            entregue = _AgenteSaude("star-sim-1").query("a1")
        assert entregue["indicadores"] == _registros()
        assert injetor.gabarito("a1") == {}

    def test_patch_e_desfeito(self):
        _, stack = self._instalar(False)
        with stack:
            pass
        assert _AgenteSaude("star-sinan-1").query("a1")["indicadores"] == _registros()


def _gabarito(corrompidos):
    return {"agente": "sinan", "persistente": False,
            "entregas": [{"numero": 0, "registros": 10, "corrompidos": corrompidos}]}


class TestAvaliarContraGabarito:
    def _fakes(self):
        consultas = []

        def analisar(dados):
            consultas.append(dados)
            return {"correlacoes": [], "anomalias": []}

        def cruzar(despesas, indicadores):
            return [{"n": len(indicadores), "soma": sum(i["valor"] for i in indicadores)}]

        def consistencia(a, b):
            return {"all_identical": a == b}

        return analisar, cruzar, consistencia, consultas

    def test_conta_contaminados_perdidos_e_sinais(self):
        regs = _registros()
        copias, corrompidos = corromper(regs, "s")
        i0, i1 = corrompidos[0]["indice"], corrompidos[1]["indice"]
        finais = [r for j, r in enumerate(copias) if j != i1]  # i1 descartado, i0 segue corrompido
        resultado = {
            "indicadores": finais, "despesas": [], "correlacoes": [], "anomalias": [],
            "revisao": {"sinalizados": [
                {"agente": "sinan", "chave": corrompidos[1]["chave"]},
                {"agente": "sinan", "chave": ["dengue", 1900, None, None]},
            ]},
        }
        analisar, cruzar, consistencia, consultas = self._fakes()
        m = avaliar_contra_gabarito(resultado, _gabarito(corrompidos), analisar, cruzar, consistencia)
        assert m == {"corrupcao_registros": 2, "revisao_vp": 1, "revisao_fp": 1,
                     "contaminados_finais": 1, "perdidos": 1, "achados_iguais_limpos": 1}
        # os achados "limpos" são calculados com todos os 10 registros, valores limpos
        assert consultas[0] == [{"n": 10, "soma": sum(r["valor"] for r in regs)}]
        assert regs[i0]["valor"] != finais[[r["ano"] for r in finais].index(regs[i0]["ano"])]["valor"]

    def test_sem_gabarito(self):
        analisar, cruzar, consistencia, _ = self._fakes()
        m = avaliar_contra_gabarito({"revisao": {"sinalizados": [{}]}}, None, analisar, cruzar, consistencia)
        assert m["corrupcao_registros"] == 0 and m["revisao_fp"] == 1 and m["achados_iguais_limpos"] is None


# ---------------------------------------------------------------------------
# Consolidação
# ---------------------------------------------------------------------------


def _linha(cenario, topo, escopo, vp, fp, iguais, tokens, corrompidos=5, erro=None):
    return {
        "scenario": cenario, "topology": topo, "fault": "dados_corrompidos", "escopo": escopo,
        "alvo_falha": "sinan", "corrupcao_registros": corrompidos, "revisao_vp": vp, "revisao_fp": fp,
        "revisao_corrigidos": vp, "contaminados_finais": corrompidos - vp, "perdidos": 0,
        "achados_iguais_limpos": iguais, "revisao_chamadas": 1 if topo == "star" else 2,
        "total_tokens": tokens, "llm_call_count": 8, "wall_clock_ms": 5000.0, "overhead_ms": 300.0,
        "revisao_falhou": 0, "run_error": erro,
    }


class TestAnaliseRevisao:
    def _rodadas(self):
        r1 = [
            _linha("c1", "star", "focal", 5, 0, 1, 10000), _linha("c1", "hierarchical", "focal", 4, 1, 0, 11000),
            _linha("c2", "star", "amplo", 2, 0, 0, 20000), _linha("c2", "hierarchical", "amplo", 5, 0, 1, 21000),
            # alvo não acionado nesta rodada: fica fora
            _linha("c3", "star", "amplo", 0, 0, None, 1, corrompidos=0),
            _linha("c3", "hierarchical", "amplo", 0, 0, None, 1, corrompidos=0),
            # linha da bateria comum: ignorada
            {"scenario": "x", "topology": "star", "fault": "nenhuma"},
        ]
        return [r1]

    def test_agrega_por_topologia_e_escopo(self):
        a = analise_revisao.analisar(self._rodadas(), ["r1.jsonl"])
        assert a["analises_validas"] == 2 and a["analises_fora"] == 1
        s, h = a["geral"]["star"], a["geral"]["hierarchical"]
        assert s["registros_corrompidos"] == 10 and s["sinais_em_corrompidos"] == 7
        assert s["sensibilidade"] == pytest.approx(0.7) and s["precisao"] == 1.0
        assert h["sensibilidade"] == pytest.approx(0.9) and h["precisao"] == pytest.approx(9 / 10)
        assert s["achados_iguais_limpos"] == 1 and h["achados_iguais_limpos"] == 1
        assert a["por_escopo"]["focal"]["star"]["analises"] == 1
        assert a["por_escopo"]["intermediario"]["comparacao"] is None
        comp = a["comparacao_geral"]
        assert comp["fisher_achados_iguais_limpos_p"] == pytest.approx(1.0)
        assert comp["pareadas"]["total_tokens"]["analises_hier_maior"] == 2

    def test_main_grava_json_e_planilha(self, tmp_path, monkeypatch):
        arq = tmp_path / "r1.jsonl"
        arq.write_text("\n".join(json.dumps(l) for l in self._rodadas()[0]), encoding="utf-8")
        out = tmp_path / "saida"
        monkeypatch.setattr("sys.argv", ["analise_revisao", "--rodada", str(arq), "--out", str(out)])
        assert analise_revisao.main() == 0
        dados = json.loads((out / "analise_revisao.json").read_text(encoding="utf-8"))
        assert dados["analises_validas"] == 2
        assert (out / "analise_revisao.xlsx").exists()
