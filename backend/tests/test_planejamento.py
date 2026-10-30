"""Planejamento por LLM nos coordenadores (agents/planejamento.py)."""

import json
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agents import planejamento
from agents.hierarchical.coordinator import CoordenadorGeral
from agents.star.orchestrator import OrquestradorEstrela

CATALOGO = [{"id": "a", "descricao": "A"}, {"id": "b", "descricao": "B"}, {"id": "c", "descricao": "C"}]


def _planejar(resposta, proposta=("a", "b"), **kw):
    gerar = resposta if callable(resposta) else (lambda *a, **k: resposta)
    with patch("core.llm_client.generate", side_effect=gerar):
        return planejamento.planejar(
            agent_id="n", papel="p", objetivo="o", candidatos=CATALOGO,
            proposta=list(proposta), use_llm=True, **kw,
        )


class TestPlanejar:
    def test_sem_llm_vale_a_proposta(self):
        with patch("core.llm_client.generate") as gerar:
            plano = planejamento.planejar(agent_id="n", papel="p", objetivo="o", candidatos=CATALOGO,
                                          proposta=["b", "a"], use_llm=False)
        gerar.assert_not_called()
        assert plano["escolhidos"] == ["a", "b"] and plano["origem"] == "proposta"

    def test_llm_acrescenta_e_retira_com_justificativa(self):
        plano = _planejar(json.dumps({
            "acrescentar": [{"id": "c", "justificativa": "pedido na pergunta"}],
            "retirar": [{"id": "a", "justificativa": "fora do tema"}],
            "justificativa": "ok",
        }))
        assert plano["origem"] == "llm" and plano["escolhidos"] == ["b", "c"]
        assert plano["acrescimos"] == [{"id": "c", "justificativa": "pedido na pergunta"}]
        assert plano["retiradas"][0]["id"] == "a" and plano["proposta"] == ["a", "b"]

    def test_confirmacao_mantem_a_proposta(self):
        plano = _planejar('```json\n{"acrescentar": [], "retirar": []}\n```')
        assert plano["origem"] == "llm" and plano["escolhidos"] == ["a", "b"]

    def test_mudanca_sem_efeito_e_ignorada(self):
        plano = _planejar(json.dumps({"acrescentar": [{"id": "a", "justificativa": "j"}],
                                      "retirar": [{"id": "c", "justificativa": "j"}]}))
        assert plano["escolhidos"] == ["a", "b"] and not plano["acrescimos"] and not plano["retiradas"]

    @pytest.mark.parametrize("resposta", [
        "",
        "não é json",
        "[]",
        json.dumps({"acrescentar": [{"id": "z", "justificativa": "inventado"}]}),
        json.dumps({"acrescentar": [{"id": "c"}]}),
        json.dumps({"retirar": [{"id": "a", "justificativa": "x"}, {"id": "b", "justificativa": "y"}]}),
    ])
    def test_resposta_invalida_vale_a_proposta(self, resposta):
        plano = _planejar(resposta)
        assert plano["origem"] == "llm_failed_fallback" and plano["escolhidos"] == ["a", "b"]

    def test_falha_da_chamada_vale_a_proposta(self):
        def falha(*a, **k):
            raise RuntimeError("fora do ar")
        assert _planejar(falha)["origem"] == "llm_failed_fallback"

    def test_subobjetivos_so_para_os_ids_pedidos_com_padrao(self):
        plano = _planejar(
            json.dumps({"subobjetivos": {"a": "entregar A", "b": "entregar B"}}),
            subobjetivos_padrao={"a": "padrão A", "c": "padrão C"},
        )
        assert plano["subobjetivos"] == {"a": "entregar A"}
        fallback = _planejar("", subobjetivos_padrao={"a": "padrão A"})
        assert fallback["subobjetivos"] == {"a": "padrão A"}

    def test_prompt_lista_o_catalogo_e_marca_a_proposta(self):
        prompts = []
        with patch("core.llm_client.generate", side_effect=lambda p, **k: prompts.append(p) or "{}"):
            planejamento.planejar(agent_id="n", papel="p", objetivo="dengue em 2020", candidatos=CATALOGO,
                                  proposta=["a"], use_llm=True)
        assert '"a": A [na proposta]' in prompts[0] and '"c": C\n' in prompts[0]
        assert "dengue em 2020" in prompts[0]
        assert planejamento.REGRA_ESCOPO in prompts[0]

    def test_objetivo_do_ramo_carrega_os_indicadores_da_pergunta(self):
        obj = planejamento.objetivo_do_ramo("Levantar os dados da covid.", "covid?", ["covid"], 2016, 2025)
        assert obj == "Levantar os dados da covid. Indicadores identificados na pergunta: covid."
        sem = planejamento.objetivo_do_ramo(None, "covid?", ["covid"], 2016, 2025)
        assert sem == planejamento.objetivo_da_analise("covid?", ["covid"], 2016, 2025)


@pytest.fixture
def neo4j_client():
    client = MagicMock()
    client.query_log = []
    client.get_indicadores_por_sistema.return_value = []
    client.get_despesas_por_subfuncao.return_value = []
    client.get_variacao_anual.return_value = []
    return client


PARAMS = {"date_from": 2019, "date_to": 2021, "health_params": ["dengue"], "use_llm": False}


class TestNasTopologias:
    def test_estrela_registra_o_plano_da_proposta(self, neo4j_client):
        r = OrquestradorEstrela("o", neo4j_client).run("a1", dict(PARAMS), Queue())
        plano = r["plano"]["orquestrador"]
        assert plano["origem"] == "proposta"
        assert "saude:sinan" in plano["escolhidos"] and "orcamento:305" in plano["escolhidos"]

    def test_estrela_aciona_o_que_o_llm_acrescenta(self, neo4j_client):
        orq = OrquestradorEstrela("o", neo4j_client)
        orq.working_memory.update({"health_params": ["dengue"], "use_llm": True, "date_from": 2019,
                                   "date_to": 2021})
        resposta = json.dumps({"acrescentar": [{"id": "saude:sih", "justificativa": "internações"}]})
        with patch("core.llm_client.generate", return_value=resposta):
            acoes = orq.evaluate_and_select(orq.propose_actions())
        saude = [a["agent_key"] for a in acoes if a["goal"] == "consultar_saude"]
        goals = [a["goal"] for a in acoes]
        assert saude == ["sih", "sinan"]
        assert goals.index("revisar_entregas") > goals.index("consultar_orcamento")
        assert goals.count("revisar_entregas") == 1

    def test_hierarquica_planeja_por_nivel(self, neo4j_client):
        r = CoordenadorGeral("c", neo4j_client).run("a2", dict(PARAMS), Queue())
        assert set(r["plano"]) == {"coordenador", "supervisor_orcamento", "supervisor_saude"}
        assert r["plano"]["coordenador"]["escolhidos"] == ["ramo:orcamento", "ramo:saude", "ramo:contexto"]
        assert r["plano"]["supervisor_saude"]["escolhidos"] == ["saude:sinan"]

    def test_subobjetivo_do_coordenador_vai_como_texto_ao_supervisor(self, neo4j_client):
        def gerar(prompt, caller="", **k):
            if caller.endswith(":planejar") and "coordenador geral" in prompt:
                return json.dumps({"retirar": [{"id": "ramo:contexto", "justificativa": "não pedido"}],
                                   "subobjetivos": {"ramo:saude": "Trazer os casos de dengue."}})
            if caller.endswith(":planejar"):
                return "{}"
            raise RuntimeError("sem LLM")

        with patch("core.llm_client.generate", side_effect=gerar):
            r = CoordenadorGeral("c", neo4j_client).run("a3", dict(PARAMS, use_llm=True), Queue())
        assert r["plano"]["supervisor_saude"]["objetivo"].startswith("Trazer os casos de dengue.")
        assert "Indicadores identificados na pergunta: dengue" in r["plano"]["supervisor_saude"]["objetivo"]
        pedidos = [m for m in r["mensagens"] if m["performativa"] == "request" and m["remetente"] == "c"]
        assert any(m["natureza"] == "texto" and m["conteudo"] == "Trazer os casos de dengue." for m in pedidos)
        assert not any(m["destinatario"].startswith("hier-sup-contexto") for m in pedidos)


def _metrica(fila: Queue) -> dict:
    while not fila.empty():
        evento = fila.get()
        if evento.get("type") == "metric":
            return evento["payload"]
    raise AssertionError("sem evento metric")


class TestOverheadDeliberacao:
    """O overhead se divide em deliberação (espera pelo LLM dos coordenadores
    no planejamento e na revisão) e estrutura (o restante)."""

    @staticmethod
    def _lento(prompt, caller="", **k):
        import time
        if caller.endswith(":planejar") or caller.endswith(":revisar_entregas"):
            time.sleep(0.05)
            return "{}"
        raise RuntimeError("sem LLM")

    def test_metrica_separa_deliberacao_e_estrutura(self):
        from core.quality_metrics import compute_coordination_overhead
        r = compute_coordination_overhead([
            {"agentName": "coordenador_geral", "executionTimeMs": 1000.0, "deliberacaoMs": 900.0},
            {"agentName": "supervisor_saude", "executionTimeMs": 5000.0},
            {"agentName": "sinan", "executionTimeMs": 250.0},
        ])
        assert r["supervisor_time_ms"] == 1000.0
        assert r["deliberation_time_ms"] == 900.0 and r["structural_time_ms"] == 100.0
        assert r["deliberation_share"] == 0.9

    def test_entrada_sem_deliberacao_conta_tudo_como_estrutura(self):
        from core.quality_metrics import compute_coordination_overhead
        r = compute_coordination_overhead([{"agentName": "orquestrador_estrela", "executionTimeMs": 40.0}])
        assert r["deliberation_time_ms"] == 0.0 and r["structural_time_ms"] == 40.0

    def test_estrela_mede_planejamento_e_revisao(self, neo4j_client):
        fila = Queue()
        with patch("core.llm_client.generate", side_effect=self._lento):
            OrquestradorEstrela("o", neo4j_client).run("d1", dict(PARAMS, use_llm=True), fila)
        m = _metrica(fila)
        raiz = next(a for a in m["agentMetrics"] if a["agentName"] == "orquestrador_estrela")
        assert 50 <= raiz["deliberacaoMs"] <= raiz["executionTimeMs"]
        assert m["deliberationTimeMs"] == raiz["deliberacaoMs"]

    def test_hierarquica_soma_coordenador_e_supervisores(self, neo4j_client):
        fila = Queue()
        with patch("core.llm_client.generate", side_effect=self._lento):
            CoordenadorGeral("c", neo4j_client).run("d2", dict(PARAMS, use_llm=True), fila)
        raiz = next(a for a in _metrica(fila)["agentMetrics"] if a["agentName"] == "coordenador_geral")
        # coordenador planeja; os supervisores de orçamento e de saúde planejam (e revisam,
        # quando há entregas): pelo menos três esperas de 50 ms
        assert 150 <= raiz["deliberacaoMs"] <= raiz["executionTimeMs"]
