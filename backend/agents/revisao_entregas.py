"""
Revisão das entregas dos agentes de dados pelo nó que as recebe.

Inspirada em Huang et al. (2025), "On the Resilience of LLM-Based
Multi-Agent Collaboration with Faulty Agents" (ICML): a resiliência que a
literatura atribui à hierarquia vem de um agente de nível superior capaz de
julgar o que os subordinados entregam. Aqui essa capacidade existe nas duas
topologias, pela mesma regra: o nó que recebe as entregas dos agentes de
dados (orçamento e saúde) as revisa numa única chamada ao LLM.

  estrela      OrquestradorEstrela revisa saúde e orçamento numa chamada
  hierárquica  SupervisorSaude revisa os indicadores e SupervisorOrcamento
               revisa as despesas, uma chamada cada (o CoordenadorGeral não
               recebe entregas de agentes de dados e não revisa)

A capacidade é a mesma; muda só onde ela fica e, com isso, quanto contexto
cada chamada carrega.

Fluxo, no estilo do mecanismo de contestação de Huang et al.:

  1. `revisar_entregas` monta uma série por indicador/subfunção (e fatia de
     dimensão) e pede ao LLM os valores incompatíveis com a própria série.
  2. `resolver_suspeitos` contesta cada entrega com valor sinalizado: pede
     ao agente que reenvie a entrega (`AgenteCoALA.reenviar`). Valor igual
     no reenvio é confirmado (o dado é real e o sinal era falso positivo);
     valor diferente pede um segundo reenvio e vale a maioria entre os três;
     sem maioria, o registro é descartado.

O LLM só aponta suspeitos. Ele nunca escreve valores: toda correção vem de
um reenvio do próprio agente. Uma falha na chamada (LLM indisponível,
resposta inválida) deixa os dados passarem sem alteração e fica registrada
no resumo — a revisão nunca interrompe a análise.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Campos de registro revisados. Indicadores vêm dos agentes de saúde e
# despesas dos agentes orçamentários; os dois têm `ano` e `valor`.
CAMPOS = ("indicadores", "despesas")

_NUMERICOS_DO_RESUMO = (
    "chamadas", "registros_revisados", "series", "confirmados", "corrigidos",
    "descartados", "nao_verificados", "reenvios",
)


def chave_registro(registro: dict[str, Any]) -> tuple:
    """Identidade de um registro dentro da sua série.

    Indicador: (tipo, ano, dimensão, mês). Despesa: (subfunção, ano,
    dimensão, mês). O mês só existe no grão mensal; fica None nos demais.
    """
    serie = registro.get("tipo")
    if serie is None:
        serie = registro.get("subfuncao")
    return (serie, registro.get("ano"), registro.get("dimensao_valor"), registro.get("mes"))


def resumo_vazio(revisores: list[str] | None = None) -> dict[str, Any]:
    """Resumo de uma revisão que não chegou a chamar o LLM."""
    return {
        "revisores": list(revisores or []),
        "chamadas": 0,
        "registros_revisados": 0,
        "series": 0,
        "sinalizados": [],
        "confirmados": 0,
        "corrigidos": 0,
        "descartados": 0,
        "nao_verificados": 0,
        "reenvios": 0,
        "falhou": False,
        "erro": None,
    }


def combinar_resumos(resumos: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Soma os resumos de vários revisores (supervisores da hierárquica)."""
    total = resumo_vazio()
    erros = []
    for r in resumos:
        if not r:
            continue
        total["revisores"] += r.get("revisores", [])
        for campo in _NUMERICOS_DO_RESUMO:
            total[campo] += r.get(campo, 0) or 0
        total["sinalizados"] += r.get("sinalizados", [])
        total["falhou"] = total["falhou"] or bool(r.get("falhou"))
        if r.get("erro"):
            erros.append(r["erro"])
    total["erro"] = "; ".join(erros) or None
    return total


# ---------------------------------------------------------------------------
# Montagem das séries e do prompt
# ---------------------------------------------------------------------------


def _formatar_valor(valor: Any) -> str:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return str(valor)
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:.2f}"


def _rotulo_serie(entrega: dict[str, Any], registro: dict[str, Any]) -> str:
    if entrega["campo"] == "despesas":
        nome = registro.get("subfuncaoNome")
        rotulo = f"orçamento/subfunção {registro.get('subfuncao')}" + (f" ({nome})" if nome else "")
    else:
        rotulo = f"{entrega['agente']}/{registro.get('tipo')}"
    dim = registro.get("dimensao_valor")
    return rotulo + (f" [{dim}]" if dim is not None else "")


def montar_series(entregas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa os registros das entregas em séries ordenadas por ano.

    Cada série recebe um id curto (S1, S2, ...) e guarda, para cada ponto,
    a posição do registro (índice da entrega, índice do registro) — é por
    ela que o sinal do LLM volta ao registro, sem depender de a chave ser
    única.
    """
    series: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
    for i, entrega in enumerate(entregas):
        for j, reg in enumerate(entrega.get("registros") or []):
            rotulo = _rotulo_serie(entrega, reg)
            chave = (entrega["campo"], rotulo, reg.get("mes") is not None)
            serie = series.get(chave)
            if serie is None:
                serie = series[chave] = {"rotulo": rotulo, "pontos": []}
            serie["pontos"].append({"ano": reg.get("ano"), "mes": reg.get("mes"),
                                    "valor": reg.get("valor"), "pos": (i, j)})
    resultado = []
    for n, serie in enumerate(series.values(), start=1):
        serie["id"] = f"S{n}"
        serie["pontos"].sort(key=lambda p: (p["ano"] is None, p["ano"] or 0, p["mes"] or 0))
        resultado.append(serie)
    return resultado


def _linha_serie(serie: dict[str, Any]) -> str:
    pontos = "; ".join(
        f"{p['ano']}{'-' + str(p['mes']).zfill(2) if p['mes'] is not None else ''}="
        f"{_formatar_valor(p['valor'])}"
        for p in serie["pontos"]
    )
    return f"{serie['id']}: {serie['rotulo']} → {pontos}"


def montar_prompt(
    series: list[dict[str, Any]],
    intent_summary: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
) -> str:
    periodo = f"de {date_from} a {date_to}" if date_from and date_to else "do período pedido"
    linhas = "\n".join(_linha_serie(s) for s in series)
    return (
        "Você revisa os dados que agentes de consulta acabaram de entregar, antes "
        "que eles sejam cruzados e analisados. Os dados vêm de uma base de "
        "indicadores de saúde pública (DATASUS) e de despesas orçamentárias da "
        f"saúde do município de Sorocaba-SP, {periodo}.\n\n"
        f"Intenção do usuário nesta análise: {intent_summary or '(não informada)'}\n\n"
        "Cada linha é uma série, no formato `id: origem/indicador [fatia] → "
        "ano=valor; ano=valor; ...`:\n"
        f"{linhas}\n\n"
        "Aponte somente valores que pareçam erro de transmissão ou de registro, "
        "isto é, incompatíveis com o restante da própria série, como um valor "
        "várias vezes maior ou menor que os vizinhos sem explicação plausível. "
        "Variações reais existem e não devem ser apontadas, como epidemias, a "
        "pandemia de COVID-19 entre 2020 e 2022 e mudanças na classificação do "
        "orçamento. Na dúvida, não aponte.\n\n"
        "Responda SOMENTE com um objeto JSON válido, sem texto antes ou depois, "
        'sem markdown: {"suspeitos": [{"serie": "<id da série>", "ano": <ano>, '
        '"motivo": "<frase curta>"}]}. Se nada parecer errado, responda '
        '{"suspeitos": []}.'
    )


def _parse_resposta(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        raise ValueError("LLM retornou resposta vazia")
    texto = raw.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if "\n" in texto:
            texto = texto.split("\n", 1)[1]
    dados = json.loads(texto)
    if not isinstance(dados, dict) or not isinstance(dados.get("suspeitos"), list):
        raise ValueError("resposta do LLM não tem o formato esperado")
    return [s for s in dados["suspeitos"] if isinstance(s, dict)]


def _ano(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Revisão e contestação
# ---------------------------------------------------------------------------


def revisar_entregas(
    entregas: list[dict[str, Any]],
    *,
    caller: str,
    intent_summary: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pede ao LLM os valores suspeitos das entregas, numa única chamada.

    Args:
        entregas: Uma entrada por agente que entregou, com "agente" (tipo do
            agente), "campo" ("indicadores" ou "despesas"), "registros" e
            "instancia" (o agente, usado depois para pedir reenvio).
        caller: Identificação da chamada nos logs e na contabilidade de tokens.

    Returns:
        (sinalizados, resumo). Cada sinalizado traz a posição do registro
        ("entrega", "indice") e o que o LLM disse ("serie", "ano", "motivo").
    """
    import core.llm_client as llm_client

    resumo = resumo_vazio()
    series = montar_series(entregas)
    resumo["series"] = len(series)
    resumo["registros_revisados"] = sum(len(s["pontos"]) for s in series)
    if not series:
        return [], resumo

    prompt = montar_prompt(series, intent_summary, date_from, date_to)
    resumo["chamadas"] = 1
    try:
        raw = llm_client.generate(prompt, caller=caller)
        suspeitos = _parse_resposta(raw)
    except Exception as exc:  # noqa: BLE001 — a revisão nunca derruba a análise
        resumo["falhou"] = True
        resumo["erro"] = f"{type(exc).__name__}: {exc}"
        logger.warning("Revisão [%s]: sem resultado utilizável — %s", caller, resumo["erro"])
        return [], resumo

    por_id = {s["id"]: s for s in series}
    sinalizados: list[dict[str, Any]] = []
    vistos: set[tuple[int, int]] = set()
    for s in suspeitos:
        serie = por_id.get(str(s.get("serie", "")).strip())
        ano = _ano(s.get("ano"))
        if serie is None or ano is None:
            continue
        for ponto in serie["pontos"]:
            if ponto["ano"] != ano or ponto["pos"] in vistos:
                continue
            vistos.add(ponto["pos"])
            i, j = ponto["pos"]
            sinalizados.append({
                "entrega": i, "indice": j,
                "chave": list(chave_registro(entregas[i]["registros"][j])),
                "serie": serie["id"], "rotulo": serie["rotulo"], "ano": ano,
                "valor": ponto["valor"], "motivo": str(s.get("motivo", ""))[:200],
            })
    logger.info(
        "Revisão [%s]: %d série(s), %d registro(s), %d sinalizado(s)",
        caller, resumo["series"], resumo["registros_revisados"], len(sinalizados),
    )
    return sinalizados, resumo


def _registro_reenviado(
    reenvio: dict[str, list[dict]] | None, campo: str, indice: int, original: dict
) -> dict | None:
    """O registro na mesma posição do reenvio, se for o mesmo registro."""
    lista = (reenvio or {}).get(campo) or []
    if indice >= len(lista) or not isinstance(lista[indice], dict):
        return None
    candidato = lista[indice]
    if chave_registro(candidato) != chave_registro(original):
        return None
    return candidato


def resolver_suspeitos(
    entregas: list[dict[str, Any]],
    sinalizados: list[dict[str, Any]],
    resumo: dict[str, Any],
    no: Any = None,
) -> list[list[dict[str, Any]]]:
    """Contesta os registros sinalizados e devolve os registros finais.

    Para cada entrega com sinal, pede reenvio ao agente. Mesmo valor no
    reenvio confirma o registro; valor diferente pede um segundo reenvio e
    vale a maioria entre original, primeiro e segundo reenvio; sem maioria,
    o registro é descartado. Se o reenvio falhar ou não trouxer o registro,
    o original é mantido e contado como não verificado.

    Os dicionários de registro nunca são alterados: correções criam cópias.
    `resumo` é atualizado no lugar e cada sinalizado ganha "decisao".
    Com `no` (o agente que revisa), cada contestação a um agente fica no
    registro de mensagens da topologia como `request` e `inform`.

    Returns:
        Uma lista de registros finais por entrega, na ordem original.
    """
    finais = [list(e.get("registros") or []) for e in entregas]
    por_entrega: dict[int, list[dict[str, Any]]] = {}
    for s in sinalizados:
        por_entrega.setdefault(s["entrega"], []).append(s)

    for i, sinais in por_entrega.items():
        entrega = entregas[i]
        campo = entrega["campo"]
        instancia = entrega.get("instancia")
        originais = entrega.get("registros") or []
        reenvios: list[dict | None] = []

        def reenviar(n: int) -> dict | None:
            while len(reenvios) < n:
                resumo["reenvios"] += 1
                try:
                    if no is not None:
                        reenvios.append(no._delegar(
                            entrega.get("agente", ""), getattr(instancia, "agent_id", "?"),
                            instancia.reenviar,
                            pedido={"contestacao": entrega.get("campo")},
                        ))
                        continue
                    reenvios.append(instancia.reenviar())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Revisão: reenvio de %s falhou — %s", entrega.get("agente"), exc)
                    reenvios.append(None)
            return reenvios[n - 1]

        descartar: set[int] = set()
        for s in sinais:
            j = s["indice"]
            original = originais[j]
            v0 = original.get("valor")
            r1 = _registro_reenviado(reenviar(1), campo, j, original)
            if r1 is None:
                s["decisao"] = "nao_verificado"
                resumo["nao_verificados"] += 1
                continue
            if r1.get("valor") == v0:
                s["decisao"] = "confirmado"
                resumo["confirmados"] += 1
                continue
            r2 = _registro_reenviado(reenviar(2), campo, j, original)
            if r2 is not None and r2.get("valor") == r1.get("valor"):
                finais[i][j] = dict(original, valor=r1.get("valor"))
                s["decisao"] = "corrigido"
                s["valor_final"] = r1.get("valor")
                resumo["corrigidos"] += 1
            elif r2 is not None and r2.get("valor") == v0:
                s["decisao"] = "confirmado"
                resumo["confirmados"] += 1
            else:
                descartar.add(j)
                s["decisao"] = "descartado"
                resumo["descartados"] += 1
        if descartar:
            finais[i] = [r for j, r in enumerate(finais[i]) if j not in descartar]

    resumo["sinalizados"] = [
        {k: v for k, v in s.items() if k not in ("entrega", "indice")}
        | {"agente": entregas[s["entrega"]].get("agente")}
        for s in sinalizados
    ]
    return finais


def revisar_e_resolver(
    entregas: list[dict[str, Any]],
    *,
    revisor: str,
    caller: str,
    intent_summary: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    no: Any = None,
) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
    """Revisão completa de um nó: uma chamada ao LLM e a contestação.

    Returns:
        (registros finais por entrega, resumo da revisão).
    """
    sinalizados, resumo = revisar_entregas(
        entregas, caller=caller, intent_summary=intent_summary,
        date_from=date_from, date_to=date_to,
    )
    resumo["revisores"] = [revisor]
    finais = resolver_suspeitos(entregas, sinalizados, resumo, no)
    return finais, resumo


def revisar_memoria_do_no(
    working_memory: dict[str, Any], *, revisor: str, caller: str, no: Any = None
) -> dict[str, Any]:
    """Executa a revisão de um nó sobre as entregas guardadas na sua memória.

    Lê `_entregas`, revisa, contesta, reconstrói `indicadores`/`despesas` e
    grava o resumo em `working_memory["revisao"]`. Qualquer exceção deixa os
    dados como chegaram e é registrada no resumo.
    """
    entregas = working_memory.get("_entregas", [])
    try:
        finais, resumo = revisar_e_resolver(
            entregas,
            revisor=revisor,
            caller=caller,
            intent_summary=working_memory.get("intent_summary"),
            date_from=working_memory.get("date_from"),
            date_to=working_memory.get("date_to"),
            no=no,
        )
        aplicar_na_memoria(working_memory, entregas, finais)
    except Exception as exc:  # noqa: BLE001 — a revisão nunca derruba a análise
        logger.error("Revisão [%s]: falhou — %s", caller, exc)
        resumo = resumo_vazio([revisor])
        resumo.update(falhou=True, erro=f"{type(exc).__name__}: {exc}")
    working_memory["revisao"] = resumo
    return resumo


def aplicar_na_memoria(
    working_memory: dict[str, Any],
    entregas: list[dict[str, Any]],
    finais: list[list[dict[str, Any]]],
) -> None:
    """Reconstrói `indicadores`/`despesas` da memória de trabalho do nó.

    As listas da memória são a concatenação das entregas, na ordem em que
    chegaram, então basta concatenar os registros finais de novo.
    """
    for campo in CAMPOS:
        if any(e["campo"] == campo for e in entregas):
            working_memory[campo] = [
                r for e, regs in zip(entregas, finais) if e["campo"] == campo for r in regs
            ]


def registrar_entrega(
    working_memory: dict[str, Any], agente: str, instancia: Any, resultado: dict[str, Any],
    campos: tuple[str, ...] = CAMPOS,
) -> None:
    """Guarda uma entrega recebida para a revisão posterior do nó."""
    for campo in campos:
        registros = resultado.get(campo)
        if registros:
            working_memory.setdefault("_entregas", []).append({
                "agente": agente, "campo": campo, "instancia": instancia,
                "registros": list(registros),
            })
