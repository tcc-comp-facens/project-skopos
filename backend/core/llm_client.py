"""
Cliente LLM centralizado (DeepSeek ou OpenAI) — sem rate limiting
próprio, com retry em caso de rate limit (429) reportado pelo provedor e
contabilização de gastos (tokens) em log.

O provedor é escolhido pela variável de ambiente `LLM_PROVIDER`
(`deepseek`, o default histórico, ou `openai`) — ver `PROVIDERS` abaixo.
Ambos falam a mesma API (o SDK da OpenAI é usado nos dois casos; o
DeepSeek é compatível, mudando só `base_url`), então a troca não afeta
nenhum call site: todo o resto do backend continua chamando
`generate()`/`generate_stream()` sem saber qual provedor está ativo.

No DeepSeek o default é `deepseek-v4-flash` com `thinking` desabilitado
(resposta direta, sem chain-of-thought) — adequado para síntese de texto
e extração de JSON estruturado; na OpenAI, `gpt-5.6-luna`. Cada provedor
tem sua própria variável de API key (`DEEPSEEK_API_KEY` /
`OPENAI_API_KEY`) e aceita override de modelo por env (`DEEPSEEK_MODEL` /
`OPENAI_MODEL`) — sem precisar mexer em código para testar outro modelo.

`generate()` aceita ainda um `provider` explícito, que ignora
`LLM_PROVIDER` só naquela chamada. É o que `core/ragas_metrics.py` usa
para manter o juiz da avaliação num provedor fixo (independente do que o
pipeline está usando) sem abrir um cliente próprio — assim a avaliação
continua contabilizada no mesmo `TokenBucket` e coberta pelo retry de 429.

Chamadas de diferentes threads (estrela e hierárquica rodam concorrentes
— ver api/runners.py) acontecem em paralelo de verdade, sem
serialização artificial: não há lock global nem intervalo mínimo
auto-imposto entre chamadas. Isso remove o gargalo real observado em
produção (uma chamada em modo streaming segurava um lock global durante
toda a duração da transmissão — não só a chamada HTTP —, bloqueando a
outra topologia por dezenas de segundos mesmo para uma chamada trivial).
Se o provedor de fato retornar 429 (limite de cota da conta, não algo
que este módulo impõe), o retry com backoff exponencial em
`_try_model`/`generate_stream` ainda se aplica — essa é uma reação a um
erro real da API, não uma limitação preventiva própria.

No provedor OpenAI, `OPENAI_STORE_LOGS=true` (opt-in) faz cada chamada
ir com `store=True` + `metadata` do `caller`, o que a torna visível e
filtrável por agente na aba Logs do dashboard — sem isso a API não retém
nada (default `store=False`) e só o consumo aparece no billing.

Observabilidade: todo call site passa um `caller` (tipicamente o
`agent_id`/`synthesizer_id` de quem disparou a chamada) — logado junto
com um preview de uma linha do prompt (nível INFO) antes de cada chamada
real à API, e o gasto de tokens (prompt/completion/total) é logado em
INFO logo após cada resposta, sem exceção — é a forma de acompanhar o
custo real sem a serialização que existia antes. O prompt completo só
aparece no log em nível DEBUG (`LOG_LEVEL=DEBUG`), para não derramar
dados de análise no log em produção por padrão.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Protege apenas a mutação do contador global _token_usage — nunca é
# mantido durante uma chamada de rede ou um yield de streaming (diferente
# do antigo lock de serialização, que foi removido). Chamadas concorrentes
# de diferentes threads não são mais seriadas entre si.
_token_lock = threading.Lock()

MAX_RETRIES = 2  # retries antes de desistir, só em caso de 429 real do provedor
RETRY_BASE_DELAY = 10.0  # segundos

TEMPERATURE = 0.7
MAX_TOKENS = 4096

# Modelos de raciocínio da OpenAI cobram o MESMO teto de saída para os
# tokens de raciocínio e para a resposta visível. Com 4096 compartilhados
# e esforço de raciocínio no default (`medium`), um prompt grande — a
# verificação de afirmações do RAGAS, ou uma síntese longa — gasta o
# orçamento inteiro pensando e devolve `content` vazio com
# `finish_reason="length"`. Daí um teto próprio, bem mais folgado (o
# gpt-5.6-luna aceita até 128k de saída), e um esforço de raciocínio
# baixo por default.
REASONING_MAX_TOKENS = 16384
REASONING_EFFORT = "low"

# thinking desabilitado — resposta direta, sem reasoning_content
_THINKING_DISABLED = {"type": "disabled"}


@dataclass(frozen=True)
class ProviderConfig:
    """Descreve um provedor de LLM compatível com o SDK da OpenAI.

    O que varia de fato entre DeepSeek e OpenAI é pouco — endpoint,
    variável da API key, modelo default e parâmetros proprietários
    (`extra_body`) —, então o resto do módulo trata os dois de forma
    idêntica a partir daqui.
    """

    name: str
    api_key_env: str  # variável de ambiente com a chave
    model_env: str  # override do modelo por env (opcional)
    base_url_env: str  # override do endpoint por env (opcional)
    default_model: str
    default_base_url: Optional[str] = None  # None = default do SDK (OpenAI)
    extra_body: dict[str, Any] = field(default_factory=dict)


PROVIDERS: dict[str, ProviderConfig] = {
    "deepseek": ProviderConfig(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        model_env="DEEPSEEK_MODEL",
        base_url_env="DEEPSEEK_BASE_URL",
        default_model="deepseek-v4-flash",
        default_base_url="https://api.deepseek.com",
        extra_body={"thinking": _THINKING_DISABLED},
    ),
    "openai": ProviderConfig(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_MODEL",
        base_url_env="OPENAI_BASE_URL",
        default_model="gpt-5.6-luna",
        default_base_url=None,
        # `thinking` é proprietário do DeepSeek — a OpenAI rejeita
        # parâmetros desconhecidos com 400, então aqui não vai nada.
        extra_body={},
    ),
}

DEFAULT_PROVIDER = "deepseek"

# Famílias de modelos de raciocínio da OpenAI: não aceitam `max_tokens`
# (exigem `max_completion_tokens`) nem `temperature` diferente do default.
# Verificado contra a API real com o modelo default (gpt-5.6-luna), que
# responde 400 "Unsupported parameter: 'max_tokens' is not supported with
# this model. Use 'max_completion_tokens' instead".
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Valores aceitos por `reasoning_effort` no Chat Completions. Um valor
# inválido é 400 na API, então é validado aqui antes de sair.
_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r inválido — usando %d", name, raw, default)
        return default
    return value if value > 0 else default


def resolve_reasoning_effort() -> str:
    """Esforço de raciocínio para modelos que o aceitam (`OPENAI_REASONING_EFFORT`).

    Default `low`: os prompts que este sistema manda para modelos de
    raciocínio são extração e julgamento de JSON estruturado, que não se
    beneficiam de chain-of-thought longo — e cada token de raciocínio sai
    do mesmo orçamento da resposta (e é cobrado). É o análogo OpenAI do
    `thinking: disabled` que o provedor DeepSeek já manda.
    """
    raw = os.environ.get("OPENAI_REASONING_EFFORT", "").strip().lower() or REASONING_EFFORT
    if raw not in _REASONING_EFFORTS:
        logger.warning(
            "OPENAI_REASONING_EFFORT=%r inválido (opções: %s) — usando %s",
            raw, ", ".join(_REASONING_EFFORTS), REASONING_EFFORT,
        )
        return REASONING_EFFORT
    return raw


def _env_flag(name: str, default: bool = False) -> bool:
    """Lê uma env var booleana (`true/1/yes/on`), a cada chamada."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def get_provider() -> ProviderConfig:
    """Provedor ativo, conforme `LLM_PROVIDER` (default: deepseek).

    Lido do ambiente a cada chamada (não no import) para que o valor
    valha mesmo se o `.env` for carregado depois deste módulo e para que
    testes possam alternar de provedor com `monkeypatch.setenv`.
    """
    raw = os.environ.get("LLM_PROVIDER", "").strip().lower() or DEFAULT_PROVIDER
    provider = PROVIDERS.get(raw)
    if provider is None:
        logger.warning(
            "LLM_PROVIDER=%r desconhecido (opções: %s) — usando %s",
            raw, ", ".join(sorted(PROVIDERS)), DEFAULT_PROVIDER,
        )
        provider = PROVIDERS[DEFAULT_PROVIDER]
    return provider


def resolve_model(provider: Optional[ProviderConfig] = None) -> str:
    """Modelo default do provedor ativo, com override por env."""
    provider = provider or get_provider()
    return os.environ.get(provider.model_env, "").strip() or provider.default_model


def _completion_params(
    provider: ProviderConfig, model: str, temperature: Optional[float] = None
) -> dict[str, Any]:
    """Parâmetros de geração aceitos por este par provedor/modelo.

    Os modelos de raciocínio da OpenAI (gpt-5, série o*) renomearam
    `max_tokens` para `max_completion_tokens` e só aceitam a temperatura
    default — mandar os parâmetros antigos resulta em 400. Todo o resto
    (DeepSeek e a linha gpt-4o) usa a forma clássica.

    `temperature` sobrescreve o default do módulo (`TEMPERATURE`) só onde
    o parâmetro é aceito — é o que a avaliação RAGAS usa para pedir
    temperatura baixa (juiz determinístico) sem quebrar nos modelos de
    raciocínio, que simplesmente ignoram o pedido.

    Nos modelos de raciocínio vão dois parâmetros que os clássicos não
    têm: um teto de saída próprio (`REASONING_MAX_TOKENS`, porque esse
    teto é compartilhado com os tokens de raciocínio) e
    `reasoning_effort` — sem ele o esforço fica no default `medium` e um
    prompt grande esgota o orçamento pensando, devolvendo resposta vazia.
    """
    if provider.name == "openai" and model.lower().startswith(_OPENAI_REASONING_PREFIXES):
        return {
            "max_completion_tokens": _env_int(
                "OPENAI_MAX_COMPLETION_TOKENS", REASONING_MAX_TOKENS
            ),
            "reasoning_effort": resolve_reasoning_effort(),
        }
    return {
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": MAX_TOKENS,
    }


def _logging_params(provider: ProviderConfig, caller: str) -> dict[str, Any]:
    """Retenção da chamada nos logs do provedor, quando ele oferece isso.

    Na OpenAI, o par requisição/resposta só é retido — e só aparece na
    aba Logs do dashboard — se a chamada mandar `store=True`; o default
    da API é `False`, então por padrão o consumo aparece no billing mas
    nenhum registro fica visível. Aqui isso é **opt-in** via
    `OPENAI_STORE_LOGS=true`, porque ativa retenção do conteúdo dos
    prompts e das respostas na conta OpenAI (numa organização com Zero
    Data Retention o parâmetro é ignorado e tratado como `False`).

    Quando ligado, vai junto o `metadata` com o `caller` — o mesmo
    identificador (`agent_id`/`synthesizer_id`) que aparece nos logs do
    backend —, o que permite filtrar no dashboard as chamadas por agente
    e cruzar com o log local. O DeepSeek não tem equivalente, então nada
    é enviado a ele.
    """
    if provider.name != "openai" or not _env_flag("OPENAI_STORE_LOGS"):
        return {}

    return {
        "store": True,
        # metadata da OpenAI aceita só pares string→string; o limite por
        # valor é 512 chars, folgado para um agent_id, mas truncado por
        # garantia (um caller inesperadamente longo derrubaria a chamada
        # com 400 em vez de só perder detalhe no log).
        "metadata": {"app": "skopos", "caller": caller[:512]},
    }

# Acumulador global de tokens (thread-safe via _token_lock) — mantido por
# compatibilidade (get_token_usage/reset_token_usage), mas não distingue
# qual topologia/segmento gastou os tokens (ver TokenBucket abaixo).
_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "call_count": 0,
}

# Contabilização por análise/segmento (Etapa 6 do PLANO_REFATORACAO.md) —
# pré-requisito para compute_token_cost(). Estrela e hierárquica rodam em
# threads concorrentes (api/runners.py), então um contador global não
# distingue qual topologia gastou quais tokens. Em vez de thread-local
# puro (que atribuiria tudo que roda na MainThread — interpretação de
# intenção E LLM Judge — ao mesmo balde), usamos um ContextVar: cada
# chamador ativa seu próprio "balde" via `with TokenBucket() as bucket:`,
# e qualquer chamada a generate()/generate_stream() feita dentro desse
# escopo (mesmo em profundidade, através de vários agentes) acumula ali —
# funciona corretamente tanto entre threads concorrentes (cada uma tem seu
# próprio contexto) quanto sequencialmente na mesma thread (ex.: LLM Judge
# chamado 2x em sequência na MainThread, um bucket por vez).
_current_bucket: contextvars.ContextVar[Optional[dict[str, int]]] = contextvars.ContextVar(
    "llm_client_current_token_bucket", default=None
)


def _new_bucket() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}


class TokenBucket:
    """Context manager: ativa um acumulador de tokens dedicado no escopo.

    Uso:
        with TokenBucket() as bucket:
            orchestrator.run(...)  # qualquer chamada LLM feita aqui dentro
                                    # (direta ou por agentes subordinados)
                                    # é contabilizada em bucket
        usage = bucket.snapshot()
    """

    def __init__(self) -> None:
        self.usage = _new_bucket()
        self._token: contextvars.Token | None = None

    def __enter__(self) -> "TokenBucket":
        self._token = _current_bucket.set(self.usage)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._token is not None:
            _current_bucket.reset(self._token)

    def snapshot(self) -> dict[str, int]:
        """Retorna uma cópia do acumulado até agora neste bucket."""
        return dict(self.usage)


def _has_api_key(provider: Optional[ProviderConfig] = None) -> bool:
    provider = provider or get_provider()
    return bool(os.environ.get(provider.api_key_env, "").strip())


def has_api_key(provider: Optional[ProviderConfig] = None) -> bool:
    """Se a chave do provedor está configurada — face pública de
    `_has_api_key` (que os testes substituem como seam interno), usada
    pelo log de startup em main.py."""
    return _has_api_key(provider)


def _strip_think_tags(text: str) -> str:
    """Remove tags <think>...</think>, caso apareçam na resposta."""
    import re

    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def _preview(text: str, max_chars: int = 300) -> str:
    """Reduz um texto a uma linha única e truncada, para log em nível INFO.

    Colapsa quebras de linha (o prompt completo, com dados de análise
    embutidos, pode ter várias linhas e milhares de caracteres) — o texto
    integral só é logado em DEBUG por quem chama `logger.debug` separadamente.
    """
    single_line = " ".join(text.split())
    if len(single_line) > max_chars:
        return single_line[:max_chars] + f"… (+{len(text) - max_chars} chars)"
    return single_line


def _client(provider: Optional[ProviderConfig] = None):
    """Cliente do SDK da OpenAI apontado para o provedor ativo.

    O DeepSeek expõe uma API compatível, então a única diferença é a
    `base_url` (para a OpenAI, `None` — deixa o SDK usar o endpoint
    oficial).
    """
    from openai import OpenAI

    provider = provider or get_provider()
    base_url = os.environ.get(provider.base_url_env, "").strip() or provider.default_base_url
    return OpenAI(api_key=os.environ[provider.api_key_env], base_url=base_url)


def build_client(provider: Optional[ProviderConfig] = None):
    """Cliente do SDK apontado para `provider` — face pública de `_client`
    (que os testes substituem como seam interno).

    Existe para `core.ragas_metrics` poder falar com o endpoint de
    *embeddings* (que não passa por `generate()`, é outra rota da API)
    sem duplicar a resolução de api key/base_url por provedor.
    """
    return _client(provider)


def _generate(
    prompt: str,
    model: str,
    provider: Optional[ProviderConfig] = None,
    caller: str = "desconhecido",
    temperature: Optional[float] = None,
) -> tuple[Optional[str], dict[str, int], Optional[str]]:
    """Chama a API do provedor e retorna (texto, token_usage, finish_reason).

    `finish_reason` sobe junto porque é o que distingue "o modelo não
    tinha o que dizer" de "o orçamento de tokens acabou no meio" — sem
    ele, os dois casos chegam ao caller como um texto vazio idêntico.
    """
    provider = provider or get_provider()
    response = _client(provider).chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        **_completion_params(provider, model, temperature),
        **_logging_params(provider, caller),
        **({"extra_body": provider.extra_body} if provider.extra_body else {}),
    )

    usage: dict[str, int] = {}
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }

    choice = response.choices[0] if response.choices else None
    text = (getattr(choice, "message", None) and choice.message.content) or ""
    finish_reason = getattr(choice, "finish_reason", None)
    return _strip_think_tags(text), usage, finish_reason


def _accumulate_tokens(usage: dict[str, int]) -> None:
    """Acumula tokens no contador global e no bucket ativo (Etapa 6), se houver.

    Chamadas de threads diferentes não são mais seriadas (lock de
    rate-limit removido) — só a mutação do contador global (`_token_usage`,
    compartilhado por todas as threads) precisa de proteção explícita
    (`_token_lock`), mantido pelo tempo mínimo possível. O bucket ativo
    (ContextVar) é isolado por thread/contexto por natureza — nunca é
    mutado por duas threads ao mesmo tempo — então não precisa de lock.
    """
    if not usage:
        return

    with _token_lock:
        _token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        _token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        _token_usage["total_tokens"] += usage.get("total_tokens", 0)
        _token_usage["call_count"] += 1

    bucket = _current_bucket.get()
    if bucket is not None:
        bucket["prompt_tokens"] += usage.get("prompt_tokens", 0)
        bucket["completion_tokens"] += usage.get("completion_tokens", 0)
        bucket["total_tokens"] += usage.get("total_tokens", 0)
        bucket["call_count"] += 1


def record_token_usage(usage: dict[str, int]) -> None:
    """Contabiliza um gasto de tokens que não veio de `generate()`.

    Face pública de `_accumulate_tokens`, para as chamadas de *embeddings*
    de `core.ragas_metrics`: elas usam outra rota da API (não
    `chat.completions`), mas o custo é real e deve aparecer no mesmo
    `TokenBucket` da avaliação — sem isso o eixo D subestimaria o custo
    de rodar as métricas.
    """
    _accumulate_tokens(usage)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Verifica se a exceção é um erro de rate limit (429)."""
    try:
        import openai

        if isinstance(exc, openai.RateLimitError):
            return True
        if isinstance(exc, openai.APIStatusError) and getattr(exc, "status_code", None) == 429:
            return True
    except ImportError:
        pass

    exc_str = str(exc)
    return (
        "429" in exc_str
        or "RESOURCE_EXHAUSTED" in exc_str
        or "rate_limit" in exc_str.lower()
    )


def _try_model(
    prompt: str,
    model: str,
    caller: str,
    provider: Optional[ProviderConfig] = None,
    temperature: Optional[float] = None,
) -> Optional[str]:
    """Tenta gerar, com retry em caso de 429 real retornado pelo provedor.

    Sem espera preventiva antes da chamada (rate limiting próprio
    removido) — dispara direto. Retorna o texto gerado ou None se falhar
    após MAX_RETRIES tentativas motivadas por 429 do provedor.

    Raises:
        Exception: Para erros não relacionados a rate limit.
    """
    for attempt in range(MAX_RETRIES):
        try:
            text, usage, finish_reason = _generate(
                prompt, model, provider, caller, temperature
            )
            _accumulate_tokens(usage)

            if usage:
                logger.info(
                    "LLM [%s] (%s): tokens — prompt=%d, completion=%d, total=%d",
                    caller,
                    model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                )

            if text:
                logger.info(
                    "LLM [%s] (%s): resposta recebida (%d chars)", caller, model, len(text)
                )
                return text

            if finish_reason == "length":
                # Nos modelos de raciocínio o teto de saída é compartilhado
                # com os tokens de raciocínio: se o esforço consumir tudo,
                # a resposta visível volta vazia. Sem esta mensagem o
                # sintoma é indistinguível de "o modelo não respondeu".
                logger.error(
                    "LLM [%s] (%s): resposta truncada — orçamento de saída "
                    "esgotado (limite atual: %s). Reduza OPENAI_REASONING_EFFORT "
                    "ou aumente OPENAI_MAX_COMPLETION_TOKENS.",
                    caller, model,
                    _completion_params(provider or get_provider(), model).get(
                        "max_completion_tokens", MAX_TOKENS
                    ),
                )
            else:
                logger.warning(
                    "LLM [%s] (%s): resposta vazia (finish_reason=%s)",
                    caller, model, finish_reason,
                )
            return None

        except Exception as exc:
            if _is_rate_limit_error(exc):
                delay = RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(
                    "LLM [%s] (%s): cota excedida (tentativa %d/%d), aguardando %.0fs",
                    caller, model, attempt + 1, MAX_RETRIES, delay,
                )
                time.sleep(delay)
            else:
                raise

    # Esgotou retries de rate limit
    return None


def generate(
    prompt: str,
    model: Optional[str] = None,
    *,
    caller: str = "desconhecido",
    provider: Optional[ProviderConfig] = None,
    temperature: Optional[float] = None,
) -> Optional[str]:
    """Chama o LLM do provedor ativo (`LLM_PROVIDER`), sem rate limiting
    próprio, com retry em caso de 429 real do provedor e contabilização
    de gastos em log.

    Chamadas de threads diferentes acontecem em paralelo de verdade —
    nenhum lock global serializa esta função entre threads.

    Args:
        prompt: Texto do prompt.
        model: Modelo específico (opcional). Default: o modelo do provedor
            ativo (ver `resolve_model`).
        caller: Identificador de quem está chamando (tipicamente `agent_id`
            ou `synthesizer_id`) — usado só para logging/observabilidade,
            não afeta o comportamento da chamada.
        provider: Provedor específico (opcional). Default: o de
            `LLM_PROVIDER`. Usado por `core.ragas_metrics` para fixar o
            juiz da avaliação num provedor independente do que o pipeline
            está usando, sem abrir um cliente próprio — assim a avaliação
            continua passando pela contabilização de tokens (`TokenBucket`)
            e pelo retry de 429 deste módulo.
        temperature: Sobrescreve `TEMPERATURE` onde o modelo aceita o
            parâmetro (ver `_completion_params`).

    Returns:
        Texto gerado, ou None se falhar ou API key ausente.
    """
    provider = provider or get_provider()
    if not _has_api_key(provider):
        logger.warning(
            "LLM [%s]: %s não configurada (LLM_PROVIDER=%s)",
            caller, provider.api_key_env, provider.name,
        )
        return None

    resolved_model = model or resolve_model(provider)
    logger.info(
        "LLM [%s]: enviando prompt (provider=%s, model=%s, %d chars) — %s",
        caller, provider.name, resolved_model, len(prompt), _preview(prompt),
    )
    logger.debug("LLM [%s]: prompt completo:\n%s", caller, prompt)

    try:
        result = _try_model(prompt, resolved_model, caller, provider, temperature)
        if result:
            return result
        logger.warning("LLM [%s]: falhou", caller)
    except Exception as exc:
        logger.error("LLM [%s]: erro inesperado — %s", caller, exc)

    return None


def get_token_usage() -> dict[str, int]:
    """Retorna o acumulado de tokens consumidos (thread-safe)."""
    with _token_lock:
        return dict(_token_usage)


def reset_token_usage() -> None:
    """Reseta o acumulador de tokens (útil entre análises)."""
    with _token_lock:
        _token_usage["prompt_tokens"] = 0
        _token_usage["completion_tokens"] = 0
        _token_usage["total_tokens"] = 0
        _token_usage["call_count"] = 0


def _stream_response(
    prompt: str,
    model: str,
    usage_out: dict[str, int] | None = None,
    provider: Optional[ProviderConfig] = None,
    caller: str = "desconhecido",
):
    """Chama a API do provedor ativo em modo streaming e yield tokens
    incrementalmente.

    No DeepSeek, com `thinking` desabilitado a resposta não deveria
    conter blocos <think>/reasoning_content; o filtro é mantido
    defensivamente (e é inofensivo na OpenAI, que não emite esses blocos).

    `stream_options={"include_usage": True}` (padrão OpenAI-compatível,
    suportado pelo DeepSeek) faz a API emitir um chunk final só com
    `usage` populado (sem `choices`) — capturado em `usage_out` (dict
    mutável passado pelo caller) para alimentar a contabilização de
    tokens por bucket (Etapa 6 do PLANO_REFATORACAO.md), que antes desta
    mudança ficava sempre zerada em chamadas de streaming (só
    `call_count` era incrementado). Se o provedor não enviar esse chunk
    (API mudar/não suportar), `usage_out` simplesmente permanece vazio —
    degrada para o comportamento anterior, sem quebrar nada.
    """
    provider = provider or get_provider()
    stream = _client(provider).chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        stream_options={"include_usage": True},
        **_completion_params(provider, model),
        **_logging_params(provider, caller),
        **({"extra_body": provider.extra_body} if provider.extra_body else {}),
    )

    buffer = ""
    inside_think = False

    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None and usage_out is not None:
            usage_out["prompt_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
            usage_out["completion_tokens"] = getattr(usage, "completion_tokens", 0) or 0
            usage_out["total_tokens"] = getattr(usage, "total_tokens", 0) or 0

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        token = getattr(delta, "content", None) or ""
        if not token:
            continue

        buffer += token

        # Detectar e pular blocos <think>...</think>
        if "<think>" in buffer and not inside_think:
            inside_think = True
            pre = buffer.split("<think>")[0]
            if pre:
                yield pre
            buffer = buffer[buffer.index("<think>"):]

        if inside_think:
            if "</think>" in buffer:
                after = buffer.split("</think>", 1)[1].lstrip()
                buffer = after
                inside_think = False
                if buffer:
                    yield buffer
                    buffer = ""
            continue

        yield token
        buffer = ""


def generate_stream(prompt: str, model: str | None = None, *, caller: str = "desconhecido"):
    """Streaming via provedor ativo (`LLM_PROVIDER`), sem rate limiting
    próprio, com retry em caso de 429 real do provedor. Yields tokens
    conforme chegam.

    Sem lock global: diferente do comportamento anterior (onde este
    generator segurava um lock durante toda a transmissão, bloqueando
    qualquer chamada de outra thread até o streaming terminar), chamadas
    concorrentes de outras threads não são mais afetadas por esta.

    Args:
        prompt: Texto do prompt.
        model: Modelo específico (opcional). Default: o modelo do provedor
            ativo (ver `resolve_model`).
        caller: Identificador de quem está chamando — só para
            logging/observabilidade (ver `generate`).

    Yields:
        Tokens de texto conforme são gerados pelo LLM.
    """
    provider = get_provider()
    if not _has_api_key(provider):
        logger.warning(
            "LLM [%s]: %s não configurada (LLM_PROVIDER=%s)",
            caller, provider.api_key_env, provider.name,
        )
        return

    resolved_model = model or resolve_model(provider)
    logger.info(
        "LLM [%s]: enviando prompt em modo streaming (provider=%s, model=%s, %d chars) — %s",
        caller, provider.name, resolved_model, len(prompt), _preview(prompt),
    )
    logger.debug("LLM [%s]: prompt completo (streaming):\n%s", caller, prompt)

    total_chars = 0

    for attempt in range(MAX_RETRIES):
        try:
            got_tokens = False
            usage_out: dict[str, int] = {}
            for token in _stream_response(prompt, resolved_model, usage_out, provider, caller):
                got_tokens = True
                total_chars += len(token)
                yield token

            if usage_out:
                _accumulate_tokens(usage_out)
                logger.info(
                    "LLM [%s] (%s): tokens — prompt=%d, completion=%d, total=%d",
                    caller, resolved_model,
                    usage_out.get("prompt_tokens", 0),
                    usage_out.get("completion_tokens", 0),
                    usage_out.get("total_tokens", 0),
                )
            else:
                with _token_lock:
                    _token_usage["call_count"] += 1
            if got_tokens:
                logger.info(
                    "LLM [%s] (%s): streaming concluído (%d chars)",
                    caller, resolved_model, total_chars,
                )
            else:
                logger.warning(
                    "LLM [%s] (%s): streaming retornou resposta vazia", caller, resolved_model
                )
            return

        except Exception as exc:
            if _is_rate_limit_error(exc):
                delay = RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(
                    "LLM [%s] (%s): 429 (tentativa %d/%d), aguardando %.0fs",
                    caller, resolved_model, attempt + 1, MAX_RETRIES, delay,
                )
                time.sleep(delay)
            else:
                logger.error("LLM [%s] (%s): erro — %s", caller, resolved_model, exc)
                return

    logger.error("LLM [%s]: todas as tentativas falharam", caller)
