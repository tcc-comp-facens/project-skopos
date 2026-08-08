"""
Sintetizador de Texto — Serviço de geração textual.

Gera texto consolidado de análise via LLM (DeepSeek)
a partir de correlações, anomalias e contexto orçamentário.

Quando o LLM está indisponível, gera texto estruturado como fallback.

Nota arquitetural (CoALA): o sintetizador NÃO é um agente CoALA — não
possui working/episodic/semantic/procedural memory própria nem participa
do ciclo propose→evaluate→select→execute. Ele é, em si, a implementação
de duas ações do espaço de ações de quem o chama (orquestrador/supervisor):
`_build_prompt`/`_generate_structured_text` são uma ação de *reasoning*
interna (transforma dados já resolvidos em texto, sem tocar o ambiente);
a chamada a `core.llm_client.generate_stream` é uma ação de *grounding*
externa (invocação de uma ferramenta fora do processo — a API do LLM). A
decisão de modelá-lo como classe normal (não agente) reflete que ele não
percebe ambiente mutável, não propõe ações concorrentes, e não escolhe
entre estratégias alternativas — quem decide isso é o caller. O streaming
é responsabilidade do caller via StreamingAdapter.

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

    Não é um agente CoALA — é um serviço consumido pelos orquestradores
    e supervisores, que implementa as ações de reasoning (montagem de
    prompt/texto estruturado) e grounding externo (chamada ao LLM) desses
    chamadores. Responsabilidade: dado um conjunto de correlações,
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
        enfase: str | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        pergunta_usuario: str | None = None,
    ) -> str:
        """Gera texto completo de análise (batch, sem streaming).

        Tenta LLM primeiro; se indisponível, retorna fallback estruturado.

        Args:
            correlacoes: Lista de dicts com correlações calculadas — se vier
                da Etapa 3 (`AgentePriorizacaoAnalitica`), já reordenada por
                relevância; os valores em si nunca são alterados.
            anomalias: Lista de dicts com anomalias detectadas, idem.
            contexto_orcamentario: Dict com tendências orçamentárias por subfunção.
            data_coverage: Dict com cobertura de dados e gaps detectados.
            use_llm: Se True, tenta usar LLM; se False, usa fallback direto.
            enfase: Descrição do ângulo de ênfase escolhido pela Etapa 3
                (opcional) — vira uma instrução explícita no prompt.
            date_from: Ano inicial do período analisado — quando informado,
                o texto (LLM ou fallback) declara esse período no início,
                mesmo quando foi inferido pelo guardrail (ver
                `AgenteInterpretacaoIntencao`), não só quando explicitado
                pelo usuário.
            date_to: Ano final do período analisado (idem).
            pergunta_usuario: Resumo da pergunta original do usuário
                (`intent_summary`, ver `AgenteInterpretacaoIntencao`) —
                quando informado, o texto responde essa pergunta
                diretamente antes de qualquer outra coisa, em vez de só
                seguir um formato de relatório genérico.

        Returns:
            Texto completo da análise gerada.
        """
        if not use_llm:
            logger.info("TextSynthesizer %s: LLM disabled, using structured fallback", self.synthesizer_id)
            return self._generate_structured_text(
                correlacoes, anomalias, contexto_orcamentario, data_coverage, date_from, date_to
            )

        try:
            import core.llm_client as llm_client

            prompt = self._build_prompt(
                correlacoes, anomalias, contexto_orcamentario, data_coverage,
                enfase, date_from, date_to, pergunta_usuario,
            )
            logger.info(
                "TextSynthesizer %s: tentando síntese via LLM (batch, %d chars de prompt)",
                self.synthesizer_id, len(prompt),
            )
            # Consume the stream fully for batch mode
            text = "".join(llm_client.generate_stream(prompt, caller=self.synthesizer_id))
            if text:
                logger.info("TextSynthesizer %s: LLM generation complete (%d chars)", self.synthesizer_id, len(text))
                return text
        except Exception:
            logger.warning(
                "TextSynthesizer %s: LLM failed, using structured fallback",
                self.synthesizer_id,
            )

        logger.info(
            "TextSynthesizer %s: usando fallback estruturado (LLM indisponível ou vazio)",
            self.synthesizer_id,
        )
        return self._generate_structured_text(
            correlacoes, anomalias, contexto_orcamentario, data_coverage, date_from, date_to
        )

    def generate_stream(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
        enfase: str | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        pergunta_usuario: str | None = None,
    ) -> Generator[str, None, None]:
        """Retorna generator de tokens para streaming via LLM.

        O caller é responsável por consumir o generator e fazer o
        streaming via StreamingAdapter. Se o LLM falhar, levanta exceção
        para que o caller use o fallback.

        Args:
            correlacoes: Lista de dicts com correlações calculadas (ver
                nota sobre `enfase`/Etapa 3 em `generate`).
            anomalias: Lista de dicts com anomalias detectadas.
            contexto_orcamentario: Dict com tendências orçamentárias.
            data_coverage: Dict com cobertura de dados e gaps detectados.
            enfase: Descrição do ângulo de ênfase escolhido pela Etapa 3
                (opcional).
            date_from: Ano inicial do período analisado (ver `generate`).
            date_to: Ano final do período analisado (ver `generate`).
            pergunta_usuario: Pergunta original do usuário (ver `generate`).

        Yields:
            Tokens individuais do LLM.

        Raises:
            Exception: Se o LLM estiver indisponível.
        """
        import core.llm_client as llm_client

        prompt = self._build_prompt(
            correlacoes, anomalias, contexto_orcamentario, data_coverage,
            enfase, date_from, date_to, pergunta_usuario,
        )
        logger.info(
            "TextSynthesizer %s: tentando síntese via LLM (streaming, %d chars de prompt)",
            self.synthesizer_id, len(prompt),
        )
        yield from llm_client.generate_stream(prompt, caller=self.synthesizer_id)

    def generate_fallback(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
    ) -> str:
        """Gera texto estruturado sem LLM (fallback determinístico).

        Args:
            correlacoes: Lista de dicts com correlações calculadas.
            anomalias: Lista de dicts com anomalias detectadas.
            contexto_orcamentario: Dict com tendências orçamentárias.
            data_coverage: Dict com cobertura de dados e gaps detectados.
            date_from: Ano inicial do período analisado (ver `generate`).
            date_to: Ano final do período analisado (ver `generate`).

        Returns:
            Texto estruturado com seções de análise.
        """
        return self._generate_structured_text(
            correlacoes, anomalias, contexto_orcamentario, data_coverage, date_from, date_to
        )

    # -- Internal methods ------------------------------------------------

    def _build_prompt(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
        enfase: str | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        pergunta_usuario: str | None = None,
    ) -> str:
        """Build the LLM prompt from analysis data (Req 7.4).

        `enfase` (Etapa 3 — AgentePriorizacaoAnalitica) vira uma instrução
        explícita de destaque, além da própria ordem de `correlacoes`/
        `anomalias` (já reordenadas por relevância quando vêm da Etapa 3).

        `date_from`/`date_to`, quando informados, viram uma instrução para
        o texto declarar o período analisado logo no início — inclusive
        quando o período foi inferido pelo guardrail de intenção (o
        usuário não especificou datas), para o leitor sempre saber qual
        intervalo de anos embasa a análise.

        `pergunta_usuario` (opcional — `intent_summary` do
        `AgenteInterpretacaoIntencao`), quando informado, faz o texto
        responder essa pergunta diretamente antes de qualquer outra
        coisa — ver seção "COMO RESPONDER" abaixo, que também abandona a
        estrutura fixa de relatório em favor de uma resposta natural,
        adaptada ao que foi perguntado.
        """
        coverage = data_coverage or {}
        gaps = coverage.get("gaps", [])
        summary = coverage.get("summary", {})

        periodo_section = ""
        if date_from is not None and date_to is not None:
            periodo_section = (
                f"\nPERÍODO ANALISADO: {date_from} a {date_to}.\n"
                "Logo no início do texto, deixe claro em linguagem natural qual "
                "período de anos foi considerado nesta análise.\n"
            )

        enfase_section = ""
        if enfase:
            enfase_section = (
                f"\nÊNFASE DESTA ANÁLISE: {enfase}\n"
                "Dê destaque especial a esse aspecto no texto (ex.: mencione-o "
                "primeiro), sem deixar de cobrir os demais achados relevantes.\n"
            )

        pergunta_section = ""
        if pergunta_usuario:
            pergunta_section = f'\nPERGUNTA DO USUÁRIO: "{pergunta_usuario}"\n'

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
            "Você é um comunicador público especializado em saúde. "
            "Seu público são cidadãos comuns e servidores do legislativo de Sorocaba-SP "
            "que NÃO têm formação técnica em estatística ou economia.\n\n"
            "Sua tarefa: transformar os dados abaixo em um texto claro, acessível e "
            "informativo sobre como o dinheiro público investido em saúde se relaciona "
            "com os resultados de saúde da população.\n\n"
            "CONTEXTO IMPORTANTE:\n"
            "- O ano de 2020, 2021 e 2022 foi impactado pela pandemia de COVID-19, o que pode "
            "explicar variações atípicas nos gastos e indicadores desse período.\n"
            "- Sempre mencione esse fator ao interpretar dados que incluam 2020 até 2022.\n\n"
            "REGRAS DE LINGUAGEM:\n"
            "- Use linguagem simples e direta, como se explicasse para um vizinho\n"
            "- Evite jargões técnicos; quando inevitável, explique entre parênteses\n"
            "- Use exemplos concretos e comparações do cotidiano\n"
            "- Prefira 'relação forte/moderada/fraca' em vez de coeficientes numéricos\n"
            "- Traduza subfunções para linguagem comum:\n"
            "  301 = investimento em postos de saúde e equipes de família\n"
            "  302 = investimento em hospitais e atendimentos especializados\n"
            "  303 = investimento em medicamentos e insumos\n"
            "  305 = investimento em prevenção de epidemias e vigilância sanitária\n"
            "- Diga o que os números significam para a vida das pessoas\n"
            "- Cada correlação abaixo já vem com um campo \"leitura\" dizendo se o "
            "sinal (positivo/negativo) é DESEJÁVEL ou INDESEJÁVEL para aquele "
            "indicador especificamente — use esse campo como a fonte da verdade "
            "sobre se o resultado é bom ou ruim, não deduza pelo nome do "
            "indicador nem por associação (ex.: \"prevenção deveria reduzir "
            "mortes\") sem checar o campo \"leitura\": o sinal real da correlação "
            "pode contrariar essa intuição\n"
            f"{periodo_section}"
            f"{enfase_section}\n"
            "DADOS ANALISADOS:\n\n"
            "Relações encontradas entre gastos e resultados de saúde:\n"
            f"{correlacoes}\n\n"
            "Situações que chamam atenção (possíveis ineficiências ou boas práticas):\n"
            f"{anomalias}\n\n"
            "Como os gastos evoluíram ao longo dos anos:\n"
            f"{contexto_orcamentario}\n\n"
            f"{coverage_section}\n"
            f"{pergunta_section}"
            "COMO RESPONDER:\n"
            "- Responda como numa conversa direta, não como um relatório "
            "formal — sem títulos de seção tipo \"Resumo Executivo\" ou "
            "\"1. Análise das Correlações\", sem se sentir obrigado a cobrir "
            "todos os dados acima em ordem fixa.\n"
            "- Se há uma PERGUNTA DO USUÁRIO acima, responda ela "
            "diretamente logo de cara — isso é o mais importante do texto, "
            "não um parágrafo de abertura antes do \"relatório de verdade\". "
            "Sem pergunta específica, comece pelo achado mais relevante "
            "dos dados.\n"
            "- O tamanho e a profundidade da resposta devem combinar com o "
            "que foi perguntado: pergunta pontual → resposta curta e "
            "direta; pergunta ampla (ex.: \"compare todos os indicadores\") "
            "→ pode desenvolver mais, mas ainda em prosa natural, não uma "
            "lista de seções obrigatórias.\n"
            "- Só entre em detalhes/dados que realmente ajudam a responder "
            "o que foi perguntado — não é preciso mencionar todas as "
            "correlações e anomalias calculadas se a maioria não for "
            "relevante para a pergunta.\n"
            "- Ainda assim, nunca esconda uma lacuna de dados relevante "
            "nem inverta o sinal de uma correlação — precisão continua "
            "não-negociável, só a embalagem deixa de ser um formulário.\n"
            "- Seja direto e específico com os dados de Sorocaba. Se "
            "houver dados faltando que sejam relevantes pra resposta, "
            "mencione de forma transparente e explique como isso limita "
            "as conclusões."
        )

    def _generate_structured_text(
        self,
        correlacoes: list[dict],
        anomalias: list[dict],
        contexto_orcamentario: dict[str, Any],
        data_coverage: dict[str, Any] | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
    ) -> str:
        """Generate structured fallback text when LLM is unavailable (Req 7.3, 7.4).

        Includes: resumo executivo, cobertura de dados, correlações analysis,
        anomalias discussion, and contexto orçamentário.

        `date_from`/`date_to`, quando informados, viram uma linha explícita
        no Resumo Executivo — mesma garantia de transparência do caminho
        via LLM (ver `_build_prompt`), inclusive quando o período foi
        inferido pelo guardrail de intenção.
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
        if date_from is not None and date_to is not None:
            sections.append(f"Período analisado: {date_from} a {date_to}.\n")
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
                    f"  Spearman: {c.get('spearman', 0):.4f}\n"
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
                    "resultado insatisfatório, o que pode indicar ineficiência na "
                    "alocação de recursos públicos em saúde.\n"
                )
            if baixo_gasto:
                sections.append(
                    f"\nForam identificados {len(baixo_gasto)} caso(s) de baixo gasto com "
                    "resultado positivo, sugerindo eficiência ou fatores externos favoráveis.\n"
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
