"""
Material entregue ao redator, em forma de trechos de texto.

Uma fonte única para as duas avaliações da qualidade do texto: a fidelidade
do RAGAS (`core/ragas_metrics.py`) e a fidelidade numérica
(`core/quality_metrics.compute_numeric_fidelity`). As duas precisam julgar o
texto contra exatamente o que o redator recebeu, então os trechos são
montados num lugar só. Este módulo não importa a biblioteca ragas, para a
métrica determinística continuar leve e gratuita.
"""

from __future__ import annotations

import typing as t

# Nomes das subfunções orçamentárias, para os chunks de contexto ficarem
# legíveis para o juiz (que só vê texto, não o dado estruturado).
SUBFUNCAO_NOMES: dict[int, str] = {
    122: "Administração Geral",
    301: "Atenção Básica",
    302: "Assistência Hospitalar e Ambulatorial",
    303: "Suporte Profilático e Terapêutico",
    304: "Vigilância Sanitária",
    305: "Vigilância Epidemiológica",
    306: "Alimentação e Nutrição",
}


def _subfuncao_label(codigo: t.Any) -> str:
    try:
        nome = SUBFUNCAO_NOMES.get(int(codigo))
    except (TypeError, ValueError):
        nome = None
    return f"{codigo} ({nome})" if nome else str(codigo)


def build_contexts(
    result: dict[str, t.Any],
    date_from: int | None = None,
    date_to: int | None = None,
) -> list[str]:
    """Um chunk de texto por informação entregue ao sintetizador.

    O conjunto tem que espelhar **tudo** que o gerador recebeu em
    `sintetizador._build_prompt` — não só os achados numéricos. Uma versão
    anterior mandava ao juiz só subfunção/indicador/coeficiente e omitia a
    `leitura` da correlação, o `n_pontos` e a `descricao` da anomalia (que
    carrega o gasto em R$ e o valor do indicador). O texto citava esses
    dados corretamente e era reprovado, porque a evidência simplesmente
    não estava no contexto do juiz — a fidelidade media a lacuna entre os
    dois contextos, não a fidelidade do texto.

    Pelo mesmo motivo entram aqui as afirmações que o prompt **injeta** no
    gerador (período, pandemia, tradução das subfunções, lacunas de dados):
    o prompt manda repeti-las, então elas fazem parte do que sustenta a
    geração. Excluí-las garantiria infidelidade artificial.

    A ordem é estável para que a seleção sob teto seja determinística e as
    duas topologias sejam tratadas do mesmo jeito.
    """
    contexts: list[str] = []

    if date_from is not None and date_to is not None:
        contexts.append(
            f"Período analisado: a análise cobre os anos de {date_from} a {date_to}."
        )

    for c in result.get("correlacoes") or []:
        chunk = (
            f"Correlação: subfunção {_subfuncao_label(c.get('subfuncao'))} × "
            f"{c.get('tipo_indicador', '?')} — coeficiente de Spearman "
            f"{c.get('spearman', '?')}, classificação {c.get('classificacao', '?')}, "
            f"calculada sobre {c.get('n_pontos', '?')} pontos."
        )
        # `leitura` não existe no ramo n<2 de agents/analytical/analitico.py.
        leitura = c.get("leitura")
        if leitura:
            chunk += f" Leitura do sinal: {leitura}."
        contexts.append(chunk)

    for a in result.get("anomalias") or []:
        chunk = (
            f"Anomalia em {a.get('ano', '?')}: subfunção "
            f"{_subfuncao_label(a.get('subfuncao'))} × {a.get('tipo_indicador', '?')} "
            f"— tipo {a.get('tipo_anomalia', '?')}."
        )
        # A descrição traz o valor gasto em R$ e o valor do indicador; sem
        # ela nenhuma cifra citada no texto é verificável.
        descricao = a.get("descricao")
        if descricao:
            chunk += f" {descricao}."
        contexts.append(chunk)

    for subfuncao, dados in (result.get("contexto_orcamentario") or {}).items():
        if not isinstance(dados, dict):
            continue
        contexts.append(
            f"Contexto orçamentário da subfunção {_subfuncao_label(subfuncao)}: "
            f"tendência {dados.get('tendencia', '?')}, variação média "
            f"{dados.get('variacao_media_percentual', '?')}%."
        )

    contexts.extend(linhas_series(result.get("despesas") or [], result.get("indicadores") or []))
    contexts.extend(_coverage_contexts(result.get("data_coverage") or {}))
    contexts.extend(_framing_contexts())

    return contexts


def _fmt_valor(valor: float) -> str:
    return f"{int(valor):,}" if float(valor).is_integer() else f"{valor:,.2f}"


def linhas_series(
    despesas: list[dict[str, t.Any]],
    indicadores: list[dict[str, t.Any]],
) -> list[str]:
    """Séries anuais entregues ao redator e ao juiz, uma linha por série.

    Gasto anual de cada subfunção e valor anual de cada indicador, os mesmos
    dados usados nos cálculos. Sem elas, o redator só via os anos que viraram
    anomalia: numa pergunta sobre dengue de 2020 a 2023 ele escreveu que "não
    há o número de 2020 e 2022", dados que existiam, e não conseguia responder
    "quantos casos". A mesma função monta o prompt e o contexto do juiz, para
    os dois receberem o mesmo texto.

    Quebras por dimensão (faixa etária, sexo...) viram séries próprias.
    Registros sem valor ficam de fora.
    """
    def agrupar(registros, chave):
        series: dict[tuple, dict[int, float]] = {}
        for r in registros:
            if not isinstance(r, dict) or r.get("valor") is None or r.get("ano") is None:
                continue
            series.setdefault(chave(r), {})[r["ano"]] = r["valor"]
        return series

    linhas: list[str] = []
    for (subfuncao, dimensao), anos in agrupar(
        despesas, lambda r: (r.get("subfuncao"), r.get("dimensao_valor"))
    ).items():
        quebra = f" ({dimensao})" if dimensao else ""
        valores = "; ".join(f"{ano}: R$ {anos[ano]:,.2f}" for ano in sorted(anos))
        linhas.append(f"Gasto anual da subfunção {_subfuncao_label(subfuncao)}{quebra}: {valores}.")
    for (tipo, dimensao), anos in agrupar(
        indicadores, lambda r: (r.get("tipo"), r.get("dimensao_valor"))
    ).items():
        quebra = f" ({dimensao})" if dimensao else ""
        valores = "; ".join(f"{ano}: {_fmt_valor(anos[ano])}" for ano in sorted(anos))
        linhas.append(f"Valor anual do indicador {tipo}{quebra}: {valores}.")
    return linhas


def _coverage_contexts(coverage: dict[str, t.Any]) -> list[str]:
    """Chunks sobre completude e lacunas dos dados.

    O prompt instrui o gerador a "mencionar explicitamente quais dados
    estão faltando e como isso limita as conclusões", então o texto vai
    falar de lacunas — que precisam estar no contexto do juiz.
    """
    if not isinstance(coverage, dict):
        return []

    contexts: list[str] = []
    summary = coverage.get("summary") or {}
    if summary:
        contexts.append(
            "Cobertura dos dados: completude das despesas "
            f"{summary.get('despesas_completeness', 1.0):.0%}, completude dos "
            f"indicadores {summary.get('indicadores_completeness', 1.0):.0%}."
        )
    for gap in coverage.get("gaps") or []:
        descricao = (gap or {}).get("description")
        if descricao:
            contexts.append(f"Lacuna de dados identificada: {descricao}.")
    return contexts


def _framing_contexts() -> list[str]:
    """Afirmações que o prompt do sintetizador fornece ao gerador.

    Importadas de `agents.analytical.sintetizador` em vez de reescritas
    aqui: se as duas cópias divergirem, o texto passa a ser reprovado por
    afirmar exatamente o que o prompt mandou afirmar.
    """
    from agents.analytical.sintetizador import CONTEXTO_PANDEMIA, TRADUCAO_SUBFUNCOES

    contexts = [f"Contexto fornecido ao gerador: {CONTEXTO_PANDEMIA}"]
    contexts.extend(
        f"Equivalência informada ao gerador: a subfunção {codigo} corresponde a "
        f"{traducao}."
        for codigo, traducao in sorted(TRADUCAO_SUBFUNCOES.items())
    )
    return contexts
