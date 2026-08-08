"""Agentes de domínio especializados em fontes de dados de saúde e orçamento."""

from agents.domain.agente_covid import AgenteCOVID
from agents.domain.agente_sih import AgenteSIH
from agents.domain.agente_sim import AgenteSIM
from agents.domain.agente_sipni import AgenteSIPNI
from agents.domain.agente_sinasc import AgenteSINASC
from agents.domain.agente_sia import AgenteSIA
from agents.domain.agente_cnes import AgenteCNES
from agents.domain.agente_sinan import AgenteSINAN
from agents.domain.agente_orcamento import AgenteOrcamentoSubfuncao

__all__ = [
    "AgenteCOVID",
    "AgenteSIH",
    "AgenteSIM",
    "AgenteSIPNI",
    "AgenteSINASC",
    "AgenteSIA",
    "AgenteCNES",
    "AgenteSINAN",
    "AgenteOrcamentoSubfuncao",
]
