"""
Utilitário de cruzamento de dados — despesas × indicadores de saúde.

Cruza despesas por subfunção com indicadores de saúde por tipo e ano,
produzindo pontos de dados cruzados para os agentes analíticos.

O mapeamento subfunção→indicador (decisão tomada com o usuário na sessão
da Fase 2, com os rótulos por SI/subtipo real — não os tokens legados de
health_params usados só para gating de ativação em
agents/star/orchestrator.py e agents/hierarchical/supervisors.py, ver
nota abaixo) segue a tabela:
  122 → CNES (qualquer subtipo — nenhum SI de saúde específico se
        encaixa melhor em "Administração Geral")
  301 → cobertura_vacinal + doses_aplicadas (SI-PNI) + nascidos_vivos
        (SINASC — pré-natal/nascimento são acompanhamento de Atenção
        Básica; escolha da Fase 3.5, sem par mais específico no
        orçamento)
  302 → internacoes (SIH) + producao_ambulatorial (SIA — "Assistência
        Hospitalar e Ambulatorial" cobre as duas frentes)
  303 → os 9 subtipos do SINAN ("qualquer tratamento ou prevenção de
        doença se encaixa" em Suporte Profilático e Terapêutico)
  304 → os 9 subtipos do SINAN + só o subtipo CNES
        "estabelecimentos_vigilancia_epidemiologica" (único subtipo do CNES
        com par temático real com Vigilância Sanitária — refinamento
        decidido com o usuário; os outros 11 subtipos do CNES não cruzam
        com 304, só com 122/306)
  305 → casos + obitos (COVID) + os 9 subtipos do SINAN (Vigilância
        Epidemiológica)
  306 → CNES (Alimentação e Nutrição)
  mortalidade → transversal (cruza com 301/302/303/305 — não estendido
        a 122/304/306, decisão explícita mantida da Fase 1)

Nota (bug corrigido na Fase 3.5): os agentes de domínio por SI
(agente_sipni.py, agente_covid.py, etc.) não relabelam mais o `tipo`
retornado pelo Neo4j para os tokens legados de health_params — cada
`IndicadorSaude.tipo` é o subtipo nativo do sistema (ex.:
"cobertura_vacinal", não "vacinacao"). Este módulo cruza por esse
`tipo` real, então precisa listar os subtipos nativos, não os tokens
de gating de agents/star/orchestrator.py::INDICADOR_TO_AGENT (que
continuam usando "vacinacao"/"covid" só para decidir quais agentes
ativar a partir dos health_params do usuário — vocabulário
propositalmente diferente, não é bug).

Requisitos: 9.4, 10.5
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Os 9 subtipos do SINAN (AgenteSINAN) — reaproveitados em 3 subfunções
# (303, 304, 305), não só uma: o mesmo agravo cruza contra o orçamento de
# até 3 categorias de gasto diferentes, habilitando um espaço de
# correlação maior que 1 par fixo por indicador.
_SINAN_TIPOS: list[str] = [
    "dengue",
    "chikungunya",
    "sifilis_adquirida",
    "sifilis_gestante",
    "sifilis_congenita",
    "coqueluche",
    "hepatites_virais",
    "tuberculose",
    "hanseniase",
]

# Os 12 subtipos do CNES (AgenteCNES) — reaproveitados em 3 subfunções
# sem SI de saúde específico correspondente (122, 304, 306).
_CNES_TIPOS: list[str] = [
    "leitos",
    "profissionais",
    "estabelecimentos_por_tipo",
    "ocupacoes",
    "equipes_saude",
    "tipo_atendimento",
    "leitos_consultorios",
    "equipamentos",
    "estabelecimentos_nivel_atencao",
    "estabelecimentos_servico_classificacao",
    "estabelecimentos_habilitacao",
    "estabelecimentos_vigilancia_epidemiologica",
]

# Mapeamento subfunção → tipos de indicador — ver docstring do módulo.
SUBFUNCAO_INDICADOR_MAP: dict[int, list[str]] = {
    122: list(_CNES_TIPOS),
    301: ["cobertura_vacinal", "doses_aplicadas", "nascidos_vivos"],
    302: ["internacoes", "producao_ambulatorial"],
    303: list(_SINAN_TIPOS),
    304: ["estabelecimentos_vigilancia_epidemiologica"] + _SINAN_TIPOS,
    305: ["casos", "obitos"] + _SINAN_TIPOS,
    306: list(_CNES_TIPOS),
}

# Subfunções com as quais mortalidade cruza (transversal) — mantido
# igual à Fase 1 (301/302/303/305), não estendido às 3 subfunções novas
# sem SI de saúde específico (122/304/306).
MORTALIDADE_SUBFUNCOES: list[int] = [301, 302, 303, 305]

SUBFUNCAO_NOMES: dict[int, str] = {
    122: "Administração Geral",
    301: "Atenção Básica",
    302: "Assistência Hospitalar e Ambulatorial",
    303: "Suporte Profilático e Terapêutico",
    304: "Vigilância Sanitária",
    305: "Vigilância Epidemiológica",
    306: "Alimentação e Nutrição",
}


def _by_year_and_dimensao(rows: list[dict[str, Any]]) -> dict[tuple[int, Any], dict[str, Any]]:
    """Indexa linhas por (ano, dimensao_valor) — não só por ano (Fase 3).

    Quando uma dimensão está ativa (POR_NATUREZA/POR_APLICACAO do lado
    despesa, ou qualquer dimensão do lado saúde), múltiplas linhas
    compartilham o mesmo ano — uma por fatia. Indexar só por ano faria
    um dict sobrescrever silenciosamente todas as fatias menos a
    última. `dimensao_valor` ausente (`None`, o caso comum — nenhuma
    dimensão ativa) preserva o comportamento anterior: uma linha por
    ano, sem colisão."""
    return {(r["ano"], r.get("dimensao_valor")): r for r in rows}


def deduplicate_despesas(despesas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove despesas duplicadas por (subfuncao, ano).

    O AgenteMortalidade retorna despesas de todas as subfunções, que podem
    sobrepor com as retornadas pelos outros agentes de domínio. Esta função
    mantém apenas a primeira ocorrência de cada par (subfuncao, ano).

    Args:
        despesas: Lista de dicts com keys: subfuncao (int), ano (int), ...

    Returns:
        Lista deduplicada preservando a ordem de inserção.
    """
    seen: set[tuple[int, int]] = set()
    unique: list[dict[str, Any]] = []
    for d in despesas:
        key = (d.get("subfuncao", 0), d.get("ano", 0))
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def cross_domain_data(
    despesas: list[dict[str, Any]],
    indicadores: list[dict[str, Any]],
    date_from: int | None = None,
    date_to: int | None = None,
) -> list[dict[str, Any]]:
    """Cruza despesas com indicadores por subfunção e ano.

    Para cada subfunção no mapeamento, encontra indicadores do tipo
    correspondente no mesmo ano. Mortalidade é transversal — cruza
    com todas as subfunções (301, 302, 303, 305).

    Args:
        despesas: Lista de dicts com keys: subfuncao (int), subfuncaoNome (str),
                  ano (int), valor (float).
        indicadores: Lista de dicts com keys: tipo (str), ano (int), valor (float).
        date_from: Ano de início do período solicitado (para detecção de gaps).
        date_to: Ano de fim do período solicitado (para detecção de gaps).

    Returns:
        Lista de CrossedDataPoint dicts com keys: subfuncao, subfuncao_nome,
        tipo_indicador, ano, valor_despesa, valor_indicador, dimensao_valor
        (None quando nenhum lado tem dimensão ativa).
    """
    if not despesas or not indicadores:
        return []

    crossed: list[dict[str, Any]] = []

    # Phase 1: Standard mapping (301→vacinacao, 302→internacoes, 305→dengue/covid)
    for subfuncao, tipos in SUBFUNCAO_INDICADOR_MAP.items():
        desp_by_key = _by_year_and_dimensao(
            [d for d in despesas if d.get("subfuncao") == subfuncao]
        )

        for tipo in tipos:
            ind_by_key = _by_year_and_dimensao(
                [i for i in indicadores if i.get("tipo") == tipo]
            )

            common_keys = sorted(set(desp_by_key) & set(ind_by_key))
            for ano, dimensao_valor in common_keys:
                d = desp_by_key[(ano, dimensao_valor)]
                ind = ind_by_key[(ano, dimensao_valor)]
                crossed.append({
                    "subfuncao": subfuncao,
                    "subfuncao_nome": d.get(
                        "subfuncaoNome",
                        SUBFUNCAO_NOMES.get(subfuncao, str(subfuncao)),
                    ),
                    "tipo_indicador": tipo,
                    "ano": ano,
                    "valor_despesa": d["valor"],
                    "valor_indicador": ind["valor"],
                    "dimensao_valor": dimensao_valor,
                })

    # Phase 2: Mortalidade — transversal, crosses with ALL subfunções
    mort_by_key = _by_year_and_dimensao(
        [i for i in indicadores if i.get("tipo") == "mortalidade"]
    )

    if mort_by_key:
        for subfuncao in MORTALIDADE_SUBFUNCOES:
            desp_by_key = _by_year_and_dimensao(
                [d for d in despesas if d.get("subfuncao") == subfuncao]
            )

            common_keys = sorted(set(desp_by_key) & set(mort_by_key))
            for ano, dimensao_valor in common_keys:
                d = desp_by_key[(ano, dimensao_valor)]
                ind = mort_by_key[(ano, dimensao_valor)]
                crossed.append({
                    "subfuncao": subfuncao,
                    "subfuncao_nome": d.get(
                        "subfuncaoNome",
                        SUBFUNCAO_NOMES.get(subfuncao, str(subfuncao)),
                    ),
                    "tipo_indicador": "mortalidade",
                    "ano": ano,
                    "valor_despesa": d["valor"],
                    "valor_indicador": ind["valor"],
                    "dimensao_valor": dimensao_valor,
                })

    logger.info("Crossed %d data points from %d despesas and %d indicadores",
                len(crossed), len(despesas), len(indicadores))

    return crossed


def detect_data_gaps(
    despesas: list[dict[str, Any]],
    indicadores: list[dict[str, Any]],
    date_from: int,
    date_to: int,
    health_params: list[str] | None = None,
) -> dict[str, Any]:
    """Detecta lacunas nos dados disponíveis para o período solicitado.

    Verifica, para cada ano no intervalo [date_from, date_to], quais
    subfunções de despesa e quais tipos de indicador estão presentes
    ou ausentes. Quando health_params é fornecido, verifica apenas
    as subfunções e indicadores relevantes à seleção do usuário.

    Args:
        despesas: Lista de despesas retornadas pelos agentes de domínio.
        indicadores: Lista de indicadores retornados pelos agentes de domínio.
        date_from: Ano de início do período solicitado.
        date_to: Ano de fim do período solicitado.
        health_params: Lista de tipos de indicador selecionados pelo usuário.
            Se None, verifica todos (comportamento legado).

    Returns:
        Dict com:
        - expected_years: lista de anos no intervalo
        - despesas_coverage: cobertura por subfunção e ano
        - indicadores_coverage: cobertura por tipo e ano
        - gaps: lista de lacunas detectadas (descrição textual)
        - summary: resumo com contagens
    """
    expected_years = list(range(date_from, date_to + 1))

    # Determinar quais subfunções e indicadores são relevantes
    if health_params:
        # Apenas os tipos selecionados pelo usuário
        relevant_tipos: set[str] = set(health_params)
        # Subfunções correspondentes aos indicadores selecionados
        relevant_subfuncoes: set[int] = set()
        for tipo in health_params:
            for sf, tipos in SUBFUNCAO_INDICADOR_MAP.items():
                if tipo in tipos:
                    relevant_subfuncoes.add(sf)
            # Mortalidade é transversal — se selecionada, inclui todas as subfunções
            if tipo == "mortalidade":
                relevant_subfuncoes.update(MORTALIDADE_SUBFUNCOES)
        all_subfuncoes = sorted(relevant_subfuncoes)
        all_tipos = sorted(relevant_tipos)
    else:
        all_subfuncoes = sorted(set(SUBFUNCAO_INDICADOR_MAP.keys()) | set(MORTALIDADE_SUBFUNCOES))
        all_tipos_set: set[str] = set()
        for tipos in SUBFUNCAO_INDICADOR_MAP.values():
            all_tipos_set.update(tipos)
        all_tipos_set.add("mortalidade")
        all_tipos = sorted(all_tipos_set)

    # Mapear dados disponíveis — só conta como disponível quando o valor
    # não é nulo. Alguns subtipos (ex.: CNES "tipo_atendimento",
    # "leitos_consultorios") sempre retornam a linha (ano, tipo) do
    # Neo4j mas com valor=None — a planilha fonte do DATASUS não tem
    # coluna "Total" para eles porque não existe soma válida das
    # categorias (tipo_atendimento: um mesmo estabelecimento conta em
    # várias colunas ao mesmo tempo; leitos_consultorios: mistura leitos
    # e consultórios, unidades diferentes — ver saude_indicadores_loader.py
    # § CNES). Sem esse filtro, esses pares apareciam como "100%
    # completos" no relatório de cobertura quando na verdade não têm
    # nenhum valor numérico utilizável em ano nenhum.
    desp_available: dict[int, set[int]] = {}  # subfuncao → {anos}
    for d in despesas:
        if d.get("valor") is None:
            continue
        sf = d.get("subfuncao", 0)
        ano = d.get("ano", 0)
        desp_available.setdefault(sf, set()).add(ano)

    ind_available: dict[str, set[int]] = {}  # tipo → {anos}
    for i in indicadores:
        if i.get("valor") is None:
            continue
        tipo = i.get("tipo", "")
        ano = i.get("ano", 0)
        ind_available.setdefault(tipo, set()).add(ano)

    # Detectar gaps
    gaps: list[dict[str, Any]] = []

    # Despesas
    despesas_coverage: dict[int, dict[str, Any]] = {}
    for sf in all_subfuncoes:
        sf_nome = SUBFUNCAO_NOMES.get(sf, str(sf))
        available_years = desp_available.get(sf, set())
        missing_years = [y for y in expected_years if y not in available_years]
        present_years = [y for y in expected_years if y in available_years]

        despesas_coverage[sf] = {
            "subfuncao_nome": sf_nome,
            "present": present_years,
            "missing": missing_years,
            "coverage": len(present_years) / len(expected_years) if expected_years else 1.0,
        }

        if missing_years:
            gaps.append({
                "type": "despesa_missing",
                "subfuncao": sf,
                "subfuncao_nome": sf_nome,
                "missing_years": missing_years,
                "description": (
                    f"Despesa subfunção {sf} ({sf_nome}): "
                    f"sem dados para {', '.join(str(y) for y in missing_years)}"
                ),
            })

    # Indicadores
    indicadores_coverage: dict[str, dict[str, Any]] = {}
    for tipo in all_tipos:
        available_years = ind_available.get(tipo, set())
        missing_years = [y for y in expected_years if y not in available_years]
        present_years = [y for y in expected_years if y in available_years]

        indicadores_coverage[tipo] = {
            "present": present_years,
            "missing": missing_years,
            "coverage": len(present_years) / len(expected_years) if expected_years else 1.0,
        }

        if missing_years:
            gaps.append({
                "type": "indicador_missing",
                "tipo_indicador": tipo,
                "missing_years": missing_years,
                "description": (
                    f"Indicador {tipo}: "
                    f"sem dados para {', '.join(str(y) for y in missing_years)}"
                ),
            })

    # Cruzamentos impossíveis (subfunção tem dados mas indicador não, ou vice-versa)
    # Verifica apenas pares relevantes aos health_params selecionados
    for sf, tipos in SUBFUNCAO_INDICADOR_MAP.items():
        if sf not in all_subfuncoes:
            continue
        sf_nome = SUBFUNCAO_NOMES.get(sf, str(sf))
        sf_years = desp_available.get(sf, set())
        for tipo in tipos:
            if tipo not in all_tipos:
                continue
            tipo_years = ind_available.get(tipo, set())
            # Anos onde só um lado tem dados
            only_despesa = sorted(sf_years - tipo_years)
            only_indicador = sorted(tipo_years - sf_years)
            if only_despesa:
                gaps.append({
                    "type": "cross_mismatch",
                    "subfuncao": sf,
                    "tipo_indicador": tipo,
                    "description": (
                        f"Cruzamento {sf_nome} × {tipo}: "
                        f"despesa sem indicador em {', '.join(str(y) for y in only_despesa)}"
                    ),
                })
            if only_indicador:
                gaps.append({
                    "type": "cross_mismatch",
                    "subfuncao": sf,
                    "tipo_indicador": tipo,
                    "description": (
                        f"Cruzamento {sf_nome} × {tipo}: "
                        f"indicador sem despesa em {', '.join(str(y) for y in only_indicador)}"
                    ),
                })

    # Resumo
    total_desp_cells = len(all_subfuncoes) * len(expected_years)
    total_ind_cells = len(all_tipos) * len(expected_years)
    present_desp = sum(
        len(desp_available.get(sf, set()) & set(expected_years))
        for sf in all_subfuncoes
    )
    present_ind = sum(
        len(ind_available.get(t, set()) & set(expected_years))
        for t in all_tipos
    )

    summary = {
        "period": f"{date_from}-{date_to}",
        "expected_years": expected_years,
        "despesas_completeness": round(present_desp / total_desp_cells, 4) if total_desp_cells else 1.0,
        "indicadores_completeness": round(present_ind / total_ind_cells, 4) if total_ind_cells else 1.0,
        "total_gaps": len(gaps),
        "has_gaps": len(gaps) > 0,
    }

    logger.info(
        "Data gap detection: %d gaps found, despesas %.0f%% complete, indicadores %.0f%% complete",
        len(gaps),
        summary["despesas_completeness"] * 100,
        summary["indicadores_completeness"] * 100,
    )

    return {
        "expected_years": expected_years,
        "despesas_coverage": despesas_coverage,
        "indicadores_coverage": indicadores_coverage,
        "gaps": gaps,
        "summary": summary,
    }
