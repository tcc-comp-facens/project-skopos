"""
Planejamento por LLM nos agentes coordenadores das duas topologias.

Em CoALA (Sumers et al., 2024), um agente de linguagem decide com o LLM
dentro de um ciclo de propor, avaliar e selecionar. Os coordenadores do
Skopos (o `OrquestradorEstrela`, o `CoordenadorGeral` e os supervisores
de saúde e de orçamento) seguem esse ciclo:

1. `propose_actions` deriva uma proposta determinística a partir do
   mapeamento fixo entre indicadores e agentes;
2. `evaluate_and_select` chama `planejar`, e o LLM confirma ou ajusta a
   proposta;
3. se a resposta for inválida ou a chamada falhar, vale a proposta.

É o mesmo padrão de `agents.domain.query_planning.arbitrar_dimensao` e de
`AgentePriorizacaoAnalitica`. O LLM só pode retirar itens da proposta ou
acrescentar itens do catálogo de subordinados daquele nó, e cada mudança
vem com uma justificativa curta. Assim nenhum agente fora da lista
permitida é acionado, e toda diferença de dados entre as topologias fica
rastreável até a decisão que a causou.

Quem escolhe subfunções orçamentárias (o orquestrador e o supervisor de
orçamento) recebe também o mapa de cruzamento, isto é, com quais
indicadores pedidos cada subfunção cruza. Um plano que acrescente uma
subfunção sem cruzamento com os indicadores do plano, ou que retire a
última subfunção que cruzava com um indicador pedido, é recusado e vale a
proposta. Sem isso o LLM decidia pelo nome da subfunção, consultava
despesas que nunca entram no cruzamento (covid levava às sete subfunções)
ou deixava um indicador pedido sem despesa para comparar.

Na estrela, o orquestrador planeja numa única chamada, sobre todos os
agentes de dados (planejamento centralizado). Na hierárquica, o
planejamento é decomposto pelos níveis da árvore, como nos sistemas
multiagentes com gerente e subordinados (MultiAgentBench, Zhu et al.,
2025). O coordenador escolhe os ramos e escreve um subobjetivo em
linguagem natural para cada um, e cada supervisor escolhe os seus
subordinados a partir desse subobjetivo.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_JUSTIFICATIVA = 300

OBS_CRUZAMENTO = (
    "A análise cruza as despesas de cada subfunção com os indicadores de saúde "
    "ligados a ela."
)

# Âncora contra a ampliação de escopo: o LLM tende a acrescentar tudo o que
# tem relação indireta com o tema (covid → internações, óbitos, vacinação),
# o que muda a pergunta respondida. Acréscimo só com menção explícita.
REGRA_ESCOPO = (
    "Só acrescente um subordinado se o objetivo citar explicitamente o tema ou o "
    "indicador dele. Uma relação indireta com o tema da pergunta (por exemplo, uma "
    "doença que também gera internações, óbitos ou vacinação) não justifica "
    "acréscimo, porque mudaria a pergunta respondida. Na dúvida, mantenha a proposta."
)

REGRA_CRUZAMENTO = (
    "A despesa de uma subfunção só é comparada com os indicadores com que ela cruza, "
    "indicados na lista. Não acrescente subfunção que não cruze com nenhum indicador "
    "pedido, e não retire a última subfunção que cruza com um indicador pedido, porque "
    "esse indicador ficaria sem despesa para comparar."
)


def objetivo_do_ramo(
    subobjetivo: str | None,
    intent_summary: str | None,
    health_params: list[str] | None,
    date_from: int | None,
    date_to: int | None,
) -> str:
    """Objetivo de um supervisor de ramo.

    O subobjetivo escrito pelo coordenador vem acompanhado dos indicadores que
    a interpretação extraiu da pergunta, que são a mesma base da proposta
    determinística. Sem subobjetivo, vale o objetivo da análise inteira."""
    if not subobjetivo:
        return objetivo_da_analise(intent_summary, health_params, date_from, date_to)
    indicadores = ", ".join(health_params or []) or "nenhum identificado"
    return f"{subobjetivo} Indicadores identificados na pergunta: {indicadores}."


def objetivo_da_analise(
    intent_summary: str | None,
    health_params: list[str] | None,
    date_from: int | None,
    date_to: int | None,
) -> str:
    """Objetivo da análise em linguagem natural, como a raiz o recebe."""
    pergunta = intent_summary or "(sem resumo da pergunta)"
    indicadores = ", ".join(health_params or []) or "nenhum identificado"
    return (
        f"{pergunta} Indicadores identificados na pergunta: {indicadores}. "
        f"Período: {date_from} a {date_to}."
    )


def catalogo_saude(chaves: list[str], indicador_to_agent: dict[str, str]) -> list[dict[str, str]]:
    """Catálogo dos agentes de saúde, na ordem de `chaves`."""
    from agents.analytical.redacao_ramos import NOME_SISTEMA

    catalogo = []
    for chave in chaves:
        tokens = sorted(t for t, agente in indicador_to_agent.items() if agente == chave)
        catalogo.append({
            "id": f"saude:{chave}",
            "descricao": f"{NOME_SISTEMA.get(chave, chave)}, indicadores {', '.join(tokens)}",
        })
    return catalogo


def catalogo_orcamento(
    subfuncoes: list[tuple[int, str]],
    cruzamentos: dict[str, set[str]] | None = None,
    pedidos: list[str] | None = None,
) -> list[dict[str, str]]:
    """Catálogo dos agentes orçamentários, um por subfunção.

    Com `cruzamentos` (id → indicadores com que a subfunção cruza) e
    indicadores pedidos, cada descrição diz com quais deles a subfunção
    cruza."""
    catalogo = []
    for codigo, nome in subfuncoes:
        cid = f"orcamento:{codigo}"
        descricao = f"despesas da subfunção {codigo} ({nome})"
        if cruzamentos is not None and pedidos:
            comuns = [p for p in pedidos if p in cruzamentos.get(cid, set())]
            descricao += (f", cruza com os indicadores pedidos {', '.join(comuns)}" if comuns
                          else ", não cruza com nenhum indicador pedido")
        catalogo.append({"id": cid, "descricao": descricao})
    return catalogo


def plano_fixo(
    agent_id: str,
    papel: str,
    objetivo: str,
    proposta: list[str],
    origem: str,
    subobjetivos: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "no": agent_id,
        "papel": papel,
        "objetivo": objetivo,
        "proposta": list(proposta),
        "escolhidos": list(proposta),
        "acrescimos": [],
        "retiradas": [],
        "justificativa": "",
        "subobjetivos": dict(subobjetivos or {}),
        "origem": origem,
    }


def planejar(
    *,
    agent_id: str,
    papel: str,
    objetivo: str,
    candidatos: list[dict[str, str]],
    proposta: list[str],
    use_llm: bool,
    subobjetivos_padrao: dict[str, str] | None = None,
    observacoes: str = "",
    cruzamentos: dict[str, set[str]] | None = None,
    pedidos: list[str] | None = None,
    fornece: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Decide quais subordinados acionar.

    Args:
        agent_id: Nó que planeja (vai no `caller` da chamada ao LLM).
        papel: Descrição curta do nó, usada no prompt.
        objetivo: O que o nó recebeu para cumprir (intenção do usuário ou
            subobjetivo vindo do nível de cima).
        candidatos: Catálogo fechado `[{"id", "descricao"}]`, na ordem em
            que os subordinados devem ser acionados.
        proposta: Ids escolhidos pelo mapeamento determinístico.
        use_llm: Sem LLM, vale a proposta.
        subobjetivos_padrao: Quando dado, o LLM também escreve um
            subobjetivo para cada subordinado escolhido que tenha um padrão
            aqui; os que faltarem na resposta usam o padrão.
        observacoes: Restrições do nó que o LLM precisa saber (por exemplo,
            dependências entre ramos).
        cruzamentos: Para cada id de subfunção, os indicadores (vocabulário
            de `health_params`) com que ela cruza. Ativa a regra de
            cruzamento, verificada em `_checar_cruzamentos`.
        pedidos: Indicadores identificados na pergunta.
        fornece: Para cada id de agente de saúde no catálogo, os indicadores
            que ele traz. Só a estrela, onde o mesmo plano escolhe saúde e
            orçamento, passa este mapa.

    Returns:
        Plano com "proposta", "escolhidos" (na ordem do catálogo),
        "acrescimos" e "retiradas" (cada um com "justificativa"),
        "justificativa", "subobjetivos" e "origem" ("proposta", "llm" ou
        "llm_failed_fallback").
    """
    ordem = [c["id"] for c in candidatos]
    proposta = [i for i in ordem if i in set(proposta)]
    padrao = {i: t for i, t in (subobjetivos_padrao or {}).items() if i in proposta}

    if not use_llm or not proposta:
        return plano_fixo(agent_id, papel, objetivo, proposta, "proposta", padrao)

    try:
        import core.llm_client as llm_client

        if cruzamentos is not None and pedidos:
            observacoes = f"{REGRA_CRUZAMENTO}\n{observacoes}".strip()
        prompt = _build_prompt(papel, objetivo, candidatos, proposta,
                               list(subobjetivos_padrao or {}), observacoes)
        raw = llm_client.generate(prompt, caller=f"{agent_id}:planejar")
        plano = _parse_plano(raw, ordem, proposta, subobjetivos_padrao)
        if cruzamentos is not None:
            _checar_cruzamentos(plano, cruzamentos, pedidos or [], fornece)
    except Exception as exc:
        logger.warning("Agent %s: planejamento via LLM falhou (%s), vale a proposta", agent_id, exc)
        return plano_fixo(agent_id, papel, objetivo, proposta, "llm_failed_fallback", padrao)

    plano.update({"no": agent_id, "papel": papel, "objetivo": objetivo, "origem": "llm"})
    logger.info(
        "Agent %s: plano via LLM, %d escolhidos (+%d, -%d)", agent_id,
        len(plano["escolhidos"]), len(plano["acrescimos"]), len(plano["retiradas"]),
    )
    return plano


def _checar_cruzamentos(
    plano: dict[str, Any],
    cruzamentos: dict[str, set[str]],
    pedidos: list[str],
    fornece: dict[str, set[str]] | None,
) -> None:
    """Recusa (ValueError) um plano que quebra a regra de cruzamento.

    Os indicadores que contam são os pedidos e, na estrela, só os pedidos
    cujo agente de saúde ficou no plano, mais os dos agentes de saúde que o
    LLM acrescentou. Uma subfunção acrescentada precisa cruzar com algum
    deles, e cada indicador pedido que cruzava com a proposta precisa
    continuar cruzando com o plano."""
    escolhidos = plano["escolhidos"]
    acrescidos = {m["id"] for m in plano["acrescimos"]}
    vivos = set(pedidos)
    indicadores = set(vivos)
    if fornece is not None:
        no_plano = set().union(*(fornece.get(i, set()) for i in escolhidos))
        vivos &= no_plano
        indicadores = vivos | set().union(*(fornece.get(i, set()) for i in acrescidos))

    for i in acrescidos:
        if i in cruzamentos and not cruzamentos[i] & indicadores:
            raise ValueError(f"'{i}' acrescentada sem cruzamento com os indicadores do plano")
    for indicador in sorted(vivos):
        cruzava = any(indicador in cruzamentos.get(i, set()) for i in plano["proposta"])
        if cruzava and not any(indicador in cruzamentos.get(i, set()) for i in escolhidos):
            raise ValueError(f"o indicador pedido '{indicador}' ficou sem subfunção para cruzar")


def _build_prompt(
    papel: str,
    objetivo: str,
    candidatos: list[dict[str, str]],
    proposta: list[str],
    com_subobjetivos: list[str],
    observacoes: str,
) -> str:
    linhas = [
        f'- "{c["id"]}": {c["descricao"]}' + (" [na proposta]" if c["id"] in proposta else "")
        for c in candidatos
    ]
    formato = (
        '{"acrescentar": [{"id": "<id>", "justificativa": "<frase>"}], '
        '"retirar": [{"id": "<id>", "justificativa": "<frase>"}], '
        '"justificativa": "<uma frase sobre o plano>"'
    )
    instrucao_sub = ""
    if com_subobjetivos:
        formato += ', "subobjetivos": {"<id>": "<frase>"}'
        instrucao_sub = (
            "Para cada um destes subordinados que ficar no plano, escreva um "
            f"subobjetivo em uma frase: {', '.join(com_subobjetivos)}. Diga o que ele "
            "deve entregar para cumprir o objetivo. O subobjetivo é a única "
            "instrução que ele recebe de você.\n"
        )
    formato += "}"
    return (
        f"Você é {papel} de um sistema multiagente que analisa despesas públicas "
        "de saúde e indicadores de saúde de Sorocaba-SP.\n\n"
        f"Objetivo recebido: {objetivo}\n\n"
        "Subordinados que você pode acionar (lista fechada, use os ids exatamente "
        "como aparecem):\n" + "\n".join(linhas) + "\n\n"
        "A proposta inicial vem do mapeamento fixo entre indicadores e agentes. "
        "Decida quais subordinados acionar. Você só pode retirar itens da proposta "
        "ou acrescentar itens da lista. Cada acréscimo ou retirada precisa de uma "
        "justificativa curta ligada ao objetivo. Se a proposta já atende ao "
        "objetivo, deixe as listas vazias. O plano não pode ficar vazio.\n"
        f"{REGRA_ESCOPO}\n"
        + (f"{observacoes}\n" if observacoes else "")
        + instrucao_sub
        + "\nResponda SOMENTE com um objeto JSON válido, sem texto antes ou depois, "
        f"sem markdown: {formato}"
    )


def _json_de(raw: str | None) -> Any:
    if not raw:
        raise ValueError("LLM retornou resposta vazia")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
    return json.loads(cleaned)


def _mudancas(data: dict, campo: str, catalogo: set[str], validos: set[str]) -> list[dict[str, str]]:
    """Lê `acrescentar` ou `retirar`. Id fora do catálogo invalida a resposta;
    id do catálogo que não muda nada (acrescentar o que já está na proposta,
    retirar o que não está) é ignorado."""
    itens = data.get(campo) or []
    if not isinstance(itens, list):
        raise ValueError(f"campo '{campo}' inválido")
    mudancas = []
    for item in itens:
        if not isinstance(item, dict) or item.get("id") not in catalogo:
            raise ValueError(f"'{campo}' com id fora da lista permitida: {item!r}")
        if item["id"] not in validos:
            continue
        justificativa = item.get("justificativa")
        if not isinstance(justificativa, str) or not justificativa.strip():
            raise ValueError(f"'{campo}' sem justificativa: {item!r}")
        mudancas.append({"id": item["id"], "justificativa": justificativa.strip()[:MAX_JUSTIFICATIVA]})
    return mudancas


def _parse_plano(
    raw: str | None,
    ordem: list[str],
    proposta: list[str],
    subobjetivos_padrao: dict[str, str] | None,
) -> dict[str, Any]:
    data = _json_de(raw)
    if not isinstance(data, dict):
        raise ValueError("resposta do LLM não é um objeto")

    acrescimos = _mudancas(data, "acrescentar", set(ordem), set(ordem) - set(proposta))
    retiradas = _mudancas(data, "retirar", set(ordem), set(proposta))
    fora = {m["id"] for m in retiradas}
    dentro = set(proposta) | {m["id"] for m in acrescimos}
    escolhidos = [i for i in ordem if i in dentro and i not in fora]
    if not escolhidos:
        raise ValueError("o plano ficou vazio")

    subobjetivos: dict[str, str] = {}
    if subobjetivos_padrao is not None:
        recebidos = data.get("subobjetivos") or {}
        if not isinstance(recebidos, dict):
            raise ValueError("campo 'subobjetivos' inválido")
        for i in escolhidos:
            if i not in subobjetivos_padrao:
                continue
            texto = recebidos.get(i)
            if isinstance(texto, str) and texto.strip():
                subobjetivos[i] = texto.strip()[:MAX_JUSTIFICATIVA]
            elif i in subobjetivos_padrao:
                subobjetivos[i] = subobjetivos_padrao[i]

    justificativa = data.get("justificativa")
    return {
        "proposta": list(proposta),
        "escolhidos": escolhidos,
        "acrescimos": acrescimos,
        "retiradas": retiradas,
        "justificativa": justificativa.strip()[:MAX_JUSTIFICATIVA] if isinstance(justificativa, str) else "",
        "subobjetivos": subobjetivos,
    }
