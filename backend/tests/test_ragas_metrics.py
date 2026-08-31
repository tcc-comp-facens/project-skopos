"""Testes de core/ragas_metrics.py — a integração com a biblioteca RAGAS.

O que é testado aqui é a *cola*: a montagem da amostra a partir do
resultado de uma arquitetura, o adaptador que faz a ragas falar por
`core.llm_client`, a normalização dos scores para JSON e a degradação
graciosa. As métricas em si são da biblioteca e não são reimplementadas
nem re-testadas — o ponto da refatoração foi justamente parar de fazer
isso.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from agents.analytical.sintetizador import CONTEXTO_PANDEMIA, TRADUCAO_SUBFUNCOES
from core import ragas_metrics as rm

RESULTADO = {
    "correlacoes": [
        {"subfuncao": 305, "tipo_indicador": "dengue", "spearman": 0.82,
         "classificacao": "alta", "n_pontos": 4,
         "leitura": "positiva — LEITURA DESEJÁVEL: mais gasto acompanhou melhora"},
        {"subfuncao": 301, "tipo_indicador": "cobertura_vacinal", "spearman": 0.10,
         "classificacao": "baixa", "n_pontos": 4,
         "leitura": "positiva — LEITURA DESEJÁVEL: mais gasto acompanhou melhora"},
    ],
    "anomalias": [
        {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2021,
         "tipo_anomalia": "alto_gasto_baixo_resultado",
         "descricao": ("Subfunção 305 (Vigilância Epidemiológica) em 2021: gasto "
                       "acima da mediana (R$ 1.234.567,89) com indicador acima da "
                       "mediana (450.0 casos) — possível ineficiência")},
    ],
    "contexto_orcamentario": {"305": {"tendencia": "crescente", "variacao_media_percentual": 12.4}},
    "texto_analise": "A subfunção 305 apresenta correlação alta com dengue.",
}


@pytest.fixture
def judge_key(monkeypatch):
    monkeypatch.setenv("RAGAS_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    for var in ("RAGAS_MODEL", "RAGAS_EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)


# Chunks que `build_contexts` sempre emite, vindos do enquadramento que o
# prompt do sintetizador injeta (pandemia + uma linha por tradução de
# subfunção). Calculado da fonte para o teste não quebrar quando o
# sintetizador ganhar mais uma tradução.
N_ENQUADRAMENTO = 1 + len(TRADUCAO_SUBFUNCOES)


def _achados(contexts):
    """Só os chunks de dado, sem o enquadramento injetado."""
    return [
        c for c in contexts
        if not c.startswith(("Contexto fornecido ao gerador:", "Equivalência informada"))
    ]


class TestBuildContexts:
    def test_um_chunk_por_achado(self):
        # 2 correlações + 1 anomalia + 1 subfunção de contexto orçamentário
        assert len(_achados(rm.build_contexts(RESULTADO))) == 4

    def test_chunk_traz_o_dado_e_o_nome_da_subfuncao(self):
        corr = rm.build_contexts(RESULTADO)[0]
        assert "305" in corr and "Vigilância Epidemiológica" in corr
        assert "0.82" in corr and "alta" in corr

    def test_correlacao_traz_n_pontos_e_leitura(self):
        """O prompt do sintetizador manda usar `leitura` como fonte da
        verdade sobre desejável/indesejável. Sem ela no contexto, toda
        afirmação do texto sobre isso é reprovada por falta de evidência."""
        corr = rm.build_contexts(RESULTADO)[0]
        assert "4 pontos" in corr
        assert "LEITURA DESEJÁVEL" in corr

    def test_correlacao_sem_leitura_nao_quebra(self):
        """O ramo n<2 de analitico.py não emite `leitura`."""
        contexts = rm.build_contexts({
            "correlacoes": [{"subfuncao": 305, "tipo_indicador": "x",
                             "spearman": 0.0, "classificacao": "baixa", "n_pontos": 1}],
        })
        assert "Leitura do sinal" not in contexts[0]

    def test_anomalia_traz_o_ano(self):
        anomalia = next(c for c in rm.build_contexts(RESULTADO) if c.startswith("Anomalia"))
        assert "2021" in anomalia and "alto_gasto_baixo_resultado" in anomalia

    def test_anomalia_traz_a_descricao_com_o_valor_em_reais(self):
        """A descrição carrega o gasto em R$ e o valor do indicador — sem
        ela, nenhuma cifra citada no texto é verificável pelo juiz."""
        anomalia = next(c for c in rm.build_contexts(RESULTADO) if c.startswith("Anomalia"))
        assert "R$ 1.234.567,89" in anomalia

    def test_periodo_entra_como_contexto(self):
        contexts = rm.build_contexts(RESULTADO, 2019, 2022)
        assert any("2019 a 2022" in c for c in contexts)

    def test_cobertura_de_dados_entra_como_contexto(self):
        contexts = rm.build_contexts({
            "data_coverage": {
                "summary": {"despesas_completeness": 0.8, "indicadores_completeness": 0.5},
                "gaps": [{"description": "indicador dengue ausente em 2021"}],
            },
        })
        assert any("80%" in c and "50%" in c for c in contexts)
        assert any("dengue ausente em 2021" in c for c in contexts)

    def test_enquadramento_injetado_vem_da_mesma_fonte_do_prompt(self):
        """Se estas strings forem duplicadas em vez de importadas, o
        contexto do juiz diverge do prompt na primeira edição — e o texto
        passa a ser reprovado por afirmar o que mandamos afirmar."""
        contexts = rm.build_contexts({})
        assert any(CONTEXTO_PANDEMIA in c for c in contexts)
        for codigo, traducao in TRADUCAO_SUBFUNCOES.items():
            assert any(str(codigo) in c and traducao in c for c in contexts)

    def test_resultado_vazio_traz_so_o_enquadramento(self):
        assert len(rm.build_contexts({})) == N_ENQUADRAMENTO
        assert _achados(rm.build_contexts({})) == []

    def test_contexto_orcamentario_nao_dict_e_ignorado(self):
        contexts = rm.build_contexts({"contexto_orcamentario": {"305": "lixo"}})
        assert _achados(contexts) == []


class TestBuildSample:
    def test_amostra_mapeia_a_tripla_do_ragas(self):
        sample, info = rm.build_sample(RESULTADO, "Qual subfunção gasta mal?")
        assert sample["user_input"] == "Qual subfunção gasta mal?"
        assert sample["response"] == RESULTADO["texto_analise"]
        assert len(_achados(sample["retrieved_contexts"])) == 4
        assert info["n_contexts_total"] == 4 + N_ENQUADRAMENTO

    def test_nao_ha_mais_teto_de_contextos(self, monkeypatch):
        """O teto existia pelo custo da precisao de contexto (1 chamada LLM
        por chunk). `ContextRelevance` custa 2 chamadas independente do
        volume, entao nenhuma metrica escala com o numero de achados e o
        teto perdeu a funcao."""
        monkeypatch.setenv("RAGAS_MAX_CONTEXTS", "2")  # ignorado
        sample, info = rm.build_sample(RESULTADO, "pergunta")
        assert len(sample["retrieved_contexts"]) == 4 + N_ENQUADRAMENTO
        assert info["n_contexts_total"] == 4 + N_ENQUADRAMENTO
        assert "retrieved_contexts_capped" not in sample
        assert not hasattr(rm, "get_max_contexts")


class _Veredito(BaseModel):
    verdict: int
    reason: str


class TestSkoposRagasLLM:
    def test_anexa_o_json_schema_e_valida_a_resposta(self):
        """A ragas pede "devolva uma instância deste modelo Pydantic"; o
        `llm_client` fala texto puro. A ponte é mandar o schema no prompt
        e validar a resposta."""
        with patch("core.llm_client.generate", return_value='{"verdict": 1, "reason": "ok"}') as mock:
            saida = rm.SkoposRagasLLM().generate("julgue isto", _Veredito)

        assert isinstance(saida, _Veredito)
        assert saida.verdict == 1
        prompt_enviado = mock.call_args.args[0]
        assert "julgue isto" in prompt_enviado
        assert "verdict" in prompt_enviado  # o JSON Schema foi anexado

    def test_tolera_cerca_de_markdown(self):
        resposta = '```json\n{"verdict": 0, "reason": "nao"}\n```'
        with patch("core.llm_client.generate", return_value=resposta):
            assert rm.SkoposRagasLLM().generate("p", _Veredito).verdict == 0

    def test_tolera_texto_ao_redor_do_json(self):
        resposta = 'Claro! Aqui vai:\n{"verdict": 1, "reason": "sim"}\nEspero ter ajudado.'
        with patch("core.llm_client.generate", return_value=resposta):
            assert rm.SkoposRagasLLM().generate("p", _Veredito).verdict == 1

    def test_repassa_provider_caller_e_modelo(self):
        llm = rm.SkoposRagasLLM(model="modelo-x", caller="ragas-star")
        with patch("core.llm_client.generate", return_value='{"verdict": 1, "reason": "r"}') as mock:
            llm.generate("p", _Veredito)
        assert mock.call_args.kwargs["caller"] == "ragas-star"
        assert mock.call_args.kwargs["provider"] is llm.provider
        assert mock.call_args.args[1] == "modelo-x"

    def test_temperatura_zero_para_o_juiz(self):
        with patch("core.llm_client.generate", return_value='{"verdict": 1, "reason": "r"}') as mock:
            rm.SkoposRagasLLM().generate("p", _Veredito)
        assert mock.call_args.kwargs["temperature"] == rm.JUDGE_TEMPERATURE

    def test_llm_indisponivel_levanta_erro_claro(self):
        """`generate` devolve None quando o LLM falha. Deixar isso seguir
        estouraria lá dentro da biblioteca, longe da origem."""
        with patch("core.llm_client.generate", return_value=None):
            with pytest.raises(RuntimeError, match="indisponível"):
                rm.SkoposRagasLLM().generate("p", _Veredito)

    def test_agenerate_delega_ao_sincrono(self):
        with patch("core.llm_client.generate", return_value='{"verdict": 1, "reason": "r"}'):
            saida = asyncio.run(rm.SkoposRagasLLM().agenerate("p", _Veredito))
        assert saida.verdict == 1


class TestSkoposRagasEmbeddings:
    class _Resp:
        data = [type("D", (), {"embedding": [0.1, 0.2]})()]
        usage = type("U", (), {"prompt_tokens": 7, "total_tokens": 7})()

    class _Client:
        embeddings = type("E", (), {"create": staticmethod(lambda **kw: TestSkoposRagasEmbeddings._Resp())})()

        def with_options(self, **kwargs):
            return self

    def test_embed_text_devolve_o_vetor(self):
        with patch("core.llm_client.build_client", return_value=self._Client()), \
             patch("core.llm_client.record_token_usage"):
            assert rm.SkoposRagasEmbeddings().embed_text("texto") == [0.1, 0.2]

    def test_contabiliza_tokens_dos_embeddings(self):
        """O endpoint de embeddings não passa por generate(), então o
        custo precisa ser registrado à mão — sem isso o eixo D
        subestimaria o custo de rodar a avaliação."""
        with patch("core.llm_client.build_client", return_value=self._Client()), \
             patch("core.llm_client.record_token_usage") as mock_record:
            rm.SkoposRagasEmbeddings().embed_text("texto")
        assert mock_record.call_args.args[0]["total_tokens"] == 7

    def _client_que_recusa(self, recusados, status=403, msg="does not have access to model"):
        """Cliente que devolve 403 model_not_found para os modelos em
        `recusados` e sucesso para qualquer outro."""
        usados = []

        class _Erro(Exception):
            def __init__(self, model):
                super().__init__(f"Error code: {status} - {{'message': '{msg} `{model}`'}}")
                self.status_code = status

        def create(**kw):
            usados.append(kw["model"])
            if kw["model"] in recusados:
                raise _Erro(kw["model"])
            return TestSkoposRagasEmbeddings._Resp()

        return type("C", (), {
            "embeddings": type("E", (), {"create": staticmethod(create)})(),
            "with_options": lambda self, **kw: self,
        })(), usados

    def test_403_tenta_sem_o_cabecalho_antes_de_trocar_de_modelo(self):
        """O cabeçalho OpenAI-Project dispara uma checagem de acesso que
        falha em algumas configurações mesmo com o modelo liberado. Vale
        tentar o mesmo modelo sem ele antes de desistir dele."""
        client, usados = self._client_que_recusa({"text-embedding-3-large"})
        with patch("core.llm_client.build_client", return_value=client),              patch("core.llm_client.record_token_usage"):
            emb = rm.SkoposRagasEmbeddings()
            assert emb.embed_text("t") == [0.1, 0.2]
        # 3-large com cabeçalho, 3-large sem cabeçalho, e só então 3-small
        assert usados == [
            "text-embedding-3-large", "text-embedding-3-large", "text-embedding-3-small",
        ]
        assert emb.resolved_model == "text-embedding-3-small"

    def test_contorno_do_cabecalho_resolve_sem_trocar_de_modelo(self):
        """Quando o problema é só o cabeçalho, o modelo configurado é
        mantido — trocar de modelo mudaria o instrumento de medida à toa."""
        recusa_com_header = {"text-embedding-3-large"}
        usados: list[tuple[str, bool]] = []

        class _Erro(Exception):
            def __init__(self):
                super().__init__("Error code: 403 - does not have access to model")
                self.status_code = 403

        class _Client:
            def __init__(self, bypass=False):
                self._bypass = bypass
                self.embeddings = type("E", (), {"create": staticmethod(self._create)})()

            def with_options(self, **kwargs):
                return _Client(bypass=True)

            def _create(self, **kw):
                usados.append((kw["model"], self._bypass))
                if kw["model"] in recusa_com_header and not self._bypass:
                    raise _Erro()
                return TestSkoposRagasEmbeddings._Resp()

        with patch("core.llm_client.build_client", side_effect=lambda *a, **k: _Client()),              patch("core.llm_client.record_token_usage"):
            emb = rm.SkoposRagasEmbeddings()
            emb.embed_text("t")

        assert emb.resolved_model == "text-embedding-3-large"
        assert emb.bypass_project_header is True
        assert usados == [("text-embedding-3-large", False), ("text-embedding-3-large", True)]

    def test_modelo_que_funcionou_e_memorizado(self):
        """Sem memorizar, cada texto pagaria de novo as chamadas mortas."""
        client, usados = self._client_que_recusa({"text-embedding-3-large"})
        with patch("core.llm_client.build_client", return_value=client),              patch("core.llm_client.record_token_usage"):
            emb = rm.SkoposRagasEmbeddings()
            emb.embed_text("um")
            emb.embed_text("dois")
        # A segunda chamada vai direto ao modelo já resolvido.
        assert usados[-1] == "text-embedding-3-small"
        assert usados.count("text-embedding-3-small") == 2
        assert usados.count("text-embedding-3-large") == 2  # com e sem cabeçalho

    def test_cadeia_inteira_recusada_da_erro_acionavel(self):
        client, _ = self._client_que_recusa({
            "text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002",
        })
        with patch("core.llm_client.build_client", return_value=client):
            with pytest.raises(RuntimeError, match="nenhum modelo de embeddings acessível"):
                rm.SkoposRagasEmbeddings().embed_text("t")

    def test_erro_que_nao_e_de_acesso_a_modelo_propaga(self):
        """Um 429 ou falha de rede deve subir para o retry existente, não
        consumir a cadeia de fallback tentando outros modelos."""
        class _Client:
            embeddings = type("E", (), {
                "create": staticmethod(lambda **kw: (_ for _ in ()).throw(TimeoutError("rede")))
            })()

            def with_options(self, **kwargs):
                return self

        with patch("core.llm_client.build_client", return_value=_Client()):
            with pytest.raises(TimeoutError):
                rm.SkoposRagasEmbeddings().embed_text("t")

    def test_usa_o_modelo_de_embeddings_configurado(self):
        capturado = {}

        class _C:
            embeddings = type("E", (), {
                "create": staticmethod(lambda **kw: capturado.update(kw) or TestSkoposRagasEmbeddings._Resp())
            })()

            def with_options(self, **kwargs):
                return self

        with patch("core.llm_client.build_client", return_value=_C()), \
             patch("core.llm_client.record_token_usage"):
            rm.SkoposRagasEmbeddings(model="text-embedding-3-large").embed_text("t")
        assert capturado["model"] == "text-embedding-3-large"


class TestCleanScore:
    def test_nan_vira_none(self):
        """`json.dumps(float('nan'))` emite o literal NaN, que é JSON
        inválido e derruba o JSON.parse do browser — levando o evento
        WebSocket inteiro junto."""
        assert rm._clean_score(float("nan")) is None
        assert json.dumps({"score": rm._clean_score(float("nan"))}) == '{"score": null}'

    def test_infinito_vira_none(self):
        assert rm._clean_score(float("inf")) is None

    def test_numpy_float_vira_float_nativo(self):
        np = pytest.importorskip("numpy")
        score = rm._clean_score(np.float64(0.87654))
        assert isinstance(score, float) and not isinstance(score, np.floating)
        assert json.dumps({"score": score})

    def test_none_permanece_none(self):
        assert rm._clean_score(None) is None

    def test_arredonda_para_4_casas(self):
        assert rm._clean_score(0.123456789) == 0.1235


class TestConfiguracaoDeEmbeddingsPorEnv:
    """A escolha do modelo vive no `.env`, não no código — as constantes
    do módulo são só o último recurso para o sistema subir sem config."""

    def test_modelo_vem_do_env(self, monkeypatch):
        monkeypatch.setenv("RAGAS_EMBEDDING_MODEL", "text-embedding-3-small")
        assert rm.get_embedding_model() == "text-embedding-3-small"

    def test_sem_env_usa_o_default_do_modulo(self, monkeypatch):
        monkeypatch.delenv("RAGAS_EMBEDDING_MODEL", raising=False)
        assert rm.get_embedding_model() == rm.DEFAULT_EMBEDDING_MODEL

    def test_cadeia_de_fallback_vem_do_env(self, monkeypatch):
        monkeypatch.setenv("RAGAS_EMBEDDING_FALLBACKS", "modelo-a, modelo-b ,, modelo-c")
        assert rm.get_embedding_fallbacks() == ("modelo-a", "modelo-b", "modelo-c")

    def test_cadeia_vazia_desliga_o_fallback(self, monkeypatch):
        """Garante que a avaliação use o modelo escolhido ou falhe, em vez
        de degradar silenciosamente para outro."""
        monkeypatch.setenv("RAGAS_EMBEDDING_FALLBACKS", "")
        assert rm.get_embedding_fallbacks() == ()
        monkeypatch.setenv("RAGAS_EMBEDDING_MODEL", "so-esse")
        assert rm.SkoposRagasEmbeddings()._candidates() == ["so-esse"]

    def test_sem_env_usa_a_cadeia_default(self, monkeypatch):
        monkeypatch.delenv("RAGAS_EMBEDDING_FALLBACKS", raising=False)
        assert rm.get_embedding_fallbacks() == rm.DEFAULT_EMBEDDING_FALLBACKS

    def test_candidatos_saem_do_env_em_ordem(self, monkeypatch):
        monkeypatch.setenv("RAGAS_EMBEDDING_MODEL", "principal")
        monkeypatch.setenv("RAGAS_EMBEDDING_FALLBACKS", "reserva-1,reserva-2")
        assert rm.SkoposRagasEmbeddings()._candidates() == [
            "principal", "reserva-1", "reserva-2",
        ]

    def test_modelo_nao_congela_no_import(self, monkeypatch):
        """O `.env` costuma ser carregado depois do import do módulo, então
        o default do dataclass precisa ser avaliado na instanciação."""
        monkeypatch.setenv("RAGAS_EMBEDDING_MODEL", "definido-depois-do-import")
        assert rm.SkoposRagasEmbeddings().model == "definido-depois-do-import"

    def test_default_prioriza_qualidade_multilingue(self):
        """answer_relevancy compara textos curtos em português; o ada-002
        é o pior nesse eixo (31,4% MIRACL) e ainda 5x mais caro que o
        3-small, então não pode ser o default nem o primeiro fallback."""
        assert rm.DEFAULT_EMBEDDING_MODEL == "text-embedding-3-large"
        assert rm.DEFAULT_EMBEDDING_FALLBACKS[0] == "text-embedding-3-small"
        assert rm.DEFAULT_EMBEDDING_FALLBACKS[-1] == "text-embedding-ada-002"


class TestJudgeConfig:
    def test_default_e_openai_independente_de_llm_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.delenv("RAGAS_PROVIDER", raising=False)
        assert rm.get_judge_provider().name == "openai"

    def test_provider_desconhecido_cai_no_default(self, monkeypatch):
        monkeypatch.setenv("RAGAS_PROVIDER", "provedor-que-nao-existe")
        assert rm.get_judge_provider().name == rm.DEFAULT_JUDGE_PROVIDER

    def test_modelo_e_embeddings_sobrescritos_por_env(self, monkeypatch):
        monkeypatch.setenv("RAGAS_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("RAGAS_EMBEDDING_MODEL", "text-embedding-3-large")
        assert rm.get_judge_model() == "gpt-4o-mini"
        assert rm.get_embedding_model() == "text-embedding-3-large"

    def test_sem_api_key_reporta_o_motivo(self, monkeypatch):
        monkeypatch.setenv("RAGAS_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        available, reason = rm.is_available()
        assert available is False
        assert "OPENAI_API_KEY" in reason


class _EmbeddingsFalsa:
    """Dublê do segundo item que `build_metrics` devolve — só precisa
    expor `resolved_model`, que vira `judge.embedding_model_used`."""

    resolved_model = "text-embedding-3-small"


class _MetricaFalsa:
    """Dublê no formato que `build_metrics` devolve: `.ascore(**kwargs)`
    async, retornando um objeto com `.value`."""

    def __init__(self, value=None, raises=None):
        self.value = value
        self.raises = raises
        self.kwargs = None

    async def ascore(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return type("MetricResult", (), {"value": self.value})()


class TestEvaluateArchitecture:
    def test_sem_api_key_nao_estoura_e_diz_por_que(self, monkeypatch):
        monkeypatch.setenv("RAGAS_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        payload = asyncio.run(rm.evaluate_architecture(RESULTADO, "pergunta"))
        assert payload["available"] is False
        assert "OPENAI_API_KEY" in payload["unavailable_reason"]
        assert payload["metrics"] == {}
        assert json.dumps(payload)  # serializável mesmo no caminho de falha

    def test_texto_vazio_nao_gasta_llm(self, judge_key):
        with patch("core.llm_client.generate") as mock_generate:
            payload = asyncio.run(
                rm.evaluate_architecture({**RESULTADO, "texto_analise": ""}, "pergunta")
            )
        mock_generate.assert_not_called()
        assert payload["available"] is False
        assert "vazio" in payload["unavailable_reason"]

    def test_falha_de_uma_metrica_nao_derruba_as_outras(self, judge_key):
        boa = _MetricaFalsa(value=0.75)
        ruim = _MetricaFalsa(raises=RuntimeError("boom"))
        with patch.object(rm, "build_metrics", return_value=([
            ("faithfulness", boa, rm.ARGS_COM_CONTEXTO), ("metrica_quebrada", ruim, rm.ARGS_COM_CONTEXTO),
        ], _EmbeddingsFalsa())):
            payload = asyncio.run(rm.evaluate_architecture(RESULTADO, "pergunta"))

        assert payload["metrics"]["faithfulness"]["score"] == 0.75
        assert payload["metrics"]["metrica_quebrada"]["score"] is None
        assert payload["errors"][0]["metric"] == "metrica_quebrada"
        assert "RuntimeError" in payload["errors"][0]["error"]
        assert json.dumps(payload)

    def test_answer_relevancy_nao_recebe_contextos(self, judge_key):
        """`AnswerRelevancy.ascore` só aceita user_input e response —
        passar retrieved_contexts levantaria TypeError."""
        com_ctx, sem_ctx = _MetricaFalsa(value=1.0), _MetricaFalsa(value=1.0)
        with patch.object(rm, "build_metrics", return_value=([
            ("faithfulness", com_ctx, rm.ARGS_COM_CONTEXTO),
            ("answer_relevancy", sem_ctx, rm.ARGS_PERGUNTA_RESPOSTA),
        ], _EmbeddingsFalsa())):
            asyncio.run(rm.evaluate_architecture(RESULTADO, "pergunta"))

        assert set(com_ctx.kwargs) == {"user_input", "response", "retrieved_contexts"}
        assert set(sem_ctx.kwargs) == {"user_input", "response"}

    def test_score_nan_da_biblioteca_vira_none(self, judge_key):
        with patch.object(rm, "build_metrics", return_value=([
            ("faithfulness", _MetricaFalsa(value=float("nan")), rm.ARGS_COM_CONTEXTO),
        ], _EmbeddingsFalsa())):
            payload = asyncio.run(rm.evaluate_architecture(RESULTADO, "pergunta"))
        assert payload["metrics"]["faithfulness"]["score"] is None
        assert json.dumps(payload)

    def test_payload_registra_o_juiz_usado(self, judge_key, monkeypatch):
        monkeypatch.setenv("RAGAS_MODEL", "gpt-4o-mini")
        with patch.object(rm, "build_metrics", return_value=([
            ("faithfulness", _MetricaFalsa(value=1.0), rm.ARGS_COM_CONTEXTO),
        ], _EmbeddingsFalsa())):
            payload = asyncio.run(rm.evaluate_architecture(RESULTADO, "pergunta"))

        assert payload["framework"] == "ragas"
        assert payload["judge"]["provider"] == "openai"
        assert payload["judge"]["model"] == "gpt-4o-mini"
        assert payload["sample"]["n_contexts_total"] == 4 + N_ENQUADRAMENTO


class TestBuildMetrics:
    def test_instancia_as_tres_metricas_do_paper(self, judge_key):
        metricas, _ = rm.build_metrics(caller="ragas-star")
        assert [nome for nome, _, _ in metricas] == [
            "faithfulness", "answer_relevancy", "context_relevance",
        ]

    def test_argumentos_batem_com_a_assinatura_de_cada_metrica(self, judge_key):
        """As assinaturas de `ascore` divergem e passar um argumento a mais
        e TypeError — em especial `ContextRelevance`, que julga contexto
        contra pergunta e NAO recebe a resposta."""
        metricas, _ = rm.build_metrics()
        args = {nome: campos for nome, _, campos in metricas}
        assert args["faithfulness"] == ("user_input", "response", "retrieved_contexts")
        assert args["answer_relevancy"] == ("user_input", "response")
        assert args["context_relevance"] == ("user_input", "retrieved_contexts")

    def test_relevancia_de_contexto_nao_e_sensivel_a_ordem(self, judge_key):
        """Regressao da troca de metrica: `ContextPrecisionWithoutReference`
        calculava *average precision*, que pressupoe um retrieval ranqueado
        e pontua conforme a POSICAO dos chunks uteis. Como `build_contexts`
        emite numa ordem estrutural fixa (nao por relevancia), o score era
        funcao dessa ordem arbitraria — os mesmos 6 chunks uteis em 77 dao
        AP 1.00 no inicio e 0.09 dispersos."""
        from ragas.metrics.collections import ContextRelevance

        metricas, _ = rm.build_metrics()
        metrica = next(m for nome, m, _ in metricas if nome == "context_relevance")
        assert isinstance(metrica, ContextRelevance)

    def test_todas_usam_o_mesmo_juiz_configurado(self, judge_key, monkeypatch):
        monkeypatch.setenv("RAGAS_MODEL", "gpt-4o-mini")
        metricas, _ = rm.build_metrics(caller="ragas-star")
        for _, metric, _ in metricas:
            assert isinstance(metric.llm, rm.SkoposRagasLLM)
            assert metric.llm.model == "gpt-4o-mini"
            assert metric.llm.caller == "ragas-star"
