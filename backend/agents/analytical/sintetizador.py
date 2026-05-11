"""
Sintetizador de Texto — Serviço de geração textual.

Gera texto consolidado de análise via LLM (Groq, cadeia de fallback entre modelos)
a partir de correlações, anomalias e contexto orçamentário.

Quando o LLM está indisponível, gera texto estruturado como fallback.

Nota arquitetural: o sintetizador NÃO é um agente BDI. Ele não possui
autonomia deliberativa — recebe dados prontos e produz texto. A decisão
de modelá-lo como classe normal (não agente) reflete que ele não percebe
ambiente mutável, não forma desejos concorrentes, e não escolhe entre
planos alternativos. O streaming é responsabilidade do caller via
StreamingAdapter.

Requisitos: 7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

import logging
from typing import Any, Generator

logger = logging.getLogger(__name__)

SUBFUNCAO_NOMES: dict[int, str] = {
    301: "Atenção Básica",
    302: "Assistência Hospitalar",
    303: "Suporte Profilático",
    305: "Vigilância Epidemiológica",
}


class TextSynthesizer:
    """Serviço de geração de texto analítico via LLM com fallback estruturado.

    Não é um agente BDI — é um serviço consumido pelos orquestradores
    e supervisores. Responsabilidade: dado um conjunto de correlações,
    anomalias e contexto orçamentário, produzir texto analítico.

    Args:
        synthesizer_id: Identificador para métricas e logging.
    """

    def __init__(self, synthesizer_id: str) -> None:
        self.synthesizer_id = synthesizer_id

    def generate(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
        use_llm: bool = True,
    ) -> str:
        """Gera texto completo de análise (batch, sem streaming).

        Tenta LLM primeiro; se indisponível, retorna fallback estruturado.

        Args:
            correlacoes: Lista de dicts com correlações calculadas.
            anomalias: Lista de dicts com anomalias detectadas.
            contexto_orcamentario: Dict com tendências orçamentárias por subfunção.
            data_coverage: Dict com cobertura de dados e gaps detectados.
            use_llm: Se True, tenta usar LLM; se False, usa fallback direto.

        Returns:
            Texto completo da análise gerada.
        """
        if not use_llm:
            logger.info("TextSynthesizer %s: LLM disabled, using structured fallback", self.synthesizer_id)
            return self._generate_structured_text(correlacoes, anomalias, contexto_orcamentario, data_coverage)

        try:
            import core.llm_client as llm_client

            prompt = self._build_prompt(correlacoes, anomalias, contexto_orcamentario, data_coverage)
            # Consume the stream fully for batch mode
            text = "".join(llm_client.generate_stream(prompt))
            if text:
                logger.info("TextSynthesizer %s: LLM generation complete (%d chars)", self.synthesizer_id, len(text))
                return text
        except Exception:
            logger.warning(
                "TextSynthesizer %s: LLM failed, using structured fallback",
                self.synthesizer_id,
            )

        return self._generate_structured_text(correlacoes, anomalias, contexto_orcamentario, data_coverage)

    def generate_stream(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
    ) -> Generator[str, None, None]:
        """Retorna generator de tokens para streaming via LLM.

        O caller é responsável por consumir o generator e fazer o
        streaming via StreamingAdapter. Se o LLM falhar, levanta exceção
        para que o caller use o fallback.

        Args:
            correlacoes: Lista de dicts com correlações calculadas.
            anomalias: Lista de dicts com anomalias detectadas.
            contexto_orcamentario: Dict com tendências orçamentárias.
            data_coverage: Dict com cobertura de dados e gaps detectados.

        Yields:
            Tokens individuais do LLM.

        Raises:
            Exception: Se o LLM estiver indisponível.
        """
        import core.llm_client as llm_client

        prompt = self._build_prompt(correlacoes, anomalias, contexto_orcamentario, data_coverage)
        yield from llm_client.generate_stream(prompt)

    def generate_fallback(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
    ) -> str:
        """Gera texto estruturado sem LLM (fallback determinístico).

        Args:
            correlacoes: Lista de dicts com correlações calculadas.
            anomalias: Lista de dicts com anomalias detectadas.
            contexto_orcamentario: Dict com tendências orçamentárias.
            data_coverage: Dict com cobertura de dados e gaps detectados.

        Returns:
            Texto estruturado com seções de análise.
        """
        return self._generate_structured_text(correlacoes, anomalias, contexto_orcamentario, data_coverage)

    # -- Internal methods ------------------------------------------------

    def _build_prompt(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
    ) -> str:
        """Build the LLM prompt from analysis data (Req 7.4)."""
        coverage = data_coverage or {}
        gaps = coverage.get("gaps", [])
        summary = coverage.get("summary", {})

        coverage_section = ""
        if gaps:
            coverage_section = (
                "\nAVISO — DADOS FALTANTES:\n"
                f"Completude das despesas: {summary.get('despesas_completeness', 1.0):.0%}\n"
                f"Completude dos indicadores: {summary.get('indicadores_completeness', 1.0):.0%}\n"
                "Lacunas detectadas:\n"
            )
            for g in gaps:
                coverage_section += f"- {g['description']}\n"
            coverage_section += (
                "\nIMPORTANTE: Mencione explicitamente na análise quais dados "
                "estão faltando e como isso limita as conclusões.\n"
            )

        return (
            "Você é um analista de políticas públicas de saúde. "
            "Analise os dados abaixo sobre gastos em saúde vs indicadores "
            "de saúde do município de Sorocaba-SP e gere uma análise detalhada "
            "em português brasileiro.\n\n"
            "Foram calculadas três métricas de correlação para cada par:\n"
            "- Pearson: mede relação linear\n"
            "- Spearman: mede relação monotônica (baseada em ranks, robusta a outliers)\n"
            "- Kendall Tau: mede concordância entre pares (ideal para amostras pequenas)\n\n"
            f"Correlações encontradas:\n{correlacoes}\n\n"
            f"Anomalias identificadas:\n{anomalias}\n\n"
            f"Contexto orçamentário (tendências de gasto):\n{contexto_orcamentario}\n\n"
            f"{coverage_section}\n"
            "Gere uma análise completa com as seguintes seções:\n"
            "1. Resumo Executivo\n"
            "2. Cobertura de Dados (quais dados estão disponíveis e quais estão faltando)\n"
            "3. Análise das Correlações (comparando os três métodos e explicando divergências)\n"
            "4. Discussão das Anomalias\n"
            "5. Contexto Orçamentário\n"
            "Use formatação com seções claras."
        )

    def _generate_structured_text(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
    ) -> str:
        """Generate structured fallback text when LLM is unavailable (Req 7.3, 7.4).

        Includes: resumo executivo, cobertura de dados, correlações analysis,
        anomalias discussion, and contexto orçamentário.
        """
        coverage = data_coverage or {}
        gaps = coverage.get("gaps", [])
        summary = coverage.get("summary", {})

        sections: list[str] = []

        # --- Resumo Executivo (Req 7.4) ---
        sections.append("=== Resumo Executivo ===\n")
        sections.append(
            "Análise consolidada dos gastos em saúde vs indicadores de saúde "
            "do município de Sorocaba-SP, integrando correlações estatísticas, "
            "detecção de anomalias e contexto orçamentário.\n"
        )
        sections.append(
            f"Foram analisadas {len(correlacoes)} correlação(ões) e "
            f"identificadas {len(anomalias)} anomalia(s).\n"
        )

        # --- Cobertura de Dados ---
        sections.append("\n=== Cobertura de Dados ===\n")
        if not gaps:
            sections.append(
                "Todos os dados esperados para o período estão disponíveis. "
                "Não foram detectadas lacunas.\n"
            )
        else:
            desp_pct = summary.get("despesas_completeness", 1.0)
            ind_pct = summary.get("indicadores_completeness", 1.0)
            sections.append(
                f"Completude das despesas SIOPS: {desp_pct:.0%}\n"
                f"Completude dos indicadores DataSUS: {ind_pct:.0%}\n"
                f"Total de lacunas detectadas: {len(gaps)}\n\n"
            )
            sections.append("Lacunas identificadas:\n")
            for g in gaps:
                sections.append(f"⚠ {g['description']}\n")
            sections.append(
                "\nNota: As correlações e anomalias foram calculadas apenas "
                "com os dados disponíveis. Anos sem dados em ambos os lados "
                "(despesa e indicador) foram excluídos do cálculo, o que pode "
                "reduzir a significância estatística dos resultados.\n"
            )

        # --- Análise das Correlações (Req 7.4) ---
        sections.append("\n=== Análise das Correlações ===\n")
        if not correlacoes:
            sections.append(
                "Não foram encontrados dados suficientes para calcular correlações "
                "entre despesas e indicadores de saúde no período selecionado.\n"
            )
        else:
            for c in correlacoes:
                subfuncao_nome = c.get(
                    "subfuncao_nome",
                    SUBFUNCAO_NOMES.get(c.get("subfuncao", 0), str(c.get("subfuncao", ""))),
                )
                direction = "positiva" if c.get("spearman", 0) >= 0 else "negativa"
                classificacao = c.get("classificacao", "baixa")
                sections.append(
                    f"• {subfuncao_nome} × {c.get('tipo_indicador', '')}:\n"
                    f"  Pearson: {c.get('pearson', 0):.4f} | "
                    f"Spearman: {c.get('spearman', 0):.4f} | "
                    f"Kendall: {c.get('kendall', 0):.4f}\n"
                    f"  Classificação: {classificacao} {direction} "
                    f"({c.get('n_pontos', 0)} pontos de dados)\n"
                )

            # Insights
            high_corr = [c for c in correlacoes if c.get("classificacao") == "alta"]
            if high_corr:
                sections.append("\nCorrelações fortes identificadas:\n")
                for c in high_corr:
                    subfuncao_nome = c.get(
                        "subfuncao_nome",
                        SUBFUNCAO_NOMES.get(c.get("subfuncao", 0), str(c.get("subfuncao", ""))),
                    )
                    if c.get("spearman", 0) > 0:
                        sections.append(
                            f"  O aumento nos gastos com {subfuncao_nome} está fortemente "
                            f"associado ao aumento de {c.get('tipo_indicador', '')}.\n"
                        )
                    else:
                        sections.append(
                            f"  O aumento nos gastos com {subfuncao_nome} está fortemente "
                            f"associado à redução de {c.get('tipo_indicador', '')}.\n"
                        )

        # --- Discussão das Anomalias (Req 7.4) ---
        sections.append("\n=== Discussão das Anomalias ===\n")
        if not anomalias:
            sections.append(
                "Nenhuma anomalia significativa foi identificada nos padrões de gasto "
                "vs resultado no período analisado.\n"
            )
        else:
            for a in anomalias:
                sections.append(f"⚠ {a.get('descricao', 'Anomalia detectada')}\n")

            alto_gasto = [
                a for a in anomalias
                if a.get("tipo_anomalia") == "alto_gasto_baixo_resultado"
            ]
            baixo_gasto = [
                a for a in anomalias
                if a.get("tipo_anomalia") == "baixo_gasto_alto_resultado"
            ]
            if alto_gasto:
                sections.append(
                    f"\nForam identificados {len(alto_gasto)} caso(s) de alto gasto com "
                    "baixo resultado, o que pode indicar ineficiência na alocação de recursos.\n"
                )
            if baixo_gasto:
                sections.append(
                    f"\nForam identificados {len(baixo_gasto)} caso(s) de baixo gasto com "
                    "alto resultado, sugerindo eficiência ou fatores externos positivos.\n"
                )

        # --- Contexto Orçamentário (Req 7.4) ---
        sections.append("\n=== Contexto Orçamentário ===\n")
        if not contexto_orcamentario:
            sections.append(
                "Dados de contexto orçamentário não disponíveis para o período analisado.\n"
            )
        else:
            for subfuncao_key, tendencia_data in contexto_orcamentario.items():
                if isinstance(tendencia_data, dict):
                    tendencia = tendencia_data.get("tendencia", "desconhecida")
                    variacao = tendencia_data.get("variacao_media_percentual", 0)
                    anos = tendencia_data.get("anos_analisados", [])
                    subfuncao_nome = SUBFUNCAO_NOMES.get(
                        int(subfuncao_key) if str(subfuncao_key).isdigit() else 0,
                        str(subfuncao_key),
                    )
                    sections.append(
                        f"• {subfuncao_nome} (subfunção {subfuncao_key}):\n"
                        f"  Tendência: {tendencia} | "
                        f"Variação média: {variacao:.1f}% | "
                        f"Anos: {anos}\n"
                    )

        sections.append("\n=== Fim da Análise ===")
        return "\n".join(sections)
