"""
Falha semântica para a bateria: um agente de saúde entrega dados corrompidos.

Equivalente ao AutoInject de Huang et al. (2025, ICML): o agente continua
respondendo, mas parte dos registros da sua mensagem chega com valor errado.
Aqui, cerca de 20% dos registros com valor diferente de zero (o P_e = 0,2
padrão do artigo) têm o valor multiplicado por um fator sorteado entre 0,1,
0,5, 2 e 10. Valores inteiros continuam inteiros depois da corrupção, para
que a contagem não denuncie o erro por ter casas decimais.

Um agente defeituoso por vez, como no artigo: só as entregas do agente-alvo
(tipo do agente, por exemplo "sinan") são interceptadas. A interceptação fica
entre o agente e o nó que recebe a entrega (`query` e `reenviar`), então a
memória do agente continua limpa:

  transitória  só a primeira entrega é corrompida (mensagem corrompida em
               trânsito; o reenvio chega limpo)
  persistente  toda entrega é corrompida, com sorteio novo a cada vez

A semente é (analysis_id, tipo do agente, número da entrega). As duas
topologias de uma análise compartilham o analysis_id e recebem, portanto, a
mesma corrupção. O gabarito (registro limpo e valor corrompido de cada
posição) fica guardado por (analysis_id, topologia) para a bateria medir o
que a revisão das entregas detectou e o que chegou aos achados.

Módulo sem dependência da bateria, para ser testado isoladamente.
"""

from __future__ import annotations

import random
import threading
from contextlib import ExitStack
from typing import Any, Callable
from unittest import mock

from agents.revisao_entregas import chave_registro

FATORES = (0.1, 0.5, 2.0, 10.0)
FRACAO_PADRAO = 0.2

_TOPOLOGIA_POR_PREFIXO = {"star": "star", "hier": "hierarchical"}


def corromper(
    registros: list[dict[str, Any]], seed: str, fracao: float = FRACAO_PADRAO
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Corrompe uma fração dos registros, sem alterar os originais.

    Returns:
        (cópias entregues, corrompidos). Cada corrompido traz a posição
        ("indice"), a chave do registro, o registro limpo e o valor entregue.
    """
    copias = [dict(r) for r in registros]
    elegiveis = [
        i for i, r in enumerate(registros)
        if isinstance(r.get("valor"), (int, float)) and not isinstance(r.get("valor"), bool)
        and r.get("valor") != 0
    ]
    if not elegiveis:
        return copias, []
    rng = random.Random(seed)
    n = max(1, round(fracao * len(elegiveis)))
    corrompidos = []
    for i in sorted(rng.sample(elegiveis, min(n, len(elegiveis)))):
        limpo = registros[i]["valor"]
        novo = limpo * rng.choice(FATORES)
        if float(limpo).is_integer():
            novo = round(novo)
            if novo == limpo:  # 0,5 de 1, por exemplo — garante valor diferente
                novo = limpo * 2
        copias[i]["valor"] = novo
        corrompidos.append({
            "indice": i,
            "chave": list(chave_registro(registros[i])),
            "limpo": dict(registros[i]),
            "valor_corrompido": novo,
        })
    return copias, corrompidos


class InjetorCorrupcao:
    """Intercepta as entregas do agente-alvo e guarda o gabarito."""

    def __init__(self, alvo: str, persistente: bool = False, fracao: float = FRACAO_PADRAO):
        self.alvo = alvo
        self.persistente = persistente
        self.fracao = fracao
        self._lock = threading.Lock()
        self._entregas_por_agente: dict[tuple[str, str], int] = {}
        self._gabarito: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _identificar(agent_id: str) -> tuple[str | None, str | None]:
        partes = str(agent_id).split("-")
        if len(partes) < 2:
            return None, None
        return _TOPOLOGIA_POR_PREFIXO.get(partes[0]), partes[1]

    def interceptar(self, agente: Any, resultado: dict, analysis_id: str | None) -> dict:
        topologia, tipo = self._identificar(getattr(agente, "agent_id", ""))
        if tipo != self.alvo or topologia is None or not analysis_id:
            return resultado
        with self._lock:
            chave = (analysis_id, agente.agent_id)
            n = self._entregas_por_agente.get(chave, 0)
            self._entregas_por_agente[chave] = n + 1
        if n > 0 and not self.persistente:
            return resultado
        registros = (resultado or {}).get("indicadores") or []
        copias, corrompidos = corromper(registros, f"{analysis_id}|{tipo}|{n}", self.fracao)
        with self._lock:
            registro = self._gabarito.setdefault(
                (analysis_id, topologia), {"agente": tipo, "persistente": self.persistente, "entregas": []}
            )
            registro["entregas"].append({"numero": n, "registros": len(registros), "corrompidos": corrompidos})
        return dict(resultado, indicadores=copias)

    def gabarito(self, analysis_id: str) -> dict[str, dict[str, Any]]:
        """Gabarito das duas topologias de uma análise (vazio se o alvo não entregou)."""
        with self._lock:
            return {topo: g for (aid, topo), g in self._gabarito.items() if aid == analysis_id}

    def aplicar(self, stack: ExitStack, classes: tuple[type, ...]) -> None:
        """Instala a interceptação em `query` e `reenviar` das classes."""
        injetor = self
        for cls in classes:
            query_original = cls.query
            reenviar_original = cls.reenviar

            def _query(agente, analysis_id, *args, __orig=query_original, **kwargs):
                return injetor.interceptar(agente, __orig(agente, analysis_id, *args, **kwargs), analysis_id)

            def _reenviar(agente, __orig=reenviar_original):
                return injetor.interceptar(agente, __orig(agente), agente.working_memory.get("analysis_id"))

            stack.enter_context(mock.patch.object(cls, "query", _query))
            stack.enter_context(mock.patch.object(cls, "reenviar", _reenviar))


def _por_chave(corrompidos: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    return {tuple(c["chave"]): c for c in corrompidos}


def avaliar_contra_gabarito(
    resultado: dict[str, Any],
    gabarito: dict[str, Any] | None,
    analisar: Callable[[list[dict]], dict[str, list]],
    cruzar: Callable[[list[dict], list[dict]], list[dict]],
    consistencia: Callable[[dict, dict], dict],
) -> dict[str, Any]:
    """Mede, numa topologia, o que a revisão fez com os dados corrompidos.

    Args:
        resultado: Resultado final da topologia (indicadores, despesas,
            achados e o resumo "revisao").
        gabarito: Gabarito da topologia nesta análise, ou None se o agente-alvo
            não entregou (por exemplo, não foi acionado pela pergunta).
        analisar: Calcula correlações e anomalias a partir dos dados cruzados.
        cruzar: Cruza despesas e indicadores.
        consistencia: Compara os achados de dois resultados (devolve
            "all_identical").

    Returns:
        corrupcao_registros (corrompidos na primeira entrega), revisao_vp e
        revisao_fp (sinais que acertaram ou não um registro corrompido),
        contaminados_finais (registros que terminaram com valor corrompido),
        perdidos (registros corrompidos descartados) e achados_iguais_limpos
        (1 se os achados coincidem com os que os dados limpos produziriam).
    """
    sinais = (resultado.get("revisao") or {}).get("sinalizados") or []
    if not gabarito:
        return {
            "corrupcao_registros": 0, "revisao_vp": 0, "revisao_fp": len(sinais),
            "contaminados_finais": 0, "perdidos": 0, "achados_iguais_limpos": None,
        }

    alvo = gabarito.get("agente")
    entregas = gabarito.get("entregas") or []
    primeira = _por_chave(entregas[0]["corrompidos"]) if entregas else {}
    todos: dict[tuple, dict[str, Any]] = {}
    for e in entregas:
        todos.update(_por_chave(e["corrompidos"]))

    vp = sum(1 for s in sinais if s.get("agente") == alvo and tuple(s.get("chave") or ()) in todos)

    indicadores = resultado.get("indicadores") or []
    contaminados = 0
    limpos: list[dict[str, Any]] = []
    presentes: set[tuple] = set()
    for r in indicadores:
        chave = chave_registro(r)
        c = todos.get(chave)
        if c is not None:
            presentes.add(chave)
            if r.get("valor") != c["limpo"].get("valor"):
                contaminados += 1
            limpos.append(dict(c["limpo"]))
        else:
            limpos.append(r)
    perdidos = [c for chave, c in todos.items() if chave not in presentes]
    limpos += [dict(c["limpo"]) for c in perdidos]

    achados_limpos = analisar(cruzar(resultado.get("despesas") or [], limpos))
    iguais = consistencia(
        {"correlacoes": resultado.get("correlacoes") or [], "anomalias": resultado.get("anomalias") or []},
        achados_limpos,
    ).get("all_identical")

    return {
        "corrupcao_registros": len(primeira),
        "revisao_vp": vp,
        "revisao_fp": len(sinais) - vp,
        "contaminados_finais": contaminados,
        "perdidos": len(perdidos),
        "achados_iguais_limpos": 1 if iguais else 0,
    }
