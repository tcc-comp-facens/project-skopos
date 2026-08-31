"""Testes da integração RAGAS no WebSocket (api/websocket.py).

Cobre a cola entre `core/ragas_metrics.py` e o que chega ao cliente: o
encaixe do resultado no payload de qualidade, a contabilização de custo
por arquitetura e a formatação do bloco de texto transmitido.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from api import websocket as ws
from api.state import active_results
from core import ragas_metrics as rm

RESULTADO = {
    "correlacoes": [
        {"subfuncao": 305, "tipo_indicador": "dengue", "spearman": 0.82, "classificacao": "alta"},
    ],
    "anomalias": [],
    "contexto_orcamentario": {},
    "texto_analise": "A subfunção 305 apresenta correlação alta com dengue.",
}


def _payload(metrics, *, errors=None):
    return {
        "framework": "ragas",
        "version": "0.4.3",
        "judge": {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "embedding_model": "text-embedding-3-small",
        },
        "sample": {"n_contexts_total": 40, "response_chars": 1840},
        "metrics": metrics,
        "errors": errors or [],
        "available": True,
        "unavailable_reason": None,
    }


class _EmbeddingsFalsa:
    resolved_model = "text-embedding-3-small"


class _MetricaFalsa:
    def __init__(self, value):
        self.value = value

    async def ascore(self, **kwargs):
        return type("MetricResult", (), {"value": self.value})()


class TestMetricLabels:
    def test_rotulos_batem_com_os_nomes_reais_das_metricas(self):
        """Regressão: os rótulos do relatório são casados por chave com o
        que `build_metrics` produz. Um nome dessincronizado não quebra
        nada — só faz a métrica aparecer eternamente como "não
        disponível", que é o pior tipo de falha silenciosa numa métrica.
        """
        rotulos = {chave for chave, _ in ws.RAGAS_METRIC_LABELS}
        metricas, _ = rm.build_metrics()
        produzidos = {nome for nome, _, _ in metricas}
        assert rotulos == produzidos

    def test_cobre_os_tres_aspectos_do_paper(self):
        assert len(ws.RAGAS_METRIC_LABELS) == 3


class TestFormatRagasReport:
    def test_mostra_as_duas_arquiteturas_lado_a_lado(self):
        star = _payload({
            "faithfulness": {"score": 0.8571},
            "answer_relevancy": {"score": 0.9123},
            "context_relevance": {"score": 0.62},
        })
        hier = _payload({
            "faithfulness": {"score": 0.79},
            "answer_relevancy": {"score": 0.5},
            "context_relevance": {"score": 0.88},
        })
        texto = ws._format_ragas_report({"star": star, "hierarchical": hier})

        assert "Fidelidade aos dados" in texto
        assert "Relevância da resposta" in texto
        assert "Relevância do contexto" in texto
        assert "0.86" in texto and "0.79" in texto  # scores das duas
        assert "0.62" in texto and "0.88" in texto
        assert "gpt-5.6-luna" in texto  # o juiz usado fica registrado

    def test_score_ausente_e_explicito(self):
        payload = _payload({"faithfulness": {"score": None}})
        texto = ws._format_ragas_report({"star": payload, "hierarchical": payload})
        assert "não disponível" in texto
        assert "0.00" not in texto  # ausência nunca vira zero

    def test_reporta_quantos_achados_foram_avaliados(self):
        """Não há mais teto: todas as métricas veem o conjunto completo,
        porque nenhuma escala em chamadas com o número de achados."""
        payload = _payload({"faithfulness": {"score": 0.9}})
        texto = ws._format_ragas_report({"star": payload, "hierarchical": payload})
        assert "40 achados avaliados" in texto
        assert "RAGAS_MAX_CONTEXTS" not in texto

    def test_erro_de_metrica_aparece_no_texto(self):
        star = _payload(
            {"faithfulness": {"score": None}},
            errors=[{"metric": "faithfulness", "error": "RuntimeError: falha de rede"}],
        )
        texto = ws._format_ragas_report({"star": star, "hierarchical": _payload({})})
        assert "RuntimeError: falha de rede" in texto

    def test_indisponivel_explica_o_motivo(self):
        indisponivel = {
            "available": False,
            "unavailable_reason": "OPENAI_API_KEY não configurada (RAGAS_PROVIDER=openai)",
            "metrics": {},
        }
        texto = ws._format_ragas_report(
            {"star": indisponivel, "hierarchical": indisponivel}
        )
        assert "não executada" in texto
        assert "OPENAI_API_KEY" in texto
        assert "SCORES" not in texto


class TestRunRagasEvaluation:
    @pytest.fixture(autouse=True)
    def _limpa_estado(self, monkeypatch):
        monkeypatch.setenv("RAGAS_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
        active_results.pop("analise-teste", None)
        yield
        active_results.pop("analise-teste", None)

    def _run(self, results):
        active_results["analise-teste"] = results
        self.contextos_recebidos: dict[str, list[str]] = {}

        class _Captura(_MetricaFalsa):
            def __init__(inner, value, nome, destino):
                super().__init__(value)
                inner.nome, inner.destino = nome, destino

            async def ascore(inner, **kwargs):
                inner.destino[inner.nome] = kwargs.get("retrieved_contexts", [])
                return await super().ascore(**kwargs)

        metricas = [
            ("faithfulness",
             _Captura(0.9, "faithfulness", self.contextos_recebidos), rm.ARGS_COM_CONTEXTO),
            ("answer_relevancy", _MetricaFalsa(0.8), rm.ARGS_PERGUNTA_RESPOSTA),
            ("context_relevance", _MetricaFalsa(0.7), rm.ARGS_CONTEXTO_SEM_RESPOSTA),
        ]
        with patch.object(rm, "build_metrics", return_value=(metricas, _EmbeddingsFalsa())):
            texto, _ = asyncio.run(
                ws._run_ragas_evaluation(
                    analysis_id="analise-teste",
                    results=results,
                    star_result=RESULTADO,
                    hier_result=RESULTADO,
                )
            )
        return texto

    def test_encaixa_o_resultado_no_payload_de_qualidade(self):
        results = {
            "quality_metrics": {"quality": {"star": {}, "hierarchical": {}}},
            "source_question": "Qual subfunção gasta mal?",
        }
        self._run(results)
        qm = results["quality_metrics"]
        for arch in ("star", "hierarchical"):
            assert qm["quality"][arch]["ragas"]["metrics"]["faithfulness"]["score"] == 0.9
        assert "ragas" in qm["cost"]
        assert set(qm["cost"]["ragas"]) == {"star", "hierarchical"}

    def test_payload_resultante_e_serializavel(self):
        results = {
            "quality_metrics": {"quality": {"star": {}, "hierarchical": {}}},
            "source_question": "pergunta",
        }
        self._run(results)
        assert json.dumps(results["quality_metrics"])

    def test_usa_a_pergunta_original_como_user_input(self):
        results = {
            "quality_metrics": {"quality": {"star": {}, "hierarchical": {}}},
            "source_question": "Qual subfunção gasta mal?",
        }
        self._run(results)
        sample = results["quality_metrics"]["quality"]["star"]["ragas"]["sample"]
        assert sample["user_input"] == "Qual subfunção gasta mal?"

    def test_sem_pergunta_usa_fallback_em_vez_de_string_vazia(self):
        """`answer_relevancy` compara a resposta com a pergunta; um
        user_input vazio produziria um score sem sentido em vez de um
        erro visível. O período vem de `active_results`, não do `result`
        dos orquestradores — que não carrega essas datas."""
        results = {
            "quality_metrics": {"quality": {"star": {}, "hierarchical": {}}},
            "source_question": None,
            "date_from": 2019,
            "date_to": 2022,
        }
        self._run(results)
        sample = results["quality_metrics"]["quality"]["star"]["ragas"]["sample"]
        assert "2019" in sample["user_input"] and "2022" in sample["user_input"]

    def test_sem_pergunta_e_sem_periodo_nao_produz_interrogacao(self):
        """Antes o fallback lia uma chave inexistente e sempre gerava
        'no período de ? a ?'."""
        results = {"quality_metrics": {"quality": {"star": {}, "hierarchical": {}}}}
        self._run(results)
        sample = results["quality_metrics"]["quality"]["star"]["ragas"]["sample"]
        assert "de ? a ?" not in sample["user_input"]
        assert "período" not in sample["user_input"]

    def test_periodo_chega_ao_contexto_do_juiz(self):
        results = {
            "quality_metrics": {"quality": {"star": {}, "hierarchical": {}}},
            "source_question": "pergunta",
            "date_from": 2019,
            "date_to": 2022,
        }
        self._run(results)
        contexts = self.contextos_recebidos["faithfulness"]
        assert any("2019 a 2022" in c for c in contexts)

    def test_arquiteturas_avaliadas_em_paralelo_com_custos_isolados(self):
        """`asyncio.gather` cria uma Task por corrotina e cada Task recebe
        uma cópia do contexto, então o TokenBucket aberto dentro de cada
        uma fica isolado. Se os buckets vazassem entre si, o custo das duas
        topologias se somaria no mesmo balde."""
        import core.llm_client as llm_client

        ativos: list[int] = []
        concorrencia_max = 0

        class _MetricaLenta:
            async def ascore(self, **kwargs):
                nonlocal concorrencia_max
                ativos.append(1)
                concorrencia_max = max(concorrencia_max, len(ativos))
                # Registra consumo no bucket ativo desta Task.
                llm_client.record_token_usage(
                    {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}
                )
                await asyncio.sleep(0.01)
                ativos.pop()
                return type("R", (), {"value": 1.0})()

        results = {
            "quality_metrics": {"quality": {"star": {}, "hierarchical": {}}},
            "source_question": "pergunta",
        }
        active_results["analise-teste"] = results
        metricas = [("faithfulness", _MetricaLenta(), rm.ARGS_COM_CONTEXTO)]
        with patch.object(rm, "build_metrics", return_value=(metricas, _EmbeddingsFalsa())):
            asyncio.run(
                ws._run_ragas_evaluation(
                    analysis_id="analise-teste", results=results,
                    star_result=RESULTADO, hier_result=RESULTADO,
                )
            )

        assert concorrencia_max == 2, "as duas arquiteturas deveriam rodar juntas"
        custos = results["quality_metrics"]["cost"]["ragas"]
        assert custos["star"]["total_tokens"] == 10
        assert custos["hierarchical"]["total_tokens"] == 10

    def test_sem_quality_metrics_nao_estoura(self):
        """A avaliação roda depois do relatório; se o cálculo das métricas
        determinísticas tiver falhado antes, isto não pode derrubar a
        conexão."""
        texto = self._run({"source_question": "pergunta"})
        assert "Fidelidade aos dados" in texto


class TestCabecalhoDoJuiz:
    def test_fallback_de_embeddings_aparece_no_cabecalho(self):
        """O cabeçalho não pode anunciar o modelo pedido quando a cadeia
        de fallback acabou usando outro — o relatório mentiria sobre o
        instrumento de medida."""
        payload = _payload({"faithfulness": {"score": 0.9}})
        payload["judge"]["embedding_model_used"] = "text-embedding-ada-002"
        texto = ws._format_ragas_report({"star": payload, "hierarchical": payload})
        assert "text-embedding-ada-002 (fallback de text-embedding-3-small)" in texto

    def test_sem_fallback_mostra_so_o_modelo(self):
        payload = _payload({"faithfulness": {"score": 0.9}})
        payload["judge"]["embedding_model_used"] = "text-embedding-3-small"
        texto = ws._format_ragas_report({"star": payload, "hierarchical": payload})
        assert "fallback" not in texto
        assert "embeddings: text-embedding-3-small" in texto
