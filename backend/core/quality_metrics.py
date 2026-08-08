"""
Métricas de qualidade e eficiência para comparação de topologias multiagente.

Módulo centralizado que calcula métricas complementares às já existentes
(tempo, CPU, memória, mensagens), cobrindo três eixos:

E. Eficiência dos Agentes:
   - E1: Overhead de coordenação (tempo supervisores / tempo total)
   - E2: Latency breakdown por fase (domínio / analítico / síntese)

Q. Qualidade da Resposta:
   - Q1: Deterministic consistency (outputs numéricos idênticos entre topologias)
   - Q2: Faithfulness (texto reflete dados numéricos)
   - Q3: Completeness (todos os achados relevantes mencionados no texto)
   - Q4: Structural quality (texto contém seções esperadas)

R. Resiliência:
   - R1: Partial result coverage (agentes que completaram com sucesso)

Requisitos: 11.1, 11.2, 11.3, 11.4
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Nomes das subfunções para verificação textual
SUBFUNCAO_NOMES: dict[int, str] = {
    122: "Administração Geral",
    301: "Atenção Básica",
    302: "Assistência Hospitalar e Ambulatorial",
    303: "Suporte Profilático e Terapêutico",
    304: "Vigilância Sanitária",
    305: "Vigilância Epidemiológica",
    306: "Alimentação e Nutrição",
}

# Tipos de agente por fase (para latency breakdown). Os 4 agentes de
# saúde legados (Fase 1) foram decompostos e aposentados na Fase 2 — os
# 7 agentes de saúde atuais (1 por Sistema de Informação) substituem a
# lista antiga — ver PLANO_NOVO_MODELO_DADOS.md §5.
FASE_DOMINIO = {
    "sinan",
    "sih",
    "sim",
    "sipni",
    "covid",
    "sinasc",
    "sia",
    "cnes",
    "orcamento_subfuncao",
}
FASE_ANALITICO = {"analitico", "priorizacao", "sintetizador", "verificacao"}
FASE_CONTEXTO = {"contexto_orcamentario"}
FASE_SUPERVISORES = {
    "supervisor_orcamento",
    "supervisor_saude",
    "supervisor_analitico",
    "supervisor_contexto",
    "orquestrador_estrela",
    "coordenador_geral",
}


# =========================================================================
# A. Eficiência dos Agentes
# =========================================================================


def compute_coordination_overhead(agent_metrics: list[dict]) -> dict[str, Any]:
    """E1 — Calcula o overhead de coordenação da topologia.

    Para a arquitetura hierárquica, mede quanto tempo é gasto em
    supervisores vs. agentes de trabalho. Para a estrela, o overhead
    é zero (orquestrador não aparece nas métricas de agentes).

    Args:
        agent_metrics: Lista de dicts com agentName e executionTimeMs.

    Returns:
        Dict com supervisor_time_ms, worker_time_ms, total_time_ms,
        overhead_ratio (0.0 a 1.0) e overhead_percent.
    """
    supervisor_time = 0.0
    worker_time = 0.0

    for m in agent_metrics:
        name = m.get("agentName", "")
        time_ms = m.get("executionTimeMs", 0)
        if name in FASE_SUPERVISORES:
            supervisor_time += time_ms
        else:
            worker_time += time_ms

    total = supervisor_time + worker_time
    ratio = supervisor_time / total if total > 0 else 0.0

    return {
        "supervisor_time_ms": round(supervisor_time, 2),
        "worker_time_ms": round(worker_time, 2),
        "total_time_ms": round(total, 2),
        "overhead_ratio": round(ratio, 4),
        "overhead_percent": round(ratio * 100, 2),
    }


def compute_latency_breakdown(agent_metrics: list[dict]) -> dict[str, Any]:
    """E2 — Calcula o breakdown de latência por fase do pipeline.

    Divide o tempo total em 4 fases: domínio, analítico, contexto
    e supervisores. Retorna tempo absoluto e percentual de cada fase.

    Args:
        agent_metrics: Lista de dicts com agentName e executionTimeMs.

    Returns:
        Dict com tempo e percentual por fase.
    """
    phases: dict[str, float] = {
        "dominio": 0.0,
        "analitico": 0.0,
        "contexto": 0.0,
        "supervisores": 0.0,
    }

    for m in agent_metrics:
        name = m.get("agentName", "")
        time_ms = m.get("executionTimeMs", 0)
        if name in FASE_DOMINIO:
            phases["dominio"] += time_ms
        elif name in FASE_ANALITICO:
            phases["analitico"] += time_ms
        elif name in FASE_CONTEXTO:
            phases["contexto"] += time_ms
        elif name in FASE_SUPERVISORES:
            phases["supervisores"] += time_ms

    total = sum(phases.values())

    breakdown: dict[str, Any] = {}
    for phase, time_ms in phases.items():
        pct = (time_ms / total * 100) if total > 0 else 0.0
        breakdown[phase] = {
            "time_ms": round(time_ms, 2),
            "percent": round(pct, 2),
        }
    breakdown["total_ms"] = round(total, 2)

    return breakdown


# =========================================================================
# B. Qualidade da Resposta
# =========================================================================


def compute_deterministic_consistency(
    star_result: dict[str, Any],
    hier_result: dict[str, Any],
) -> dict[str, Any]:
    """Q1 — Verifica se ambas as topologias produzem resultados numéricos idênticos.

    Como ambas usam os mesmos agentes analíticos com os mesmos dados,
    correlações e anomalias devem ser idênticas. Divergências indicam
    bugs ou não-determinismo.

    Args:
        star_result: Resultado completo da topologia estrela.
        hier_result: Resultado completo da topologia hierárquica.

    Returns:
        Dict com flags de consistência e detalhes de divergências.
    """
    star_corr = star_result.get("correlacoes", [])
    hier_corr = hier_result.get("correlacoes", [])
    star_anom = star_result.get("anomalias", [])
    hier_anom = hier_result.get("anomalias", [])

    # Normalizar para comparação (ordenar por chave natural)
    def _sort_corr(corrs: list[dict]) -> list[tuple]:
        return sorted(
            (c.get("subfuncao", 0), c.get("tipo_indicador", ""),
             c.get("spearman", 0))
            for c in corrs
        )

    def _sort_anom(anoms: list[dict]) -> list[tuple]:
        return sorted(
            (a.get("subfuncao", 0), a.get("tipo_indicador", ""),
             a.get("ano", 0), a.get("tipo_anomalia", ""))
            for a in anoms
        )

    corr_identical = _sort_corr(star_corr) == _sort_corr(hier_corr)
    anom_identical = _sort_anom(star_anom) == _sort_anom(hier_anom)

    # Detalhar divergências se houver
    divergences: list[str] = []
    if not corr_identical:
        divergences.append(
            f"Correlações divergem: star={len(star_corr)}, hier={len(hier_corr)}"
        )
    if not anom_identical:
        divergences.append(
            f"Anomalias divergem: star={len(star_anom)}, hier={len(hier_anom)}"
        )

    all_identical = corr_identical and anom_identical

    return {
        "all_identical": all_identical,
        "correlacoes_identical": corr_identical,
        "anomalias_identical": anom_identical,
        "star_correlacoes_count": len(star_corr),
        "hier_correlacoes_count": len(hier_corr),
        "star_anomalias_count": len(star_anom),
        "hier_anomalias_count": len(hier_anom),
        "divergences": divergences,
    }


def compute_faithfulness(
    correlacoes: list[dict],
    anomalias: list[dict],
    texto: str,
    contexto_orcamentario: dict | None = None,
    use_llm: bool = False,
    caller: str = "faithfulness_claims",
) -> dict[str, Any]:
    """Q2 — Verifica se o texto do sintetizador reflete os dados numéricos.

    Etapa 6 do PLANO_REFATORACAO.md (D8): reformulado para suportar dois
    métodos de cálculo, escolhidos por `use_llm`, mantendo sempre a mesma
    assinatura de retorno (`score`, `details`, entre outras chaves) para
    não quebrar o frontend:

    - `use_llm=False` (padrão — **comportamento inalterado**): checklist
      determinístico por substring, gratuito e síncrono, chamado sempre
      no caminho rápido de `/ws/{analysisId}` (antes do relatório).
    - `use_llm=True`: claim-based, estilo RAGAS — usa
      `core.claim_verifier.extract_claims`/`verify_claims` para extrair
      afirmações do texto e verificar cada uma contra os dados brutos,
      `score = claims_suportados / total_claims`. Envolve 2 chamadas LLM;
      só deve ser acionado no caminho opcional (mesmo gate de
      `use_llm_judge`), nunca no cálculo padrão — ver `compute_all_quality_metrics`.

    Args:
        correlacoes: Lista de correlações calculadas.
        anomalias: Lista de anomalias detectadas.
        texto: Texto gerado pelo sintetizador.
        contexto_orcamentario: Usado apenas no modo claim-based (dado
            adicional para `verify_claims`); ignorado no modo substring.
        use_llm: Se True, usa o método claim-based via `claim_verifier`.
        caller: Identificador para logging (ver core.llm_client),
            repassado ao `claim_verifier` no modo claim-based.

    Returns:
        Dict com score (0.0 a 1.0), detalhes de hits/misses.
    """
    if use_llm:
        return _compute_faithfulness_claims(
            correlacoes, anomalias, contexto_orcamentario or {}, texto, caller
        )

    if not texto:
        return {
            "score": 0.0,
            "total_checkpoints": 0,
            "hits": 0,
            "misses": 0,
            "details": [],
        }

    texto_lower = texto.lower()
    hits = 0
    total = 0
    details: list[dict[str, Any]] = []

    # Verificar correlações fortes (classificação "alta")
    for c in correlacoes:
        if c.get("classificacao") != "alta":
            continue
        total += 1
        subfuncao = c.get("subfuncao", 0)
        tipo = c.get("tipo_indicador", "")
        subfuncao_nome = SUBFUNCAO_NOMES.get(subfuncao, str(subfuncao))

        # Verificar se subfunção OU nome OU tipo indicador aparece no texto
        found = (
            str(subfuncao) in texto
            or subfuncao_nome.lower() in texto_lower
            or tipo.lower() in texto_lower
        )
        if found:
            hits += 1
        details.append({
            "type": "correlacao_alta",
            "subfuncao": subfuncao,
            "tipo_indicador": tipo,
            "found": found,
        })

    # Verificar todas as anomalias
    for a in anomalias:
        total += 1
        subfuncao = a.get("subfuncao", 0)
        tipo = a.get("tipo_indicador", "")
        ano = a.get("ano", 0)
        tipo_anomalia = a.get("tipo_anomalia", "")
        subfuncao_nome = SUBFUNCAO_NOMES.get(subfuncao, str(subfuncao))

        # Verificar se ano E (subfunção OU nome OU tipo) aparecem no texto
        ano_found = str(ano) in texto
        entity_found = (
            str(subfuncao) in texto
            or subfuncao_nome.lower() in texto_lower
            or tipo.lower() in texto_lower
        )
        found = ano_found and entity_found
        if found:
            hits += 1
        details.append({
            "type": "anomalia",
            "subfuncao": subfuncao,
            "tipo_indicador": tipo,
            "ano": ano,
            "tipo_anomalia": tipo_anomalia,
            "found": found,
        })

    score = hits / total if total > 0 else 1.0

    return {
        "score": round(score, 4),
        "total_checkpoints": total,
        "hits": hits,
        "misses": total - hits,
        "details": details,
    }


def _compute_faithfulness_claims(
    correlacoes: list[dict],
    anomalias: list[dict],
    contexto_orcamentario: dict,
    texto: str,
    caller: str,
) -> dict[str, Any]:
    """Q2 (claim-based, estilo RAGAS) — Etapa 6, D8.

    Reaproveita `core.claim_verifier` (Etapa 4) sem a passada de revisão
    (essa métrica só mede, não corrige o texto). Nunca lança exceção —
    falha do LLM em qualquer passo resulta em `total_checkpoints=0`,
    `score=1.0` (mesma convenção de "nada a penalizar" já usada no modo
    substring quando não há correlações/anomalias fortes).
    """
    from core.claim_verifier import extract_claims, verify_claims

    if not texto:
        return {
            "score": 0.0,
            "method": "claim_based",
            "total_checkpoints": 0,
            "hits": 0,
            "misses": 0,
            "details": [],
        }

    try:
        claims = extract_claims(texto, caller=caller)
    except Exception as exc:
        logger.warning("Faithfulness claim-based: extração de claims falhou — %s", exc)
        claims = []

    if not claims:
        return {
            "score": 1.0,
            "method": "claim_based",
            "total_checkpoints": 0,
            "hits": 0,
            "misses": 0,
            "details": [],
        }

    dados = {
        "correlacoes": correlacoes,
        "anomalias": anomalias,
        "contexto_orcamentario": contexto_orcamentario,
    }
    try:
        verificacoes = verify_claims(claims, dados, caller=caller)
    except Exception as exc:
        logger.warning("Faithfulness claim-based: verificação de claims falhou — %s", exc)
        verificacoes = []

    total = len(verificacoes)
    hits = sum(1 for v in verificacoes if v.get("suportado"))
    score = hits / total if total > 0 else 1.0

    return {
        "score": round(score, 4),
        "method": "claim_based",
        "total_checkpoints": total,
        "hits": hits,
        "misses": total - hits,
        "details": verificacoes,
    }


def compute_faithfulness_llm(
    correlacoes: list[dict],
    anomalias: list[dict],
    contexto_orcamentario: dict,
    texto: str,
    caller: str = "llm_judge",
) -> dict[str, Any]:
    """Q2 (LLM-as-judge) — Avalia faithfulness via chamada ao LLM.

    Envia os dados numéricos e o texto gerado para o LLM e pede
    uma avaliação de fidelidade estruturada numa escala de 1 a 5.

    Args:
        correlacoes: Lista de correlações calculadas.
        anomalias: Lista de anomalias detectadas.
        contexto_orcamentario: Dict com tendências orçamentárias.
        texto: Texto gerado pelo sintetizador.
        caller: Identificador para logging (ver core.llm_client) — por
            padrão só "llm_judge"; `compute_all_quality_metrics` passa
            algo como "llm_judge-star"/"llm_judge-hierarchical".

    Returns:
        Dict com score (1-5), justificativa e método usado.
    """
    import core.llm_client as llm_client

    # Formatar dados de forma legível para o LLM
    corr_summary = []
    for c in correlacoes:
        sf = c.get("subfuncao", "?")
        tipo = c.get("tipo_indicador", "?")
        sp = c.get("spearman", 0)
        cls = c.get("classificacao", "?")
        corr_summary.append(f"  - Subfunção {sf} × {tipo}: Spearman={sp}, classificação={cls}")

    anom_summary = []
    for a in anomalias:
        sf = a.get("subfuncao", "?")
        tipo = a.get("tipo_indicador", "?")
        ano = a.get("ano", "?")
        tipo_a = a.get("tipo_anomalia", "?")
        anom_summary.append(f"  - {ano}: subfunção {sf} × {tipo} → {tipo_a}")

    ctx_summary = []
    for sf_key, dados in contexto_orcamentario.items():
        if isinstance(dados, dict):
            tend = dados.get("tendencia", "?")
            var = dados.get("variacao_media_percentual", 0)
            ctx_summary.append(f"  - Subfunção {sf_key}: tendência={tend}, variação média={var}%")

    prompt = (
        "Você é um avaliador especializado em qualidade de textos analíticos sobre "
        "gastos públicos em saúde. Sua tarefa é avaliar se o TEXTO GERADO abaixo é "
        "fiel aos DADOS NUMÉRICOS fornecidos.\n\n"
        "DADOS NUMÉRICOS:\n\n"
        f"Correlações ({len(correlacoes)} pares):\n"
        + ("\n".join(corr_summary) if corr_summary else "  Nenhuma correlação calculada") + "\n\n"
        f"Anomalias ({len(anomalias)} detectadas):\n"
        + ("\n".join(anom_summary) if anom_summary else "  Nenhuma anomalia detectada") + "\n\n"
        f"Contexto Orçamentário ({len(contexto_orcamentario)} subfunções):\n"
        + ("\n".join(ctx_summary) if ctx_summary else "  Sem contexto orçamentário") + "\n\n"
        "TEXTO GERADO:\n"
        f"{texto}\n\n"
        "INSTRUÇÕES DE AVALIAÇÃO:\n"
        "Avalie o texto nos seguintes critérios:\n"
        "1. ACURÁCIA: O texto apresenta os dados corretamente, sem inventar valores?\n"
        "2. COBERTURA: O texto menciona as correlações fortes, anomalias e tendências?\n"
        "3. COERÊNCIA: As conclusões do texto são consistentes com os dados?\n\n"
        "Responda APENAS com um JSON válido no formato abaixo (sem texto antes ou depois):\n"
        "{\n"
        '  "score": <1-5>,\n'
        '  "justificativa": "<avaliação em 2-3 frases cobrindo acurácia, cobertura e coerência>"\n'
        "}\n\n"
        "Escala:\n"
        "1 = Texto contradiz ou inventa dados\n"
        "2 = Texto omite a maioria dos achados relevantes\n"
        "3 = Texto parcialmente fiel, com omissões significativas\n"
        "4 = Texto majoritariamente fiel, com omissões menores\n"
        "5 = Texto completamente fiel e coerente com os dados"
    )

    try:
        response = llm_client.generate(prompt, caller=caller)
        if response:
            import json
            json_match = re.search(r'\{[^}]+\}', response)
            if json_match:
                parsed = json.loads(json_match.group())
                return {
                    "method": "llm_as_judge",
                    "score": parsed.get("score", 0),
                    "justificativa": parsed.get("justificativa", ""),
                    "raw_response": response,
                }
    except Exception as exc:
        logger.warning("Faithfulness LLM evaluation failed: %s", exc)

    return {
        "method": "llm_as_judge",
        "score": 0,
        "justificativa": "LLM indisponível para avaliação",
        "raw_response": None,
    }


def compute_completeness(
    correlacoes: list[dict],
    anomalias: list[dict],
    contexto_orcamentario: dict,
    texto: str,
) -> dict[str, Any]:
    """Q3 — Verifica se todos os achados relevantes aparecem no texto.

    Diferente de faithfulness (que verifica se o que está no texto é
    verdadeiro), completeness verifica se TUDO que deveria estar no
    texto está lá.

    Args:
        correlacoes: Lista de correlações calculadas.
        anomalias: Lista de anomalias detectadas.
        contexto_orcamentario: Dict com tendências orçamentárias.
        texto: Texto gerado pelo sintetizador.

    Returns:
        Dict com score (0.0 a 1.0) e detalhes por categoria.
    """
    if not texto:
        return {
            "score": 0.0,
            "correlacoes_coverage": 0.0,
            "anomalias_coverage": 0.0,
            "contexto_coverage": 0.0,
            "details": {},
        }

    texto_lower = texto.lower()

    # 1. Cobertura de correlações (todas, não só as fortes)
    corr_total = len(correlacoes)
    corr_found = 0
    for c in correlacoes:
        subfuncao = c.get("subfuncao", 0)
        tipo = c.get("tipo_indicador", "")
        subfuncao_nome = SUBFUNCAO_NOMES.get(subfuncao, str(subfuncao))
        if (
            str(subfuncao) in texto
            or subfuncao_nome.lower() in texto_lower
            or tipo.lower() in texto_lower
        ):
            corr_found += 1

    # 2. Cobertura de anomalias
    anom_total = len(anomalias)
    anom_found = 0
    for a in anomalias:
        tipo_anomalia = a.get("tipo_anomalia", "")
        # Verificar menção do tipo de anomalia no texto
        if tipo_anomalia == "alto_gasto_baixo_resultado":
            keywords = ["alto gasto", "gasto acima", "ineficiência", "ineficiente", "resultado insatisfatório"]
        else:
            keywords = ["baixo gasto", "gasto abaixo", "eficiência", "eficiente", "resultado positivo"]
        if any(kw in texto_lower for kw in keywords):
            anom_found += 1

    # 3. Cobertura de contexto orçamentário
    ctx_total = len(contexto_orcamentario)
    ctx_found = 0
    for subfuncao_key in contexto_orcamentario:
        subfuncao_nome = SUBFUNCAO_NOMES.get(
            int(subfuncao_key) if str(subfuncao_key).isdigit() else 0,
            str(subfuncao_key),
        )
        if (
            str(subfuncao_key) in texto
            or subfuncao_nome.lower() in texto_lower
        ):
            ctx_found += 1

    corr_cov = corr_found / corr_total if corr_total > 0 else 1.0
    anom_cov = anom_found / anom_total if anom_total > 0 else 1.0
    ctx_cov = ctx_found / ctx_total if ctx_total > 0 else 1.0

    # Score ponderado: correlações (40%), anomalias (40%), contexto (20%)
    score = corr_cov * 0.4 + anom_cov * 0.4 + ctx_cov * 0.2

    return {
        "score": round(score, 4),
        "correlacoes_coverage": round(corr_cov, 4),
        "anomalias_coverage": round(anom_cov, 4),
        "contexto_coverage": round(ctx_cov, 4),
        "details": {
            "correlacoes": {"found": corr_found, "total": corr_total},
            "anomalias": {"found": anom_found, "total": anom_total},
            "contexto": {"found": ctx_found, "total": ctx_total},
        },
    }



# =========================================================================
# C. Resiliência
# =========================================================================


def compute_partial_result_coverage(result: dict[str, Any]) -> dict[str, Any]:
    """R1 — Calcula a cobertura de resultados parciais.

    Verifica quantos componentes do resultado estão presentes e
    não-vazios, indicando quantos agentes completaram com sucesso.

    Args:
        result: Resultado completo de uma topologia.

    Returns:
        Dict com score (0.0 a 1.0) e status de cada componente.
    """
    components = {
        "despesas": bool(result.get("despesas")),
        "indicadores": bool(result.get("indicadores")),
        "dados_cruzados": bool(result.get("dados_cruzados")),
        "correlacoes": bool(result.get("correlacoes")),
        "anomalias": bool(result.get("anomalias")),
        "contexto_orcamentario": bool(result.get("contexto_orcamentario")),
        "texto_analise": bool(result.get("texto_analise")),
    }

    completed = sum(1 for v in components.values() if v)
    total = len(components)
    score = completed / total if total > 0 else 0.0

    return {
        "score": round(score, 4),
        "completed": completed,
        "total": total,
        "components": components,
    }


# =========================================================================
# D. Custo e Comunicação (Etapa 6 do PLANO_REFATORACAO.md)
# =========================================================================


def compute_token_cost(token_usage: dict[str, int] | None) -> dict[str, Any]:
    """Custo de tokens de um segmento (uma topologia, a interpretação de
    intenção, ou o LLM Judge), a partir de um snapshot já capturado por
    `core.llm_client.TokenBucket`.

    Não lê `core.llm_client.get_token_usage()` (contador global cumulativo
    do processo inteiro) como o plano original sugeria — ver "Desvios" no
    topo do PLANO_REFATORACAO.md: o pré-requisito de contabilização
    por-análise/por-topologia foi resolvido com `TokenBucket`
    (ContextVar), e o snapshot já vem pronto e corretamente escopado de
    quem chamou `with TokenBucket(): ...` (ver `api/runners.py`,
    `api/chat_websocket.py`, `api/websocket.py`). Esta função só
    normaliza o formato — um `None`/dict vazio produz zeros, nunca lança
    exceção.

    Args:
        token_usage: Snapshot de `TokenBucket.snapshot()`, ou None se o
            segmento não rodou (ex.: LLM Judge quando `use_llm_judge=False`).

    Returns:
        Dict com prompt_tokens, completion_tokens, total_tokens, call_count.
    """
    usage = token_usage or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "call_count": usage.get("call_count", 0),
    }


def compute_communication_volume(
    architecture: str,
    agent_metrics: list[dict],
    despesas_count: int = 0,
    indicadores_count: int = 0,
) -> dict[str, Any]:
    """Volume de comunicação de uma topologia nesta análise.

    **Decisão de engenharia** (desvio do nome de parâmetro sugerido no
    plano original, `message_log`): o sistema não mantém um log de
    mensagens brutas (sockets/filas) — o proxy usado aqui é determinístico
    e real, nunca estimado: cada entrada em `agent_metrics` corresponde a
    exatamente 1 agente efetivamente invocado pelo hub/coordenador nesta
    análise (contabilizado como 1 chamada + 1 retorno = 2 mensagens). Na
    hierárquica, soma-se os 3 hops de comunicação lateral fixos entre
    supervisores (Etapa 5: Dominio→Analitico, Dominio→Contexto,
    Contexto→Analitico — sempre propostos pelo `CoordenadorGeral`,
    independente de falha upstream) e os 2 resumos textuais semânticos
    que os acompanham (resumo_dominio, resumo_contexto). A estrela nunca
    tem comunicação lateral (hub-and-spoke), então ambos ficam em 0.

    Args:
        architecture: "star" ou "hierarchical".
        agent_metrics: Métricas por agente desta topologia (usado só para
            contar quantos agentes foram de fato invocados).
        despesas_count: Nº de registros de despesa no resultado (proxy de
            tamanho do payload transportado).
        indicadores_count: Nº de registros de indicador, idem.

    Returns:
        Dict com contagem de invocações, hops laterais, resumos
        semânticos, total de mensagens e tamanho aproximado do payload.
    """
    n_agents = len(agent_metrics)
    is_hierarchical = architecture == "hierarchical"
    lateral_hops = 3 if is_hierarchical else 0
    lateral_summaries = 2 if is_hierarchical else 0
    message_count = n_agents * 2 + lateral_hops

    return {
        "agent_invocations": n_agents,
        "lateral_hops": lateral_hops,
        "lateral_summaries": lateral_summaries,
        "message_count": message_count,
        "payload_records": despesas_count + indicadores_count,
    }


# =========================================================================
# E. Outcome agregado (Etapa 6 do PLANO_REFATORACAO.md)
# =========================================================================

DEFAULT_TIME_BUDGET_MS = 60_000.0


def compute_analysis_success(
    result: dict[str, Any],
    wall_clock_ms: float = 0,
    time_budget_ms: float = DEFAULT_TIME_BUDGET_MS,
) -> dict[str, Any]:
    """Métrica composta (opcional) de "a análise foi bem-sucedida".

    Sucesso = R1 completo (todos os componentes de `compute_partial_result_coverage`
    presentes) **E** nenhuma afirmação não-suportada remanescente no
    self-check (Etapa 4, quando ele rodou) **E** dentro do orçamento de
    tempo. Inspirada conceitualmente em métricas de sucesso baseadas em
    marcos (ver D11) — fórmula própria, não replicação de benchmark.

    **Decisão de engenharia**: o orçamento de tempo default (60s) não é
    prescrito por nenhuma fonte consultada — mesma lógica de "sem
    limiares mágicos escondidos" já aplicada a E1/E2 (D12): em vez de uma
    constante interna, é um parâmetro explícito que o caller pode
    sobrescrever.

    Args:
        result: Resultado completo de uma topologia (star_result ou
            hier_result), incluindo `self_check` quando disponível.
        wall_clock_ms: Tempo real de execução; 0 desativa a checagem de
            orçamento (sempre `within_time_budget=True`).
        time_budget_ms: Orçamento de tempo de referência.

    Returns:
        Dict com o veredito composto e cada critério isolado.
    """
    r1 = compute_partial_result_coverage(result)
    r1_complete = r1["completed"] == r1["total"]

    self_check = result.get("self_check")
    if self_check and self_check.get("verificado"):
        claims_nao_suportadas = sum(
            1 for c in self_check.get("claims", []) if not c.get("suportado", True)
        )
        # Etapa 4 corrige em no máximo 1 passada (sem reverificação) —
        # "revisado" já significa que a correção rodou sobre as claims
        # remanescentes; sem correção, exige 0 claims não suportadas.
        self_check_ok = self_check.get("revisado", False) or claims_nao_suportadas == 0
    else:
        self_check_ok = True  # self-check não rodou (opcional) — não penaliza

    within_budget = wall_clock_ms <= time_budget_ms if wall_clock_ms > 0 else True

    success = r1_complete and self_check_ok and within_budget

    return {
        "success": success,
        "r1_complete": r1_complete,
        "self_check_ok": self_check_ok,
        "within_time_budget": within_budget,
        "wall_clock_ms": round(wall_clock_ms, 2),
        "time_budget_ms": time_budget_ms,
    }


# =========================================================================
# Função agregadora — calcula todas as métricas de uma vez
# =========================================================================


def compute_all_quality_metrics(
    star_result: dict[str, Any],
    hier_result: dict[str, Any],
    star_agent_metrics: list[dict],
    hier_agent_metrics: list[dict],
    use_llm_judge: bool = False,
    use_llm: bool = True,
    star_wall_clock_ms: float = 0,
    hier_wall_clock_ms: float = 0,
    star_token_usage: dict[str, int] | None = None,
    hier_token_usage: dict[str, int] | None = None,
    intent_token_usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Calcula todas as métricas de qualidade e eficiência.

    Função de conveniência que agrega todas as métricas em um único
    dicionário, pronto para ser enviado via WebSocket ou persistido.

    Args:
        star_result: Resultado completo da topologia estrela.
        hier_result: Resultado completo da topologia hierárquica.
        star_agent_metrics: Métricas por agente da estrela.
        hier_agent_metrics: Métricas por agente da hierárquica.
        use_llm_judge: Se True, também executa avaliação via LLM (LLM
            Judge) **e** a reformulação claim-based de Q2 (Etapa 6, D8) —
            os dois únicos pontos desta função que gastam LLM extra além
            do próprio pipeline, por isso compartilham o mesmo gate.
        use_llm: Se False, LLM Judge/Q2 claim-based são desabilitados
            independente de use_llm_judge.
        star_wall_clock_ms: Tempo real (wall-clock) da estrela em ms.
        hier_wall_clock_ms: Tempo real (wall-clock) da hierárquica em ms.
        star_token_usage: Snapshot de `TokenBucket` do pipeline estrela
            (Etapa 6) — ver `api/runners.py::run_star`.
        hier_token_usage: Idem, hierárquica.
        intent_token_usage: Snapshot de `TokenBucket` da interpretação de
            intenção (Etapa 1/6) — None quando a análise veio do
            formulário REST direto (sem chat).

    Returns:
        Dict com todas as métricas organizadas por eixo.
    """
    metrics: dict[str, Any] = {}

    # --- A. Eficiência ---
    star_breakdown = compute_latency_breakdown(star_agent_metrics)
    hier_breakdown = compute_latency_breakdown(hier_agent_metrics)

    # Usa wall-clock real se disponível (evita dupla contagem de supervisores)
    if star_wall_clock_ms > 0:
        star_breakdown["total_ms"] = round(star_wall_clock_ms, 2)
    if hier_wall_clock_ms > 0:
        hier_breakdown["total_ms"] = round(hier_wall_clock_ms, 2)

    metrics["efficiency"] = {
        "star": {
            "coordination_overhead": compute_coordination_overhead(
                star_agent_metrics
            ),
            "latency_breakdown": star_breakdown,
        },
        "hierarchical": {
            "coordination_overhead": compute_coordination_overhead(
                hier_agent_metrics
            ),
            "latency_breakdown": hier_breakdown,
        },
    }

    # --- B. Qualidade da Resposta ---
    metrics["quality"] = {
        "deterministic_consistency": compute_deterministic_consistency(
            star_result, hier_result
        ),
        "star": {
            "faithfulness": compute_faithfulness(
                star_result.get("correlacoes", []),
                star_result.get("anomalias", []),
                star_result.get("texto_analise", ""),
            ),
            "completeness": compute_completeness(
                star_result.get("correlacoes", []),
                star_result.get("anomalias", []),
                star_result.get("contexto_orcamentario", {}),
                star_result.get("texto_analise", ""),
            ),
        },
        "hierarchical": {
            "faithfulness": compute_faithfulness(
                hier_result.get("correlacoes", []),
                hier_result.get("anomalias", []),
                hier_result.get("texto_analise", ""),
            ),
            "completeness": compute_completeness(
                hier_result.get("correlacoes", []),
                hier_result.get("anomalias", []),
                hier_result.get("contexto_orcamentario", {}),
                hier_result.get("texto_analise", ""),
            ),
        },
    }

    # LLM-as-judge + Q2 claim-based (Etapa 6, D8) — ambos exigem use_llm
    # ativo (sem sentido avaliar texto de fallback determinístico) e
    # compartilham o gate use_llm_judge, já que ambos são LLM extra além
    # do próprio pipeline.
    if use_llm_judge and use_llm:
        metrics["quality"]["star"]["faithfulness_llm"] = (
            compute_faithfulness_llm(
                star_result.get("correlacoes", []),
                star_result.get("anomalias", []),
                star_result.get("contexto_orcamentario", {}),
                star_result.get("texto_analise", ""),
                caller="llm_judge-star",
            )
        )
        metrics["quality"]["hierarchical"]["faithfulness_llm"] = (
            compute_faithfulness_llm(
                hier_result.get("correlacoes", []),
                hier_result.get("anomalias", []),
                hier_result.get("contexto_orcamentario", {}),
                hier_result.get("texto_analise", ""),
                caller="llm_judge-hierarchical",
            )
        )
        metrics["quality"]["star"]["faithfulness_claims"] = compute_faithfulness(
            star_result.get("correlacoes", []),
            star_result.get("anomalias", []),
            star_result.get("texto_analise", ""),
            star_result.get("contexto_orcamentario", {}),
            use_llm=True,
            caller="faithfulness_claims-star",
        )
        metrics["quality"]["hierarchical"]["faithfulness_claims"] = compute_faithfulness(
            hier_result.get("correlacoes", []),
            hier_result.get("anomalias", []),
            hier_result.get("texto_analise", ""),
            hier_result.get("contexto_orcamentario", {}),
            use_llm=True,
            caller="faithfulness_claims-hierarchical",
        )

    # --- C. Resiliência ---
    metrics["resilience"] = {
        "star": compute_partial_result_coverage(star_result),
        "hierarchical": compute_partial_result_coverage(hier_result),
    }

    # --- D. Custo e Comunicação ---
    metrics["cost"] = {
        "star": compute_token_cost(star_token_usage),
        "hierarchical": compute_token_cost(hier_token_usage),
        "intent_interpretation": compute_token_cost(intent_token_usage),
    }
    metrics["communication"] = {
        "star": compute_communication_volume(
            "star", star_agent_metrics,
            despesas_count=len(star_result.get("despesas", [])),
            indicadores_count=len(star_result.get("indicadores", [])),
        ),
        "hierarchical": compute_communication_volume(
            "hierarchical", hier_agent_metrics,
            despesas_count=len(hier_result.get("despesas", [])),
            indicadores_count=len(hier_result.get("indicadores", [])),
        ),
    }

    # --- E. Outcome agregado ---
    metrics["outcome"] = {
        "star": compute_analysis_success(star_result, star_wall_clock_ms),
        "hierarchical": compute_analysis_success(hier_result, hier_wall_clock_ms),
    }

    logger.info(
        "Quality metrics computed: consistency=%s, star_faithfulness=%.2f, "
        "hier_faithfulness=%.2f, star_completeness=%.2f, hier_completeness=%.2f, "
        "star_tokens=%d, hier_tokens=%d",
        metrics["quality"]["deterministic_consistency"]["all_identical"],
        metrics["quality"]["star"]["faithfulness"]["score"],
        metrics["quality"]["hierarchical"]["faithfulness"]["score"],
        metrics["quality"]["star"]["completeness"]["score"],
        metrics["quality"]["hierarchical"]["completeness"]["score"],
        metrics["cost"]["star"]["total_tokens"],
        metrics["cost"]["hierarchical"]["total_tokens"],
    )

    return metrics


# =========================================================================
# Relatório comparativo textual — gerado após ambas as topologias
# =========================================================================


def generate_comparative_report(
    quality: dict[str, Any],
    star_agent_metrics: list[dict],
    hier_agent_metrics: list[dict],
    data_coverage: dict[str, Any] | None = None,
    star_wall_clock_ms: float = 0,
    hier_wall_clock_ms: float = 0,
    star_result: dict[str, Any] | None = None,
    hier_result: dict[str, Any] | None = None,
) -> str:
    """Gera relatório textual comparativo entre as duas topologias.

    Produzido após ambas as topologias completarem, consolida todas
    as métricas de eficiência, qualidade e resiliência em texto
    legível para o usuário.

    Args:
        quality: Dict retornado por compute_all_quality_metrics().
        star_agent_metrics: Métricas por agente da estrela.
        hier_agent_metrics: Métricas por agente da hierárquica.
        data_coverage: Dict com cobertura de dados e gaps detectados.
        star_wall_clock_ms: Tempo real (wall-clock) da estrela em ms.
        hier_wall_clock_ms: Tempo real (wall-clock) da hierárquica em ms.
        star_result: Resultado completo da topologia estrela (correlações, anomalias).
        hier_result: Resultado completo da topologia hierárquica.

    Returns:
        Texto formatado do relatório comparativo.
    """
    sections: list[str] = []

    # ── Cabeçalho ──
    sections.append("=" * 60)
    sections.append("  RELATÓRIO COMPARATIVO — ESTRELA vs. HIERÁRQUICA")
    sections.append("=" * 60)
    sections.append("")

    eff = quality.get("efficiency", {})
    qual = quality.get("quality", {})
    resil = quality.get("resilience", {})

    star_eff = eff.get("star", {})
    hier_eff = eff.get("hierarchical", {})
    # Use wall-clock real (tempo percebido pelo usuário) em vez da soma dos tempos individuais
    star_total = star_wall_clock_ms if star_wall_clock_ms > 0 else star_eff.get("latency_breakdown", {}).get("total_ms", 0)
    hier_total = hier_wall_clock_ms if hier_wall_clock_ms > 0 else hier_eff.get("latency_breakdown", {}).get("total_ms", 0)

    # ── 1. Eficiência ──
    sections.append("━━━ 1. Eficiência Operacional ━━━")
    sections.append("")

    sections.append(f"  Tempo total de execução:")
    sections.append(f"    Estrela:      {star_total:,.0f} ms")
    sections.append(f"    Hierárquica:  {hier_total:,.0f} ms")
    if star_total > 0 and hier_total > 0:
        diff_pct = ((hier_total - star_total) / star_total) * 100
        faster = "Estrela" if star_total < hier_total else "Hierárquica"
        sections.append(
            f"    → {faster} foi {abs(diff_pct):.1f}% mais rápida"
        )
    sections.append("")

    star_overhead = star_eff.get("coordination_overhead", {})
    hier_overhead = hier_eff.get("coordination_overhead", {})
    sections.append(f"  Overhead de coordenação:")
    sections.append(
        f"    Estrela:      {star_overhead.get('overhead_percent', 0):.1f}%"
    )
    sections.append(
        f"    Hierárquica:  {hier_overhead.get('overhead_percent', 0):.1f}%"
    )
    sections.append("")

    # ── 2. Qualidade ──
    sections.append("━━━ 2. Qualidade da Resposta ━━━")
    sections.append("")

    consistency = qual.get("deterministic_consistency", {})
    if consistency.get("all_identical"):
        sections.append(
            "  ✓ Resultados numéricos idênticos entre topologias"
        )
        sections.append(
            f"    ({consistency.get('star_correlacoes_count', 0)} correlações, "
            f"{consistency.get('star_anomalias_count', 0)} anomalias)"
        )
    else:
        sections.append("  ✗ Divergências detectadas:")
        for d in consistency.get("divergences", []):
            sections.append(f"    - {d}")
    sections.append("")

    # Detalhar correlações e anomalias de cada topologia
    _src = star_result if star_result else {}
    star_corrs = _src.get("correlacoes", [])
    star_anoms = _src.get("anomalias", [])

    if star_corrs:
        sections.append("  Correlações (ambas topologias — idênticas):")
        for c in star_corrs:
            sf = c.get("subfuncao", "?")
            sf_nome = SUBFUNCAO_NOMES.get(sf, str(sf))
            tipo = c.get("tipo_indicador", "?")
            sp = c.get("spearman", 0)
            cls = c.get("classificacao", "?")
            n = c.get("n_pontos", "?")
            sections.append(
                f"    • {sf_nome} (sf{sf}) × {tipo}: "
                f"ρ={sp:.4f} ({cls}, n={n})"
            )
        sections.append("")

    if star_anoms:
        # Ordena por ano (mais antigo primeiro)
        sorted_anoms = sorted(star_anoms, key=lambda a: (a.get("ano", 0), a.get("subfuncao", 0)))
        sections.append(f"  Anomalias ({len(sorted_anoms)} detectadas):")
        sections.append("")
        # Cabeçalho da tabela
        sections.append(f"    {'Ano':<6}{'Subfunção':<28}{'Indicador':<16}{'Diagnóstico'}")
        sections.append(f"    {'─'*6}{'─'*28}{'─'*16}{'─'*20}")
        for a in sorted_anoms:
            sf = a.get("subfuncao", "?")
            sf_nome = SUBFUNCAO_NOMES.get(sf, str(sf))
            tipo = a.get("tipo_indicador", "?")
            ano = str(a.get("ano", "?"))
            tipo_a = a.get("tipo_anomalia", "?")
            label = "ineficiência" if tipo_a == "alto_gasto_baixo_resultado" else "eficiência"
            sections.append(
                f"    {ano:<6}{sf_nome:<28}{tipo:<16}{label}"
            )
        sections.append("")

    for arch_name, arch_key in [("Estrela", "star"), ("Hierárquica", "hierarchical")]:
        arch_qual = qual.get(arch_key, {})
        faith_data = arch_qual.get("faithfulness", {})
        comp_data = arch_qual.get("completeness", {})
        faith = faith_data.get("score", 0)
        comp = comp_data.get("score", 0)
        sections.append(
            f"  {arch_name}: fidelidade {faith:.0%} | "
            f"completude {comp:.0%}"
        )

        # Detalhar faithfulness (hits/misses)
        faith_hits = faith_data.get("hits", 0)
        faith_total = faith_data.get("total_checkpoints", 0)
        faith_misses = faith_data.get("misses", 0)
        if faith_total > 0:
            sections.append(
                f"    Q2: {faith_hits}/{faith_total} checkpoints verificados"
            )
            if faith_misses > 0:
                details = faith_data.get("details", [])
                for d in details:
                    if not d.get("found"):
                        d_type = d.get("type", "?")
                        sf = d.get("subfuncao", "?")
                        tipo = d.get("tipo_indicador", "?")
                        if d_type == "anomalia":
                            ano = d.get("ano", "?")
                            sections.append(
                                f"      ✗ Anomalia não mencionada: {ano} sf{sf} × {tipo}"
                            )
                        else:
                            sections.append(
                                f"      ✗ Correlação alta não mencionada: sf{sf} × {tipo}"
                            )

        # Detalhar completeness
        comp_details = comp_data.get("details", {})
        corr_d = comp_details.get("correlacoes", {})
        anom_d = comp_details.get("anomalias", {})
        ctx_d = comp_details.get("contexto", {})
        if comp_details:
            sections.append(
                f"    Q3: correlações {corr_d.get('found', 0)}/{corr_d.get('total', 0)} | "
                f"anomalias {anom_d.get('found', 0)}/{anom_d.get('total', 0)} | "
                f"contexto {ctx_d.get('found', 0)}/{ctx_d.get('total', 0)}"
            )

    sections.append("")

    # ── 3. Resiliência ──
    sections.append("━━━ 3. Resiliência ━━━")
    sections.append("")

    for arch_name, arch_key in [("Estrela", "star"), ("Hierárquica", "hierarchical")]:
        arch_resil = resil.get(arch_key, {})
        sections.append(
            f"  {arch_name}: {arch_resil.get('score', 0):.0%} "
            f"({arch_resil.get('completed', 0)}/{arch_resil.get('total', 0)} componentes)"
        )
        components = arch_resil.get("components", {})
        present = [k for k, v in components.items() if v]
        missing = [k for k, v in components.items() if not v]
        if present:
            sections.append(f"    ✓ {', '.join(present)}")
        if missing:
            sections.append(f"    ✗ Ausentes: {', '.join(missing)}")
    sections.append("")

    # ── Conclusão ──
    # Prioridade: qualidade (fidelidade) > eficiência.
    # A resposta mais confiável é preferida sobre a mais rápida.
    sections.append("━━━ Conclusão ━━━")
    sections.append("")

    star_faith = qual.get("star", {}).get("faithfulness", {}).get("score", 0)
    hier_faith = qual.get("hierarchical", {}).get("faithfulness", {}).get("score", 0)

    if star_faith > hier_faith:
        sections.append("  • Qualidade: Estrela")
    elif hier_faith > star_faith:
        sections.append("  • Qualidade: Hierárquica")
    else:
        sections.append("  • Qualidade: Empate")

    if star_total < hier_total:
        sections.append("  • Eficiência: Estrela")
    elif hier_total < star_total:
        sections.append("  • Eficiência: Hierárquica")
    else:
        sections.append("  • Eficiência: Empate")

    if consistency.get("all_identical"):
        sections.append("  • Consistência: Idêntica")

    sections.append("")

    # Decisão final: qualidade tem peso maior que eficiência
    if star_faith > hier_faith:
        sections.append("  → Topologia Estrela apresentou melhor desempenho geral.")
    elif hier_faith > star_faith:
        sections.append("  → Topologia Hierárquica apresentou melhor desempenho geral.")
    elif star_total < hier_total:
        # Empate em qualidade — desempata por eficiência
        sections.append("  → Topologia Estrela apresentou melhor desempenho geral (desempate por eficiência).")
    elif hier_total < star_total:
        sections.append("  → Topologia Hierárquica apresentou melhor desempenho geral (desempate por eficiência).")
    else:
        sections.append("  → Ambas as topologias apresentaram desempenho equivalente.")

    sections.append("")
    sections.append("=" * 60)

    return "\n".join(sections)
