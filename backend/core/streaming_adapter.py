"""
Adaptador de streaming para WebSocket.

Responsabilidade única: chunking de texto e envio para ws_queue.
Não é um agente BDI — é infraestrutura de transporte reutilizável
pelo orquestrador estrela, coordenador hierárquico e relatório comparativo.

Requisitos: 7.2, 7.6
"""

from __future__ import annotations

import logging
from queue import Queue
from typing import Generator

logger = logging.getLogger(__name__)

CHUNK_SIZE = 80  # approximate chars per streaming chunk


class StreamingAdapter:
    """Adapta saída textual para streaming WebSocket em chunks.

    Encapsula a lógica de chunking (~80 chars) e envio de eventos
    WSEvent para a fila compartilhada. Reutilizável por qualquer
    componente que precise fazer streaming de texto.

    Args:
        ws_queue: Fila compartilhada para eventos WebSocket.
        analysis_id: UUID da análise corrente.
        architecture: Identificador da topologia ("star", "hierarchical", "both").
        chunk_size: Tamanho aproximado de cada chunk em caracteres.
    """

    def __init__(
        self,
        ws_queue: Queue,
        analysis_id: str,
        architecture: str,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self.ws_queue = ws_queue
        self.analysis_id = analysis_id
        self.architecture = architecture
        self.chunk_size = chunk_size

    def stream_text(self, text: str) -> None:
        """Envia texto pré-gerado em chunks para ws_queue.

        Args:
            text: Texto completo a ser enviado em chunks.
        """
        for i in range(0, len(text), self.chunk_size):
            chunk = text[i: i + self.chunk_size]
            self.ws_queue.put({
                "analysisId": self.analysis_id,
                "architecture": self.architecture,
                "type": "chunk",
                "payload": chunk,
            })

    def stream_tokens(self, token_generator: Generator[str, None, None]) -> str:
        """Consome generator de tokens, faz buffering e streaming.

        Acumula tokens em buffer até atingir chunk_size, então envia
        o buffer como evento chunk. Retorna o texto completo acumulado.

        Args:
            token_generator: Generator que yield tokens individuais do LLM.

        Returns:
            Texto completo concatenado de todos os tokens.
        """
        full_text = ""
        buffer = ""

        for token in token_generator:
            full_text += token
            buffer += token

            if len(buffer) >= self.chunk_size:
                self.ws_queue.put({
                    "analysisId": self.analysis_id,
                    "architecture": self.architecture,
                    "type": "chunk",
                    "payload": buffer,
                })
                buffer = ""

        # Enviar resto do buffer
        if buffer:
            self.ws_queue.put({
                "analysisId": self.analysis_id,
                "architecture": self.architecture,
                "type": "chunk",
                "payload": buffer,
            })

        return full_text
