"""
Cliente LLM centralizado (DeepSeek) com rate limiting, retry em caso de
rate limit (429) e contabilização de tokens.

Usa o modelo `deepseek-v4-flash` com `thinking` desabilitado (resposta
direta, sem chain-of-thought) — adequado para síntese de texto e
extração de JSON estruturado. API compatível com o SDK da OpenAI
(`base_url="https://api.deepseek.com"`).

Serializa chamadas via lock global para evitar estouro de cota, com
retry automático em caso de 429.

Observabilidade: todo call site passa um `caller` (tipicamente o
`agent_id`/`synthesizer_id` de quem disparou a chamada) — logado junto
com um preview de uma linha do prompt (nível INFO) antes de cada chamada
real à API. O prompt completo só aparece no log em nível DEBUG
(`LOG_LEVEL=DEBUG`), para não derramar dados de análise no log em produção
por padrão.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Lock global — garante que só uma chamada ao LLM acontece por vez
_lock = threading.Lock()

# Timestamp da última chamada
_last_call_time = 0.0
_MIN_INTERVAL = 2.0  # intervalo mínimo entre chamadas

MAX_RETRIES = 2  # retries antes de desistir
RETRY_BASE_DELAY = 10.0  # segundos

MODEL = "deepseek-v4-flash"

# thinking desabilitado — resposta direta, sem reasoning_content
_THINKING_DISABLED = {"type": "disabled"}

# Acumulador global de tokens (thread-safe via _lock)
_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "call_count": 0,
}


def _has_api_key() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


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


def _client():
    from openai import OpenAI

    return OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")


def _generate(prompt: str, model: str) -> tuple[Optional[str], dict[str, int]]:
    """Chama a API do DeepSeek e retorna (texto, token_usage)."""
    response = _client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
        extra_body={"thinking": _THINKING_DISABLED},
    )

    usage: dict[str, int] = {}
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }

    text = response.choices[0].message.content or ""
    return _strip_think_tags(text), usage


def _accumulate_tokens(usage: dict[str, int]) -> None:
    """Acumula tokens no contador global (chamado dentro do _lock)."""
    if not usage:
        return
    _token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
    _token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
    _token_usage["total_tokens"] += usage.get("total_tokens", 0)
    _token_usage["call_count"] += 1


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


def _try_model(prompt: str, model: str, caller: str) -> Optional[str]:
    """Tenta gerar com retry em caso de 429.

    Chamado dentro do _lock global. Retorna o texto gerado ou None
    se falhar após MAX_RETRIES tentativas de rate limit.

    Raises:
        Exception: Para erros não relacionados a rate limit.
    """
    global _last_call_time

    for attempt in range(MAX_RETRIES):
        elapsed = time.time() - _last_call_time
        if elapsed < _MIN_INTERVAL:
            wait = _MIN_INTERVAL - elapsed
            logger.info("LLM [%s]: rate limit — aguardando %.1fs", caller, wait)
            time.sleep(wait)

        try:
            _last_call_time = time.time()
            text, usage = _generate(prompt, model)
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

            logger.warning("LLM [%s] (%s): resposta vazia", caller, model)
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
    prompt: str, model: Optional[str] = None, *, caller: str = "desconhecido"
) -> Optional[str]:
    """Chama o LLM (DeepSeek) com rate limiting, retry e contabilização.

    Args:
        prompt: Texto do prompt.
        model: Modelo específico (opcional). Default: MODEL ("deepseek-v4-flash").
        caller: Identificador de quem está chamando (tipicamente `agent_id`
            ou `synthesizer_id`) — usado só para logging/observabilidade,
            não afeta o comportamento da chamada.

    Returns:
        Texto gerado, ou None se falhar ou API key ausente.
    """
    if not _has_api_key():
        logger.warning("LLM [%s]: DEEPSEEK_API_KEY não configurada", caller)
        return None

    resolved_model = model or MODEL
    logger.info(
        "LLM [%s]: enviando prompt (model=%s, %d chars) — %s",
        caller, resolved_model, len(prompt), _preview(prompt),
    )
    logger.debug("LLM [%s]: prompt completo:\n%s", caller, prompt)

    with _lock:
        try:
            result = _try_model(prompt, resolved_model, caller)
            if result:
                return result
            logger.warning("LLM [%s]: falhou", caller)
        except Exception as exc:
            logger.error("LLM [%s]: erro inesperado — %s", caller, exc)

    return None


def get_token_usage() -> dict[str, int]:
    """Retorna o acumulado de tokens consumidos (thread-safe)."""
    with _lock:
        return dict(_token_usage)


def reset_token_usage() -> None:
    """Reseta o acumulador de tokens (útil entre análises)."""
    with _lock:
        _token_usage["prompt_tokens"] = 0
        _token_usage["completion_tokens"] = 0
        _token_usage["total_tokens"] = 0
        _token_usage["call_count"] = 0


def _stream_response(prompt: str, model: str):
    """Chama a API do DeepSeek em modo streaming e yield tokens incrementalmente.

    Com `thinking` desabilitado a resposta não deveria conter blocos
    <think>/reasoning_content, mas o filtro é mantido defensivamente.
    """
    stream = _client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
        stream=True,
        extra_body={"thinking": _THINKING_DISABLED},
    )

    buffer = ""
    inside_think = False

    for chunk in stream:
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
    """Streaming via DeepSeek, com retry em caso de 429. Yields tokens conforme chegam.

    Args:
        prompt: Texto do prompt.
        model: Modelo específico (opcional). Default: MODEL ("deepseek-v4-flash").
        caller: Identificador de quem está chamando — só para
            logging/observabilidade (ver `generate`).

    Yields:
        Tokens de texto conforme são gerados pelo LLM.
    """
    global _last_call_time

    if not _has_api_key():
        logger.warning("LLM [%s]: DEEPSEEK_API_KEY não configurada", caller)
        return

    resolved_model = model or MODEL
    logger.info(
        "LLM [%s]: enviando prompt em modo streaming (model=%s, %d chars) — %s",
        caller, resolved_model, len(prompt), _preview(prompt),
    )
    logger.debug("LLM [%s]: prompt completo (streaming):\n%s", caller, prompt)

    total_chars = 0

    with _lock:
        for attempt in range(MAX_RETRIES):
            elapsed = time.time() - _last_call_time
            if elapsed < _MIN_INTERVAL:
                wait = _MIN_INTERVAL - elapsed
                logger.info("LLM [%s]: rate limit — aguardando %.1fs", caller, wait)
                time.sleep(wait)

            try:
                _last_call_time = time.time()
                got_tokens = False
                for token in _stream_response(prompt, resolved_model):
                    got_tokens = True
                    total_chars += len(token)
                    yield token

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
