"""Redação por ramo (hierárquica) e registro de mensagens entre agentes."""

from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agents.analytical import redacao_ramos
from agents.analytical.sintetizador import TextSynthesizer
from agents.base import Mensagem
from agents.hierarchical.coordinator import CoordenadorGeral
from agents.hierarchical.supervisors import SupervisorAnalitico
from agents.star.orchestrator import OrquestradorEstrela

ANOS = (2019, 2020, 2021)


def _despesas():
    return [
        {"subfuncao": sf, "subfuncaoNome": "x", "ano": a, "valor": 1000.0 * (i + 1) + sf}
        for sf in (302, 305) for i, a in enumerate(ANOS)
    ]


def _indicadores():
    return [{"tipo": "internacoes", "ano": a, "valor": 50.0 + i * 7} for i, a in enumerate(ANOS)] + [
        {"tipo": "dengue", "ano": a, "valor": 300.0 - i * 40} for i, a in enumerate(ANOS)
    ]


class TestParticao:
    def test_agrupa_por_sistema_com_contexto_e_lacunas_do_ramo(self):
        correlacoes = [
            {"subfuncao": 305, "tipo_indicador": "dengue"},
            {"subfuncao": 302, "tipo_indicador": "internacoes"},
            {"subfuncao": 305, "tipo_indicador": "casos"},
        ]
        anomalias = [{"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2020}]
        cobertura = {"summary": {"indicadores_completeness": 0.5}, "gaps": [
            {"tipo_indicador": "casos", "description": "casos sem dados"},
            {"tipo_indicador": "internacoes", "description": "internações sem dados"},
        ]}
        ramos = redacao_ramos.particionar(
            correlacoes, anomalias, {"305": {"t": 1}, "302": {"t": 2}, "301": {}}, cobertura
        )
        assert list(ramos) == ["sinan", "sih", "covid"]
        assert len(ramos["sinan"]["correlacoes"]) == 1 and len(ramos["sinan"]["anomalias"]) == 1
        assert set(ramos["sih"]["contexto_orcamentario"]) == {"302"}
        assert [g["description"] for g in ramos["covid"]["data_coverage"]["gaps"]] == ["casos sem dados"]
        assert ramos["covid"]["data_coverage"]["summary"] == {"indicadores_completeness": 0.5}

    def test_subtipos_mapeiam_para_o_sistema(self):
        assert redacao_ramos.sistema_de("obitos") == "covid"
        assert redacao_ramos.sistema_de("cobertura_vacinal") == "sipni"
        assert redacao_ramos.sistema_de("leitos") == "cnes"
        assert redacao_ramos.sistema_de("desconhecido") == "outros"


def _rodar_supervisor(**kw):
    sup = SupervisorAnalitico("hier-sup-analitico-t")
    sup.registro_mensagens = []
    resultado = sup.run(
        analysis_id="a1", ws_queue=Queue(), despesas=_despesas(), indicadores=_indicadores(),
        contexto_orcamentario={}, date_from=2019, date_to=2021, use_llm=True, **kw,
    )
    return sup, resultado


class TestRedacaoPorRamo:
    def test_varios_sistemas_redige_por_ramo_e_compoe(self):
        with patch.object(TextSynthesizer, "generate_ramo", side_effect=lambda nome, *a, **k: f"texto {nome}") as ramo, \
             patch.object(TextSynthesizer, "generate_stream_composicao",
                          side_effect=lambda textos, *a, **k: iter(["final com ", str(len(textos)), " ramos"])) as comp:
            sup, r = _rodar_supervisor()
        assert ramo.call_count == 2
        comp.assert_called_once()
        assert r["redacao"]["modo"] == "por_ramo" and set(r["redacao"]["ramos"]) == {"sinan", "sih"}
        assert len(r["textos_parciais"]) == 2
        assert r["texto_analise"] == "final com 2 ramos"
        textos = [m for m in sup.registro_mensagens if m["natureza"] == "texto"]
        assert len(textos) == 2 and all(m["performativa"] == "inform" for m in textos)
        assert all(m["destinatario"] == "hier-sup-analitico-t" for m in textos)

    def test_ramo_que_falha_vai_cru_para_a_composicao(self):
        def ramo(nome, *a, **k):
            if nome.startswith("SIH"):
                raise RuntimeError("fora")
            return "texto sinan"

        with patch.object(TextSynthesizer, "generate_ramo", side_effect=ramo), \
             patch.object(TextSynthesizer, "generate_stream_composicao", return_value=iter(["ok"])) as comp:
            sup, r = _rodar_supervisor()
        assert r["redacao"]["falhas"] == ["sih"]
        sem_texto = comp.call_args.args[1]
        assert [s["sistema"] for s in sem_texto] == [redacao_ramos.NOME_SISTEMA["sih"]]
        assert any(m["performativa"] == "failure" for m in sup.registro_mensagens)

    def test_todos_os_ramos_falham_volta_ao_redator_unico(self):
        with patch.object(TextSynthesizer, "generate_ramo", side_effect=RuntimeError("fora")), \
             patch.object(TextSynthesizer, "generate_stream_composicao") as comp, \
             patch.object(TextSynthesizer, "generate_stream", return_value=iter(["unico"])):
            _, r = _rodar_supervisor()
        comp.assert_not_called()
        assert r["redacao"]["modo"] == "unica" and r["texto_analise"] == "unico"

    def test_um_sistema_so_escreve_direto(self):
        indicadores = [i for i in _indicadores() if i["tipo"] == "dengue"]
        sup = SupervisorAnalitico("s")
        with patch.object(TextSynthesizer, "generate_ramo") as ramo, \
             patch.object(TextSynthesizer, "generate_stream", return_value=iter(["unico"])):
            r = sup.run(analysis_id="a1", ws_queue=Queue(), despesas=_despesas(), indicadores=indicadores,
                        contexto_orcamentario={}, date_from=2019, date_to=2021, use_llm=True)
        ramo.assert_not_called()
        assert r["redacao"]["modo"] == "unica" and r["redacao"]["ramos"] == ["sinan"]

    def test_prompts_de_ramo_e_composicao_tem_fidelidade(self):
        synth = TextSynthesizer("x")
        p_ramo = synth._build_prompt_ramo("SINAN", [], [], {}, None, 2019, 2021, "dengue?")
        p_comp = synth._build_prompt_composicao([{"sistema": "SINAN", "texto": "t"}], [], None, None, 2019, 2021, None)
        for p in (p_ramo, p_comp):
            assert "FIDELIDADE AOS DADOS" in p and "nem como hipótese" in p
        assert "TEXTOS DOS RAMOS" in p_comp and "[SINAN]\nt" in p_comp


@pytest.fixture
def neo4j_client():
    client = MagicMock()
    client.get_indicadores_por_sistema.side_effect = lambda sistema, *a, **k: [
        i for i in _indicadores() if (sistema == "sinan") == (i["tipo"] == "dengue")
    ]
    client.get_despesas_por_subfuncao.side_effect = lambda codigos, *a, **k: [
        d for d in _despesas() if d["subfuncao"] == codigos[0]
    ]
    client.get_variacao_anual.return_value = []
    return client


PARAMS = {"date_from": 2019, "date_to": 2021, "health_params": ["dengue", "internacoes"], "use_llm": False}


class TestRegistroDeMensagens:
    def test_envelope(self):
        m = Mensagem.criar("a", "b", "inform", "texto", "abc")
        assert m.tamanho == 3 and m.conteudo == "abc"
        d = Mensagem.criar("a", "b", "inform", "dados", {"indicadores": [1, 2, 3]})
        assert d.conteudo == {"indicadores": 3} and d.tamanho > 0
        with pytest.raises(ValueError):
            Mensagem.criar("a", "b", "propose", "dados", {})

    def test_estrela_registra_trocas_com_os_agentes(self, neo4j_client):
        r = OrquestradorEstrela("star-orch", neo4j_client).run("m1", dict(PARAMS), Queue())
        msgs = r["mensagens"]
        assert msgs and all(m["performativa"] in ("request", "inform", "failure") for m in msgs)
        pedidos = [m for m in msgs if m["performativa"] == "request"]
        respostas = [m for m in msgs if m["performativa"] != "request"]
        assert len(pedidos) == len(respostas)
        assert {m["remetente"] for m in pedidos} == {"star-orch"}

    def test_hierarquica_registra_trocas_em_todos_os_niveis(self, neo4j_client):
        r = CoordenadorGeral("hier-coord", neo4j_client).run("m2", dict(PARAMS), Queue())
        remetentes = {m["remetente"] for m in r["mensagens"] if m["performativa"] == "request"}
        assert "hier-coord" in remetentes
        assert any(x.startswith("hier-sup-saude") for x in remetentes)
        assert any(x.startswith("hier-sup-orcamento") for x in remetentes)
        # a resposta do supervisor analítico ao coordenador é o texto
        assert any(
            m["remetente"].startswith("hier-sup-analitico") and m["natureza"] == "texto"
            for m in r["mensagens"]
        )

    def test_ferramentas_nao_geram_mensagens(self, neo4j_client):
        params = dict(PARAMS, health_params=["covid", "dengue"])
        r = OrquestradorEstrela("star-orch", neo4j_client).run("m3", params, Queue())
        assert not any("covid" in m["destinatario"] for m in r["mensagens"])


class TestPerguntaFocalSoTrazOIndicadorPedido:
    """Ponta a ponta: numa pergunta sobre dengue, nenhum achado de outro agravo."""

    def test_estrela_nao_traz_outro_agravo(self):
        client = MagicMock()
        consultados = []

        def indicadores(sistema, subtipos, *a, **k):
            consultados.append(list(subtipos))
            return [{"tipo": t, "ano": ano, "valor": 10.0 + i + ano % 7}
                    for t in subtipos for i, ano in enumerate(ANOS)]

        client.get_indicadores_por_sistema.side_effect = indicadores
        client.get_despesas_por_subfuncao.side_effect = lambda codigos, *a, **k: [
            {"subfuncao": codigos[0], "subfuncaoNome": "x", "ano": ano, "valor": 1000.0 * (i + 1)}
            for i, ano in enumerate(ANOS)
        ]
        client.get_variacao_anual.return_value = []
        params = dict(PARAMS, health_params=["dengue"])
        r = OrquestradorEstrela("star-orch", client).run("m4", params, Queue())
        assert consultados == [["dengue"]]
        tipos = {c["tipo_indicador"] for c in r["correlacoes"]} | {a["tipo_indicador"] for a in r["anomalias"]}
        assert tipos <= {"dengue"}
