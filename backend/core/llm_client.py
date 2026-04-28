"""
Cliente LLM centralizado com rate limiting, retry e contabilização de tokens.

Usa Groq (llama-3.3-70b-versatile) como provider único.
Serializa chamadas para evitar estouro de cota,
com retry automático em caso de 429 (rate limit).
"""

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

MAX_RETRIES = 3
RETRY_BASE_DELAY = 10.0  # segundos

# Acumulador global de tokens (thread-safe via _lock)
_token_usage: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "call_count": 0,
}


def _generate_groq(prompt: str, model: str) -> tuple[Optional[str], dict[str, int]]:
    """Chama a API do Groq e retorna (texto, token_usage)."""
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
    return text, usage


def _accumulate_tokens(usage: dict[str, int]) -> None:
    """Acumula tokens no contador global (chamado dentro do _lock)."""
    if not usage:
        return
    _token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
    _token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
    _token_usage["total_tokens"] += usage.get("total_tokens", 0)
    _token_usage["call_count"] += 1


def generate(prompt: str, model: Optional[str] = None) -> Optional[str]:
    """Chama o LLM (Groq) com rate limiting, retry e contabilização de tokens.

    Returns:
        Texto gerado, ou None se falhar após retries ou API key ausente.
    """
    global _last_call_time

    if not os.environ.get("GROQ_API_KEY", "").strip():
        logger.warning("LLM: GROQ_API_KEY não configurada")
        return None

    if model is None:
        model = "llama-3.3-70b-versatile"

    with _lock:
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
                        "LLM (groq): tokens — prompt=%d, completion=%d, total=%d",
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        usage.get("total_tokens", 0),
                    )

                if text:
                    logger.info("LLM (groq): resposta gerada com sucesso")
                    return text

                logger.warning("LLM (groq): resposta vazia")
                return None

            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "rate_limit" in exc_str.lower():
                    delay = RETRY_BASE_DELAY * (attempt + 1)
                    logger.warning(
                        "LLM (groq): cota excedida (tentativa %d/%d), aguardando %.0fs",
                        attempt + 1, MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("LLM (groq): erro inesperado — %s", exc)
                    return None

    logger.error("LLM (groq): falhou apos %d tentativas", MAX_RETRIES)
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
