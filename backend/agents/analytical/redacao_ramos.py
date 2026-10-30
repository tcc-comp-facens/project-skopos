"""
Partição dos achados por sistema de informação, para a redação por ramo.

Na topologia hierárquica, o `SupervisorAnalitico` reparte a redação como a
árvore reparte a coleta: um redator por sistema de saúde, cada um vendo só
os achados do seu sistema, e uma composição final a partir desses textos.
É o contexto repartido que Qian et al. (2025) descrevem nas topologias em
árvore, e o mesmo padrão de gerente e subordinados dos sistemas
multiagentes baseados em LLM. Na estrela, o orquestrador redige sozinho,
com todos os achados.

O sistema de cada achado vem do `tipo_indicador` (subtipo nativo do
`IndicadorSaude`), mapeado pelos `SUBTIPOS` de cada agente de saúde.
"""

from __future__ import annotations

from typing import Any

from agents.domain import (
    agente_cnes,
    agente_covid,
    agente_sia,
    agente_sih,
    agente_sim,
    agente_sinan,
    agente_sinasc,
    agente_sipni,
)

_SUBTIPOS_POR_SISTEMA: dict[str, list[str]] = {
    "covid": agente_covid.SUBTIPOS,
    "sih": agente_sih.SUBTIPOS,
    "sipni": agente_sipni.SUBTIPOS,
    "sim": agente_sim.SUBTIPOS,
    "sinan": agente_sinan.SUBTIPOS,
    "sinasc": agente_sinasc.SUBTIPOS,
    "sia": agente_sia.SUBTIPOS,
    "cnes": agente_cnes.SUBTIPOS,
}

SUBTIPO_SISTEMA: dict[str, str] = {
    tipo: sistema for sistema, tipos in _SUBTIPOS_POR_SISTEMA.items() for tipo in tipos
}

NOME_SISTEMA: dict[str, str] = {
    "covid": "COVID-19 (casos e óbitos)",
    "sih": "SIH (internações hospitalares)",
    "sipni": "SI-PNI (vacinação)",
    "sim": "SIM (mortalidade)",
    "sinan": "SINAN (agravos de notificação)",
    "sinasc": "SINASC (nascidos vivos)",
    "sia": "SIA (produção ambulatorial)",
    "cnes": "CNES (rede assistencial)",
}


def sistema_de(tipo_indicador: str | None) -> str:
    return SUBTIPO_SISTEMA.get(tipo_indicador or "", "outros")


def particionar(
    correlacoes: list[dict[str, Any]],
    anomalias: list[dict[str, Any]],
    contexto_orcamentario: dict[Any, Any],
    data_coverage: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Agrupa os achados por sistema, preservando a ordem recebida.

    Cada ramo recebe também o contexto orçamentário das subfunções que
    aparecem nos seus achados e só as lacunas que citam os seus
    indicadores (o resumo de completude é global).

    Returns:
        {sistema: {"correlacoes", "anomalias", "contexto_orcamentario",
        "data_coverage"}}, na ordem em que cada sistema aparece primeiro.
    """
    ramos: dict[str, dict[str, Any]] = {}
    for campo, achados in (("correlacoes", correlacoes), ("anomalias", anomalias)):
        for achado in achados or []:
            ramo = ramos.setdefault(
                sistema_de(achado.get("tipo_indicador")), {"correlacoes": [], "anomalias": []}
            )
            ramo[campo].append(achado)

    coverage = data_coverage or {}
    for sistema, ramo in ramos.items():
        tipos = {a.get("tipo_indicador") for a in ramo["correlacoes"] + ramo["anomalias"]}
        subfuncoes = {str(a.get("subfuncao")) for a in ramo["correlacoes"] + ramo["anomalias"]}
        ramo["contexto_orcamentario"] = {
            k: v for k, v in (contexto_orcamentario or {}).items() if str(k) in subfuncoes
        }
        ramo["data_coverage"] = {
            "summary": coverage.get("summary", {}),
            "gaps": [
                g for g in coverage.get("gaps", [])
                if g.get("tipo_indicador") in tipos
            ],
        }
    return ramos


def series_do_ramo(
    sistema: str,
    ramo: dict[str, Any],
    despesas: list[dict[str, Any]],
    indicadores: list[dict[str, Any]],
) -> list[str]:
    """Séries anuais de um ramo: os indicadores do sistema e o gasto das
    subfunções que aparecem nos achados dele (`linhas_series`)."""
    from core.contextos_avaliacao import linhas_series

    subfuncoes = {a.get("subfuncao") for a in ramo["correlacoes"] + ramo["anomalias"]}
    return linhas_series(
        [d for d in despesas or [] if d.get("subfuncao") in subfuncoes],
        [i for i in indicadores or [] if sistema_de(i.get("tipo")) == sistema],
    )
