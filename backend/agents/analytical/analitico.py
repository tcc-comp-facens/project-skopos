"""
Agente Analítico — Correlação Estatística (Spearman) + Detecção de Anomalias.

Consolida os antigos `AgenteCorrelacao` e `AgenteAnomalias` num único
agente CoALA com 2 ações de reasoning (`calcular_correlacao`,
`detectar_anomalia`) — decisão tomada em PLANO_NOVO_MODELO_DADOS.md §7
item 6, relevante justamente para o reforço CoALA da Fase 1: os dois
cálculos sempre operaram sobre o mesmo `dados_cruzados`, com
`propose_actions`/`evaluate_and_select` idênticos (passthrough — nenhum
candidato concorrente a arbitrar), então mantê-los como dois agentes
separados só duplicava código e overhead de coordenação sem nenhum
ganho real de modularidade.

Nenhum dos dois cálculos muda: cada `_act_*` é a migração 1:1 dos
métodos originais. Ambos continuam raciocínio simbólico/determinístico
— nunca LLM (PLANO_NOVO_MODELO_DADOS.md §6 item 2: "correlação/
estatística deve continuar sendo ferramenta determinística, nunca
calculada pelo LLM").
"""

from __future__ import annotations

import logging
import math
from typing import Any

from scipy import stats

from agents.base import ActionFailure, AgenteCoALA

logger = logging.getLogger(__name__)


def _safe_correlation(func, xs: list[float], ys: list[float]) -> float:
    """Run a scipy correlation function, returning 0.0 on NaN/error.

    Handles constant-value arrays (scipy returns NaN) and other edge cases.
    Result is clamped to [-1, 1].
    """
    try:
        result = func(xs, ys)
        r = result.statistic if hasattr(result, "statistic") else result[0]
        if math.isnan(r):
            return 0.0
        return max(-1.0, min(1.0, r))
    except Exception:
        return 0.0


def _classify(r: float, limiar_alta: float = 0.7, limiar_media: float = 0.4) -> str:
    """Classify correlation strength as alta/média/baixa (|r| contra limiares)."""
    abs_r = abs(r)
    if abs_r >= limiar_alta:
        return "alta"
    if abs_r >= limiar_media:
        return "média"
    return "baixa"


def _interpretar_correlacao(tipo: str, r: float, indicadores_negativos: set[str]) -> str:
    """Traduz o sinal da correlação em "desejável"/"indesejável" dado a
    polaridade do indicador — sem isso, quem só recebe `tipo_indicador` +
    `spearman` cru (o sintetizador via LLM, ver sintetizador._build_prompt)
    tem que adivinhar sozinho se uma correlação positiva com, por ex.,
    "mortalidade" é boa ou má notícia. Bug real observado em produção: o
    LLM descreveu uma correlação positiva gasto×mortalidade (r=+0,77 —
    mais gasto acompanhando mais mortes) como "mais investimento = menos
    mortes", o oposto do dado real. Esta função não impede o LLM de
    ignorar a leitura, mas remove a ambiguidade que o levou a inverter.

    Sem sinal claro (correlação nula/indeterminada) não arrisca leitura.
    """
    if r == 0.0:
        return "sem direção clara (correlação nula ou dados insuficientes)"
    indicador_negativo = tipo in indicadores_negativos
    positiva = r > 0
    if indicador_negativo:
        return (
            "positiva — LEITURA INDESEJÁVEL: mais gasto acompanhou mais "
            "casos/óbitos/internações neste indicador, não menos"
            if positiva else
            "negativa — LEITURA DESEJÁVEL: mais gasto acompanhou menos "
            "casos/óbitos/internações neste indicador"
        )
    return (
        "positiva — LEITURA DESEJÁVEL: mais gasto acompanhou melhora "
        "neste indicador"
        if positiva else
        "negativa — LEITURA INDESEJÁVEL: mais gasto acompanhou piora "
        "neste indicador"
    )


def _median(values: list[float]) -> float:
    """Calculate the median of a list of floats (média dos 2 centrais se par)."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


SUBFUNCAO_NOMES: dict[int, str] = {
    122: "Administração Geral",
    301: "Atenção Básica",
    302: "Assistência Hospitalar e Ambulatorial",
    303: "Suporte Profilático e Terapêutico",
    304: "Vigilância Sanitária",
    305: "Vigilância Epidemiológica",
    306: "Alimentação e Nutrição",
}

# Indicadores onde valor alto = resultado RUIM (mais casos/óbitos/
# internações = pior). Inclui os legados (dengue, internacoes,
# mortalidade), os subtipos nativos do COVID (`casos`, `obitos` — não
# mais o token legado "covid", que nunca aparece em `tipo_indicador`
# desde que agente_covid.py parou de relabelar, mesma causa do bug
# corrigido em data_crossing.SUBFUNCAO_INDICADOR_MAP) e os 8 subtipos
# SINAN adicionais cobertos por AgenteSINAN (Fase 1) — dengue já está
# listado, não repetido.
INDICADORES_NEGATIVOS: set[str] = {
    "dengue",
    "casos",
    "obitos",
    "internacoes",
    "mortalidade",
    "chikungunya",
    "sifilis_adquirida",
    "sifilis_gestante",
    "sifilis_congenita",
    "coqueluche",
    "hepatites_virais",
    "tuberculose",
    "hanseniase",
}

# Indicadores onde valor alto = resultado BOM (mais cobertura/doses
# aplicadas = melhor). Subtipos nativos do SI-PNI — não mais o token
# legado "vacinacao", pela mesma razão do comentário acima.
INDICADORES_POSITIVOS: set[str] = {"cobertura_vacinal", "doses_aplicadas"}


class AgenteAnalitico(AgenteCoALA):
    """Agente analítico consolidado: correlação Spearman + anomalias por mediana.

    Recebe dados cruzados (despesas × indicadores por subfunção e ano) e
    calcula, para cada par subfunção-indicador: (a) a correlação de
    Spearman, classificada por força; (b) anomalias onde gasto e
    resultado divergem da mediana do par, considerando a polaridade do
    indicador.

    Attributes:
        semantic_memory: limiares de classificação de correlação
            (`limiar_alta`, `limiar_media`) e polaridade/nomenclatura de
            indicadores (`indicadores_negativos`, `indicadores_positivos`,
            `subfuncao_nomes`).
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.semantic_memory = {
            "limiar_alta": 0.7,
            "limiar_media": 0.4,
            "indicadores_negativos": INDICADORES_NEGATIVOS,
            "indicadores_positivos": INDICADORES_POSITIVOS,
            "subfuncao_nomes": SUBFUNCAO_NOMES,
        }
        self.procedural_memory = {
            "calcular_correlacao": [self._act_calcular_correlacao],
            "detectar_anomalia": [self._act_detectar_anomalia],
        }

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        return {"dados_cruzados": self.working_memory.get("dados_cruzados", [])}

    def propose_actions(self) -> list[dict]:
        """Propõe as duas ações de reasoning se há dados cruzados
        disponíveis — ambas determinísticas, sem motivo para tornar
        condicional entre si (mesmo input, custo desprezível)."""
        actions: list[dict] = []
        if self.working_memory.get("dados_cruzados"):
            actions.append({"goal": "calcular_correlacao"})
            actions.append({"goal": "detectar_anomalia"})
        return actions

    def _act_calcular_correlacao(self, action: dict) -> None:
        """Ação interna (reasoning): calcula Spearman por par subfunção-indicador
        (migração 1:1 de `AgenteCorrelacao._compute_correlations`)."""
        try:
            crossed = self.working_memory.get("dados_cruzados", [])
            correlacoes: list[dict[str, Any]] = []
            limiar_alta = self.semantic_memory["limiar_alta"]
            limiar_media = self.semantic_memory["limiar_media"]
            indicadores_negativos = self.semantic_memory["indicadores_negativos"]

            pairs: dict[tuple[int, str], list[dict]] = {}
            for item in crossed:
                key = (item["subfuncao"], item["tipo_indicador"])
                pairs.setdefault(key, []).append(item)

            for (subfuncao, tipo), raw_items in pairs.items():
                # Mesmo motivo do filtro em _act_detectar_anomalia: pontos
                # com valor_indicador nulo (subtipos só quebrados por
                # dimensão) não entram no cálculo — sem isso, um único
                # None no par faz stats.spearmanr levantar exceção e
                # _safe_correlation devolver 0.0 mesmo havendo pontos
                # válidos suficientes para uma correlação real.
                items = [
                    it for it in raw_items
                    if it["valor_despesa"] is not None and it["valor_indicador"] is not None
                ]
                n = len(items)
                if n == 0:
                    continue
                xs = [it["valor_despesa"] for it in items]
                ys = [it["valor_indicador"] for it in items]

                if n < 2:
                    correlacoes.append({
                        "subfuncao": subfuncao,
                        "tipo_indicador": tipo,
                        "spearman": 0.0,
                        "classificacao": "baixa",
                        "n_pontos": n,
                    })
                else:
                    r_spearman = _safe_correlation(stats.spearmanr, xs, ys)
                    r_rounded = round(r_spearman, 4)
                    correlacoes.append({
                        "subfuncao": subfuncao,
                        "tipo_indicador": tipo,
                        "spearman": r_rounded,
                        "classificacao": _classify(r_rounded, limiar_alta, limiar_media),
                        "n_pontos": n,
                        "leitura": _interpretar_correlacao(
                            tipo, r_rounded, indicadores_negativos
                        ),
                    })

            self.working_memory["correlacoes"] = correlacoes
            logger.info(
                "Agent %s: computed %d correlations", self.agent_id, len(correlacoes)
            )
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    def _act_detectar_anomalia(self, action: dict) -> None:
        """Ação interna (reasoning): detecta anomalias via mediana por par
        subfunção-indicador (migração 1:1 de
        `AgenteAnomalias._detect_anomalies`)."""
        try:
            crossed = self.working_memory.get("dados_cruzados", [])
            anomalias: list[dict[str, Any]] = []
            indicadores_negativos = self.semantic_memory["indicadores_negativos"]
            subfuncao_nomes = self.semantic_memory["subfuncao_nomes"]

            pairs: dict[tuple[int, str], list[dict]] = {}
            for item in crossed:
                key = (item["subfuncao"], item["tipo_indicador"])
                pairs.setdefault(key, []).append(item)

            for (subfuncao, tipo), raw_items in pairs.items():
                # Alguns subtipos (ex.: CNES "tipo_atendimento",
                # "leitos_consultorios") só existem quebrados por
                # dimensão — sem quebra, o valor agregado vem nulo do
                # Neo4j. `_median()` não compara None (TypeError,
                # derrubando a ação inteira antes da correção), então
                # esses pontos são descartados aqui, não silenciados
                # lá dentro.
                items = [
                    it for it in raw_items
                    if it["valor_despesa"] is not None and it["valor_indicador"] is not None
                ]
                if len(items) < 2:
                    continue

                despesas_vals = [it["valor_despesa"] for it in items]
                indicador_vals = [it["valor_indicador"] for it in items]

                med_desp = _median(despesas_vals)
                med_ind = _median(indicador_vals)

                subfuncao_nome = items[0].get(
                    "subfuncao_nome", subfuncao_nomes.get(subfuncao, str(subfuncao))
                )

                is_negative = tipo in indicadores_negativos

                for it in items:
                    high_spend = it["valor_despesa"] > med_desp
                    low_spend = it["valor_despesa"] < med_desp
                    high_indicator = it["valor_indicador"] > med_ind
                    low_indicator = it["valor_indicador"] < med_ind

                    if is_negative:
                        bad_outcome = high_indicator
                        good_outcome = low_indicator
                    else:
                        bad_outcome = low_indicator
                        good_outcome = high_indicator

                    if high_spend and bad_outcome:
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
        except Exception as e:
            raise ActionFailure(action, str(e)) from e

    # -- Interface pública chamada pelo orquestrador/supervisor -----------

    def analisar(self, dados_cruzados: list[dict]) -> dict[str, Any]:
        """Calcula correlações e detecta anomalias sobre os mesmos dados
        cruzados. Substitui as chamadas separadas a
        `AgenteCorrelacao.compute()`/`AgenteAnomalias.detect()`.

        Args:
            dados_cruzados: Lista de dicts com keys: subfuncao,
                subfuncao_nome, tipo_indicador, ano, valor_despesa,
                valor_indicador.

        Returns:
            Dict com `correlacoes` e `anomalias` (listas vazias se input
            vazio).
        """
        self.update_working_memory({"dados_cruzados": dados_cruzados})
        self.run_coala_cycle()
        return {
            "correlacoes": self.working_memory.get("correlacoes", []),
            "anomalias": self.working_memory.get("anomalias", []),
        }
