"""Tests for the analysis WebSocket endpoint (/ws/{analysis_id}).

Regressão: um desconexão antes de done_count==2 (reload do cliente,
blip de rede, o double-connect de desenvolvimento do React StrictMode)
não pode descartar a fila/threads da análise — as threads star/
hierarchical continuam rodando independente da conexão, e uma
reconexão precisa conseguir retomar o consumo da mesma fila. Bug real:
o cleanup vivia num `finally` disparado por QUALQUER desconexão, então
uma reconexão caía em "No active analysis found for this ID" mesmo com
a análise de verdade ainda em andamento (ou já concluída) no backend.
"""

from __future__ import annotations

import queue
import uuid

from fastapi.testclient import TestClient

from api.state import active_queues, active_results, active_threads, active_ws_generation
from main import app

client = TestClient(app)


def _analysis_id() -> str:
    return str(uuid.uuid4())


class TestDisconnectBeforeCompletion:
    def test_premature_disconnect_does_not_discard_queue(self):
        """Desconectar antes de done_count==2 preserva a fila — quem
        reconectar em seguida precisa conseguir continuar consumindo."""
        analysis_id = _analysis_id()
        q: queue.Queue = queue.Queue()
        q.put({"analysisId": analysis_id, "architecture": "star", "type": "chunk", "payload": "olá"})
        active_queues[analysis_id] = q

        try:
            with client.websocket_connect(f"/ws/{analysis_id}") as ws:
                event = ws.receive_json()
                assert event["type"] == "chunk"
                assert event["architecture"] == "star"
                # Desconecta aqui de propósito, antes de done_count chegar a 2
                # (equivalente ao cleanup do React StrictMode fechando a
                # conexão "canário", ou a um reload do navegador).

            assert analysis_id in active_queues, (
                "fila foi descartada numa desconexão prematura — uma "
                "reconexão legítima não teria como retomar o consumo"
            )
        finally:
            active_queues.pop(analysis_id, None)
            active_threads.pop(analysis_id, None)
            active_results.pop(analysis_id, None)

    def test_reconnect_after_premature_disconnect_resumes_same_queue(self):
        """Depois de uma desconexão prematura, uma nova conexão pro mesmo
        analysis_id deve continuar recebendo os eventos que a análise
        (ainda rodando em background) empurra na mesma fila — não deve
        cair em 'No active analysis found'."""
        analysis_id = _analysis_id()
        q: queue.Queue = queue.Queue()
        q.put({"analysisId": analysis_id, "architecture": "star", "type": "chunk", "payload": "primeira parte"})
        active_queues[analysis_id] = q

        try:
            with client.websocket_connect(f"/ws/{analysis_id}") as ws:
                ws.receive_json()
                # desconexão prematura (sai do `with` antes de done_count==2)

            # A análise (rodando em background, alheia à conexão) continua
            # empurrando eventos na MESMA fila.
            q.put({"analysisId": analysis_id, "architecture": "star", "type": "chunk", "payload": "segunda parte"})

            with client.websocket_connect(f"/ws/{analysis_id}") as ws2:
                event = ws2.receive_json()
                assert event["type"] == "chunk"
                assert event["payload"] == "segunda parte"
        finally:
            active_queues.pop(analysis_id, None)
            active_threads.pop(analysis_id, None)
            active_results.pop(analysis_id, None)

    def test_normal_completion_still_discards_queue_and_threads(self):
        """O caminho feliz (done_count chega a 2) continua limpando a
        fila/threads normalmente — a correção só afeta desconexões
        prematuras, não o fim de análise de verdade."""
        analysis_id = _analysis_id()
        q: queue.Queue = queue.Queue()
        q.put({"analysisId": analysis_id, "architecture": "star", "type": "done", "payload": ""})
        q.put({"analysisId": analysis_id, "architecture": "hierarchical", "type": "done", "payload": ""})
        active_queues[analysis_id] = q
        active_threads[analysis_id] = []
        # Sem "star"/"hierarchical" em active_results, o endpoint pula o
        # cálculo de métricas/relatório comparativo (branch `if star_result
        # and hier_result`) e retorna logo após done_count==2 — suficiente
        # pra este teste, que só quer confirmar a limpeza pós-conclusão.

        try:
            with client.websocket_connect(f"/ws/{analysis_id}") as ws:
                ws.receive_json()  # done star
                ws.receive_json()  # done hierarchical

            assert analysis_id not in active_queues
            assert analysis_id not in active_threads
        finally:
            active_queues.pop(analysis_id, None)
            active_threads.pop(analysis_id, None)
            active_results.pop(analysis_id, None)


class TestConcurrentConnectionsDoNotRaceForEvents:
    def test_superseded_connection_stops_consuming_instead_of_racing(self):
        """Regressão: duas conexões vivas ao mesmo tempo para o mesmo
        analysis_id (ex.: o double-connect de dev do React StrictMode,
        onde a conexão "canário" ainda não fechou quando a de verdade já
        conectou) não podem competir pelos mesmos itens da fila — a mais
        antiga precisa perceber que foi substituída e parar, em vez de
        arriscar roubar um evento e falhar ao entregá-lo (seu socket já
        pode estar fechado do lado do cliente nesse ponto), derrubando o
        evento no vácuo para sempre."""
        analysis_id = _analysis_id()
        q: queue.Queue = queue.Queue()
        active_queues[analysis_id] = q

        try:
            with client.websocket_connect(f"/ws/{analysis_id}") as ws_old:
                # ws_old já conectou e está bloqueado tentando consumir de
                # uma fila vazia (timeout=1.0 por iteração). Conecta uma
                # segunda vez para o MESMO analysis_id enquanto a primeira
                # ainda está viva — isso incrementa a geração e supera
                # ws_old.
                with client.websocket_connect(f"/ws/{analysis_id}") as ws_new:
                    # Dá tempo de ws_old notar, na próxima iteração do seu
                    # laço, que foi superada (timeout=1.0 do get + folga).
                    import time
                    time.sleep(1.3)

                    q.put({
                        "analysisId": analysis_id, "architecture": "star",
                        "type": "chunk", "payload": "só pra quem é atual",
                    })

                    event = ws_new.receive_json()
                    assert event["payload"] == "só pra quem é atual"

                    # ws_old não deve ter recebido nada — nem roubado o
                    # item da fila sem conseguir entregá-lo. Fecha a conexão
                    # nova primeiro pra permitir observar o estado final.
        finally:
            active_queues.pop(analysis_id, None)
            active_threads.pop(analysis_id, None)
            active_ws_generation.pop(analysis_id, None)
            active_results.pop(analysis_id, None)


class TestNoActiveAnalysis:
    def test_unknown_analysis_id_gets_error_and_closes(self):
        analysis_id = _analysis_id()
        with client.websocket_connect(f"/ws/{analysis_id}") as ws:
            event = ws.receive_json()
            assert event["type"] == "error"
            assert "No active analysis" in event["payload"]
