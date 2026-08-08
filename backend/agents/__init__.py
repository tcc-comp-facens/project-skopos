"""Pacote de agentes multiagente — exporta todos os agentes especializados.

Organização:
- agents.base: Classe base AgenteCoALA (framework CoALA)
- agents.intent: Agente de interpretação de intenção (chat, sem regex)
- agents.domain: Agentes de domínio de saúde e orçamento (Req 14.1)
- agents.analytical: Agentes analíticos (Req 14.2) + TextSynthesizer
- agents.context: Agente de contexto (Req 14.3)
- agents.star: Topologia estrela (Req 14.4)
- agents.hierarchical: Topologia hierárquica (Req 14.4)
"""

# Base
from agents.base import AgenteCoALA, ActionFailure

# Intent agent (PLANO_REFATORACAO.md, Etapa 1)
from agents.intent import AgenteInterpretacaoIntencao, AnalysisIntent, IntentResult

# Domain agents (Req 14.1)
from agents.domain import (
    AgenteCOVID,
    AgenteSIH,
    AgenteSIM,
    AgenteSIPNI,
    AgenteSINASC,
    AgenteSIA,
    AgenteCNES,
    AgenteSINAN,
    AgenteOrcamentoSubfuncao,
)

# Analytical agents (Req 14.2)
from agents.analytical import (
    AgenteAnalitico,
    TextSynthesizer,
)

# Context agent (Req 14.3)
from agents.context import AgenteContextoOrcamentario

# Star topology (Req 14.4)
from agents.star import OrquestradorEstrela

# Hierarchical topology (Req 14.4)
from agents.hierarchical import CoordenadorGeral

__all__ = [
    # Base
    "AgenteCoALA",
    "ActionFailure",
    # Intent
    "AgenteInterpretacaoIntencao",
    "AnalysisIntent",
    "IntentResult",
    # Domain
    "AgenteCOVID",
    "AgenteSIH",
    "AgenteSIM",
    "AgenteSIPNI",
    "AgenteSINASC",
    "AgenteSIA",
    "AgenteCNES",
    "AgenteSINAN",
    "AgenteOrcamentoSubfuncao",
    # Analytical
    "AgenteAnalitico",
    "TextSynthesizer",
    # Context
    "AgenteContextoOrcamentario",
    # Star
    "OrquestradorEstrela",
    # Hierarchical
    "CoordenadorGeral",
]
