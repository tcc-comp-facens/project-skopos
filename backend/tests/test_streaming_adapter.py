"""Tests for StreamingAdapter."""

import pytest
from queue import Queue

from core.streaming_adapter import StreamingAdapter, CHUNK_SIZE


@pytest.fixture
def ws_queue():
    return Queue()


@pytest.fixture
def adapter(ws_queue):
    return StreamingAdapter(ws_queue, "test-analysis-id", "star")


class TestStreamText:
    def test_chunks_are_within_chunk_size(self, adapter, ws_queue):
        text = "A" * 250  # longer than CHUNK_SIZE
        adapter.stream_text(text)
        while not ws_queue.empty():
            event = ws_queue.get()
            assert len(event["payload"]) <= CHUNK_SIZE

    def test_concatenation_equals_original(self, adapter, ws_queue):
        text = "Hello, this is a test of the streaming adapter with enough text to span multiple chunks easily."
        adapter.stream_text(text)
        chunks = []
        while not ws_queue.empty():
            event = ws_queue.get()
            chunks.append(event["payload"])
        assert "".join(chunks) == text


class TestStreamTokens:
    def test_buffers_and_sends_in_chunks(self, adapter, ws_queue):
        # Generate tokens smaller than chunk_size
        tokens = ["word "] * 50  # 5 chars each, 250 total
        full_text = adapter.stream_tokens(iter(tokens))
        assert full_text == "".join(tokens)
        # Should have produced multiple events
        event_count = ws_queue.qsize()
        assert event_count > 1

    def test_returns_complete_text(self, adapter, ws_queue):
        tokens = ["Hello", " ", "World", "!"]
        full_text = adapter.stream_tokens(iter(tokens))
        assert full_text == "Hello World!"


class TestEventFormat:
    def test_events_have_correct_format(self, adapter, ws_queue):
        adapter.stream_text("test payload")
        event = ws_queue.get()
        assert event["analysisId"] == "test-analysis-id"
        assert event["architecture"] == "star"
        assert event["type"] == "chunk"
        assert "payload" in event
