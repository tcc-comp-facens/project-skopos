"""Tests for the chat WebSocket endpoint (/ws/chat/{session_id})."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.state import active_chat_sessions
from main import app

client = TestClient(app)


def _session_id() -> str:
    return str(uuid.uuid4())


def _drain_chunks(ws) -> list[str]:
    chunks = []
    while True:
        event = ws.receive_json()
        if event["type"] == "system_done":
            break
        assert event["type"] == "system_chunk"
        chunks.append(event["payload"])
    return chunks


class TestChatWebsocketProtocol:
    def test_complete_message_dispatches_analysis(self):
        session_id = _session_id()
        with patch(
            "api.chat_websocket.get_available_year_range", return_value=(2015, 2025)
        ), patch(
            "api.chat_websocket.run_chat_analysis",
            return_value=("analysis-123", "Analisar dengue de 2019 a 2022."),
        ):
            with client.websocket_connect(f"/ws/chat/{session_id}") as ws:
                ws.send_json({
                    "type": "user_message",
                    "payload": {"text": "compare dengue de 2019 a 2022"},
                })

                ack = ws.receive_json()
                assert ack["type"] == "user_ack"

                started = ws.receive_json()
                assert started["type"] == "analysis_started"
                assert started["payload"] == "analysis-123"

                assert "".join(_drain_chunks(ws)) == "Analisar dengue de 2019 a 2022."

    def test_incomplete_message_asks_for_clarification(self):
        session_id = _session_id()
        with patch(
            "api.chat_websocket.get_available_year_range", return_value=(2015, 2025)
        ), patch("core.llm_client.generate", return_value=None):
            with client.websocket_connect(f"/ws/chat/{session_id}") as ws:
                ws.send_json({"type": "user_message", "payload": {"text": "oi"}})

                ack = ws.receive_json()
                assert ack["type"] == "user_ack"

                chunks = _drain_chunks(ws)
                assert "".join(chunks)

    def test_message_too_long_is_rejected(self):
        session_id = _session_id()
        with patch(
            "api.chat_websocket.get_available_year_range", return_value=(2015, 2025)
        ):
            with client.websocket_connect(f"/ws/chat/{session_id}") as ws:
                ws.send_json({
                    "type": "user_message",
                    "payload": {"text": "a" * 2000},
                })

                ack = ws.receive_json()
                assert ack["type"] == "user_ack"

                err = ws.receive_json()
                assert err["type"] == "error"

    def test_invalid_session_id_is_rejected(self):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/chat/not-a-uuid") as ws:
                ws.receive_json()

    def test_second_message_rejected_while_previous_in_progress(self):
        session_id = _session_id()
        try:
            with patch(
                "api.chat_websocket.get_available_year_range",
                return_value=(2015, 2025),
            ):
                with client.websocket_connect(f"/ws/chat/{session_id}") as ws:
                    active_chat_sessions[session_id] = True

                    ws.send_json({
                        "type": "user_message",
                        "payload": {"text": "compare dengue de 2019 a 2022"},
                    })

                    ack = ws.receive_json()
                    assert ack["type"] == "user_ack"

                    err = ws.receive_json()
                    assert err["type"] == "error"
        finally:
            active_chat_sessions.pop(session_id, None)
