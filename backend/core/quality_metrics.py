"""
Métricas de qualidade e eficiência para comparação de topologias multiagente.

Módulo centralizado que calcula métricas complementares às já existentes
(tempo, CPU, memória, mensagens), cobrindo três eixos:

E. Eficiência dos Agentes:
   - E1: Overhead de coordenação (tempo supervisores / tempo total)
   - E2: Latency breakdown por fase (domínio / analítico / síntese)

Q. Qualidade da Resposta:
   - Q1: Deterministic consistency (outputs numéricos idênticos entre topologias)
   - Q3: Completeness (todos os achados relevantes mencionados no texto)

R. Resiliência:
   - R1: Partial result coverage (agentes que completaram com sucesso)

   - Q4: Fidelidade numérica (números citados no texto que existem no
     material entregue ao redator) — complemento determinístico do RAGAS,
     que não oscila entre avaliações

A fidelidade do texto aos dados (o antigo Q2) NÃO está mais aqui: é
medida por `core/ragas_metrics.py`, que usa a biblioteca RAGAS (Es et
al., 2024). As três implementações caseiras que existiam neste módulo
— checklist por substring, claim-based "estilo RAGAS" e LLM-as-judge de
1 a 5 — foram removidas; a última era justamente o baseline "GPT Score"
que o paper do RAGAS mede como inferior à própria metodologia (0.72 vs
0.95 de concordância com anotadores humanos, Tabela 4).

Com isso tudo o que este módulo calcula é determinístico e gratuito:
nenhuma função aqui chama o LLM. A avaliação que custa LLM roda à parte,
de forma assíncrona, em `api/websocket.py`.

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
# Entradas-raiz de coordenação: o `executionTimeMs` delas já é o overhead (relógio
# determinístico menos a soma dos agentes folha), calculado igual nas duas
# topologias. Os `supervisor_*` da hierárquica NÃO entram aqui: o tempo de cada
# um engloba o dos seus subordinados e só serve para exibição.
NOS_RAIZ = {"orquestrador_estrela", "coordenador_geral"}


# =========================================================================
# A. Eficiência dos Agentes
# =========================================================================


def compute_coordination_overhead(agent_metrics: list[dict]) -> dict[str, Any]:
    """E1 — Calcula o overhead de coordenação da topologia.

    Overhead = relógio determinístico da topologia menos a soma dos agentes
    folha, já calculado pelo orquestrador/coordenador e exposto na entrada
    `orquestrador_estrela`/`coordenador_geral`. A mesma definição vale para as
    duas topologias. Os `supervisor_*` da hierárquica são ignorados porque
    seu tempo já contém o dos subordinados (somá-los dobraria a contagem).

    Args:
        agent_metrics: Lista de dicts com agentName e executionTimeMs.

    O overhead se divide em deliberação (tempo das decisões por LLM dos
    coordenadores dentro do relógio, planejamento e revisão das entregas,
    `deliberacaoMs` na entrada da raiz) e estrutura (o restante: instanciar,
    repassar e agregar).

    Returns:
        Dict com supervisor_time_ms, worker_time_ms, total_time_ms,
        overhead_ratio (0.0 a 1.0), overhead_percent, deliberation_time_ms,
        structural_time_ms e deliberation_share (fração do overhead).
    """
    supervisor_time = 0.0
    worker_time = 0.0
    deliberation_time = 0.0

    for m in agent_metrics:
        name = m.get("agentName", "")
        time_ms = m.get("executionTimeMs", 0)
        if name in NOS_RAIZ:
            supervisor_time += time_ms
            deliberation_time += min(m.get("deliberacaoMs", 0) or 0, time_ms)
        elif name.startswith("supervisor_"):
            continue
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
        "deliberation_time_ms": round(deliberation_time, 2),
        "structural_time_ms": round(supervisor_time - deliberation_time, 2),
        "deliberation_share": round(deliberation_time / supervisor_time, 4) if supervisor_time > 0 else 0.0,
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
        elif name in NOS_RAIZ:
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


def compute_completeness(
    correlacoes: list[dict],
    anomalias: list[dict],
    contexto_orcamentario: dict,
    texto: str,
) -> dict[str, Any]:
    """Q3 — Verifica se todos os achados relevantes aparecem no texto.

    Diferente da fidelidade medida pelo RAGAS (que verifica se o que está
    no texto é sustentado pelos dados), completeness verifica se TUDO que
    deveria estar no texto está lá — e faz isso sem chamar o LLM.

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

    # 2. Cobertura de anomalias — verificada anomalia a anomalia.
    #
    # A versão anterior fazia uma busca GLOBAL por palavra-chave de
    # categoria ("ineficiência", "alto gasto", ...): uma única ocorrência
    # de qualquer uma delas marcava TODAS as anomalias daquele tipo como
    # cobertas, então o score não distinguia um texto que menciona 1 de
    # 20 anomalias de um que menciona as 20. Agora cada anomalia é
    # procurada pela sua própria identidade (ano + subfunção/indicador),
    # o mesmo critério que o resto do módulo usa.
    anom_total = len(anomalias)
    anom_found = 0
    for a in anomalias:
        subfuncao = a.get("subfuncao", 0)
        tipo = a.get("tipo_indicador", "")
        ano = a.get("ano", 0)
        subfuncao_nome = SUBFUNCAO_NOMES.get(subfuncao, str(subfuncao))
        if str(ano) in texto and (
            str(subfuncao) in texto
            or subfuncao_nome.lower() in texto_lower
            or (tipo and tipo.lower() in texto_lower)
        ):
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


# Números colados a letras ou hífen não são valores (COVID-19, CID-10, sf305);
# um sinal de menos só conta depois de espaço ou parêntese ("-0.8").
_INICIO = r"(?:(?<![\w\-/])|(?<=[\s(]-)|(?<=^-))"
_NUM_TEXTO = re.compile(
    _INICIO + r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
    r"(?:\s*(mil|milhão|milhões|milhao|milhoes|bilhão|bilhões|bilhao|bilhoes)\b)?",
    re.IGNORECASE,
)
_NUM_CONTEXTO = re.compile(_INICIO + r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_ESCALAS = {"mil": 1e3, "milhão": 1e6, "milhões": 1e6, "milhao": 1e6, "milhoes": 1e6,
            "bilhão": 1e9, "bilhões": 1e9, "bilhao": 1e9, "bilhoes": 1e9}
TOLERANCIA_RELATIVA = 0.02


def _numeros_do_texto(texto: str) -> list[tuple[str, float, float]]:
    """Números do texto em formato brasileiro: (literal, valor, meia unidade).

    `54.647` é cinquenta e quatro mil, `24,4 milhões` é 24 400 000. A meia
    unidade é a precisão que o próprio texto declarou (24,4 milhões vale
    ±50 mil), usada como tolerância de arredondamento.
    """
    numeros = []
    for m in _NUM_TEXTO.finditer(texto):
        literal, escala = m.group(1), (m.group(2) or "").lower()
        inteiro, _, decimais = literal.replace(".", "").partition(",")
        valor = float(f"{inteiro}.{decimais}" if decimais else inteiro)
        fator = _ESCALAS.get(escala, 1.0)
        meia_unidade = 0.5 * fator * (10 ** -len(decimais))
        numeros.append((m.group(0).strip(), valor * fator, meia_unidade))
    return numeros


def _numeros_do_contexto(contextos: list[str]) -> list[float]:
    """Números dos trechos de contexto, formatados pelo Python (7,629,751.43)."""
    return [float(m.group(1).replace(",", "")) for c in contextos for m in _NUM_CONTEXTO.finditer(c)]


def compute_numeric_fidelity(
    texto: str, contextos: list[str], anos: tuple[int, int] | None = None
) -> dict[str, Any]:
    """Q4 — Fidelidade numérica: números do texto que existem no material.

    Complemento determinístico da fidelidade do RAGAS. Cada número citado
    no texto (contagens, valores em reais, percentuais, coeficientes) é
    procurado nos trechos de contexto que o redator recebeu
    (`core/contextos_avaliacao.build_contexts`). Conta como verificado se
    coincide com algum número do contexto dentro do arredondamento que o
    texto declarou ou de 2% do valor. Anos (1900 a 2100) contam como
    verificados quando estão no período analisado. Inteiros de um dígito
    ficam de fora, porque no texto quase sempre são contagens de itens
    ("dois exemplos", "4 pontos").

    Returns:
        score (None quando o texto não cita números), numeros_no_texto,
        verificados e nao_verificados (os literais).
    """
    if not texto:
        return {"score": None, "numeros_no_texto": 0, "verificados": 0, "nao_verificados": []}

    referencia = [abs(v) for v in _numeros_do_contexto(contextos)]
    verificados, nao_verificados, total = 0, [], 0
    for literal, valor, meia_unidade in _numeros_do_texto(texto):
        if valor.is_integer() and valor < 10 and meia_unidade >= 0.5 and not literal[-1:].isalpha():
            continue
        total += 1
        # Só é ano o número escrito sem separador: "2.000 casos" é contagem.
        if valor.is_integer() and 1900 <= valor <= 2100 and meia_unidade == 0.5 and literal.isdigit():
            ok = anos is not None and anos[0] <= valor <= anos[1]
        else:
            ok = any(
                abs(valor - c) <= max(meia_unidade, TOLERANCIA_RELATIVA * abs(c))
                for c in referencia
            )
        if ok:
            verificados += 1
        else:
            nao_verificados.append(literal)
    return {
        "score": round(verificados / total, 4) if total else None,
        "numeros_no_texto": total,
        "verificados": verificados,
        "nao_verificados": nao_verificados,
    }


def _anos_do_resultado(result: dict[str, Any]) -> tuple[int, int] | None:
    anos = ((result.get("data_coverage") or {}).get("expected_years")) or []
    return (min(anos), max(anos)) if anos else None


def numeric_fidelity_of(result: dict[str, Any]) -> dict[str, Any]:
    """Q4 de uma topologia, montando o mesmo material que o RAGAS julga."""
    from core.contextos_avaliacao import build_contexts

    anos = _anos_do_resultado(result)
    contextos = build_contexts(result, *(anos or (None, None)))
    return compute_numeric_fidelity(result.get("texto_analise") or "", contextos, anos)


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
            segmento não rodou (ex.: a avaliação RAGAS quando o usuário
            não a solicitou).

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


# Classificação CoALA (Sumers et al., 2024): agente de linguagem é quem
# decide com o LLM. Componentes que só executam cálculo ou consulta, sem
# deliberação, são ferramentas — ações de grounding no espaço de ações de
# quem as chama. O cálculo estatístico, o contexto orçamentário e as
# consultas sem dimensão a escolher (SIA, COVID) estão nesse grupo.
FERRAMENTAS: frozenset[str] = frozenset({"analitico", "contexto_orcamentario", "sia", "covid"})


def papel_de(entrada: dict[str, Any] | str) -> str:
    """'agente' ou 'ferramenta' para uma entrada de agentMetrics (ou um nome)."""
    if isinstance(entrada, dict):
        if entrada.get("papel"):
            return entrada["papel"]
        entrada = entrada.get("agentName", "")
    return "ferramenta" if entrada in FERRAMENTAS else "agente"


def compute_communication_volume(
    architecture: str,
    agent_metrics: list[dict],
    despesas_count: int = 0,
    indicadores_count: int = 0,
    mensagens: list[dict] | None = None,
) -> dict[str, Any]:
    """Volume de comunicação de uma topologia nesta análise.

    Conta só agentes de linguagem em `agent_invocations` (ver `papel_de`);
    as ferramentas acionadas aparecem à parte, porque chamar uma ferramenta
    é uma ação do agente, não uma troca de mensagens entre agentes.

    Mensagens: quando a topologia entrega o registro real das trocas
    (`result["mensagens"]`, envelope no estilo FIPA-ACL — ver
    `agents/base.Mensagem`), a contagem vem dele, separando as mensagens de
    dados das de texto em linguagem natural. Sem o registro (execuções
    antigas, testes unitários de métrica), vale a estimativa anterior de
    uma chamada e um retorno por agente.

    Args:
        architecture: "star" ou "hierarchical".
        agent_metrics: Métricas por agente desta topologia.
        despesas_count: Nº de registros de despesa no resultado.
        indicadores_count: Nº de registros de indicador, idem.
        mensagens: Registro de mensagens da topologia, se houver.

    Returns:
        Dict com agentes e ferramentas acionados, mensagens (total, por
        natureza e em caracteres), a fonte da contagem e o volume de
        registros transportados.
    """
    agentes = [m for m in agent_metrics if papel_de(m) == "agente"]
    resultado: dict[str, Any] = {
        "agent_invocations": len(agentes),
        "ferramentas_acionadas": len(agent_metrics) - len(agentes),
        "payload_records": despesas_count + indicadores_count,
    }
    if mensagens is not None:
        resultado.update({
            "message_count": len(mensagens),
            "mensagens_dados": sum(1 for m in mensagens if m.get("natureza") == "dados"),
            "mensagens_texto": sum(1 for m in mensagens if m.get("natureza") == "texto"),
            "caracteres_texto": sum(m.get("tamanho", 0) for m in mensagens if m.get("natureza") == "texto"),
            "fonte_mensagens": "registro",
        })
    else:
        resultado.update({
            "message_count": len(agentes) * 2,
            "mensagens_dados": None,
            "mensagens_texto": None,
            "caracteres_texto": None,
            "fonte_mensagens": "estimativa",
        })
    return resultado


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
    star_wall_clock_ms: float = 0,
    hier_wall_clock_ms: float = 0,
    star_token_usage: dict[str, int] | None = None,
    hier_token_usage: dict[str, int] | None = None,
    intent_token_usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Calcula todas as métricas determinísticas de qualidade e eficiência.

    Função de conveniência que agrega todas as métricas em um único
    dicionário, pronto para ser enviado via WebSocket ou persistido.

    **Nenhuma métrica aqui chama o LLM** — esta função é sempre gratuita e
    reproduzível. A avaliação da fidelidade do texto (RAGAS) é assíncrona
    e roda separada, em `api/websocket.py`, encaixando o resultado em
    `quality.{star,hierarchical}.ragas`.

    Args:
        star_result: Resultado completo da topologia estrela.
        hier_result: Resultado completo da topologia hierárquica.
        star_agent_metrics: Métricas por agente da estrela.
        hier_agent_metrics: Métricas por agente da hierárquica.
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
            "completeness": compute_completeness(
                star_result.get("correlacoes", []),
                star_result.get("anomalias", []),
                star_result.get("contexto_orcamentario", {}),
                star_result.get("texto_analise", ""),
            ),
            "numeric_fidelity": numeric_fidelity_of(star_result),
        },
        "hierarchical": {
            "completeness": compute_completeness(
                hier_result.get("correlacoes", []),
                hier_result.get("anomalias", []),
                hier_result.get("contexto_orcamentario", {}),
                hier_result.get("texto_analise", ""),
            ),
            "numeric_fidelity": numeric_fidelity_of(hier_result),
        },
    }

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
            mensagens=star_result.get("mensagens"),
        ),
        "hierarchical": compute_communication_volume(
            "hierarchical", hier_agent_metrics,
            despesas_count=len(hier_result.get("despesas", [])),
            indicadores_count=len(hier_result.get("indicadores", [])),
            mensagens=hier_result.get("mensagens"),
        ),
    }

    # --- E. Outcome agregado ---
    metrics["outcome"] = {
        "star": compute_analysis_success(star_result, star_wall_clock_ms),
        "hierarchical": compute_analysis_success(hier_result, hier_wall_clock_ms),
    }

    logger.info(
        "Quality metrics computed: consistency=%s, "
        "star_completeness=%.2f, hier_completeness=%.2f, "
        "star_tokens=%d, hier_tokens=%d",
        metrics["quality"]["deterministic_consistency"]["all_identical"],
        metrics["quality"]["star"]["completeness"]["score"],
        metrics["quality"]["hierarchical"]["completeness"]["score"],
        metrics["cost"]["star"]["total_tokens"],
        metrics["cost"]["hierarchical"]["total_tokens"],
    )

    return metrics


# =========================================================================
# Relatório comparativo textual — gerado após ambas as topologias
# =========================================================================


# Diferença mínima de fidelidade para desempatar as topologias. O juiz é
# um LLM: reavaliar o mesmo texto produz variação, então uma diferença de
# 0,01 não distingue arquitetura, distingue ruído. Abaixo deste valor o
# veredito cai para a completude e diz que caiu. O número é escolha de
# engenharia — nenhuma fonte prescreve um limiar (ver D25 no doc).
FAITHFULNESS_TIE_THRESHOLD = 0.05


def _ragas_faithfulness(ragas: dict[str, dict[str, Any]] | None, arch: str) -> float | None:
    """Score de fidelidade do RAGAS de uma arquitetura, ou None.

    None significa "não medido" (juiz indisponível, métrica falhou), que é
    diferente de zero. Quem decide o vencedor precisa tratar os dois casos
    de forma distinta — ver `_decide_winner`.
    """
    if not ragas:
        return None
    metric = ((ragas.get(arch) or {}).get("metrics") or {}).get("faithfulness") or {}
    score = metric.get("score")
    return score if isinstance(score, (int, float)) else None


def _decide_winner(
    quality: dict[str, Any],
    ragas: dict[str, dict[str, Any]] | None,
    star_total: float,
    hier_total: float,
) -> tuple[str, str]:
    """Escolhe a topologia vencedora e o critério que decidiu.

    Ordem lexicográfica: fidelidade (RAGAS) > completude (Q3) > tempo. A
    fidelidade vem primeiro por ser a única métrica de qualidade textual
    com validação publicada (Es et al., 2024); o tempo vem por último
    porque uma resposta errada mais rápida não é melhor.

    A fidelidade só é usada quando **as duas** arquiteturas têm score.
    Tratar um `None` como zero entregaria a vitória por falha de medição
    do adversário, não por qualidade própria — por isso, se qualquer uma
    das duas não foi medida, o critério cai para completude e isso é dito
    em voz alta no relatório.

    Diferenças menores que `FAITHFULNESS_TIE_THRESHOLD` contam como
    empate: o juiz é um LLM e reproduz o mesmo texto com alguma variação,
    então decidir a topologia vencedora por 0,01 seria decidir por ruído.

    Returns:
        (vencedor, critério) — vencedor em {"star", "hierarchical", "tie"}.
    """
    star_faith = _ragas_faithfulness(ragas, "star")
    hier_faith = _ragas_faithfulness(ragas, "hierarchical")
    faithfulness_medida = star_faith is not None and hier_faith is not None

    if faithfulness_medida and abs(star_faith - hier_faith) >= FAITHFULNESS_TIE_THRESHOLD:
        return ("star" if star_faith > hier_faith else "hierarchical", "fidelidade")

    qual = quality.get("quality", {})
    star_comp = qual.get("star", {}).get("completeness", {}).get("score", 0)
    hier_comp = qual.get("hierarchical", {}).get("completeness", {}).get("score", 0)

    if not faithfulness_medida:
        criterio = "completude (fidelidade não medida)"
    elif star_faith == hier_faith:
        criterio = "completude"
    else:
        criterio = "completude (fidelidade tecnicamente empatada)"
    if star_comp != hier_comp:
        return ("star" if star_comp > hier_comp else "hierarchical", criterio)

    if star_total != hier_total:
        return (
            "star" if star_total < hier_total else "hierarchical",
            "eficiência (empate em fidelidade e completude)",
        )

    return ("tie", criterio)


def _secao_achados(rotulo: str, corrs: list[dict], anoms: list[dict]) -> list[str]:
    """Correlações e tabela de anomalias de uma topologia (ou das duas, se iguais)."""
    linhas_secao: list[str] = []
    if corrs:
        linhas_secao.append(f"  Correlações ({rotulo}):")
        for c in corrs:
            sf = c.get("subfuncao", "?")
            sf_nome = SUBFUNCAO_NOMES.get(sf, str(sf))
            tipo = c.get("tipo_indicador", "?")
            sp = c.get("spearman", 0)
            cls = c.get("classificacao", "?")
            n = c.get("n_pontos", "?")
            linhas_secao.append(
                f"    • {sf_nome} (sf{sf}) × {tipo}: "
                f"ρ={sp:.4f} ({cls}, n={n})"
            )
        linhas_secao.append("")

    if anoms:
        # Ordena por ano (mais antigo primeiro)
        sorted_anoms = sorted(anoms, key=lambda a: (a.get("ano", 0), a.get("subfuncao", 0)))
        linhas_secao.append(f"  Anomalias ({rotulo}, {len(sorted_anoms)} detectadas):")
        linhas_secao.append("")

        # Larguras derivadas do conteúdo, não fixas: nomes de subfunção
        # ("Suporte Profilático e Terapêutico", 33 chars) e indicadores
        # ("sifilis_adquirida", 17) estouravam as constantes 28/16 e as
        # colunas colidiam no relatório ("Terapêuticodengue").
        linhas = [
            (
                str(a.get("ano", "?")),
                SUBFUNCAO_NOMES.get(a.get("subfuncao", "?"), str(a.get("subfuncao", "?"))),
                str(a.get("tipo_indicador", "?")),
                "ineficiência"
                if a.get("tipo_anomalia") == "alto_gasto_baixo_resultado"
                else "eficiência",
            )
            for a in sorted_anoms
        ]
        w_ano = max(len("Ano"), *(len(l[0]) for l in linhas)) + 2
        w_sf = max(len("Subfunção"), *(len(l[1]) for l in linhas)) + 2
        w_ind = max(len("Indicador"), *(len(l[2]) for l in linhas)) + 2
        w_diag = max(len("Diagnóstico"), *(len(l[3]) for l in linhas))

        linhas_secao.append(
            f"    {'Ano':<{w_ano}}{'Subfunção':<{w_sf}}"
            f"{'Indicador':<{w_ind}}{'Diagnóstico'}"
        )
        linhas_secao.append(
            f"    {'─' * w_ano}{'─' * w_sf}{'─' * w_ind}{'─' * w_diag}"
        )
        for ano, sf_nome, tipo, label in linhas:
            linhas_secao.append(
                f"    {ano:<{w_ano}}{sf_nome:<{w_sf}}{tipo:<{w_ind}}{label}"
            )
        linhas_secao.append("")
    return linhas_secao


def generate_comparative_report(
    quality: dict[str, Any],
    star_agent_metrics: list[dict],
    hier_agent_metrics: list[dict],
    data_coverage: dict[str, Any] | None = None,
    star_wall_clock_ms: float = 0,
    hier_wall_clock_ms: float = 0,
    star_result: dict[str, Any] | None = None,
    hier_result: dict[str, Any] | None = None,
    ragas: dict[str, dict[str, Any]] | None = None,
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
    if star_total > 0 and hier_total > 0 and star_total != hier_total:
        # Redução relativa ao tempo da mais lenta: "X% menos tempo" nunca passa
        # de 100%, ao contrário do antigo "X% mais rápida" (que chegava a 225%).
        faster = "Estrela" if star_total < hier_total else "Hierárquica"
        lenta, rapida = max(star_total, hier_total), min(star_total, hier_total)
        sections.append(
            f"    → {faster} levou {(lenta - rapida) / lenta * 100:.1f}% menos tempo"
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

    # Correlações e anomalias: uma listagem só quando as topologias coincidem,
    # uma por topologia quando divergem (o planejamento pode levar cada uma a
    # buscar dados diferentes).
    if consistency.get("all_identical"):
        listagens = [("idênticas nas duas topologias", star_result or {})]
    else:
        listagens = [("Estrela", star_result or {}), ("Hierárquica", hier_result or {})]
    for rotulo, res in listagens:
        sections.extend(_secao_achados(rotulo, res.get("correlacoes", []), res.get("anomalias", [])))

    for arch_name, arch_key in [("Estrela", "star"), ("Hierárquica", "hierarchical")]:
        arch_qual = qual.get(arch_key, {})
        comp_data = arch_qual.get("completeness", {})
        comp = comp_data.get("score", 0)
        sections.append(f"  {arch_name}: completude {comp:.0%}")

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
    # Ordem lexicográfica: fidelidade (RAGAS) > completude (Q3) > tempo.
    # Ver `_decide_winner` para o porquê de cada nível e para o tratamento
    # de fidelidade não medida.
    sections.append("━━━ Conclusão ━━━")
    sections.append("")

    star_faith = _ragas_faithfulness(ragas, "star")
    hier_faith = _ragas_faithfulness(ragas, "hierarchical")

    def _fmt(score: float | None) -> str:
        return "não medida" if score is None else f"{score:.0%}"

    if star_faith is not None or hier_faith is not None:
        sections.append(
            f"  • Fidelidade (RAGAS): Estrela {_fmt(star_faith)} | "
            f"Hierárquica {_fmt(hier_faith)}"
        )

    star_comp = qual.get("star", {}).get("completeness", {}).get("score", 0)
    hier_comp = qual.get("hierarchical", {}).get("completeness", {}).get("score", 0)
    sections.append(
        f"  • Completude: Estrela {star_comp:.0%} | Hierárquica {hier_comp:.0%}"
    )

    if star_total < hier_total:
        sections.append("  • Eficiência: Estrela")
    elif hier_total < star_total:
        sections.append("  • Eficiência: Hierárquica")
    else:
        sections.append("  • Eficiência: Empate")

    if consistency.get("all_identical"):
        sections.append("  • Consistência: Idêntica")

    sections.append("")

    winner, criterio = _decide_winner(quality, ragas, star_total, hier_total)
    nomes = {"star": "Estrela", "hierarchical": "Hierárquica"}
    if winner == "tie":
        sections.append(
            "  → Ambas as topologias apresentaram desempenho equivalente "
            f"(critério: {criterio})."
        )
    else:
        sections.append(
            f"  → Topologia {nomes[winner]} apresentou melhor desempenho geral "
            f"(critério: {criterio})."
        )

    sections.append("")
    sections.append("=" * 60)

    return "\n".join(sections)
