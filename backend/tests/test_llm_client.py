"""Testes de core/llm_client.py.

Módulo nunca teve testes diretos (só cobertura indireta via mocks de
`generate`/`generate_stream` nos agentes) — ganhou testes dedicados agora
porque o comportamento de concorrência mudou de forma significativa:
o rate limiting próprio (lock global + intervalo mínimo entre chamadas)
foi completamente removido a pedido explícito, após um log de execução
real mostrar que ele segurava o lock durante toda a duração de uma
chamada em streaming, bloqueando a outra topologia por dezenas de
segundos. O retry em caso de 429 real do provedor foi mantido.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.llm_client as llm_client
from core.llm_client import TokenBucket


def _fake_response(text: str, prompt_tokens=10, completion_tokens=5, total_tokens=15):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def _fake_stream_chunks(tokens: list[str], usage=(20, 8, 28)):
    """Simula os chunks de uma resposta em streaming, incluindo o chunk
    final só com `usage` (stream_options={"include_usage": True})."""
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])
        for t in tokens
    ]
    chunks.append(
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=usage[0], completion_tokens=usage[1], total_tokens=usage[2]
            ),
        )
    )
    return chunks


@pytest.fixture(autouse=True)
def _reset_token_usage():
    llm_client.reset_token_usage()
    yield
    llm_client.reset_token_usage()


@pytest.fixture
def has_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")


class TestGenerateSuccess:
    def test_returns_text_and_accumulates_tokens(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("olá mundo")
        with patch("core.llm_client._client", return_value=mock_client):
            result = llm_client.generate("prompt", caller="test")

        assert result == "olá mundo"
        usage = llm_client.get_token_usage()
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 5
        assert usage["call_count"] == 1

    def test_no_api_key_returns_none(self):
        with patch("core.llm_client._has_api_key", return_value=False):
            result = llm_client.generate("prompt", caller="test")
        assert result is None


class TestGenerateNoSelfImposedRateLimit:
    """Confirma a remoção do rate limiting próprio: chamadas consecutivas
    não esperam nada entre si (sem sleep preventivo)."""

    def test_back_to_back_calls_never_sleep(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("ok")
        with patch("core.llm_client._client", return_value=mock_client), \
             patch("time.sleep") as mock_sleep:
            llm_client.generate("prompt 1", caller="a")
            llm_client.generate("prompt 2", caller="b")

        mock_sleep.assert_not_called()

    def test_concurrent_threads_do_not_block_each_other(self, has_api_key):
        """Antes da remoção do lock global, uma chamada presa (ex.: em
        streaming) bloqueava qualquer chamada de outra thread. Agora duas
        chamadas simultâneas de threads diferentes devem poder progredir
        em paralelo — nenhuma delas deve esperar pela outra."""
        release_first = threading.Event()
        entered_first = threading.Event()

        def slow_create(*args, **kwargs):
            entered_first.set()
            release_first.wait(timeout=5)
            return _fake_response("primeira")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = slow_create

        results = {}

        def call_first():
            with patch("core.llm_client._client", return_value=mock_client):
                results["first"] = llm_client.generate("devagar", caller="first")

        t = threading.Thread(target=call_first)
        t.start()
        assert entered_first.wait(timeout=2), "primeira chamada não iniciou a tempo"

        # Enquanto a primeira chamada está presa (segurando "recursos" só
        # dela), uma segunda chamada de OUTRA thread deve conseguir
        # completar sem esperar a primeira liberar nada.
        mock_client_2 = MagicMock()
        mock_client_2.chat.completions.create.return_value = _fake_response("segunda")
        started = time.time()
        with patch("core.llm_client._client", return_value=mock_client_2):
            second_result = llm_client.generate("rapida", caller="second")
        elapsed = time.time() - started

        assert second_result == "segunda"
        assert elapsed < 1.0, "segunda chamada esperou pela primeira — lock global voltou"

        release_first.set()
        t.join(timeout=5)
        assert results["first"] == "primeira"


class TestGenerateRetryOn429:
    def test_retries_on_rate_limit_then_succeeds(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            Exception("429 rate_limit exceeded"),
            _fake_response("sucesso na segunda tentativa"),
        ]
        with patch("core.llm_client._client", return_value=mock_client), \
             patch("time.sleep"):
            result = llm_client.generate("prompt", caller="test")

        assert result == "sucesso na segunda tentativa"
        assert mock_client.chat.completions.create.call_count == 2

    def test_exhausts_retries_returns_none(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("429 rate_limit exceeded")
        with patch("core.llm_client._client", return_value=mock_client), \
             patch("time.sleep"):
            result = llm_client.generate("prompt", caller="test")

        assert result is None
        assert mock_client.chat.completions.create.call_count == llm_client.MAX_RETRIES

    def test_non_rate_limit_exception_does_not_retry(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError("erro qualquer")
        with patch("core.llm_client._client", return_value=mock_client):
            result = llm_client.generate("prompt", caller="test")

        assert result is None
        assert mock_client.chat.completions.create.call_count == 1


class TestGenerateStream:
    def test_yields_tokens_and_captures_usage(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(
            _fake_stream_chunks(["ol", "á ", "mundo"])
        )
        with patch("core.llm_client._client", return_value=mock_client):
            tokens = list(llm_client.generate_stream("prompt", caller="test"))

        assert "".join(tokens) == "olá mundo"
        usage = llm_client.get_token_usage()
        assert usage["prompt_tokens"] == 20
        assert usage["completion_tokens"] == 8

    def test_include_usage_requested_in_stream_options(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(
            _fake_stream_chunks(["x"])
        )
        with patch("core.llm_client._client", return_value=mock_client):
            list(llm_client.generate_stream("prompt", caller="test"))

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs.get("stream_options") == {"include_usage": True}

    def test_no_api_key_yields_nothing(self):
        with patch("core.llm_client._has_api_key", return_value=False):
            tokens = list(llm_client.generate_stream("prompt", caller="test"))
        assert tokens == []

    def test_retries_on_rate_limit_during_streaming(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            Exception("429 rate_limit exceeded"),
            iter(_fake_stream_chunks(["depois", " do", " retry"])),
        ]
        with patch("core.llm_client._client", return_value=mock_client), \
             patch("time.sleep"):
            tokens = list(llm_client.generate_stream("prompt", caller="test"))

        assert "".join(tokens) == "depois do retry"


class TestTokenBucket:
    def test_isolates_usage_within_scope(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("x")
        with patch("core.llm_client._client", return_value=mock_client):
            with TokenBucket() as bucket:
                llm_client.generate("prompt", caller="dentro")
            llm_client.generate("prompt", caller="fora")

        snapshot = bucket.snapshot()
        assert snapshot["call_count"] == 1
        # Global continua acumulando as duas chamadas (dentro + fora)
        assert llm_client.get_token_usage()["call_count"] == 2

    def test_sequential_buckets_do_not_leak_into_each_other(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("x")
        with patch("core.llm_client._client", return_value=mock_client):
            with TokenBucket() as bucket_a:
                llm_client.generate("prompt", caller="a")
            with TokenBucket() as bucket_b:
                llm_client.generate("prompt", caller="b")
                llm_client.generate("prompt", caller="b2")

        assert bucket_a.snapshot()["call_count"] == 1
        assert bucket_b.snapshot()["call_count"] == 2

    def test_concurrent_threads_get_isolated_buckets(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("x")
        snapshots = {}

        def worker(name: str, n_calls: int):
            with patch("core.llm_client._client", return_value=mock_client):
                with TokenBucket() as bucket:
                    for _ in range(n_calls):
                        llm_client.generate("prompt", caller=name)
                snapshots[name] = bucket.snapshot()

        t1 = threading.Thread(target=worker, args=("star", 3))
        t2 = threading.Thread(target=worker, args=("hier", 5))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert snapshots["star"]["call_count"] == 3
        assert snapshots["hier"]["call_count"] == 5

    def test_no_active_bucket_outside_with_block(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("x")
        with TokenBucket() as bucket:
            pass
        with patch("core.llm_client._client", return_value=mock_client):
            llm_client.generate("prompt", caller="depois")

        assert bucket.snapshot()["call_count"] == 0


class TestTokenUsageResetAndGet:
    def test_reset_clears_counters(self, has_api_key):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("x")
        with patch("core.llm_client._client", return_value=mock_client):
            llm_client.generate("prompt", caller="test")
        assert llm_client.get_token_usage()["call_count"] == 1

        llm_client.reset_token_usage()
        usage = llm_client.get_token_usage()
        assert usage == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }
