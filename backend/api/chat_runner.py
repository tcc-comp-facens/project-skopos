"""
Orquestração fina do disparo de análise a partir do chat.

Traduz um AnalysisIntent já validado pelo AgenteInterpretacaoIntencao em uma
análise disparada (mesma lógica do formulário REST, via dispatch.py) e
produz o texto de confirmação mostrado ao usuário no chat.

Requisitos: 6.1, 6.5 (spec realtime-chat-interface)
"""

from __future__ import annotations

import logging
from typing import Any

from agents.intent import AgenteInterpretacaoIntencao, AnalysisIntent
from api.dispatch import dispatch_analysis

logger = logging.getLogger(__name__)


def run_chat_analysis(
    params: AnalysisIntent,
    use_llm: bool,
    source_question: str,
    interpreted_via: str,
    interpreter: AgenteInterpretacaoIntencao,
    use_self_check: bool = False,
    intent_token_usage: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Dispara a análise a partir de parâmetros extraídos do chat.

    Args:
        params: Parâmetros já validados pelo AgenteInterpretacaoIntencao,
            incluindo `intent_summary` — repassado a `dispatch_analysis`
            para alimentar a camada de entrada compartilhada pelas duas
            arquiteturas (Etapa 1 do plano de refatoração).
        use_llm: Se True, usa LLM para síntese textual das arquiteturas.
        source_question: Texto original digitado pelo usuário (auditoria).
        interpreted_via: sempre "llm" — mantido para compatibilidade do
            registro de auditoria em Neo4j (não há mais caminho "regex").
        interpreter: Instância usada para gerar o texto de confirmação.
        use_self_check: Se True, habilita a verificação pós-síntese via
            LLM (Etapa 4 do plano de refatoração). Default False.
        intent_token_usage: Snapshot de `core.llm_client.TokenBucket`
            capturado ao redor de `interpreter.parse()` (Etapa 6) — custo
            de tokens da interpretação de intenção, anterior à bifurcação
            estrela/hierárquica, reportado separado do custo por topologia.

    Returns:
        Tupla (analysis_id, texto_de_confirmacao).
    """
    logger.info(
        "ChatRunner: despachando análise a partir do chat (intent_summary=%r, "
        "periodo=%s-%s, health_params=%s)",
        params.intent_summary, params.date_from, params.date_to, params.health_params,
    )
    analysis_id = dispatch_analysis(
        date_from=params.date_from,
        date_to=params.date_to,
        health_params=params.health_params,
        use_llm=use_llm,
        source_question=source_question,
        interpreted_via=interpreted_via,
        intent_summary=params.intent_summary,
        use_self_check=use_self_check,
        intent_token_usage=intent_token_usage,
    )
    confirmation = interpreter.pretty_print(params)
    logger.info("ChatRunner: análise [%s] despachada a partir do chat", analysis_id[:8])
    return analysis_id, confirmation
