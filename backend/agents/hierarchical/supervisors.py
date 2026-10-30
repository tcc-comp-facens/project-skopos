"""
Supervisores da arquitetura hierárquica (Nível 1).

Quatro supervisores especializados coordenam os agentes de nível 2:

- **SupervisorOrcamento** coordena agentes `AgenteOrcamentoSubfuncao`
  (1 por subfunção, 7 no total — PLANO_NOVO_MODELO_DADOS.md §5).
- **SupervisorSaude** coordena os 8 agentes de saúde (SINAN, SIH, SIM,
  SI-PNI, COVID, SINASC, SIA, CNES — 1 por Sistema de Informação).
  Substitui o antigo `SupervisorDominio`, que combinava despesas+
  indicadores num único nível — separado aqui em SupervisorOrcamento
  (despesas) vs. SupervisorSaude (indicadores), decisão tomada com o
  usuário na Fase 1.
- **SupervisorAnalitico** coordena os agentes analíticos (correlação +
  anomalias consolidadas em `AgenteAnalitico`, priorização, síntese).
- **SupervisorContexto** coordena `AgenteContextoOrcamentario` (tendências
  de todas as subfunções presentes nas despesas recebidas).

Árvore estrita: cada supervisor só troca dados com o `CoordenadorGeral`
(seu pai), nunca diretamente com outro supervisor. Um design anterior
tinha um canal `receive_from_peer`/`peer_data` mediado pela raiz
(comunicação "lateral", inclusive com resumos textuais via LLM) — removido
por decisão explícita: o modelo de hierárquica deste projeto não é uma
árvore estrita se dois nós do mesmo nível trocam dado entre si, mesmo que
a troca seja disparada pelo coordenador. Hoje `SupervisorContexto.run()` e
`SupervisorAnalitico.run()` recebem tudo de que precisam como parâmetros
diretos de `CoordenadorGeral` (que colhe o retorno de cada supervisor e
repassa o que for necessário ao próximo) — sem estado intermediário de
"dado de par".

SupervisorOrcamento e SupervisorSaude recebem diretamente as entregas dos
agentes de dados e, por isso, as revisam antes de repassá-las ao
coordenador: cada um faz uma chamada ao LLM sobre o seu ramo e contesta por
reenvio os valores sinalizados (ver agents/revisao_entregas.py). A mesma
regra vale na estrela, onde o orquestrador revisa tudo numa chamada.

Cada supervisor executa de fato pelo ciclo CoALA: `run()` configura a
working memory e delega a `run_coala_cycle()`, que percorre as
macro-ações registradas em `procedural_memory` na ordem proposta por
`propose_actions` — a mesma degradação graciosa e coleta de métricas por
agente subordinado que o pipeline imperativo anterior já tinha.

Requisitos: 10.2, 10.3, 10.4, 10.7
"""

from __future__ import annotations

import logging
import time
import uuid
from queue import Queue
from typing import Any, TYPE_CHECKING

from agents import planejamento, revisao_entregas
from agents.analytical import redacao_ramos
from core.contextos_avaliacao import linhas_series
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
    301: _tokens_for_agent("sipni") | _tokens_for_agent("sinasc"),
    302: _tokens_for_agent("sih") | _tokens_for_agent("sia"),
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
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.procedural_memory = {
            "consultar_orcamento": [self._act_consultar_orcamento],
            "revisar_entregas": [self._act_revisar_entregas],
            "agregar_resultados": [self._act_agregar_resultados],
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
            actions.append({"goal": "revisar_entregas"})
            actions.append({"goal": "agregar_resultados"})
        return actions

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Planeja com o LLM quais subfunções consultar.

        A proposta são os candidatos `consultar_orcamento`. O objetivo é o
        subobjetivo recebido do coordenador (ou, sem ele, a análise
        inteira). A revisão e a agregação vêm depois da coleta.
        """
        plano = self._deliberar(
            planejamento.planejar,
            agent_id=self.agent_id,
            papel="o supervisor de orçamento, que coordena um agente de despesas por subfunção",
            objetivo=self._objetivo(),
            candidatos=planejamento.catalogo_orcamento(_ORCAMENTO_SUBFUNCOES),
            proposta=[
                f"orcamento:{c['subfuncao_codigo']}" for c in candidates
                if c["goal"] == "consultar_orcamento"
            ],
            use_llm=self.working_memory.get("use_llm", True),
        )
        self.working_memory["plano"] = plano
        nomes = dict(_ORCAMENTO_SUBFUNCOES)
        selecionadas: list[dict] = []
        for e in plano["escolhidos"]:
            codigo = int(e.split(":", 1)[1])
            selecionadas.append({
                "goal": "consultar_orcamento", "subfuncao_codigo": codigo, "subfuncao_nome": nomes[codigo]
            })
        if selecionadas:
            selecionadas.append({"goal": "revisar_entregas"})
            selecionadas.append({"goal": "agregar_resultados"})
        return [dict(c, status="pending") for c in selecionadas]

    def _objetivo(self) -> str:
        wm = self.working_memory
        return planejamento.objetivo_do_ramo(
            wm.get("objetivo"), wm.get("intent_summary"), wm.get("health_params"),
            wm.get("date_from"), wm.get("date_to"),
        )

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
            result = self._delegar(
                "orcamento_subfuncao", agent_id_str,
                lambda: agent.query(
                analysis_id, date_from, date_to,
                intent_summary=intent_summary,
                use_llm=self.working_memory.get("use_llm", True),
            ),
                pedido={"subfuncao": codigo, "periodo": [date_from, date_to]}, natureza_resposta="dados", extrair=None,
            )
            mc.stop()
            queries = self.neo4j_client.query_log[log_start:]
            self.working_memory.setdefault("agent_queries", []).append({
                "agentName": f"orcamento_subfuncao_{codigo}",
                "agentLabel": f"{nome} ({codigo})",
                "queries": queries,
            })
            self.working_memory.setdefault("despesas", []).extend(result.get("despesas", []))
            revisao_entregas.registrar_entrega(
                self.working_memory, "orcamento_subfuncao", agent, result, campos=("despesas",)
            )
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

    def _act_revisar_entregas(self, action: dict) -> None:
        """Ação interna (reasoning): revisa as despesas entregues pelo ramo.

        Uma chamada ao LLM sobre as despesas dos agentes orçamentários
        subordinados, com contestação por reenvio dos valores sinalizados
        (`agents/revisao_entregas.py`). Falha aqui deixa os dados como
        chegaram.
        """
        self._deliberar(
            revisao_entregas.revisar_memoria_do_no,
            self.working_memory,
            revisor="supervisor_orcamento",
            caller=f"{self.agent_id}:revisar_entregas",
            no=self,
        )

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

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        date_from: int | None,
        date_to: int | None,
        health_params: list[str] | None = None,
        intent_summary: str | None = None,
        use_llm: bool = True,
        objetivo: str | None = None,
    ) -> dict[str, Any]:
        """Executa o pipeline orçamentário via agentes subordinados.

        Args:
            intent_summary: Resumo da intenção do usuário — insumo da
                deliberação de dimensão (Fase 3) de cada
                `AgenteOrcamentoSubfuncao` subordinado.
            objetivo: Subobjetivo escrito pelo coordenador, que orienta o
                planejamento deste supervisor.

        Returns:
            Dicionário com "despesas", "tendencias" (por subfuncaoCodigo)
            e "agent_queries".
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
            "_entregas": [],
            "revisao": revisao_entregas.resumo_vazio(["supervisor_orcamento"]),
            "objetivo": objetivo,
            "plano": None,
        })
        self.deliberacao_ms = 0.0

        self.run_coala_cycle()

        result = dict(
            self.working_memory.get("aggregated", {"despesas": [], "tendencias": {}})
        )
        result["agent_queries"] = self.working_memory.get("agent_queries", [])
        result["revisao"] = self.working_memory["revisao"]
        result["plano"] = self.working_memory.get("plano")
        return result


class SupervisorSaude(AgenteCoALA):
    """Supervisor de saúde — nível 1 da topologia hierárquica.

    Coordena os 8 agentes de saúde (nível 2) — 1 por Sistema de
    Informação: `AgenteSINAN` (9 agravos), `AgenteSIH` (internações),
    `AgenteSIM` (mortalidade), `AgenteSIPNI` (cobertura vacinal, doses
    aplicadas), `AgenteCOVID` (casos, óbitos), `AgenteSINASC` (nascidos
    vivos), `AgenteSIA` (produção ambulatorial), `AgenteCNES` (12
    subtipos da rede assistencial).

    Substitui o antigo `SupervisorDominio`, que também coordenava
    despesas — agora responsabilidade exclusiva de `SupervisorOrcamento`.
    Nenhum dos 8 agentes de saúde consulta despesas (decomposição
    completa) — o campo "despesas" do agregado fica sempre vazio aqui;
    o dedupe em `CoordenadorGeral._act_combinar_despesas` permanece como
    salvaguarda estrutural, não porque haja sobreposição real de origem.

    Attributes:
        neo4j_client: Cliente Neo4j repassado aos agentes de saúde.
    """

    def __init__(self, agent_id: str, neo4j_client: "Neo4jClient") -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.semantic_memory = {"indicador_to_agent": INDICADOR_TO_AGENT}
        self.procedural_memory = {
            "consultar_saude": [self._act_consultar_saude],
            "revisar_entregas": [self._act_revisar_entregas],
            "agregar_resultados": [self._act_agregar_resultados],
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

        if keys:
            actions.append({"goal": "revisar_entregas"})
        actions.append({"goal": "agregar_resultados"})
        return actions

    def evaluate_and_select(self, candidates: list[dict]) -> list[dict]:
        """Planeja com o LLM quais agentes de saúde acionar.

        A proposta são os candidatos `consultar_saude`. O objetivo é o
        subobjetivo recebido do coordenador (ou, sem ele, a análise
        inteira). A revisão e a agregação vêm depois da coleta.
        """
        plano = self._deliberar(
            planejamento.planejar,
            agent_id=self.agent_id,
            papel="o supervisor de saúde, que coordena um agente por sistema de informação",
            objetivo=self._objetivo(),
            candidatos=planejamento.catalogo_saude(
                _SAUDE_AGENT_KEY_ORDER, self.semantic_memory["indicador_to_agent"]
            ),
            proposta=[f"saude:{c['agent_key']}" for c in candidates if c["goal"] == "consultar_saude"],
            use_llm=self.working_memory.get("use_llm", True),
        )
        self.working_memory["plano"] = plano
        selecionadas = [
            {"goal": "consultar_saude", "agent_key": e.split(":", 1)[1]} for e in plano["escolhidos"]
        ]
        if selecionadas:
            selecionadas.append({"goal": "revisar_entregas"})
        selecionadas.append({"goal": "agregar_resultados"})
        return [dict(c, status="pending") for c in selecionadas]

    def _objetivo(self) -> str:
        wm = self.working_memory
        return planejamento.objetivo_do_ramo(
            wm.get("objetivo"), wm.get("intent_summary"), wm.get("health_params"),
            wm.get("date_from"), wm.get("date_to"),
        )

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
            result = self._delegar(
                agent_type, agent_id_str,
                lambda: agent.query(analysis_id, date_from, date_to, **query_kwargs),
                pedido={"periodo": [date_from, date_to], "intencao": intent_summary},
            )
            mc.stop()
            queries = self.neo4j_client.query_log[log_start:]
            self.working_memory.setdefault("agent_queries", []).append({
                "agentName": agent_type,
                "agentLabel": agent_type.upper(),
                "queries": queries,
            })
            self.working_memory.setdefault("despesas", []).extend(result.get("despesas", []))
            self.working_memory.setdefault("indicadores", []).extend(result.get("indicadores", []))
            revisao_entregas.registrar_entrega(
                self.working_memory, agent_type, agent, result, campos=("indicadores",)
            )
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

    def _act_revisar_entregas(self, action: dict) -> None:
        """Ação interna (reasoning): revisa os indicadores entregues pelo ramo.

        Uma chamada ao LLM sobre os indicadores dos agentes de saúde
        subordinados, com contestação por reenvio dos valores sinalizados
        (`agents/revisao_entregas.py`). Falha aqui deixa os dados como
        chegaram.
        """
        self._deliberar(
            revisao_entregas.revisar_memoria_do_no,
            self.working_memory,
            revisor="supervisor_saude",
            caller=f"{self.agent_id}:revisar_entregas",
            no=self,
        )

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

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(
        self,
        analysis_id: str,
        date_from: int | None,
        date_to: int | None,
        health_params: list[str] | None = None,
        intent_summary: str | None = None,
        use_llm: bool = True,
        objetivo: str | None = None,
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
            use_llm: Se False, a deliberação de dimensão dos agentes de
                saúde pula direto para o comportamento determinístico —
                nenhuma chamada LLM extra.
            objetivo: Subobjetivo escrito pelo coordenador, que orienta o
                planejamento deste supervisor.

        Returns:
            Dicionário com "despesas" (sempre vazia — nenhum agente de
            saúde retorna despesas) e "indicadores" agregados.
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
            "_entregas": [],
            "revisao": revisao_entregas.resumo_vazio(["supervisor_saude"]),
            "objetivo": objetivo,
            "plano": None,
        })
        self.deliberacao_ms = 0.0

        self.run_coala_cycle()

        result = dict(
            self.working_memory.get("aggregated", {"despesas": [], "indicadores": []})
        )
        result["agent_queries"] = self.working_memory.get("agent_queries", [])
        result["revisao"] = self.working_memory["revisao"]
        result["plano"] = self.working_memory.get("plano")
        return result


class SupervisorAnalitico(AgenteCoALA):
    """Supervisor analítico — nível 1 da topologia hierárquica (Req 10.3).

    Coordena os agentes analíticos (nível 2): `AgenteAnalitico`
    (correlação Spearman + anomalias consolidadas), `AgentePriorizacaoAnalitica`
    e `TextSynthesizer`.

    Recebe despesas, indicadores e contexto orçamentário como parâmetros
    diretos de `run()` (repassados pelo `CoordenadorGeral`, que os colheu
    do retorno de `SupervisorOrcamento`/`SupervisorSaude`/`SupervisorContexto`
    — árvore estrita, sem canal lateral entre supervisores), cruza os dados
    usando ``cross_domain_data()``, e executa o pipeline analítico via
    macro-ações registradas em `procedural_memory`: cruzamento → gaps →
    correlação/anomalias → síntese.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.procedural_memory = {
            "cruzar_dados": [self._act_cruzar_dados],
            "detectar_gaps": [self._act_detectar_gaps],
            "analisar": [self._act_analisar],
            "capturar_wallclock": [self._act_capturar_wallclock],
            "priorizar_achados": [self._act_priorizar_achados],
            "redigir_ramos": [self._act_redigir_ramos],
            "sintetizar_texto": [self._act_sintetizar_texto],
            "verificar_afirmacoes": [self._act_verificar_afirmacoes],
        }
        self._collectors: list[MetricsCollector] = []
        # Marca o fim da parte determinística (antes do sintetizador/LLM) —
        # usado pelo CoordenadorGeral para excluir o tempo do LLM do overhead.
        self._coala_leaf_end_time: float | None = None

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe os dados recebidos diretamente de `run()` (já na working memory)."""
        return {}

    def propose_actions(self) -> list[dict]:
        """Propõe o pipeline analítico, se há análise e fila WS configuradas.

        `verificar_afirmacoes` só é proposta quando `use_self_check=True`
        e `use_llm=True` — decisão tomada aqui, não dentro da ação.
        """
        if not (
            self.working_memory.get("analysis_id")
            and self.working_memory.get("_ws_queue") is not None
        ):
            return []
        actions = [
            {"goal": "cruzar_dados"},
            {"goal": "detectar_gaps"},
            {"goal": "analisar"},
            {"goal": "capturar_wallclock"},
            {"goal": "priorizar_achados"},
            {"goal": "redigir_ramos"},
            {"goal": "sintetizar_texto"},
        ]
        if self.working_memory.get("use_self_check", False) and self.working_memory.get(
            "use_llm", True
        ):
            actions.append({"goal": "verificar_afirmacoes"})
        return actions

    # -- Ações --------------------------------------------------------------

    def _act_cruzar_dados(self, action: dict) -> None:
        """Ação interna (reasoning): cruza despesas × indicadores recebidos do coordenador."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            dados_cruzados = cross_domain_data(despesas, indicadores)
            self.working_memory["dados_cruzados"] = dados_cruzados
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_gaps(self, action: dict) -> None:
        """Ação interna (reasoning): detecta lacunas de dados para transparência."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            date_from = self.working_memory.get("date_from")
            date_to = self.working_memory.get("date_to")
            health_params = self.working_memory.get("health_params")
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

    def _act_priorizar_achados(self, action: dict) -> None:
        """Ação interna (reasoning): delega a AgentePriorizacaoAnalitica (Etapa 3).

        Roda depois de `capturar_wallclock` — mesma lógica do sintetizador:
        seu tempo (inclui 1 chamada LLM) fica fora da métrica de tempo
        "determinística" do supervisor. Falha aqui nunca propaga.
        """
        prior_id = f"hier-priorizacao-{uuid.uuid4().hex[:8]}"
        agente_priorizacao = AgentePriorizacaoAnalitica(prior_id)
        mc = MetricsCollector(prior_id, "priorizacao")
        mc.start()
        try:
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})
            intent_summary = self.working_memory.get("intent_summary")
            use_llm = self.working_memory.get("use_llm", True)

            achados_priorizados = self._delegar(
                "priorizacao", prior_id,
                lambda: agente_priorizacao.prioritize(
                correlacoes, anomalias, contexto_orcamentario, intent_summary, use_llm=use_llm
            ),
                pedido={"correlacoes": correlacoes, "anomalias": anomalias}, natureza_resposta="dados", extrair=None,
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

    def _achados_para_redacao(self) -> tuple[list[dict], list[dict], str | None]:
        """Correlações, anomalias e ênfase priorizadas (Etapa 3), se houver."""
        achados = self.working_memory.get("achados_priorizados")
        if achados:
            return (
                achados.get("correlacoes", self.working_memory.get("correlacoes", [])),
                achados.get("anomalias", self.working_memory.get("anomalias", [])),
                achados.get("descricao_angulo"),
            )
        return (
            self.working_memory.get("correlacoes", []),
            self.working_memory.get("anomalias", []),
            None,
        )

    def _act_redigir_ramos(self, action: dict) -> None:
        """Ação externa: delega a redação a um redator por sistema de saúde.

        O supervisor reparte a redação como a árvore reparte a coleta: cada
        redator subordinado vê só os achados do seu sistema
        (`agents/analytical/redacao_ramos.particionar`), e a composição em
        `_act_sintetizar_texto` junta os textos. Com um único sistema não há
        o que repartir, e a redação segue única, igual à da estrela. Roda
        depois de `capturar_wallclock`, como o sintetizador. Falha de um
        ramo não derruba os demais: os achados dele vão crus para a
        composição.
        """
        self.working_memory["redacao"] = {"modo": "unica", "ramos": [], "falhas": []}
        if not self.working_memory.get("use_llm", True):
            return
        correlacoes, anomalias, _ = self._achados_para_redacao()
        ramos = redacao_ramos.particionar(
            correlacoes, anomalias,
            self.working_memory.get("contexto_orcamentario", {}),
            self.working_memory.get("data_coverage"),
        )
        self.working_memory["redacao"]["ramos"] = list(ramos)
        if len(ramos) < 2:
            return

        textos, sem_texto = [], []
        for sistema, ramo in ramos.items():
            redator_id = f"hier-redator-{sistema}-{uuid.uuid4().hex[:8]}"
            nome = redacao_ramos.NOME_SISTEMA.get(sistema, sistema)
            mc = MetricsCollector(redator_id, "redator_ramo")
            mc.start()
            self._enviar(redator_id, "request", "dados", {
                "sistema": sistema,
                "correlacoes": len(ramo["correlacoes"]), "anomalias": len(ramo["anomalias"]),
            })
            try:
                texto = TextSynthesizer(redator_id).generate_ramo(
                    nome, ramo["correlacoes"], ramo["anomalias"],
                    ramo["contexto_orcamentario"], ramo["data_coverage"],
                    self.working_memory.get("date_from"), self.working_memory.get("date_to"),
                    self.working_memory.get("intent_summary"),
                    series=redacao_ramos.series_do_ramo(
                        sistema, ramo,
                        self.working_memory.get("despesas", []),
                        self.working_memory.get("indicadores", []),
                    ),
                )
                textos.append({"sistema": nome, "texto": texto})
                self._enviar(redator_id, "inform", "texto", texto, recebida=True)
            except Exception as exc:  # noqa: BLE001 — um ramo não derruba os demais
                logger.warning(
                    "SupervisorAnalitico %s: redator do ramo %s falhou — %s", self.agent_id, sistema, exc
                )
                sem_texto.append({"sistema": nome, **ramo})
                self.working_memory["redacao"]["falhas"].append(sistema)
                self._enviar(redator_id, "failure", "dados", str(exc), recebida=True)
            finally:
                mc.stop()
                self._collectors.append(mc)

        if textos:
            self.working_memory["redacao"]["modo"] = "por_ramo"
            self.working_memory["textos_parciais"] = textos
            self.working_memory["_achados_sem_texto"] = sem_texto

    def _act_sintetizar_texto(self, action: dict) -> None:
        """Ação interna (reasoning): gera o texto via TextSynthesizer."""
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
            date_from = self.working_memory.get("date_from")
            date_to = self.working_memory.get("date_to")
            pergunta_usuario = self.working_memory.get("intent_summary")

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
            contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})
            series = linhas_series(
                self.working_memory.get("despesas", []),
                self.working_memory.get("indicadores", []),
            )

            adapter = StreamingAdapter(ws_queue, analysis_id, "hierarchical")

            if use_llm:
                try:
                    if self.working_memory.get("redacao", {}).get("modo") == "por_ramo":
                        # Composição a partir dos textos dos redatores de ramo.
                        token_gen = sintetizador.generate_stream_composicao(
                            self.working_memory.get("textos_parciais", []),
                            self.working_memory.get("_achados_sem_texto", []),
                            data_coverage=data_coverage,
                            enfase=enfase,
                            date_from=date_from,
                            date_to=date_to,
                            pergunta_usuario=pergunta_usuario,
                            series=series,
                        )
                    else:
                        token_gen = sintetizador.generate_stream(
                            correlacoes=correlacoes,
                            anomalias=anomalias,
                            contexto_orcamentario=contexto_orcamentario,
                            data_coverage=data_coverage,
                            enfase=enfase,
                            date_from=date_from,
                            date_to=date_to,
                            pergunta_usuario=pergunta_usuario,
                            series=series,
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
        """Ação interna (reasoning): self-check pós-síntese (Etapa 4).

        Mesmo mecanismo/regras do OrquestradorEstrela (ver docstring
        equivalente lá): `propose_actions()` só propõe este goal quando
        `use_self_check=True` e `use_llm=True`. O guard abaixo (texto
        vazio) permanece na ação porque depende de `sintetizar_texto` já
        ter rodado neste mesmo ciclo. Nunca propaga falha.
        """
        texto_analise = self.working_memory.get("texto_analise", "")
        if not texto_analise:
            return

        verif_id = f"hier-verificacao-{uuid.uuid4().hex[:8]}"
        mc = MetricsCollector(verif_id, "verificacao")
        mc.start()
        try:
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})

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
        despesas: list[dict],
        indicadores: list[dict],
        contexto_orcamentario: dict[str, Any],
        date_from: int | None = None,
        date_to: int | None = None,
        health_params: list[str] | None = None,
        intent_summary: str | None = None,
        use_llm: bool = True,
        use_self_check: bool = False,
    ) -> dict[str, Any]:
        """Executa o pipeline analítico via agentes subordinados.

        Recebe despesas, indicadores e contexto orçamentário como
        parâmetros diretos — já colhidos pelo `CoordenadorGeral` do
        retorno de `SupervisorOrcamento`/`SupervisorSaude`/`SupervisorContexto`
        (árvore estrita, sem canal lateral). Configura a working memory e
        delega ao ciclo CoALA (Req 10.3).

        Args:
            analysis_id: UUID da análise em andamento.
            ws_queue: Fila para streaming de eventos WebSocket.
            despesas: Despesas combinadas (orçamento + saúde, deduplicadas).
            indicadores: Indicadores agregados dos agentes de saúde.
            contexto_orcamentario: Tendências já calculadas por `SupervisorContexto`.
            date_from: Ano inicial do período.
            date_to: Ano final do período.
            health_params: Indicadores solicitados na análise.
            intent_summary: Resumo da intenção do usuário (Etapa 1).
            use_llm: Se True, tenta gerar texto via LLM antes do fallback.
            use_self_check: Se True (e use_llm=True), roda a verificação
                pós-síntese (Etapa 4) sobre o texto gerado.

        Returns:
            Dicionário com "correlacoes", "anomalias", "texto_analise",
            "data_coverage" e "dados_cruzados".
        """
        self._collectors = []
        self._coala_leaf_end_time = None
        for chave in ("redacao", "textos_parciais", "_achados_sem_texto"):
            self.working_memory.pop(chave, None)
        self.update_working_memory({
            "analysis_id": analysis_id,
            "_ws_queue": ws_queue,
            "despesas": despesas,
            "indicadores": indicadores,
            "contexto_orcamentario": contexto_orcamentario,
            "date_from": date_from,
            "date_to": date_to,
            "health_params": health_params,
            "intent_summary": intent_summary,
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
            "redacao": self.working_memory.get("redacao", {"modo": "unica", "ramos": [], "falhas": []}),
            "textos_parciais": self.working_memory.get("textos_parciais", []),
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

    Recebe despesas combinadas (SupervisorOrcamento + SupervisorSaude, já
    deduplicadas pelo CoordenadorGeral) como parâmetro direto de `run()`.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__(agent_id)
        self.procedural_memory = {
            "executar_contexto_orcamentario": [self._act_executar_contexto_orcamentario],
        }
        self._collectors: list[MetricsCollector] = []

    # -- Ciclo CoALA ------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe as despesas recebidas diretamente de `run()` (já na working memory)."""
        return {}

    def propose_actions(self) -> list[dict]:
        """Propõe executar a análise de tendências se há despesas disponíveis."""
        if self.working_memory.get("despesas"):
            return [{"goal": "executar_contexto_orcamentario"}]
        return []

    # -- Ações --------------------------------------------------------------

    def _act_executar_contexto_orcamentario(self, action: dict) -> None:
        """Ação externa: delega a AgenteContextoOrcamentario."""
        ctx_id = f"hier-contexto-{uuid.uuid4().hex[:8]}"
        agente_contexto = AgenteContextoOrcamentario(ctx_id)
        mc = MetricsCollector(ctx_id, "contexto_orcamentario")
        mc.start()
        try:
            despesas = self.working_memory.get("despesas", [])
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

    # -- Interface pública chamada pelo CoordenadorGeral -------------------

    def run(self, despesas: list[dict], use_llm: bool = True) -> dict[str, Any]:
        """Executa a análise de tendências orçamentárias via subordinado.

        Args:
            despesas: Despesas combinadas (orçamento + saúde, deduplicadas
                pelo CoordenadorGeral), repassadas como parâmetro direto —
                sem canal lateral entre supervisores.
            use_llm: Aceito por simetria de assinatura com os demais
                supervisores; esta análise nunca chama o LLM (cálculo
                determinístico, ver `AgenteContextoOrcamentario`).

        Returns:
            Dicionário com "contexto_orcamentario".
        """
        self._collectors = []
        self.update_working_memory({"despesas": despesas, "use_llm": use_llm})
        self.run_coala_cycle()

        result = {
            "contexto_orcamentario": self.working_memory.get("contexto_orcamentario", {}),
        }

        self.working_memory["aggregated"] = result
        logger.info("SupervisorContexto %s: pipeline complete", self.agent_id)

        return result
