"""
Agente Analítico — Detecção de Anomalias.

Detecta ineficiências nos gastos públicos em saúde comparando despesas
e indicadores com suas respectivas medianas por par subfunção-indicador.

A polaridade do indicador é considerada:
- Indicadores NEGATIVOS (mais = pior): dengue, covid, internacoes, mortalidade
- Indicadores POSITIVOS (mais = melhor): vacinacao

Anomalias detectadas:
- "alto_gasto_baixo_resultado": gasto alto sem resultado proporcional (ineficiência)
  → Indicador negativo: gastou muito E casos continuam altos (acima da mediana)
  → Indicador positivo: gastou muito E cobertura continua baixa (abaixo da mediana)
- "baixo_gasto_alto_resultado": resultado bom apesar de gasto baixo (eficiência)
  → Indicador negativo: gastou pouco E casos estão baixos (abaixo da mediana)
  → Indicador positivo: gastou pouco E cobertura está alta (acima da mediana)

Opera sobre dados em memória (CrossedDataPoint dicts) — sem dependência
de Neo4j ou outros serviços externos. A detecção via mediana é raciocínio
simbólico/determinístico — não depende de LLM (Req 6.1, 6.2, 6.3, 6.4, 6.5).
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import ActionFailure, AgenteCoALA

logger = logging.getLogger(__name__)

SUBFUNCAO_NOMES: dict[int, str] = {
    301: "Atenção Básica",
    302: "Assistência Hospitalar",
    303: "Suporte Profilático",
    305: "Vigilância Epidemiológica",
}

# Indicadores onde valor alto = resultado RUIM (mais casos/óbitos/internações = pior)
INDICADORES_NEGATIVOS: set[str] = {"dengue", "covid", "internacoes", "mortalidade"}

# Indicadores onde valor alto = resultado BOM (mais cobertura vacinal = melhor)
INDICADORES_POSITIVOS: set[str] = {"vacinacao"}


def _median(values: list[float]) -> float:
    """Calculate the median of a list of floats.

    For even-length lists, returns the average of the two middle values.
    """
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


class AgenteAnomalias(AgenteCoALA):
    """Agente analítico que detecta ineficiências via mediana (Req 6).

    Recebe dados cruzados (despesas × indicadores por subfunção e ano)
    e identifica anomalias onde o gasto e o resultado divergem da mediana
    do par subfunção-indicador.

    A polaridade do indicador (`semantic_memory["indicadores_negativos"]`/
    `["indicadores_positivos"]`) é considerada:
    - Indicadores negativos (dengue, covid, internacoes, mortalidade):
      valor alto = resultado RUIM (muitos casos)
    - Indicadores positivos (vacinacao):
      valor alto = resultado BOM (boa cobertura)

    Tipos de anomalia:
    - "alto_gasto_baixo_resultado": ineficiência — gastou muito sem resultado (Req 6.1)
      → Negativo: despesa > mediana E indicador > mediana (gastou muito, casos altos)
      → Positivo: despesa > mediana E indicador < mediana (gastou muito, cobertura baixa)
    - "baixo_gasto_alto_resultado": eficiência — resultado bom com pouco gasto (Req 6.2)
      → Negativo: despesa < mediana E indicador < mediana (gastou pouco, casos baixos)
      → Positivo: despesa < mediana E indicador > mediana (gastou pouco, cobertura alta)

    Pares com menos de 2 pontos de dados são ignorados (Req 6.5).
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.semantic_memory = {
            "indicadores_negativos": INDICADORES_NEGATIVOS,
            "indicadores_positivos": INDICADORES_POSITIVOS,
            "subfuncao_nomes": SUBFUNCAO_NOMES,
        }
        self.procedural_memory = {
            "detectar_anomalias": [self._act_detectar_anomalias],
        }

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Retorna a working memory atual como percepção (dado definido pelo caller)."""
        return {
            "dados_cruzados": self.working_memory.get("dados_cruzados", []),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe detectar anomalias se há dados cruzados disponíveis."""
        actions: list[dict] = []
        if self.working_memory.get("dados_cruzados"):
            actions.append({"goal": "detectar_anomalias"})
        return actions

    def _act_detectar_anomalias(self, action: dict) -> None:
        """Ação interna de reasoning: detecta anomalias via mediana. Sem
        fallback registrado — falha propaga (tratada no nível do
        orquestrador/supervisor).
        """
        try:
            self._detect_anomalies()
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    # -- Interface pública chamada pelo orquestrador/supervisor -----------

    def detect(self, dados_cruzados: list[dict]) -> list[dict]:
        """Detecta anomalias para cada par subfunção-indicador.

        Recebe dados cruzados (CrossedDataPoint dicts) e retorna lista
        de anomalias com tipo e descrição em português.

        Args:
            dados_cruzados: Lista de dicts com keys: subfuncao, subfuncao_nome,
                tipo_indicador, ano, valor_despesa, valor_indicador.

        Returns:
            Lista de dicts com keys: subfuncao, tipo_indicador, ano,
            tipo_anomalia, descricao.
            Retorna lista vazia se input for vazio.
            Ignora pares com < 2 pontos de dados (Req 6.5).
        """
        self.update_working_memory({"dados_cruzados": dados_cruzados})
        self.run_coala_cycle()
        return self.working_memory.get("anomalias", [])

    # -- Reasoning interno -------------------------------------------------

    def _detect_anomalies(self) -> None:
        """Detect anomalies per subfuncao-indicador pair (Reqs 6.1-6.5).

        A polaridade do indicador é obtida via *retrieval* de
        `semantic_memory` (não hardcoded direto do módulo):
        - Indicadores negativos: indicador ALTO = resultado RUIM
        - Indicadores positivos: indicador ALTO = resultado BOM
        """
        crossed = self.working_memory.get("dados_cruzados", [])
        anomalias: list[dict[str, Any]] = []
        indicadores_negativos = self.semantic_memory["indicadores_negativos"]
        subfuncao_nomes = self.semantic_memory["subfuncao_nomes"]

        # Group data points by (subfuncao, tipo_indicador)
        pairs: dict[tuple[int, str], list[dict]] = {}
        for item in crossed:
            key = (item["subfuncao"], item["tipo_indicador"])
            pairs.setdefault(key, []).append(item)

        for (subfuncao, tipo), items in pairs.items():
            # Req 6.5: Ignore pairs with < 2 data points
            if len(items) < 2:
                continue

            despesas_vals = [it["valor_despesa"] for it in items]
            indicador_vals = [it["valor_indicador"] for it in items]

            med_desp = _median(despesas_vals)
            med_ind = _median(indicador_vals)

            subfuncao_nome = items[0].get(
                "subfuncao_nome",
                subfuncao_nomes.get(subfuncao, str(subfuncao)),
            )

            # Determinar polaridade do indicador
            is_negative = tipo in indicadores_negativos

            for it in items:
                high_spend = it["valor_despesa"] > med_desp
                low_spend = it["valor_despesa"] < med_desp
                high_indicator = it["valor_indicador"] > med_ind
                low_indicator = it["valor_indicador"] < med_ind

                # Determinar "resultado ruim" e "resultado bom" conforme polaridade
                if is_negative:
                    # Indicador negativo: valor alto = resultado ruim
                    bad_outcome = high_indicator  # muitos casos = ruim
                    good_outcome = low_indicator  # poucos casos = bom
                else:
                    # Indicador positivo: valor baixo = resultado ruim
                    bad_outcome = low_indicator   # baixa cobertura = ruim
                    good_outcome = high_indicator  # alta cobertura = bom

                if high_spend and bad_outcome:
                    # Req 6.1: alto gasto sem resultado — ineficiência
                    if is_negative:
                        desc_resultado = (
                            f"indicador acima da mediana "
                            f"({it['valor_indicador']:.1f} casos)"
                        )
                    else:
                        desc_resultado = (
                            f"indicador abaixo da mediana "
                            f"({it['valor_indicador']:.1f})"
                        )
                    anomalias.append({
                        "subfuncao": subfuncao,
                        "tipo_indicador": tipo,
                        "ano": it["ano"],
                        "tipo_anomalia": "alto_gasto_baixo_resultado",
                        "descricao": (
                            f"Subfunção {subfuncao} ({subfuncao_nome}) "
                            f"em {it['ano']}: gasto acima da mediana "
                            f"(R$ {it['valor_despesa']:,.2f}) com "
                            f"{desc_resultado} — possível ineficiência"
                        ),
                    })
                elif low_spend and good_outcome:
                    # Req 6.2: baixo gasto com bom resultado — eficiência
                    if is_negative:
                        desc_resultado = (
                            f"indicador abaixo da mediana "
                            f"({it['valor_indicador']:.1f} casos)"
                        )
                    else:
                        desc_resultado = (
                            f"indicador acima da mediana "
                            f"({it['valor_indicador']:.1f})"
                        )
                    anomalias.append({
                        "subfuncao": subfuncao,
                        "tipo_indicador": tipo,
                        "ano": it["ano"],
                        "tipo_anomalia": "baixo_gasto_alto_resultado",
                        "descricao": (
                            f"Subfunção {subfuncao} ({subfuncao_nome}) "
                            f"em {it['ano']}: gasto abaixo da mediana "
                            f"(R$ {it['valor_despesa']:,.2f}) com "
                            f"{desc_resultado} — possível eficiência"
                        ),
                    })

        self.working_memory["anomalias"] = anomalias
        logger.info(
            "Agent %s: detected %d anomalies", self.agent_id, len(anomalias)
        )
