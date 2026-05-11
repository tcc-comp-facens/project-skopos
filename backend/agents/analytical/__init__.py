"""Agentes analíticos para processamento estatístico e síntese."""

from agents.analytical.correlacao import AgenteCorrelacao
from agents.analytical.anomalias import AgenteAnomalias
from agents.analytical.sintetizador import TextSynthesizer

__all__ = [
    "AgenteCorrelacao",
    "AgenteAnomalias",
    "TextSynthesizer",
]
