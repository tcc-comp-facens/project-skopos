"""
Planejamento de consulta via LLM para agentes de domínio.

Preparação arquitetural (Etapa 2 do PLANO_REFATORACAO.md) para quando a
base de dados crescer com novas fontes/indicadores — a base atual tem
mapeamento 1:1 fixo (subfunção -> indicador) e NÃO exige LLM para isso
hoje. Por isso, por padrão (`USE_LLM_QUERY_PLANNING` ausente ou "false"),
todo agente de domínio usa o fast-path determinístico (leitura direta de
`semantic_memory`) — custo e comportamento idênticos ao sistema
pré-Etapa-2. A flag existe para ser ligada quando o mapeamento deixar de
ser trivial (indicadores/subfunções novos, nomenclatura ambígua).

Decisão de custo: mesmo com a flag ligada, o caminho LLM só é de fato
disparado quando o mapeamento estático do agente não é mais trivial (ver
`is_trivial` em cada agente de domínio) — nunca para reproduzir uma
decisão hoje óbvia. Um cache por (agente, intent_summary, health_params)
evita reprocessar o mesmo plano em análises repetidas.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)


def _sem_acentos(texto: str) -> str:
    """Remove acentos para comparação de substring tolerante (ex.: 'faixa
    etária' bate com 'faixa etaria') — usado pelo score determinístico de
    dimensão, não em nenhum caminho que toca o banco."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )

# Cache de planos de consulta já resolvidos via LLM — process-wide,
# limpo apenas via reset_cache() (usado nos testes).
_cache: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}

# Contadores de origem do plano (Etapa 6 do PLANO_REFATORACAO.md) —
# process-wide, alimenta compute_cache_hit_rate(). Não é por análise (o
# volume é baixo demais por análise — no máximo 4 chamadas — para uma
# taxa fazer sentido isolada; o valor é acompanhar a tendência ao longo
# da vida do processo, à medida que a base cresce e a flag é ligada).
_stats: dict[str, int] = {"fast_path": 0, "cache": 0, "llm": 0, "llm_failed_fallback": 0}


def llm_query_planning_enabled() -> bool:
    """Lê a flag a cada chamada (não cacheada em import) para permitir
    ligar/desligar em runtime e facilitar testes com monkeypatch de env."""
    return os.environ.get("USE_LLM_QUERY_PLANNING", "").strip().lower() in {"1", "true", "yes"}


def reset_cache() -> None:
    """Limpa o cache de planos de consulta. Usado nos testes."""
    _cache.clear()


def reset_stats() -> None:
    """Zera os contadores de origem do plano. Usado nos testes."""
    for key in _stats:
        _stats[key] = 0


def compute_cache_hit_rate() -> dict[str, Any]:
    """Etapa 6 — proporção de planos de consulta resolvidos sem chamada LLM
    (fast-path ou cache) vs. via LLM (sucesso ou fallback por falha).

    Evidencia diretamente o trade-off custo-atual/preparação-futura
    descrito na Etapa 2: enquanto a base tiver mapeamento trivial e/ou a
    flag estiver desligada, a taxa fica em 100% (nenhuma chamada LLM).
    """
    total = sum(_stats.values())
    llm_calls = _stats["llm"] + _stats["llm_failed_fallback"]
    non_llm_calls = total - llm_calls
    rate = non_llm_calls / total if total > 0 else 1.0
    return {
        "total": total,
        "fast_path": _stats["fast_path"],
        "cache": _stats["cache"],
        "llm": _stats["llm"],
        "llm_failed_fallback": _stats["llm_failed_fallback"],
        "cache_or_fastpath_rate": round(rate, 4),
    }


def plan_query(
    *,
    agent_id: str,
    agent_type: str,
    static_plan: dict[str, Any],
    is_trivial: bool,
    intent_summary: str | None,
    health_params: list[str] | None,
) -> tuple[dict[str, Any], str]:
    """Decide o plano de consulta (subfunções + tipos de indicador) a
    aplicar nas queries Neo4j subsequentes do agente de domínio.

    Fast-path determinístico (retorna `static_plan` direto, sem tocar o
    LLM) quando o mapeamento é trivial OU a flag está desligada — o caso
    de hoje. Só chama o LLM quando ambas as condições opostas se
    verificam. Falha do LLM (indisponível, resposta inválida) cai no
    `static_plan` — nunca propaga exceção, nunca quebra o pipeline.

    Args:
        agent_id: Identificador do agente chamador (para logs).
        agent_type: Nome do tipo de agente (ex.: "vigilancia_epidemiologica"),
            usado como parte da chave de cache.
        static_plan: Plano determinístico já resolvido a partir de
            `semantic_memory` — usado no fast-path e como fallback.
        is_trivial: True quando `semantic_memory` ainda é o mapeamento
            estático padrão do agente (não foi estendido).
        intent_summary: Resumo da intenção do usuário (Etapa 1), usado
            como parte da chave de cache e do prompt.
        health_params: Indicadores solicitados na análise, idem.

    Returns:
        Tupla (plano, origem) — origem é uma de "fast_path", "cache",
        "llm", "llm_failed_fallback".
    """
    if is_trivial or not llm_query_planning_enabled():
        _stats["fast_path"] += 1
        return static_plan, "fast_path"

    cache_key = (agent_type, intent_summary or "", tuple(sorted(health_params or [])))
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info("Agent %s: plano de consulta obtido do cache", agent_id)
        _stats["cache"] += 1
        return cached, "cache"

    try:
        import core.llm_client as llm_client

        prompt = _build_prompt(agent_type, static_plan, intent_summary, health_params)
        raw = llm_client.generate(prompt, caller=f"{agent_id}:planejar_consulta")
        plan = _parse_plan(raw)
        _cache[cache_key] = plan
        logger.info("Agent %s: plano de consulta resolvido via LLM: %s", agent_id, plan)
        _stats["llm"] += 1
        return plan, "llm"
    except Exception as exc:
        logger.warning(
            "Agent %s: planejamento de consulta via LLM falhou (%s) — usando plano estático",
            agent_id, exc,
        )
        _stats["llm_failed_fallback"] += 1
        return static_plan, "llm_failed_fallback"


def _build_prompt(
    agent_type: str,
    static_plan: dict[str, Any],
    intent_summary: str | None,
    health_params: list[str] | None,
) -> str:
    return (
        "Você ajuda a planejar uma consulta a uma base de despesas públicas "
        "de saúde e indicadores epidemiológicos de Sorocaba-SP.\n\n"
        f"Agente de domínio: {agent_type}\n"
        f"Mapeamento estático já conhecido por este agente (subfunções "
        f"orçamentárias e tipos de indicador que ele sabe buscar): "
        f"{json.dumps(static_plan, ensure_ascii=False)}\n"
        f"Intenção do usuário nesta análise: {intent_summary or '(não informada)'}\n"
        f"Indicadores de saúde solicitados na análise (todos os agentes, "
        f"não só este): {health_params or []}\n\n"
        "Se o mapeamento estático já cobre corretamente a intenção do "
        "usuário, apenas confirme-o (devolva ele mesmo). Se algum "
        "indicador solicitado não está coberto pelo mapeamento estático "
        "mas parece pertencer ao domínio deste agente, tente identificar "
        "a que código de subfunção orçamentária e a que tipo de indicador "
        "ele corresponde, com base no nome, e inclua no plano.\n\n"
        "Responda SOMENTE com um objeto JSON válido, sem texto antes ou "
        "depois, sem markdown: "
        '{"subfuncoes": [<inteiros>], "tipos_indicador": [<strings>]}'
    )


def _parse_plan(raw: str | None) -> dict[str, Any]:
    if not raw:
        raise ValueError("LLM retornou resposta vazia")

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]

    data = json.loads(cleaned)
    if not isinstance(data, dict) or "subfuncoes" not in data or "tipos_indicador" not in data:
        raise ValueError("resposta do LLM não tem o formato esperado")

    subfuncoes = data["subfuncoes"]
    tipos = data["tipos_indicador"]
    if not isinstance(subfuncoes, list) or not all(
        isinstance(s, int) and not isinstance(s, bool) for s in subfuncoes
    ):
        raise ValueError("campo 'subfuncoes' inválido")
    if not isinstance(tipos, list) or not all(isinstance(t, str) for t in tipos):
        raise ValueError("campo 'tipos_indicador' inválido")

    return {"subfuncoes": subfuncoes, "tipos_indicador": tipos}


# ---------------------------------------------------------------------------
# Deliberação real de dimensão (Fase 1 — reforço de rigor CoALA)
#
# Diferente de `plan_query` acima (single-shot, fast-path por padrão), isto
# implementa o ciclo CoALA propose_actions -> evaluate_and_select de verdade
# para a decisão "por qual dimensão quebrar esta consulta de indicadores?" —
# mesmo padrão de dois estágios de agents/analytical/priorizacao.py: score
# determinístico + arbitragem LLM opcional, com fallback ao maior score em
# caso de falha/desligamento do LLM. É chamado pelos agentes de domínio de
# saúde dentro do próprio propose_actions/evaluate_and_select (ver
# agents/domain/agente_sinan.py) — não substitui o ciclo do agente, é o
# insumo dos candidatos que ele delibera.
# ---------------------------------------------------------------------------


def propose_dimensao_candidatos(dimensoes_validas: list[str]) -> list[dict[str, Any]]:
    """Propõe um candidato por dimensão válida + o candidato "sem quebra".

    Args:
        dimensoes_validas: Dimensões válidas para o sistema do agente
            (retrieval de `semantic_memory`, ver `db.query_builder.dimensoes_validas`).

    Returns:
        Lista de candidatos `{"dimensao": str | None, "descricao": str}`.
    """
    candidatos: list[dict[str, Any]] = [
        {"dimensao": None, "descricao": "sem quebra dimensional (total por ano)"}
    ]
    for dim in dimensoes_validas:
        legivel = dim.replace("POR_", "").replace("_", " ").lower()
        candidatos.append({"dimensao": dim, "descricao": f"quebra por {legivel}"})
    return candidatos


def score_dimensao_candidato(candidate: dict[str, Any], intent_summary: str | None) -> float:
    """Score determinístico: a dimensão foi mencionada (por nome legível)
    na intenção do usuário? "Sem quebra" recebe um score base baixo mas
    não-zero — garante que sempre há um candidato vencedor mesmo quando
    nenhuma dimensão é mencionada."""
    if candidate.get("dimensao") is None:
        return 0.5
    if not intent_summary:
        return 0.0
    legivel = candidate["dimensao"].replace("POR_", "").replace("_", " ").lower()
    return 1.0 if legivel in _sem_acentos(intent_summary.lower()) else 0.0


def arbitrar_dimensao(
    *,
    agent_id: str,
    candidatos: list[dict[str, Any]],
    intent_summary: str | None,
    use_llm: bool,
) -> tuple[dict[str, Any], str]:
    """Arbitra entre candidatos de dimensão já propostos (`evaluate_and_select`
    do ciclo CoALA do agente chamador — ver `agents/domain/agente_sinan.py`).

    Recebe os candidatos já construídos (tipicamente via
    `propose_dimensao_candidatos`, chamado antes em `propose_actions`) em
    vez de reconstruí-los, para não duplicar a etapa "propose" do ciclo.
    Pontua cada um deterministicamente (`score_dimensao_candidato`). Se
    `use_llm=False`, decide direto pelo maior score. Caso contrário, um
    LLM arbitra entre os candidatos — qualquer falha (exceção, resposta
    vazia/inválida, dimensão não reconhecida) cai no maior score, nunca
    quebra o pipeline.

    Returns:
        Tupla (candidato_escolhido, origem) — origem é uma de "score",
        "llm", "llm_failed_fallback".
    """
    if len(candidatos) == 1:
        return candidatos[0], "score"

    scored = [(c, score_dimensao_candidato(c, intent_summary)) for c in candidatos]

    if not use_llm:
        melhor, _ = max(scored, key=lambda item: item[1])
        return melhor, "score"

    try:
        import core.llm_client as llm_client

        prompt = _build_dimensao_prompt(scored, intent_summary)
        raw = llm_client.generate(prompt, caller=f"{agent_id}:arbitrar_dimensao")
        escolha = _parse_dimensao_escolha(raw, candidatos)
        if escolha is not None:
            logger.info("Agent %s: dimensão escolhida via LLM: %s", agent_id, escolha["dimensao"])
            return escolha, "llm"
    except Exception as exc:
        logger.warning(
            "Agent %s: escolha de dimensão via LLM falhou (%s) — usando maior score",
            agent_id, exc,
        )

    melhor, melhor_score = max(scored, key=lambda item: item[1])
    logger.info(
        "Agent %s: dimensão escolhida via score determinístico: %s (score=%.1f)",
        agent_id, melhor["dimensao"], melhor_score,
    )
    return melhor, "llm_failed_fallback"


def _build_dimensao_prompt(
    scored: list[tuple[dict[str, Any], float]], intent_summary: str | None
) -> str:
    linhas = [
        f'- dimensao={c["dimensao"]!r}: {c["descricao"]} (score determinístico: {score:.1f})'
        for c, score in scored
    ]
    return (
        "Você ajuda a decidir se uma consulta de indicadores de saúde de "
        "Sorocaba-SP deve ser quebrada por alguma dimensão (ex.: faixa "
        "etária, sexo) ou trazer só o total por ano.\n\n"
        f"Intenção do usuário nesta análise: {intent_summary or '(não informada)'}\n\n"
        "Candidatos (com um score determinístico de apoio, baseado em "
        "menção explícita da dimensão na intenção do usuário — você não "
        "deve contestar esse score, só usá-lo como referência):\n"
        + "\n".join(linhas)
        + "\n\n"
        "Escolha o candidato mais adequado à intenção do usuário. Responda "
        "SOMENTE com um objeto JSON, sem texto antes ou depois, sem "
        'markdown: {"dimensao": <string exatamente como aparece acima, ou null>}'
    )


def _parse_dimensao_escolha(
    raw: str | None, candidatos: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    dimensao = data.get("dimensao")
    for c in candidatos:
        if c["dimensao"] == dimensao:
            return c
    return None
