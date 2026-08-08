"""
Supervisores da arquitetura hierárquica (Nível 1).

Quatro supervisores especializados coordenam os agentes de nível 2:

- **SupervisorOrcamento** coordena agentes `AgenteOrcamentoSubfuncao`
  (1 por subfunção, 7 no total — PLANO_NOVO_MODELO_DADOS.md §5).
- **SupervisorSaude** coordena os 5 agentes de saúde (SINAN, SIH, SIM,
  SI-PNI, COVID — 1 por Sistema de Informação). Substitui o antigo
  `SupervisorDominio`, que combinava despesas+indicadores num único
  nível — separado aqui em SupervisorOrcamento (despesas) vs.
  SupervisorSaude (indicadores), decisão tomada com o usuário na Fase 1.
- **SupervisorAnalitico** coordena os agentes analíticos (correlação +
  anomalias consolidadas em `AgenteAnalitico`, priorização, síntese).
- **SupervisorContexto** coordena `AgenteContextoOrcamentario` (tendências
  de todas as subfunções presentes nas despesas recebidas).

Todos implementam ``receive_from_peer`` para comunicação lateral
direta entre supervisores do mesmo nível, sem intermediação do
CoordenadorGeral (Reqs 10.5, 10.6) — tratada como uma ação externa de
comunicação que escreve numa região nomeada da working memory
(``peer_data``), sem passar pelo ciclo `propose_actions`/`execute` de
quem recebe (é o par que empurra o dado, não o receptor que o busca).

Cada supervisor executa de fato pelo ciclo CoALA: `run()` configura a
working memory e delega a `run_coala_cycle()`, que percorre as
macro-ações registradas em `procedural_memory` na ordem proposta por
`propose_actions` — a mesma degradação graciosa e coleta de métricas por
agente subordinado que o pipeline imperativo anterior já tinha.

Requisitos: 10.2, 10.3, 10.4, 10.5, 10.6, 10.7
"""

from __future__ import annotations

import logging
import time
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents.base import ActionFailure, AgenteCoALA
from agents.data_crossing import cross_domain_data, deduplicate_despesas, detect_data_gaps
from agents.domain.agente_covid import AgenteCOVID
from agents.domain.agente_sih import AgenteSIH
from agents.domain.agente_sim import AgenteSIM
from agents.domain.agente_sipni import AgenteSIPNI
from agents.domain.agente_sinasc import AgenteSINASC
from agents.domain.agente_sia import AgenteSIA
from agents.domain.agente_cnes import AgenteCNES
from agents.domain.agente_sinan import AgenteSINAN
from agents.domain.agente_orcamento import AgenteOrcamentoSubfuncao
from agents.analytical.analitico import AgenteAnalitico
from agents.analytical.priorizacao import AgentePriorizacaoAnalitica
from agents.analytical.sintetizador import TextSynthesizer
from agents.context.contexto_orcamentario import AgenteContextoOrcamentario
from core import claim_verifier
from core.metrics import MetricsCollector
from core.streaming_adapter import StreamingAdapter

if TYPE_CHECKING:
    from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Mapeamento: tipo de indicador → agente de saúde responsável. Os 9
# subtipos SINAN roteiam para "sinan" (AgenteSINAN). "covid" roteia para
# o AgenteCOVID dedicado (Fase 2 — cobre casos e óbitos, substitui a
# metade "covid" do antigo AgenteVigilanciaEpidemiologica, que só
# consultava casos). "internacoes"/"vacinacao"/"mortalidade" preservam os
# tokens legados de vocabulário (vocabulário novo por sistema é Fase 2,
# item 5), mas agora roteiam para os agentes decompostos SIH/SIPNI/SIM.
INDICADOR_TO_AGENT: dict[str, str] = {
    "dengue": "sinan",
    "chikungunya": "sinan",
    "sifilis_adquirida": "sinan",
    "sifilis_gestante": "sinan",
    "sifilis_congenita": "sinan",
    "coqueluche": "sinan",
    "hepatites_virais": "sinan",
    "tuberculose": "sinan",
    "hanseniase": "sinan",
    "covid": "covid",
    "internacoes": "sih",
    "vacinacao": "sipni",
    "mortalidade": "sim",
    "nascidos_vivos": "sinasc",
    "producao_ambulatorial": "sia",
    "leitos": "cnes",
    "profissionais": "cnes",
    "estabelecimentos_por_tipo": "cnes",
    "ocupacoes": "cnes",
    "equipes_saude": "cnes",
    "tipo_atendimento": "cnes",
    "leitos_consultorios": "cnes",
    "equipamentos": "cnes",
    "estabelecimentos_nivel_atencao": "cnes",
    "estabelecimentos_servico_classificacao": "cnes",
    "estabelecimentos_habilitacao": "cnes",
    "estabelecimentos_vigilancia_epidemiologica": "cnes",
}

# Ordem fixa de avaliação dos agentes de saúde
_SAUDE_AGENT_KEY_ORDER: list[str] = [
    "covid", "sih", "sipni", "sim", "sinan", "sinasc", "sia", "cnes",
]
_AGENT_KEY_TO_TYPE: dict[str, str] = {
    "covid": "covid",
    "sih": "sih",
    "sipni": "sipni",
    "sim": "sim",
    "sinan": "sinan",
    "sinasc": "sinasc",
    "sia": "sia",
    "cnes": "cnes",
}


def _tokens_for_agent(agent_key: str) -> set[str]:
    """Tokens de indicador que roteiam para um dado agente de saúde —
    ver docstring equivalente em `agents/star/orchestrator.py`."""
    return {tipo for tipo, agente in INDICADOR_TO_AGENT.items() if agente == agent_key}


# Correspondência subfunção orçamentária → tokens de indicador que a
# tornam relevante — mesma decisão/documentação de
# `agents/star/orchestrator.py` (mantida duplicada aqui pelo mesmo motivo
# que INDICADOR_TO_AGENT já era duplicado entre os dois módulos).
_SUBFUNCAO_TOKENS: dict[int, set[str]] = {
    122: _tokens_for_agent("cnes"),
    301: _tokens_for_agent("sipni"),
    302: _tokens_for_agent("sih"),
    303: _tokens_for_agent("sinan"),
    304: {"estabelecimentos_vigilancia_epidemiologica"} | _tokens_for_agent("sinan"),
    305: _tokens_for_agent("sinan") | _tokens_for_agent("covid"),
    306: _tokens_for_agent("cnes"),
}

# Mortalidade (SIM) é transversal para fins de cruzamento de dados — ver
# docstring equivalente em `agents/star/orchestrator.py`.
_MORTALIDADE_SUBFUNCOES: set[int] = {301, 302, 303, 305}
_MORTALIDADE_TOKENS: set[str] = _tokens_for_agent("sim")


def _subfuncao_ativa(codigo: int, health_params: set[str]) -> bool:
    """Decide se a subfunção orçamentária deve ser consultada nesta
    análise, com base nos health_params selecionados pelo usuário."""
    if _SUBFUNCAO_TOKENS.get(codigo, set()) & health_params:
        return True
    if codigo in _MORTALIDADE_SUBFUNCOES and _MORTALIDADE_TOKENS & health_params:
        return True
    return False


# As 7 subfunções orçamentárias alvo de PLANO_NOVO_MODELO_DADOS.md §5 —
# todas instanciadas via AgenteOrcamentoSubfuncao (classe parametrizada,
# nenhum arquivo novo por subfunção).
_ORCAMENTO_SUBFUNCOES: list[tuple[int, str]] = [
    (122, "Administração Geral"),
    (301, "Atenção Básica"),
    (302, "Assistência Hospitalar e Ambulatorial"),
    (303, "Suporte Profilático e Terapêutico"),
    (304, "Vigilância Sanitária"),
    (305, "Vigilância Epidemiológica"),
    (306, "Alimentação e Nutrição"),
]


class SupervisorOrcamento(AgenteCoALA):
    """Supervisor de orçamento — nível 1 da topologia hierárquica.

    Coordena agentes `AgenteOrcamentoSubfuncao` (nível 2) — 1 por
    subfunção (`_ORCAMENTO_SUBFUNCOES`, PLANO_NOVO_MODELO_DADOS.md §5),
    7 no total, cada uma ativada condicionalmente conforme os
    health_params selecionados (ver `_SUBFUNCAO_TOKENS`).

    Attributes:
        neo4j_client: Cliente Neo4j repassado aos agentes orçamentários.
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.peer_data: dict[str, Any] = {}
        self.procedural_memory = {
            "consultar_orcamento": [self._act_consultar_orcamento],
            "agregar_resultados": [self._act_agregar_resultados],
            "resumir_para_par": [self._act_resumir_para_par, self._act_resumir_fallback],
        }
        self._collectors: list[MetricsCollector] = []

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe consultar as subfunções orçamentárias relevantes.

        Cada subfunção é ativada quando algum token do seu domínio
        correspondente está em health_params (ver _SUBFUNCAO_TOKENS), ou
        quando health_params está vazio (comportamento legado — ativa
        tudo).
        """
        actions: list[dict] = []
        health_params = self.working_memory.get("health_params")
        hp_set = set(health_params) if health_params else set()

        for codigo, nome in _ORCAMENTO_SUBFUNCOES:
            if health_params and not _subfuncao_ativa(codigo, hp_set):
                continue
            actions.append({
                "goal": "consultar_orcamento", "subfuncao_codigo": codigo, "subfuncao_nome": nome
            })

        logger.info(
            "SupervisorOrcamento %s: activating %d subfunção(ões) for health_params=%s",
            self.agent_id, len(actions), health_params,
        )

        if actions:
            actions.append({"goal": "agregar_resultados"})
            actions.append({"goal": "resumir_para_par"})
        return actions

    # -- Ações ------------------------------------------------------------

    def _act_consultar_orcamento(self, action: dict) -> None:
        """Ação externa: consulta um agente orçamentário subordinado."""
        codigo = action["subfuncao_codigo"]
        nome = action["subfuncao_nome"]
        agent_id_str = f"hier-orcamento-{codigo}-{uuid.uuid4().hex[:8]}"
        agent = AgenteOrcamentoSubfuncao(agent_id_str, self.neo4j_client, codigo, nome)

        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        intent_summary = self.working_memory.get("intent_summary")

        mc = MetricsCollector(agent_id_str, "orcamento_subfuncao")
        mc.start()
        try:
            log_start = len(self.neo4j_client.query_log)
            result = agent.query(
                analysis_id, date_from, date_to,
                intent_summary=intent_summary,
                use_llm=self.working_memory.get("use_llm", True),
            )
            mc.stop()
            queries = self.neo4j_client.query_log[log_start:]
            self.working_memory.setdefault("agent_queries", []).append({
                "agentName": f"orcamento_subfuncao_{codigo}",
                "agentLabel": f"{nome} ({codigo})",
                "queries": queries,
            })
            self.working_memory.setdefault("despesas", []).extend(result.get("despesas", []))
            self.working_memory.setdefault("tendencias", {})[codigo] = result.get("tendencia", {})
            self.working_memory.setdefault("variacao_anual", {})[codigo] = result.get(
                "variacao_anual", []
            )
            logger.info(
                "SupervisorOrcamento %s: subfuncao %s returned %d despesas",
                self.agent_id, codigo, len(result.get("despesas", [])),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorOrcamento %s: subfuncao %s failed — %s", self.agent_id, codigo, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_agregar_resultados(self, action: dict) -> None:
        """Ação interna (reasoning): deduplica despesas e agrega o resultado."""
        try:
            despesas = self.working_memory.get("despesas", [])
            unique_despesas = deduplicate_despesas(despesas)
            tendencias = self.working_memory.get("tendencias", {})
            variacao_anual = self.working_memory.get("variacao_anual", {})

            aggregated = {
                "despesas": unique_despesas,
                "tendencias": tendencias,
                "variacao_anual": variacao_anual,
            }
            self.working_memory["despesas"] = unique_despesas
            self.working_memory["aggregated"] = aggregated
            logger.info(
                "SupervisorOrcamento %s: aggregated %d despesas", self.agent_id, len(unique_despesas)
            )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_resumir_para_par(self, action: dict) -> None:
        """Ação externa (grounding): gera um resumo textual curto via LLM (Etapa 5).

        Mesmo mecanismo/regras do SupervisorSaude (ver docstring
        equivalente lá): respeita `use_llm=False`, cai no fallback
        determinístico se o LLM falhar.
        """
        if not self.working_memory.get("use_llm", True):
            raise ActionFailure(action, "use_llm=False")

        aggregated = self.working_memory.get("aggregated", {"despesas": [], "tendencias": {}})
        despesas = aggregated.get("despesas", [])
        date_from = self.working_memory.get("date_from")
        date_to = self.working_memory.get("date_to")

        prompt = (
            "Resuma em no máximo 2 frases curtas, em português, o que foi "
            "encontrado nesta consulta de execução orçamentária de saúde "
            "de Sorocaba-SP, para um colega que vai usar esse resumo como "
            "contexto (não invente números além dos fornecidos):\n"
            f"- Período: {date_from}-{date_to}\n"
            f"- {len(despesas)} registros de despesa encontrados"
        )

        try:
            import core.llm_client as llm_client

            raw = llm_client.generate(prompt, caller=f"{self.agent_id}:resumir_para_par")
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

        if not raw or not raw.strip():
            raise ActionFailure(action, "LLM retornou resposta vazia")

        self.working_memory["resumo"] = raw.strip()
        logger.info(
            "SupervisorOrcamento %s: resumo lateral gerado via LLM: %r",
            self.agent_id, self.working_memory["resumo"],
        )

    def _act_resumir_fallback(self, action: dict) -> None:
        """Estratégia de fallback: LLM indisponível — resumo determinístico."""
        aggregated = self.working_memory.get("aggregated", {"despesas": [], "tendencias": {}})
        despesas = aggregated.get("despesas", [])

        self.working_memory["resumo"] = (
            f"Foram encontradas {len(despesas)} despesas relacionadas ao orçamento consultado."
        )
        logger.warning(
            "SupervisorOrcamento %s: LLM indisponível — resumo lateral via fallback determinístico",
            self.agent_id,
        )

    # -- Comunicação lateral ------------------------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação)."""
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorOrcamento %s: received peer data with keys %s",
            self.agent_id, list(data.keys()),
        )

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        date_from: int | None,
        date_to: int | None,
        health_params: list[str] | None = None,
        intent_summary: str | None = None,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Executa o pipeline orçamentário via agentes subordinados.

        Args:
            intent_summary: Resumo da intenção do usuário — insumo da
                deliberação de dimensão (Fase 3) de cada
                `AgenteOrcamentoSubfuncao` subordinado.

        Returns:
            Dicionário com "despesas", "tendencias" (por subfuncaoCodigo)
            e "resumo".
        """
        self._collectors = []
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
            "health_params": health_params,
            "intent_summary": intent_summary,
            "use_llm": use_llm,
            "despesas": [],
            "tendencias": {},
        })

        self.run_coala_cycle()

        result = dict(
            self.working_memory.get("aggregated", {"despesas": [], "tendencias": {}})
        )
        result["resumo"] = self.working_memory.get("resumo", "")
        result["agent_queries"] = self.working_memory.get("agent_queries", [])
        return result


class SupervisorSaude(AgenteCoALA):
    """Supervisor de saúde — nível 1 da topologia hierárquica.

    Coordena os 5 agentes de saúde (nível 2) — 1 por Sistema de
    Informação: `AgenteSINAN` (9 agravos), `AgenteSIH` (internações),
    `AgenteSIM` (mortalidade), `AgenteSIPNI` (cobertura vacinal, doses
    aplicadas), `AgenteCOVID` (casos, óbitos).

    Substitui o antigo `SupervisorDominio`, que também coordenava
    despesas — agora responsabilidade exclusiva de `SupervisorOrcamento`.
    Nenhum dos 5 agentes de saúde consulta despesas (decomposição
    completa — diferente da Fase 1, onde os agentes legados ainda
    misturavam despesas+indicadores) — o campo "despesas" do agregado
    fica sempre vazio aqui; o dedupe em `CoordenadorGeral._act_combinar_despesas`
    permanece como salvaguarda estrutural, não porque haja sobreposição
    real de origem.

    Attributes:
        neo4j_client: Cliente Neo4j repassado aos agentes de saúde.
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.peer_data: dict[str, Any] = {}
        self.semantic_memory = {"indicador_to_agent": INDICADOR_TO_AGENT}
        self.procedural_memory = {
            "consultar_saude": [self._act_consultar_saude],
            "agregar_resultados": [self._act_agregar_resultados],
            "resumir_para_par": [self._act_resumir_para_par, self._act_resumir_fallback],
        }
        self._collectors: list[MetricsCollector] = []

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe parâmetros da análise a partir da working memory."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe consultar os agentes de saúde ativos e agregar o resultado.

        Ativa apenas os agentes cujos indicadores estão presentes em
        health_params (retrieval de `semantic_memory`). Se health_params
        for None ou vazio, ativa todos (comportamento legado).
        """
        actions: list[dict] = []
        health_params = self.working_memory.get("health_params")
        indicador_to_agent = self.semantic_memory["indicador_to_agent"]

        if health_params:
            active_agent_types: set[str] = set()
            for hp in health_params:
                agent_type = indicador_to_agent.get(hp)
                if agent_type:
                    active_agent_types.add(agent_type)
            keys = [k for k in _SAUDE_AGENT_KEY_ORDER if _AGENT_KEY_TO_TYPE[k] in active_agent_types]
        else:
            keys = list(_SAUDE_AGENT_KEY_ORDER)

        for key in keys:
            actions.append({"goal": "consultar_saude", "agent_key": key})

        logger.info(
            "SupervisorSaude %s: activating %d/%d agentes de saúde for health_params=%s",
            self.agent_id, len(keys), len(_SAUDE_AGENT_KEY_ORDER), health_params,
        )

        actions.append({"goal": "agregar_resultados"})
        actions.append({"goal": "resumir_para_par"})
        return actions

    # -- Ações ------------------------------------------------------------

    def _act_consultar_saude(self, action: dict) -> None:
        """Ação externa: consulta um agente de saúde subordinado.

        Resolve a classe do agente pelo nome global no momento da
        chamada, para permitir substituição via `unittest.mock.patch` em
        testes.
        """
        specs = {
            "covid": ("covid", AgenteCOVID, "hier-covid"),
            "sih": ("sih", AgenteSIH, "hier-sih"),
            "sipni": ("sipni", AgenteSIPNI, "hier-sipni"),
            "sim": ("sim", AgenteSIM, "hier-sim"),
            "sinan": ("sinan", AgenteSINAN, "hier-sinan"),
            "sinasc": ("sinasc", AgenteSINASC, "hier-sinasc"),
            "sia": ("sia", AgenteSIA, "hier-sia"),
            "cnes": ("cnes", AgenteCNES, "hier-cnes"),
        }
        agent_type, agent_cls, id_prefix = specs[action["agent_key"]]
        agent_id_str = f"{id_prefix}-{uuid.uuid4().hex[:8]}"
        agent = agent_cls(agent_id_str, self.neo4j_client)

        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]
        intent_summary = self.working_memory.get("intent_summary")
        health_params = self.working_memory.get("health_params")

        # SINAN/SIH/SIM/SIPNI/SINASC/CNES deliberam dimensão via LLM
        # (mesmo padrão de AgenteSINAN) — precisam de use_llm; COVID/SIA
        # não têm dimensões válidas e não aceitam esse kwarg.
        query_kwargs: dict[str, Any] = dict(intent_summary=intent_summary, health_params=health_params)
        if action["agent_key"] in ("sinan", "sih", "sim", "sipni", "sinasc", "cnes"):
            query_kwargs["use_llm"] = self.working_memory.get("use_llm", True)

        mc = MetricsCollector(agent_id_str, agent_type)
        mc.start()
        try:
            log_start = len(self.neo4j_client.query_log)
            result = agent.query(analysis_id, date_from, date_to, **query_kwargs)
            mc.stop()
            queries = self.neo4j_client.query_log[log_start:]
            self.working_memory.setdefault("agent_queries", []).append({
                "agentName": agent_type,
                "agentLabel": agent_type.upper(),
                "queries": queries,
            })
            self.working_memory.setdefault("despesas", []).extend(result.get("despesas", []))
            self.working_memory.setdefault("indicadores", []).extend(result.get("indicadores", []))
            logger.info(
                "SupervisorSaude %s: %s returned %d despesas, %d indicadores",
                self.agent_id,
                agent_type,
                len(result.get("despesas", [])),
                len(result.get("indicadores", [])),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            # Graceful degradation: exclude failed agent, continue
            logger.error(
                "SupervisorSaude %s: %s failed — %s", self.agent_id, agent_type, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_agregar_resultados(self, action: dict) -> None:
        """Ação interna (reasoning): deduplica despesas e agrega o resultado."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            unique_despesas = deduplicate_despesas(despesas)

            aggregated = {"despesas": unique_despesas, "indicadores": indicadores}
            self.working_memory["despesas"] = unique_despesas
            self.working_memory["aggregated"] = aggregated
            logger.info(
                "SupervisorSaude %s: aggregated %d despesas, %d indicadores",
                self.agent_id,
                len(unique_despesas),
                len(indicadores),
            )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_resumir_para_par(self, action: dict) -> None:
        """Ação externa (grounding): gera um resumo textual curto via LLM (Etapa 5).

        Descreve em 1-2 frases o que este supervisor encontrou (cobertura
        de dados, não uma conclusão analítica — isso é papel dos agentes
        analíticos). Esse resumo acompanha os dados brutos quando
        repassados lateralmente a SupervisorAnalitico (ver
        CoordenadorGeral), dando conteúdo semântico à comunicação lateral
        em vez de só transportar dicts (D4 do PLANO_REFATORACAO.md).
        Se o LLM falhar, `_act_resumir_fallback` (próxima estratégia)
        gera um resumo determinístico equivalente — a comunicação lateral
        nunca fica sem resumo.

        Respeita `use_llm=False` (mesma regra já aplicada a
        `priorizar_achados`/`verificar_afirmacoes`): levanta ActionFailure
        de propósito para cair direto no fallback determinístico, sem
        gastar uma chamada LLM quando a análise pediu explicitamente para
        não usar LLM em lugar nenhum.
        """
        if not self.working_memory.get("use_llm", True):
            raise ActionFailure(action, "use_llm=False")

        aggregated = self.working_memory.get("aggregated", {"despesas": [], "indicadores": []})
        despesas = aggregated.get("despesas", [])
        indicadores = aggregated.get("indicadores", [])
        health_params = self.working_memory.get("health_params") or []
        date_from = self.working_memory.get("date_from")
        date_to = self.working_memory.get("date_to")

        prompt = (
            "Resuma em no máximo 2 frases curtas, em português, o que foi "
            "encontrado nesta consulta de indicadores de saúde pública de "
            "Sorocaba-SP, para um colega que vai usar esse resumo como "
            "contexto (não invente números além dos fornecidos):\n"
            f"- Período: {date_from}-{date_to}\n"
            f"- Indicadores solicitados: {health_params}\n"
            f"- {len(despesas)} registros de despesa encontrados\n"
            f"- {len(indicadores)} registros de indicador encontrados"
        )

        try:
            import core.llm_client as llm_client

            raw = llm_client.generate(prompt, caller=f"{self.agent_id}:resumir_para_par")
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

        if not raw or not raw.strip():
            raise ActionFailure(action, "LLM retornou resposta vazia")

        self.working_memory["resumo"] = raw.strip()
        logger.info(
            "SupervisorSaude %s: resumo lateral gerado via LLM: %r",
            self.agent_id, self.working_memory["resumo"],
        )

    def _act_resumir_fallback(self, action: dict) -> None:
        """Estratégia de fallback: LLM indisponível — resumo determinístico (Etapa 5)."""
        aggregated = self.working_memory.get("aggregated", {"despesas": [], "indicadores": []})
        despesas = aggregated.get("despesas", [])
        indicadores = aggregated.get("indicadores", [])
        health_params = self.working_memory.get("health_params") or []
        labels = ", ".join(health_params) if health_params else "os indicadores solicitados"

        self.working_memory["resumo"] = (
            f"Foram encontradas {len(despesas)} despesas e {len(indicadores)} "
            f"indicadores relacionados a {labels}."
        )
        logger.warning(
            "SupervisorSaude %s: LLM indisponível — resumo lateral via fallback determinístico",
            self.agent_id,
        )

    # -- Comunicação lateral (Req 10.5) ------------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação).

        Args:
            data: Dicionário com dados enviados pelo supervisor par.
        """
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorSaude %s: received peer data with keys %s",
            self.agent_id,
            list(data.keys()),
        )

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        date_from: int | None,
        date_to: int | None,
        health_params: list[str] | None = None,
        intent_summary: str | None = None,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """Executa o pipeline de saúde via agentes subordinados.

        Configura a working memory e delega ao ciclo CoALA, que percorre
        as macro-ações registradas em `procedural_memory` (Req 10.2, 10.7).

        Args:
            analysis_id: UUID da análise.
            date_from: Ano inicial do período.
            date_to: Ano final do período.
            health_params: Lista de tipos de indicador selecionados pelo usuário.
            intent_summary: Resumo da intenção do usuário — insumo da
                deliberação de dimensão de `AgenteSINAN` (Fase 1).
            use_llm: Se False, `resumir_para_par` e a deliberação de
                dimensão de `AgenteSINAN` pulam direto para o
                comportamento determinístico — nenhuma chamada LLM extra.

        Returns:
            Dicionário com "despesas" (herdadas dos agentes legados ainda
            não decompostos) e "indicadores" agregados.
        """
        self._collectors = []
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": date_from,
            "date_to": date_to,
            "health_params": health_params,
            "intent_summary": intent_summary,
            "use_llm": use_llm,
            "despesas": [],
            "indicadores": [],
        })

        self.run_coala_cycle()

        result = dict(
            self.working_memory.get("aggregated", {"despesas": [], "indicadores": []})
        )
        result["resumo"] = self.working_memory.get("resumo", "")
        result["agent_queries"] = self.working_memory.get("agent_queries", [])
        return result


class SupervisorAnalitico(AgenteCoALA):
    """Supervisor analítico — nível 1 da topologia hierárquica (Req 10.3).

    Coordena 3 agentes analíticos (nível 2): AgenteCorrelacao,
    AgenteAnomalias e TextSynthesizer.

    Recebe dados de domínio e contexto orçamentário via
    ``receive_from_peer`` (Reqs 10.5, 10.6), cruza dados usando
    ``cross_domain_data()``, e executa o pipeline analítico via macro-ações
    registradas em `procedural_memory`: cruzamento → gaps → correlação →
    anomalias → sintetizador.

    Attributes:
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.peer_data: dict[str, Any] = {}
        self.procedural_memory = {
            "cruzar_dados": [self._act_cruzar_dados],
            "detectar_gaps": [self._act_detectar_gaps],
            "analisar": [self._act_analisar],
            "capturar_wallclock": [self._act_capturar_wallclock],
            "priorizar_achados": [self._act_priorizar_achados],
            "sintetizar_texto": [self._act_sintetizar_texto],
            "verificar_afirmacoes": [self._act_verificar_afirmacoes],
        }
        self._collectors: list[MetricsCollector] = []
        # Marca o fim da parte determinística (antes do sintetizador/LLM) —
        # usado pelo CoordenadorGeral para excluir o tempo do LLM do overhead.
        self._coala_leaf_end_time: float | None = None

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe dados disponíveis (recebidos dos peers ou já na working memory)."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "despesas": self.peer_data.get("despesas", []),
            "indicadores": self.peer_data.get("indicadores", []),
            "contexto_orcamentario": self.peer_data.get("contexto_orcamentario", {}),
            "resumo_dominio": self.peer_data.get("resumo_dominio", ""),
            "resumo_contexto": self.peer_data.get("resumo_contexto", ""),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe o pipeline analítico completo, se há análise e fila WS configuradas."""
        if not (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("_ws_queue") is not None
        ):
            return []
        return [
            {"goal": "cruzar_dados"},
            {"goal": "detectar_gaps"},
            {"goal": "analisar"},
            {"goal": "capturar_wallclock"},
            {"goal": "priorizar_achados"},
            {"goal": "sintetizar_texto"},
            {"goal": "verificar_afirmacoes"},
        ]

    # -- Comunicação lateral (Reqs 10.5, 10.6) ------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação).

        Tipicamente chamado pelo CoordenadorGeral para repassar despesas
        (combinadas de SupervisorOrcamento + SupervisorSaude) e
        indicadores (de SupervisorSaude), e do SupervisorContexto
        (contexto_orcamentario).

        Args:
            data: Dicionário com dados enviados pelo supervisor par.
        """
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorAnalitico %s: received peer data with keys %s",
            self.agent_id,
            list(data.keys()),
        )

    # -- Ações --------------------------------------------------------------

    def _act_cruzar_dados(self, action: dict) -> None:
        """Ação interna (reasoning): cruza dados de domínio (Req 10.5)."""
        try:
            despesas = self.peer_data.get("despesas", [])
            indicadores = self.peer_data.get("indicadores", [])
            dados_cruzados = cross_domain_data(despesas, indicadores)
            self.working_memory["dados_cruzados"] = dados_cruzados
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_gaps(self, action: dict) -> None:
        """Ação interna (reasoning): detecta lacunas de dados para transparência."""
        try:
            despesas = self.peer_data.get("despesas", [])
            indicadores = self.peer_data.get("indicadores", [])
            date_from = self.peer_data.get("date_from")
            date_to = self.peer_data.get("date_to")
            health_params = self.peer_data.get("health_params")
            data_coverage: dict = {}
            if date_from is not None and date_to is not None:
                data_coverage = detect_data_gaps(
                    despesas, indicadores, date_from, date_to, health_params
                )
                if data_coverage.get("summary", {}).get("has_gaps"):
                    logger.warning(
                        "SupervisorAnalitico %s: %d data gaps detected",
                        self.agent_id,
                        data_coverage["summary"]["total_gaps"],
                    )
            self.working_memory["data_coverage"] = data_coverage
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_analisar(self, action: dict) -> None:
        """Ação externa: delega a AgenteAnalitico — correlação Spearman +
        detecção de anomalias consolidadas num único agente
        (PLANO_NOVO_MODELO_DADOS.md §7 item 6)."""
        an_id = f"hier-analitico-{uuid.uuid4().hex[:8]}"
        agente_analitico = AgenteAnalitico(an_id)
        mc = MetricsCollector(an_id, "analitico")
        mc.start()
        try:
            dados_cruzados = self.working_memory.get("dados_cruzados", [])
            resultado = agente_analitico.analisar(dados_cruzados)
            mc.stop()
            self.working_memory["correlacoes"] = resultado["correlacoes"]
            self.working_memory["anomalias"] = resultado["anomalias"]
            logger.info(
                "SupervisorAnalitico %s: analitico computed %d correlações, %d anomalias",
                self.agent_id, len(resultado["correlacoes"]), len(resultado["anomalias"]),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorAnalitico %s: analitico failed — %s", self.agent_id, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_capturar_wallclock(self, action: dict) -> None:
        """Ação interna (bookkeeping): marca o fim da parte determinística (antes do LLM)."""
        self._coala_leaf_end_time = time.time()

    def _compor_intent_summary(self) -> str | None:
        """Enriquece o intent_summary (Etapa 1) com os resumos laterais (Etapa 5).

        Reaproveita a Etapa 3 sem alterar a assinatura de
        `AgentePriorizacaoAnalitica.prioritize` — os resumos recebidos de
        SupervisorOrcamento+SupervisorSaude (combinados em
        "resumo_dominio" pelo CoordenadorGeral) e de SupervisorContexto
        via `receive_from_peer` são concatenados como contexto adicional,
        não substituem a intenção original do usuário.
        """
        intent_summary = self.peer_data.get("intent_summary")
        resumo_dominio = self.peer_data.get("resumo_dominio", "")
        resumo_contexto = self.peer_data.get("resumo_contexto", "")
        partes = [p for p in (intent_summary, resumo_dominio, resumo_contexto) if p]
        return " ".join(partes) if partes else None

    def _act_priorizar_achados(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): delega a AgentePriorizacaoAnalitica (Etapa 3).

        Roda depois de `capturar_wallclock` — mesma lógica do sintetizador:
        seu tempo (inclui 1 chamada LLM) fica fora da métrica de tempo
        "determinística" do supervisor. Falha aqui nunca propaga.

        `intent_summary` já vem enriquecido com os resumos laterais
        recebidos de SupervisorOrcamento+SupervisorSaude/SupervisorContexto
        (Etapa 5, ver `_compor_intent_summary`) — insumo adicional opcional para a
        escolha do ângulo de ênfase, sem alterar o contrato de
        `AgentePriorizacaoAnalitica.prioritize`.
        """
        prior_id = f"hier-priorizacao-{uuid.uuid4().hex[:8]}"
        agente_priorizacao = AgentePriorizacaoAnalitica(prior_id)
        mc = MetricsCollector(prior_id, "priorizacao")
        mc.start()
        try:
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.peer_data.get("contexto_orcamentario", {})
            intent_summary = self._compor_intent_summary()
            use_llm = self.working_memory.get("use_llm", True)

            achados_priorizados = agente_priorizacao.prioritize(
                correlacoes, anomalias, contexto_orcamentario, intent_summary, use_llm=use_llm
            )
            mc.stop()
            self.working_memory["achados_priorizados"] = achados_priorizados
            logger.info(
                "SupervisorAnalitico %s: achados priorizados via ângulo '%s'",
                self.agent_id, achados_priorizados.get("angulo"),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.warning(
                "SupervisorAnalitico %s: priorização de achados falhou (%s) — "
                "sintetizador seguirá sem ênfase/reordenação",
                self.agent_id, exc,
            )
            self._collectors.append(mc)

    def _act_sintetizar_texto(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): gera o texto via TextSynthesizer."""
        sint_id = f"hier-sintetizador-{uuid.uuid4().hex[:8]}"
        sintetizador = TextSynthesizer(sint_id)
        mc = MetricsCollector(sint_id, "sintetizador")
        mc.start()
        texto_analise = ""
        try:
            ws_queue = self.working_memory["_ws_queue"]
            analysis_id = self.working_memory["analysis_id"]
            data_coverage = self.working_memory.get("data_coverage")
            use_llm = self.working_memory.get("use_llm", True)
            date_from = self.peer_data.get("date_from")
            date_to = self.peer_data.get("date_to")
            pergunta_usuario = self.peer_data.get("intent_summary")

            # Correlações/anomalias/ênfase priorizadas (Etapa 3), se
            # disponíveis — nunca substitui os dados brutos usados em Q1.
            achados = self.working_memory.get("achados_priorizados")
            if achados:
                correlacoes = achados.get("correlacoes", self.working_memory.get("correlacoes", []))
                anomalias = achados.get("anomalias", self.working_memory.get("anomalias", []))
                enfase = achados.get("descricao_angulo")
            else:
                correlacoes = self.working_memory.get("correlacoes", [])
                anomalias = self.working_memory.get("anomalias", [])
                enfase = None
            contexto_orcamentario = self.peer_data.get("contexto_orcamentario", {})

            adapter = StreamingAdapter(ws_queue, analysis_id, "hierarchical")

            if use_llm:
                try:
                    token_gen = sintetizador.generate_stream(
                        correlacoes=correlacoes,
                        anomalias=anomalias,
                        contexto_orcamentario=contexto_orcamentario,
                        data_coverage=data_coverage,
                        enfase=enfase,
                        date_from=date_from,
                        date_to=date_to,
                        pergunta_usuario=pergunta_usuario,
                    )
                    texto_analise = adapter.stream_tokens(token_gen)
                    if not texto_analise:
                        texto_analise = sintetizador.generate_fallback(
                            correlacoes, anomalias, contexto_orcamentario, data_coverage,
                            date_from, date_to,
                        )
                        adapter.stream_text(texto_analise)
                except Exception:
                    logger.warning(
                        "SupervisorAnalitico %s: LLM streaming failed, using fallback",
                        self.agent_id,
                    )
                    texto_analise = sintetizador.generate_fallback(
                        correlacoes, anomalias, contexto_orcamentario, data_coverage,
                        date_from, date_to,
                    )
                    adapter.stream_text(texto_analise)
            else:
                texto_analise = sintetizador.generate_fallback(
                    correlacoes, anomalias, contexto_orcamentario, data_coverage,
                    date_from, date_to,
                )
                adapter.stream_text(texto_analise)

            mc.stop()
            self.working_memory["texto_analise"] = texto_analise
            logger.info(
                "SupervisorAnalitico %s: synthesis complete (%d chars)",
                self.agent_id,
                len(texto_analise),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorAnalitico %s: sintetizador failed — %s", self.agent_id, exc
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_verificar_afirmacoes(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): self-check pós-síntese (Etapa 4).

        Mesmo mecanismo/regras do OrquestradorEstrela (ver docstring
        equivalente lá): opcional via `use_self_check`, só roda com
        `use_llm=True`, nunca propaga falha.
        """
        if not self.working_memory.get("use_self_check", False):
            return
        if not self.working_memory.get("use_llm", True):
            return

        texto_analise = self.working_memory.get("texto_analise", "")
        if not texto_analise:
            return

        verif_id = f"hier-verificacao-{uuid.uuid4().hex[:8]}"
        mc = MetricsCollector(verif_id, "verificacao")
        mc.start()
        try:
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.peer_data.get("contexto_orcamentario", {})

            resultado = claim_verifier.self_check(
                texto_analise, correlacoes, anomalias, contexto_orcamentario, caller=verif_id,
            )
            mc.stop()
            self.working_memory["self_check"] = resultado
            if resultado["revisado"]:
                self.working_memory["texto_analise"] = resultado["texto_final"]
                nao_suportadas = sum(1 for c in resultado["claims"] if not c["suportado"])
                logger.info(
                    "SupervisorAnalitico %s: self-check corrigiu o texto "
                    "(%d/%d afirmações não suportadas)",
                    self.agent_id, nao_suportadas, len(resultado["claims"]),
                )
            else:
                logger.info(
                    "SupervisorAnalitico %s: self-check concluído sem correções "
                    "(%d afirmações verificadas)",
                    self.agent_id, len(resultado["claims"]),
                )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.warning(
                "SupervisorAnalitico %s: self-check falhou (%s) — texto original mantido",
                self.agent_id, exc,
            )
            self._collectors.append(mc)

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        ws_queue: Queue,
        use_llm: bool = True,
        use_self_check: bool = False,
    ) -> dict[str, Any]:
        """Executa o pipeline analítico via 3 agentes subordinados.

        Espera que ``receive_from_peer`` já tenha sido chamado com
        despesas, indicadores e contexto_orcamentario. Configura a working
        memory e delega ao ciclo CoALA (Req 10.3).

        Args:
            analysis_id: UUID da análise em andamento.
            ws_queue: Fila para streaming de eventos WebSocket.
            use_llm: Se True, tenta gerar texto via LLM antes do fallback.
            use_self_check: Se True (e use_llm=True), roda a verificação
                pós-síntese (Etapa 4) sobre o texto gerado.

        Returns:
            Dicionário com "correlacoes", "anomalias", "texto_analise",
            "data_coverage" e "dados_cruzados".
        """
        self._collectors = []
        self._coala_leaf_end_time = None
        self.update_working_memory({
            "analysis_id": analysis_id,
            "_ws_queue": ws_queue,
            "use_llm": use_llm,
            "use_self_check": use_self_check,
        })

        self.run_coala_cycle()

        result = {
            "correlacoes": self.working_memory.get("correlacoes", []),
            "anomalias": self.working_memory.get("anomalias", []),
            "texto_analise": self.working_memory.get("texto_analise", ""),
            "data_coverage": self.working_memory.get("data_coverage", {}),
            "dados_cruzados": self.working_memory.get("dados_cruzados", []),
            "self_check": self.working_memory.get("self_check"),
        }

        self.working_memory["aggregated"] = result
        logger.info(
            "SupervisorAnalitico %s: pipeline complete — %d correlacoes, %d anomalias",
            self.agent_id,
            len(result["correlacoes"]),
            len(result["anomalias"]),
        )

        return result


class SupervisorContexto(AgenteCoALA):
    """Supervisor de contexto — nível 1 da topologia hierárquica (Req 10.4).

    Coordena o AgenteContextoOrcamentario (nível 2) para análise de
    tendências temporais de gasto orçamentário.

    Recebe despesas combinadas (SupervisorOrcamento + SupervisorSaude,
    já deduplicadas pelo CoordenadorGeral) via ``receive_from_peer``
    (Req 10.6) e executa a análise de tendências via ciclo CoALA.

    Attributes:
        peer_data: Dados recebidos de supervisores pares via comunicação lateral.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.peer_data: dict[str, Any] = {}
        self.procedural_memory = {
            "executar_contexto_orcamentario": [self._act_executar_contexto_orcamentario],
            "resumir_para_par": [self._act_resumir_para_par, self._act_resumir_fallback],
        }
        self._collectors: list[MetricsCollector] = []

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe dados disponíveis (recebidos dos peers)."""
        return {
            "despesas": self.peer_data.get("despesas", []),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe executar a análise de tendências se há despesas disponíveis."""
        if self.peer_data.get("despesas"):
            return [
                {"goal": "executar_contexto_orcamentario"},
                {"goal": "resumir_para_par"},
            ]
        return []

    # -- Comunicação lateral (Req 10.6) ------------------------------------

    def receive_from_peer(self, data: dict[str, Any]) -> None:
        """Recebe dados de um supervisor par (ação externa de comunicação).

        Tipicamente chamado pelo CoordenadorGeral para repassar despesas
        combinadas de SupervisorOrcamento + SupervisorSaude.

        Args:
            data: Dicionário com "despesas" do supervisor par.
        """
        self.peer_data.update(data)
        self.update_working_memory({"peer_data": self.peer_data})
        logger.info(
            "SupervisorContexto %s: received peer data with keys %s",
            self.agent_id,
            list(data.keys()),
        )

    # -- Ações --------------------------------------------------------------

    def _act_executar_contexto_orcamentario(self, action: dict) -> None:
        """Ação externa: delega a AgenteContextoOrcamentario."""
        ctx_id = f"hier-contexto-{uuid.uuid4().hex[:8]}"
        agente_contexto = AgenteContextoOrcamentario(ctx_id)
        mc = MetricsCollector(ctx_id, "contexto_orcamentario")
        mc.start()
        try:
            despesas = self.peer_data.get("despesas", [])
            contexto_orcamentario = agente_contexto.analyze_trends(despesas)
            mc.stop()
            self.working_memory["contexto_orcamentario"] = contexto_orcamentario
            logger.info(
                "SupervisorContexto %s: computed trends for %d subfunções",
                self.agent_id,
                len(contexto_orcamentario),
            )
            self._collectors.append(mc)
        except Exception as exc:
            mc.stop()
            logger.error(
                "SupervisorContexto %s: contexto_orcamentario failed — %s",
                self.agent_id,
                exc,
            )
            self._collectors.append(mc)
            raise ActionFailure(action, str(exc)) from exc

    def _act_resumir_para_par(self, action: dict) -> None:
        """Ação externa (grounding): gera um resumo textual curto via LLM (Etapa 5).

        Descreve em 1-2 frases a tendência orçamentária mais relevante
        (maior variação em módulo), para acompanhar o `contexto_orcamentario`
        bruto quando repassado a SupervisorAnalitico — o mesmo tipo de
        conteúdo semântico do exemplo do PLANO_REFATORACAO.md ("gasto em
        Vigilância Epidemiológica caiu de forma consistente nos últimos
        2 anos"). Se o LLM falhar, `_act_resumir_fallback` gera o
        equivalente determinístico.

        Respeita `use_llm=False` — ver docstring equivalente em
        `SupervisorSaude._act_resumir_para_par`.
        """
        if not self.working_memory.get("use_llm", True):
            raise ActionFailure(action, "use_llm=False")

        contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})
        if not contexto_orcamentario:
            self.working_memory["resumo"] = ""
            return

        prompt = (
            "Resuma em no máximo 2 frases curtas, em português, a tendência "
            "orçamentária mais relevante abaixo (maior variação, positiva ou "
            "negativa), para um colega que vai usar esse resumo como "
            "contexto — não invente números além dos fornecidos:\n"
            f"{contexto_orcamentario}"
        )

        try:
            import core.llm_client as llm_client

            raw = llm_client.generate(prompt, caller=f"{self.agent_id}:resumir_para_par")
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

        if not raw or not raw.strip():
            raise ActionFailure(action, "LLM retornou resposta vazia")

        self.working_memory["resumo"] = raw.strip()
        logger.info(
            "SupervisorContexto %s: resumo lateral gerado via LLM: %r",
            self.agent_id, self.working_memory["resumo"],
        )

    def _act_resumir_fallback(self, action: dict) -> None:
        """Estratégia de fallback: LLM indisponível — resumo determinístico (Etapa 5)."""
        contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})
        if not contexto_orcamentario:
            self.working_memory["resumo"] = ""
            return

        subfuncao_key, dados = max(
            contexto_orcamentario.items(),
            key=lambda item: abs(item[1].get("variacao_media_percentual", 0))
            if isinstance(item[1], dict) else 0,
        )
        variacao = dados.get("variacao_media_percentual", 0) if isinstance(dados, dict) else 0
        tendencia = dados.get("tendencia", "estável") if isinstance(dados, dict) else "estável"
        self.working_memory["resumo"] = (
            f"A subfunção {subfuncao_key} apresentou a maior variação orçamentária "
            f"do período, com tendência de {tendencia} ({variacao:.1f}% em média)."
        )
        logger.warning(
            "SupervisorContexto %s: LLM indisponível — resumo lateral via fallback determinístico",
            self.agent_id,
        )

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(self, use_llm: bool = True) -> dict[str, Any]:
        """Executa a análise de contexto orçamentário via subordinado.

        Espera que ``receive_from_peer`` já tenha sido chamado com as
        despesas combinadas (SupervisorOrcamento + SupervisorSaude).
        Delega ao ciclo CoALA (Req 10.4).

        Args:
            use_llm: Se False, `resumir_para_par` (Etapa 5) pula direto
                para o fallback determinístico.

        Returns:
            Dicionário com "contexto_orcamentario" e "resumo".
        """
        self._collectors = []
        self.update_working_memory({"use_llm": use_llm})
        self.run_coala_cycle()

        result = {
            "contexto_orcamentario": self.working_memory.get("contexto_orcamentario", {}),
            "resumo": self.working_memory.get("resumo", ""),
        }

        self.working_memory["aggregated"] = result
        logger.info("SupervisorContexto %s: pipeline complete", self.agent_id)

        return result
