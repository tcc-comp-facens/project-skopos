"""
Orquestração fina do disparo de análise a partir do chat.

Traduz um AnalysisParams já validado pelo IntentInterpreter em uma
análise disparada (mesma lógica do formulário REST, via dispatch.py) e
produz o texto de confirmação mostrado ao usuário no chat.

Requisitos: 6.1, 6.5 (spec realtime-chat-interface)
"""

from __future__ import annotations

from api.dispatch import dispatch_analysis
from core.intent_interpreter import AnalysisParams, IntentInterpreter


def run_chat_analysis(
    params: AnalysisParams,
    use_llm: bool,
    use_llm_judge: bool,
    source_question: str,
    interpreted_via: str,
    interpreter: IntentInterpreter,
) -> tuple[str, str]:
    """Dispara a análise a partir de parâmetros extraídos do chat.

    Args:
        params: Parâmetros já validados pelo IntentInterpreter.
        use_llm: Se True, usa LLM para síntese textual das arquiteturas.
        use_llm_judge: Se True, habilita avaliação LLM-as-Judge.
        source_question: Texto original digitado pelo usuário (auditoria).
        interpreted_via: "regex" ou "llm" — como os parâmetros foram extraídos.
        interpreter: Instância usada para gerar o texto de confirmação.

    Returns:
        Tupla (analysis_id, texto_de_confirmacao).
    """
    analysis_id = dispatch_analysis(
        date_from=params.date_from,
        date_to=params.date_to,
        health_params=params.health_params,
        use_llm=use_llm,
        use_llm_judge=use_llm_judge,
        source_question=source_question,
        interpreted_via=interpreted_via,
    )
    confirmation = interpreter.pretty_print(params)
    return analysis_id, confirmation
