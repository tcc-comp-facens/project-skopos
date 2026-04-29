"""
Cliente LLM centralizado com rate limiting, fallback entre modelos e
contabilização de tokens.

Cadeia de fallback (limites independentes por modelo no Groq free tier):
  1. llama-3.3-70b-versatile  — melhor qualidade, 100K TPD
  2. qwen/qwen3-32b           — boa qualidade, 500K TPD
  3. llama-4-scout-17b-16e    — rápido, 500K TPD

Serializa chamadas via lock global para evitar estouro de cota,
com retry automático em caso de 429 (rate limit) antes de avançar
para o próximo modelo da cadeia.
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
_MIN_INTERVAL = 2.0  # Groq free tier: 30 RPM

MAX_RETRIES = 2  # retries por modelo antes de cair para o próximo
RETRY_BASE_DELAY = 10.0  # segundos

# Cadeia de fallback — ordem de prioridade
MODEL_CHAIN: list[str] = [
    "llama-3.3-70b-versatile",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]

# Acumulador global de tokens (thread-safe via _lock)
_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "call_count": 0,
}


def _generate_groq(prompt: str, model: str) -> tuple[Optional[str], dict[str, int]]:
    """Chama a API do Groq e retorna (texto, token_usage).

    Remove tags <think>...</think> de modelos de raciocínio (ex: Qwen3)
    que incluem processo de pensamento na resposta.
    """
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
    )

    usage: dict[str, int] = {}
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }

    text = response.choices[0].message.content or ""

    # Remover tags <think>...</think> de modelos de raciocínio (Qwen3, etc.)
    import re
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()

    return text, usage


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
    exc_str = str(exc)
    return (
        "429" in exc_str
        or "RESOURCE_EXHAUSTED" in exc_str
        or "rate_limit" in exc_str.lower()
    )


def _try_model(prompt: str, model: str) -> Optional[str]:
    """Tenta gerar com um modelo específico, com retry em caso de 429.

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
            logger.info("LLM rate limit: aguardando %.1fs", wait)
            time.sleep(wait)

        try:
            _last_call_time = time.time()
            text, usage = _generate_groq(prompt, model)
            _accumulate_tokens(usage)

            if usage:
                logger.info(
                    "LLM (%s): tokens — prompt=%d, completion=%d, total=%d",
                    model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                )

            if text:
                logger.info("LLM (%s): resposta gerada com sucesso", model)
                return text

            logger.warning("LLM (%s): resposta vazia", model)
            return None

        except Exception as exc:
            if _is_rate_limit_error(exc):
                delay = RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(
                    "LLM (%s): cota excedida (tentativa %d/%d), aguardando %.0fs",
                    model, attempt + 1, MAX_RETRIES, delay,
                )
                time.sleep(delay)
            else:
                raise

    # Esgotou retries de rate limit para este modelo
    return None


def generate(prompt: str, model: Optional[str] = None) -> Optional[str]:
    """Chama o LLM com fallback entre modelos, rate limiting e contabilização.

    Se um modelo específico é passado, usa apenas ele (sem fallback).
    Caso contrário, percorre a cadeia MODEL_CHAIN até obter resposta.

    Args:
        prompt: Texto do prompt.
        model: Modelo específico (opcional). Se None, usa a cadeia de fallback.

    Returns:
        Texto gerado, ou None se todos os modelos falharem ou API key ausente.
    """
    if not os.environ.get("GROQ_API_KEY", "").strip():
        logger.warning("LLM: GROQ_API_KEY não configurada")
        return None

    models = [model] if model else MODEL_CHAIN

    with _lock:
        for current_model in models:
            try:
                result = _try_model(prompt, current_model)
                if result:
                    return result
                # Resposta vazia ou rate limit esgotado — tentar próximo modelo
                logger.warning(
                    "LLM (%s): falhou, tentando próximo modelo da cadeia",
                    current_model,
                )
            except Exception as exc:
                # Erro não-429 — logar e tentar próximo modelo
                logger.error(
                    "LLM (%s): erro inesperado — %s, tentando próximo modelo",
                    current_model, exc,
                )

    logger.error("LLM: todos os modelos da cadeia falharam")
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


def _stream_groq(prompt: str, model: str):
    """Chama a API do Groq em modo streaming e yield tokens incrementalmente.

    Remove tags <think>...</think> acumulando o texto e filtrando antes de yield.
    Yields tokens conforme chegam da API.
    """
    import re
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
        stream=True,
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
            # Yield tudo antes do <think>
            pre = buffer.split("<think>")[0]
            if pre:
                yield pre
            buffer = buffer[buffer.index("<think>"):]

        if inside_think:
            if "</think>" in buffer:
                # Fim do bloco think — descartar e continuar
                after = buffer.split("</think>", 1)[1].lstrip()
                buffer = after
                inside_think = False
                if buffer:
                    yield buffer
                    buffer = ""
            # Enquanto dentro de <think>, não yield nada
            continue

        # Fora de <think> — yield o token diretamente
        yield token
        buffer = ""


def generate_stream(prompt: str, model: str | None = None):
    """Streaming com fallback entre modelos. Yields tokens conforme chegam.

    Args:
        prompt: Texto do prompt.
        model: Modelo específico (opcional). Se None, usa a cadeia de fallback.

    Yields:
        Tokens de texto conforme são gerados pelo LLM.
    """
    global _last_call_time

    if not os.environ.get("GROQ_API_KEY", "").strip():
        logger.warning("LLM: GROQ_API_KEY não configurada")
        return

    models = [model] if model else MODEL_CHAIN

    with _lock:
        for current_model in models:
            for attempt in range(MAX_RETRIES):
                elapsed = time.time() - _last_call_time
                if elapsed < _MIN_INTERVAL:
                    wait = _MIN_INTERVAL - elapsed
                    time.sleep(wait)

                try:
                    _last_call_time = time.time()
                    got_tokens = False
                    for token in _stream_groq(prompt, current_model):
                        got_tokens = True
                        yield token

                    _token_usage["call_count"] += 1
                    if got_tokens:
                        logger.info("LLM stream (%s): concluído", current_model)
                        return
                    else:
                        logger.warning("LLM stream (%s): resposta vazia", current_model)
                        break  # próximo modelo

                except Exception as exc:
                    if _is_rate_limit_error(exc):
                        delay = RETRY_BASE_DELAY * (attempt + 1)
                        logger.warning(
                            "LLM stream (%s): 429 (tentativa %d/%d), aguardando %.0fs",
                            current_model, attempt + 1, MAX_RETRIES, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "LLM stream (%s): erro — %s", current_model, exc,
                        )
                        break  # próximo modelo

            logger.warning("LLM stream (%s): falhou, próximo modelo", current_model)

    logger.error("LLM stream: todos os modelos falharam")
