"""
Avaliação da qualidade da resposta via RAGAS — a biblioteca, não uma
reimplementação.

Referência: Es, S., James, J., Espinosa-Anke, L., Schockaert, S. (2024).
"RAGAS: Automated Evaluation of Retrieval Augmented Generation".
Proceedings of the 18th Conference of the European Chapter of the ACL:
System Demonstrations, pp. 150-158.

Três métricas, todas *reference-free* (o projeto não tem respostas-padrão
anotadas, que é exatamente o cenário para o qual o RAGAS foi desenhado),
uma para cada aspecto de qualidade da Seção 3 do paper:

    faithfulness       — o texto é sustentado pelos dados?
    answer_relevancy   — o texto responde à pergunta feita?
    context_relevance  — os achados entregues eram relevantes à pergunta?

São importadas de `ragas.metrics.collections`, não de `ragas.metrics`: o
namespace antigo emite DeprecationWarning e a própria biblioteca anuncia
sua remoção na v1.0.

Mapeamento de um pipeline que NÃO é RAG clássico
-------------------------------------------------
Não há retriever nem corpus de documentos aqui: os agentes consultam o
Neo4j e calculam achados determinísticos (correlações de Spearman,
anomalias, tendências orçamentárias), e é esse conjunto — e só ele — que
o TextSynthesizer recebe para escrever o texto. Ele ocupa exatamente a
posição do contexto recuperado num RAG, então a tripla do RAGAS é montada
assim (ver `build_contexts`):

    user_input         = a pergunta original do usuário no chat
    response           = result["texto_analise"] (texto do sintetizador)
    retrieved_contexts = TUDO que o sintetizador recebeu (ver build_contexts)

O contexto do juiz tem que ser idêntico ao do gerador. Mandar uma versão
empobrecida dos achados faz a fidelidade medir a lacuna entre os dois
contextos, não a fidelidade do texto — ver a docstring de
`build_contexts`, que detalha o que entra e por quê.

Um chunk por achado (em vez de um bloco único) mantém cada afirmação
verificável isoladamente pela fidelidade. Nenhuma métrica escala em
número de chamadas com a quantidade de chunks, então não há teto: ver
`build_metrics` para por que a relevância do contexto usa
`ContextRelevance` e não a precisão de contexto ranqueada.

Juiz fixo
---------
O juiz é `MODELO_JUIZ` (`gpt-5-mini`), fixo no código e diferente do modelo
que gera os textos (`llm_client.MODELO_ANALISE`, `gpt-5.6-luna`). Um modelo
avaliando o próprio texto tende a favorecê-lo (viés de autopreferência,
Zheng et al., 2023; Panickssery et al., 2024), então gerador e juiz são
modelos distintos. Fixo também porque o instrumento de medida não pode
mudar entre execuções, ou os scores deixam de ser comparáveis.

As chamadas passam por `core.llm_client.generate(model=MODELO_JUIZ)` em vez de
a ragas abrir seu próprio cliente — assim a avaliação continua coberta
pela contabilização de tokens (`TokenBucket`), pelo retry de 429 e pelo
tratamento de modelos de raciocínio da OpenAI (que exigem
`max_completion_tokens` e rejeitam temperatura customizada; a ragas pede
temperatura baixa por padrão, o que daria 400 num `gpt-5*`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import typing as t
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Antes de qualquer import da ragas: a lib manda telemetria de uso por
# padrão e a decisão é cacheada (lru_cache) na primeira leitura.
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

from ragas.embeddings.base import BaseRagasEmbedding  # noqa: E402
from ragas.llms.base import InstructorBaseRagasLLM  # noqa: E402
from ragas.metrics.collections import (  # noqa: E402
    AnswerRelevancy,
    ContextRelevance,
    Faithfulness,
)

from core import llm_client  # noqa: E402
from core.contextos_avaliacao import (  # noqa: E402,F401 — reexportados
    SUBFUNCAO_NOMES,
    _coverage_contexts,
    _framing_contexts,
    _subfuncao_label,
    build_contexts,
)
# Juiz da avaliação: fixo e diferente do gerador (ver "Juiz fixo" acima).
MODELO_JUIZ = "gpt-5-mini"
PROVEDOR_JUIZ = "openai"

# Modelo de embeddings da `answer_relevancy`, fixo como o juiz.
#
# É o `3-large` porque `answer_relevancy` compara textos curtos
# em português, e é aí que os modelos se separam: no MIRACL (multilíngue)
# são 54,9% contra 44,0% do `3-small` e 31,4% do `ada-002`. O custo não
# desempata — são ~8 textos curtos por análise, na casa de US$ 0,00005.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"

# Cadeia tentada quando o modelo escolhido não está acessível ao projeto
# (403 `model_not_found`), em ordem de qualidade decrescente. Termina no
# ada-002: a geração anterior, pior em multilíngue, mas a que a OpenAI
# declarou que NÃO vai depreciar — e por isso a mais amplamente liberada.
DEFAULT_EMBEDDING_FALLBACKS = ("text-embedding-3-small", "text-embedding-ada-002")

# Temperatura pedida ao juiz: determinismo, não criatividade. Aplicada só
# onde o modelo aceita o parâmetro — ver llm_client._completion_params.
JUDGE_TEMPERATURE = 0.0



# =========================================================================
# Configuração do juiz
# =========================================================================


def get_judge_model() -> str:
    """Modelo do juiz (fixo)."""
    return MODELO_JUIZ


def get_embedding_model() -> str:
    """Modelo de embeddings (fixo)."""
    return DEFAULT_EMBEDDING_MODEL


def get_embedding_fallbacks() -> tuple[str, ...]:
    """Cadeia de alternativas de embeddings (fixa)."""
    return DEFAULT_EMBEDDING_FALLBACKS


def is_available() -> tuple[bool, str | None]:
    """Se a avaliação pode rodar, e por que não quando não pode.

    Devolver o motivo importa: um score 0 por falta de API key é
    indistinguível de um score 0 legítimo, e é justamente o tipo de
    ambiguidade que as métricas antigas tinham.
    """
    if not llm_client.has_api_key():
        return False, f"{llm_client.API_KEY_ENV} não configurada"
    return True, None

# =========================================================================
# Adaptadores: ragas -> core.llm_client
# =========================================================================


@dataclass
class SkoposRagasLLM(InstructorBaseRagasLLM):
    """LLM da ragas que delega para `core.llm_client.generate`.

    A interface que a ragas pede é "devolva uma instância deste modelo
    Pydantic"; a ponte é anexar o JSON Schema do modelo ao prompt e
    validar a resposta. `llm_client.generate` fala texto puro, não
    structured outputs, então o adaptador não depende de tool calling.

    Alternativa descartada: montar um `instructor`/`ChatOpenAI` próprio.
    Ele abriria um cliente fora do `TokenBucket` (o custo da avaliação
    sumiria do eixo D), fora do retry de 429, e mandaria `temperature`
    para modelos que a rejeitam.
    """

    model: str = MODELO_JUIZ
    caller: str = "ragas"

    def generate(self, prompt: str, response_model: t.Type[t.Any]) -> t.Any:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        full_prompt = (
            f"{prompt}\n\n"
            "Responda SOMENTE com um JSON válido que satisfaça exatamente "
            "este JSON Schema — sem texto antes ou depois, sem markdown:\n"
            f"{schema}"
        )
        raw = llm_client.generate(
            full_prompt,
            self.model or MODELO_JUIZ,
            caller=self.caller,
            temperature=JUDGE_TEMPERATURE,
        )
        if not raw:
            raise RuntimeError("LLM indisponível ou resposta vazia")
        return _parse_model(raw, response_model)

    async def agenerate(self, prompt: str, response_model: t.Type[t.Any]) -> t.Any:
        # `llm_client.generate` é síncrono e bloqueante. `to_thread` copia
        # o contexto atual, então o TokenBucket ativo continua recebendo
        # os tokens gastos aqui dentro.
        return await asyncio.to_thread(self.generate, prompt, response_model)


def _parse_model(raw: str, response_model: t.Type[t.Any]) -> t.Any:
    """Valida `raw` no modelo, tolerando cercas de markdown e texto ao redor."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]

    try:
        return response_model.model_validate_json(cleaned)
    except Exception:
        match = re.search(r"[\{\[].*[\}\]]", cleaned, re.DOTALL)
        if match is None:
            raise
        return response_model.model_validate_json(match.group())


def _is_model_access_error(exc: Exception) -> bool:
    """Se a exceção é "este projeto não pode usar este modelo".

    Distinguir isso de falha de rede/cota importa: só faz sentido tentar
    outro modelo quando o problema é o modelo. Um 429 ou um timeout devem
    subir para o retry existente, não consumir a cadeia de fallback.
    """
    status = getattr(exc, "status_code", None)
    if status not in (403, 404):
        return False
    texto = str(exc).lower()
    return "model" in texto


@dataclass
class SkoposRagasEmbeddings(BaseRagasEmbedding):
    """Embeddings pelo endpoint da OpenAI.

    Rota diferente da API (`embeddings`, não `chat.completions`), então
    não passa por `generate()` — daí o cliente ser montado à parte e a
    contabilização de tokens ser explícita.

    Projetos da OpenAI restringem acesso **por modelo**, e o sintoma é um
    403 `model_not_found` — não um erro de credencial, e pode ocorrer com
    a mesma chave que chama `chat.completions` sem problema. Duas causas
    diferentes produzem esse mesmo erro, e a classe cobre as duas:

    1. O modelo não está liberado no projeto — daí a cadeia de fallback
       (`DEFAULT_EMBEDDING_FALLBACKS`), tentada em ordem de qualidade
       decrescente.
    2. O cabeçalho `OpenAI-Project`, que o SDK injeta a partir do projeto
       da chave, dispara uma checagem que falha em algumas configurações
       *mesmo com o modelo liberado* — daí o retry sem o cabeçalho antes
       de desistir do modelo (ver `_try_model`).

    O modelo e o modo que funcionaram são memorizados na instância (para
    não repetir a chamada morta a cada texto) e reportados em
    `judge.embedding_model_used`, de forma que o payload nunca afirme ter
    usado um modelo que apenas pediu.
    """

    model: str = DEFAULT_EMBEDDING_MODEL
    resolved_model: str | None = field(default=None, init=False)
    # Se o contorno do cabeçalho de projeto foi necessário (ver _create).
    bypass_project_header: bool = field(default=False, init=False)

    def _candidates(self) -> list[str]:
        if self.resolved_model:
            return [self.resolved_model]
        vistos: list[str] = []
        for nome in (self.model, *get_embedding_fallbacks()):
            if nome and nome not in vistos:
                vistos.append(nome)
        return vistos

    def _create(self, model: str, text: str, bypass_header: bool, **kwargs: t.Any):
        client = llm_client.build_client()
        if bypass_header:
            # O SDK injeta `OpenAI-Project` a partir do projeto da chave, e
            # esse cabeçalho dispara uma checagem de acesso por projeto que
            # falha em algumas configurações mesmo com o modelo liberado na
            # allowlist. Mandar o cabeçalho vazio pula a checagem — a chave
            # por si só já identifica organização e projeto.
            client = client.with_options(default_headers={"OpenAI-Project": ""})
        return client.embeddings.create(input=[text], model=model, **kwargs)

    def _try_model(self, modelo: str, text: str, **kwargs: t.Any):
        """Tenta um modelo, com e sem o cabeçalho de projeto.

        Devolve `(response, bypass_usado)` ou levanta a exceção de acesso.
        """
        try:
            return self._create(modelo, text, self.bypass_project_header, **kwargs), \
                self.bypass_project_header
        except Exception as exc:  # noqa: BLE001 — reclassificado pelo caller
            if self.bypass_project_header or not _is_model_access_error(exc):
                raise
            logger.info(
                "RAGAS: 403 de acesso ao modelo %r — repetindo sem o cabeçalho "
                "OpenAI-Project antes de trocar de modelo",
                modelo,
            )
            return self._create(modelo, text, True, **kwargs), True

    def embed_text(self, text: str, **kwargs: t.Any) -> t.List[float]:
        candidatos = self._candidates()
        recusados: list[str] = []

        for i, modelo in enumerate(candidatos):
            try:
                response, usou_bypass = self._try_model(modelo, text, **kwargs)
            except Exception as exc:  # noqa: BLE001 — reclassificado abaixo
                recusados.append(modelo)
                if not _is_model_access_error(exc) or i == len(candidatos) - 1:
                    if _is_model_access_error(exc):
                        raise RuntimeError(
                            "nenhum modelo de embeddings acessível neste projeto "
                            f"(recusados: {', '.join(recusados)}). Habilite um em "
                            "platform.openai.com > Project > Limits (permissões de "
                            "modelo)."
                        ) from exc
                    raise
                logger.warning(
                    "RAGAS: sem acesso ao modelo de embeddings %r — tentando %r",
                    modelo, candidatos[i + 1],
                )
                continue

            if self.resolved_model != modelo or self.bypass_project_header != usou_bypass:
                self.resolved_model = modelo
                self.bypass_project_header = usou_bypass
                if recusados or usou_bypass:
                    logger.info(
                        "RAGAS: usando modelo de embeddings %r (cabeçalho de "
                        "projeto omitido: %s)",
                        modelo, usou_bypass,
                    )

            usage = getattr(response, "usage", None)
            if usage is not None:
                # Sem isto o eixo D subestimaria o custo de rodar a avaliação.
                llm_client.record_token_usage({
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                })
            return list(response.data[0].embedding)

        raise RuntimeError("nenhum modelo de embeddings disponível")

    async def aembed_text(self, text: str, **kwargs: t.Any) -> t.List[float]:
        return await asyncio.to_thread(self.embed_text, text, **kwargs)



# =========================================================================
# Montagem da amostra
# =========================================================================


def build_sample(
    result: dict[str, t.Any],
    user_input: str,
    date_from: int | None = None,
    date_to: int | None = None,
) -> tuple[dict[str, t.Any], dict[str, t.Any]]:
    """Monta a tripla do RAGAS e o resumo do que foi avaliado.

    Todas as métricas recebem o conjunto completo de contextos. Houve um
    teto (`RAGAS_MAX_CONTEXTS`) enquanto a relevância do contexto era
    medida por `ContextPrecisionWithoutReference`, que gasta 1 chamada LLM
    por chunk; `ContextRelevance` custa 2 chamadas independente do volume,
    então nenhuma métrica escala mais com o número de achados e o teto
    deixou de ter função.
    """
    contexts = build_contexts(result, date_from, date_to)

    sample = {
        "user_input": user_input,
        "response": result.get("texto_analise") or "",
        "retrieved_contexts": contexts,
    }
    info = {
        "n_contexts_total": len(contexts),
        "response_chars": len(sample["response"]),
        "user_input": user_input,
    }
    return sample, info


# =========================================================================
# Execução
# =========================================================================


def _clean_score(value: t.Any) -> float | None:
    """Normaliza o score da ragas para algo serializável em JSON.

    Uma métrica que não pôde ser calculada vira `nan` — e
    `json.dumps(nan)` emite o literal `NaN`, que é JSON inválido e
    derruba o `JSON.parse` do browser, levando o evento WebSocket inteiro
    junto. Escalares de numpy também não são serializáveis pelo json do
    stdlib.
    """
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(as_float) or math.isinf(as_float):
        return None
    return round(as_float, 4)


# Argumentos que cada métrica aceita em `ascore` — as assinaturas
# divergem entre elas, e passar um argumento a mais é TypeError.
ARGS_PERGUNTA_RESPOSTA = ("user_input", "response")
ARGS_COM_CONTEXTO = ("user_input", "response", "retrieved_contexts")
ARGS_CONTEXTO_SEM_RESPOSTA = ("user_input", "retrieved_contexts")


def build_metrics(
    caller: str = "ragas",
) -> tuple[list[tuple[str, t.Any, tuple[str, ...]]], "SkoposRagasEmbeddings"]:
    """As três métricas já ligadas ao juiz configurado.

    Devolve `(métricas, embeddings)`. Cada métrica é
    `(nome, objeto, argumentos)`, com os argumentos que aquela métrica
    aceita em `ascore`. O objeto de embeddings volta junto porque só
    depois de rodar ele sabe qual modelo da cadeia de fallback funcionou.

    Sobre o terceiro pilar do paper (relevância do contexto): usamos
    `ContextRelevance`, não `ContextPrecisionWithoutReference`. A segunda
    calcula *average precision*, que é sensível à ORDEM dos chunks —
    ela pressupõe um resultado de retrieval ranqueado e pergunta "o
    retriever colocou o bom primeiro?". Aqui não há retriever nem
    ranking: `build_contexts` emite os achados numa ordem estrutural
    fixa, então o score virava função dessa ordem arbitrária. Medido com
    77 chunks reais: os mesmos 6 chunks úteis dão AP 1.00 se estiverem no
    início, 0.09 dispersos e 0.05 no fim.

    `ContextRelevance` concatena os contextos e julga o conjunto com dois
    juízes numa escala 0/1/2 — independente da ordem, mais próxima da
    definição do paper (uma proporção, não uma AP ranqueada) e com custo
    fixo de 2 chamadas, em vez de 1 por chunk.
    """
    llm = SkoposRagasLLM(model=MODELO_JUIZ, caller=caller)
    embeddings = SkoposRagasEmbeddings(model=DEFAULT_EMBEDDING_MODEL)

    metrics = [
        ("faithfulness", Faithfulness(llm=llm), ARGS_COM_CONTEXTO),
        (
            "answer_relevancy",
            AnswerRelevancy(llm=llm, embeddings=embeddings),
            ARGS_PERGUNTA_RESPOSTA,
        ),
        ("context_relevance", ContextRelevance(llm=llm), ARGS_CONTEXTO_SEM_RESPOSTA),
    ]
    # Devolvido junto para o caller poder ler, depois da execução, qual
    # modelo de embeddings a cadeia de fallback acabou usando.
    return metrics, embeddings


async def evaluate_architecture(
    result: dict[str, t.Any],
    user_input: str,
    *,
    caller: str = "ragas",
    date_from: int | None = None,
    date_to: int | None = None,
) -> dict[str, t.Any]:
    """Roda as três métricas RAGAS sobre o resultado de uma arquitetura.

    Nunca levanta exceção: a falha de uma métrica não derruba as outras e
    a indisponibilidade do juiz vira `available=False` com motivo, em vez
    de um score 0 ambíguo.
    """
    available, reason = is_available()

    sample, info = build_sample(
        result, user_input, date_from=date_from, date_to=date_to
    )

    payload: dict[str, t.Any] = {
        "framework": "ragas",
        "version": _ragas_version(),
        "judge": {
            "provider": PROVEDOR_JUIZ,
            "model": MODELO_JUIZ,
            "embedding_model": DEFAULT_EMBEDDING_MODEL,
        },
        "sample": info,
        "metrics": {},
        "errors": [],
        "available": available,
        "unavailable_reason": reason,
    }

    if not available:
        logger.warning("RAGAS [%s]: avaliação não executada — %s", caller, reason)
        return payload

    if not sample["response"]:
        payload["available"] = False
        payload["unavailable_reason"] = "texto de análise vazio — nada a avaliar"
        return payload

    metrics, embeddings = build_metrics(caller)
    for name, metric, argumentos in metrics:
        kwargs = {campo: sample[campo] for campo in argumentos}
        try:
            result_obj = await metric.ascore(**kwargs)
            payload["metrics"][name] = {"score": _clean_score(getattr(result_obj, "value", None))}
        except Exception as exc:  # noqa: BLE001 — nenhuma métrica derruba as outras
            logger.warning("RAGAS [%s]: métrica %s falhou — %s", caller, name, exc)
            payload["metrics"][name] = {"score": None}
            payload["errors"].append({"metric": name, "error": f"{type(exc).__name__}: {exc}"})

    # Só é conhecido depois de rodar: a cadeia de fallback pode ter caído
    # para outro modelo, e o payload não pode afirmar ter usado o que foi
    # apenas pedido.
    payload["judge"]["embedding_model_used"] = embeddings.resolved_model

    logger.info(
        "RAGAS [%s]: %s (achados avaliados=%d)",
        caller,
        ", ".join(f"{k}={v['score']}" for k, v in payload["metrics"].items()),
        info["n_contexts_total"],
    )
    return payload


def _ragas_version() -> str:
    try:
        import ragas

        return str(ragas.__version__)
    except Exception:  # noqa: BLE001
        return "desconhecida"
