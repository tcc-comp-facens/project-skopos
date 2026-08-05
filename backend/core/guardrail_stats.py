"""
Contadores agregados do guardrail de escopo (Etapa 1) — Etapa 6 do
PLANO_REFATORACAO.md.

Diferente das demais métricas novas da Etapa 6 (custo de tokens, volume
de comunicação, sucesso da análise), a taxa de rejeição do guardrail não
faz sentido calculada por análise isolada — uma análise sequer é
disparada quando o guardrail rejeita a mensagem. É uma métrica
process-wide, acumulada ao longo de várias mensagens de chat, pensada
para monitorar falsos positivos/negativos do prompt de classificação de
escopo ao longo do tempo (ver risco registrado na Etapa 1 do plano).

Cada chamada a `AgenteInterpretacaoIntencao.parse()` que efetivamente
chega a classificar escopo (ou seja, exclui mensagem vazia e falha
técnica do LLM, que não são decisões de escopo) deve registrar seu
resultado via `record_guardrail_decision()`.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_counts: dict[str, int] = {"total": 0, "rejected_out_of_scope": 0}


def record_guardrail_decision(out_of_scope: bool) -> None:
    """Registra uma decisão de escopo do guardrail (dentro ou fora)."""
    with _lock:
        _counts["total"] += 1
        if out_of_scope:
            _counts["rejected_out_of_scope"] += 1


def compute_guardrail_rejection_rate() -> dict[str, float | int]:
    """Etapa 6 — proporção de mensagens de chat rejeitadas por estarem
    fora do escopo orçamentário/saúde pública (guardrail da Etapa 1).

    Returns:
        Dict com total de mensagens classificadas, quantas foram
        rejeitadas, e a taxa de rejeição (0.0 a 1.0).
    """
    with _lock:
        total = _counts["total"]
        rejected = _counts["rejected_out_of_scope"]
    rate = rejected / total if total > 0 else 0.0
    return {
        "total_messages": total,
        "rejected": rejected,
        "rejection_rate": round(rate, 4),
    }


def reset_guardrail_counts() -> None:
    """Zera os contadores. Usado nos testes."""
    with _lock:
        _counts["total"] = 0
        _counts["rejected_out_of_scope"] = 0
