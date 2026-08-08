"""
Orquestrador central da arquitetura estrela.

Hub central que instancia e coordena os agentes especializados: 5
agentes de saúde (SINAN, SIH, SIM, SI-PNI, COVID — 1 por Sistema de
Informação, todos com o mesmo padrão de deliberação real de dimensão,
exceto COVID que não tem dimensões válidas), agentes orçamentários
(AgenteOrcamentoSubfuncao — 1 por subfunção, 7 no total), analíticos e
de contexto. Intermedia toda comunicação entre agentes — nenhum agente
periférico chama outro diretamente.

Pipeline (cada etapa é uma ação nomeada em `procedural_memory`,
executada pelo ciclo CoALA da classe base):
  1. Fase de Saúde: até 5 agentes consultam Neo4j em sequência
     (ativação condicional conforme health_params)
  2. Fase de Orçamento: agentes AgenteOrcamentoSubfuncao (até 7,
     ativação condicional conforme health_params — ver _SUBFUNCAO_TOKENS)
  3. Cruzamento de dados: despesas × indicadores por subfunção
  4. Detecção de lacunas de dados
  5. Fase Analítica: contexto orçamentário, correlação, anomalias, síntese
  6. Persistência de métricas

Nota arquitetural (CoALA): diferente dos agentes-folha (domínio,
analíticos, contexto), aqui `propose_actions()` não arbitra entre
candidatos concorrentes — a ordem das macro-ações é imposta por
dependência de dados (cruzamento só pode ocorrer depois do domínio,
síntese só depois de correlação/anomalias/contexto). `evaluate_and_select`
portanto colapsa num passthrough, por decisão intencional documentada
aqui, não por omissão. O `run()` público delega inteiramente ao ciclo
`run_coala_cycle()` da base — cada etapa do pipeline hoje vive como um
método `_act_*` registrado em `procedural_memory`, o que preserva
exatamente a mesma degradação graciosa, coleta de métricas por agente e
eventos WebSocket de erro que o pipeline imperativo anterior já tinha,
só que percorridos pelo laço genérico de `execute()` em vez de um
try/except manual por etapa.

Requisitos: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 11.1, 11.2
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
# Usado para ativar apenas os agentes relevantes aos health_params do usuário
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

# Ordem fixa de avaliação dos agentes de saúde (preserva a sequência
# histórica Vigilância → Hospitalar → Primária → Mortalidade, com SINAN
# ao final; SINASC/SIA/CNES — cobertura nova sem legado — vêm por último)
_SAUDE_AGENT_KEY_ORDER: list[str] = [
    "covid", "sih", "sipni", "sim", "sinan", "sinasc", "sia", "cnes",
]

# Apenas strings (usadas para filtrar por health_params em propose_actions) —
# a resolução para classe real acontece em _act_consultar_saude, lendo o
# nome global no momento da chamada (permite mock/patch em testes).
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
    """Tokens de indicador que roteiam para um dado agente de saúde
    (retrieval sobre INDICADOR_TO_AGENT) — usado para derivar o gatilho
    de ativação das subfunções orçamentárias sem duplicar a lista de
    tokens à mão."""
    return {tipo for tipo, agente in INDICADOR_TO_AGENT.items() if agente == agent_key}


# Correspondência subfunção orçamentária → tokens de indicador que a
# tornam relevante (decisão tomada com o usuário na sessão da Fase 2):
#   301 Atenção Básica            -> SI-PNI (vacinação)
#   302 Assist. Hospitalar/Ambul. -> SIH (internações)
#   303 Suporte Profilático/Terap.-> SINAN (qualquer agravo — "qualquer
#                                    tratamento ou prevenção de doença
#                                    se encaixa")
#   304 Vigilância Sanitária      -> SINAN + só o subtipo CNES
#                                    "estabelecimentos_vigilancia_epidemiologica"
#                                    (único subtipo do CNES com par temático
#                                    real — refinamento decidido com o usuário;
#                                    os outros 11 subtipos do CNES não ativam
#                                    304, só 122/306)
#   305 Vigilância Epidemiológica -> SINAN + COVID
#   122 Administração Geral       -> qualquer subtipo do CNES (sem candidato
#                                    mais específico — decisão explícita de
#                                    não refinar sem base real)
#   306 Alimentação e Nutrição    -> qualquer subtipo do CNES (idem 122)
_SUBFUNCAO_TOKENS: dict[int, set[str]] = {
    122: _tokens_for_agent("cnes"),
    301: _tokens_for_agent("sipni"),
    302: _tokens_for_agent("sih"),
    303: _tokens_for_agent("sinan"),
    304: {"estabelecimentos_vigilancia_epidemiologica"} | _tokens_for_agent("sinan"),
    305: _tokens_for_agent("sinan") | _tokens_for_agent("covid"),
    306: _tokens_for_agent("cnes"),
}

# Mortalidade (SIM) é transversal para fins de cruzamento de dados
# (data_crossing.MORTALIDADE_SUBFUNCOES) — o indicador de mortalidade
# cruza com o gasto de várias subfunções ao mesmo tempo, não uma só.
# Diferente da Fase 1 (onde o próprio AgenteMortalidade legado buscava
# essas despesas), agora quem busca despesa é sempre
# AgenteOrcamentoSubfuncao — então o token "mortalidade" precisa ativar
# essas mesmas subfunções explicitamente, ou o cruzamento roda contra
# uma lista de despesas vazia.
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


class OrquestradorEstrela(AgenteCoALA):
    """Hub central da topologia estrela coordenando os agentes especializados (Req 9):
    5 de saúde (SINAN, SIH, SIM, SI-PNI, COVID), até 7 orçamentários
    (AgenteOrcamentoSubfuncao, 1 por subfunção), AgenteAnalitico
    (correlação+anomalias consolidadas), AgenteContextoOrcamentario,
    AgentePriorizacaoAnalitica e TextSynthesizer.

    Toda comunicação entre agentes periféricos passa por este
    orquestrador (Req 9.3). Distribui tarefas via chamadas de
    método Python, registra métricas de tempo de execução por agente
    (Req 9.7), e trata falhas com resultados parciais (Req 9.8).

    Herda de AgenteCoALA e executa de fato pelo ciclo CoALA — cada etapa
    do pipeline é uma ação registrada em `procedural_memory`, proposta em
    ordem fixa por `propose_actions()` (a ordem já reflete as dependências
    de dados entre etapas).

    Attributes:
        neo4j_client: Cliente Neo4j para queries e persistência.
    """

    def __init__(self, agent_id: str, neo4j_client: Neo4jClient) -> None:
        super().__init__(agent_id)
        self.neo4j_client = neo4j_client
        self.semantic_memory = {"indicador_to_agent": INDICADOR_TO_AGENT}
        self.procedural_memory = {
            "consultar_saude": [self._act_consultar_saude],
            "consultar_orcamento": [self._act_consultar_orcamento],
            "cruzar_dados": [self._act_cruzar_dados],
            "detectar_gaps": [self._act_detectar_gaps],
            "analisar_contexto": [self._act_analisar_contexto],
            "analisar": [self._act_analisar],
            "capturar_wallclock": [self._act_capturar_wallclock],
            "priorizar_achados": [self._act_priorizar_achados],
            "sintetizar_texto": [self._act_sintetizar_texto],
            "verificar_afirmacoes": [self._act_verificar_afirmacoes],
            "persistir_metricas": [self._act_persistir_metricas],
        }

    # -- Ciclo CoALA --------------------------------------------------------

    def perceive(self) -> dict:
        """Percebe parâmetros da análise a partir da working memory."""
        return {
            "analysis_id": self.working_memory.get("analysis_id"),
            "date_from": self.working_memory.get("date_from"),
            "date_to": self.working_memory.get("date_to"),
            "health_params": self.working_memory.get("health_params", []),
        }

    def propose_actions(self) -> list[dict]:
        """Propõe as macro-ações do pipeline, em ordem de dependência de dados.

        A ativação dos agentes de saúde é condicional aos health_params
        (retrieval de `semantic_memory["indicador_to_agent"]`) — só os
        agentes relevantes à seleção do usuário são propostos (Req 9.1).
        A ativação dos agentes orçamentários segue o mesmo critério
        (health_params vazio → nenhum agente ativado, mesmo comportamento
        histórico da fase de saúde nesta topologia).
        """
        actions: list[dict] = []
        health_params = self.working_memory.get("health_params", [])
        indicador_to_agent = self.semantic_memory["indicador_to_agent"]

        active_agent_types: set[str] = set()
        for hp in health_params:
            agent_type = indicador_to_agent.get(hp)
            if agent_type:
                active_agent_types.add(agent_type)

        for key in _SAUDE_AGENT_KEY_ORDER:
            if _AGENT_KEY_TO_TYPE[key] in active_agent_types:
                actions.append({"goal": "consultar_saude", "agent_key": key})

        logger.info(
            "[%s] OrquestradorEstrela: activating %d/%d agentes de saúde for health_params=%s",
            self.agent_id,
            len(actions),
            len(_SAUDE_AGENT_KEY_ORDER),
            health_params,
        )

        hp_set = set(health_params)
        for codigo, nome in _ORCAMENTO_SUBFUNCOES:
            if not _subfuncao_ativa(codigo, hp_set):
                continue
            actions.append({
                "goal": "consultar_orcamento", "subfuncao_codigo": codigo, "subfuncao_nome": nome
            })

        actions.append({"goal": "cruzar_dados"})
        actions.append({"goal": "detectar_gaps"})
        actions.append({"goal": "analisar_contexto"})
        actions.append({"goal": "analisar"})
        actions.append({"goal": "capturar_wallclock"})
        actions.append({"goal": "priorizar_achados"})
        actions.append({"goal": "sintetizar_texto"})
        actions.append({"goal": "verificar_afirmacoes"})
        actions.append({"goal": "persistir_metricas"})
        return actions

    # -- Ações (grounding/reasoning) -----------------------------------------

    def _act_consultar_saude(self, action: dict) -> None:
        """Ação externa: consulta um agente de saúde (Req 9.2, 9.3).

        Resolve a classe do agente pelo nome global no momento da chamada
        (não via dict pré-construído), para que os testes possam
        substituir a classe (`unittest.mock.patch`) normalmente.
        """
        specs = {
            "covid": ("covid", AgenteCOVID, "star-covid"),
            "sih": ("sih", AgenteSIH, "star-sih"),
            "sipni": ("sipni", AgenteSIPNI, "star-sipni"),
            "sim": ("sim", AgenteSIM, "star-sim"),
            "sinan": ("sinan", AgenteSINAN, "star-sinan"),
            "sinasc": ("sinasc", AgenteSINASC, "star-sinasc"),
            "sia": ("sia", AgenteSIA, "star-sia"),
            "cnes": ("cnes", AgenteCNES, "star-cnes"),
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
        # não têm dimensões válidas (SISTEMA_DIMENSOES[...] == []) e não
        # aceitam esse kwarg.
        query_kwargs: dict[str, Any] = dict(intent_summary=intent_summary, health_params=health_params)
        if action["agent_key"] in ("sinan", "sih", "sim", "sipni", "sinasc", "cnes"):
            query_kwargs["use_llm"] = self.working_memory.get("use_llm", True)

        logger.info(
            "[%s] OrquestradorEstrela: delegando a %s (agent_id=%s)",
            self.agent_id, agent_type, agent_id_str,
        )
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
                "[%s] OrquestradorEstrela: %s (agent_id=%s) returned %d despesas, %d indicadores",
                self.agent_id,
                agent_type,
                agent_id_str,
                len(result.get("despesas", [])),
                len(result.get("indicadores", [])),
            )
            self.working_memory.setdefault("_collectors", []).append((agent_id_str, agent_type, mc))
        except Exception as exc:
            mc.stop()
            logger.error(
                "[%s] OrquestradorEstrela: %s (agent_id=%s) failed — %s",
                self.agent_id, agent_type, agent_id_str, exc,
            )
            self._send_error(f"Agente {agent_type} falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((agent_id_str, agent_type, mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_consultar_orcamento(self, action: dict) -> None:
        """Ação externa: consulta um agente orçamentário
        (`AgenteOrcamentoSubfuncao`, Fase 1 — PLANO_NOVO_MODELO_DADOS.md §5)."""
        codigo = action["subfuncao_codigo"]
        nome = action["subfuncao_nome"]
        agent_id_str = f"star-orcamento-{codigo}-{uuid.uuid4().hex[:8]}"
        agent = AgenteOrcamentoSubfuncao(agent_id_str, self.neo4j_client, codigo, nome)

        analysis_id = self.working_memory["analysis_id"]
        date_from = self.working_memory["date_from"]
        date_to = self.working_memory["date_to"]

        logger.info(
            "[%s] OrquestradorEstrela: delegando a orcamento_subfuncao(%s) (agent_id=%s)",
            self.agent_id, codigo, agent_id_str,
        )
        mc = MetricsCollector(agent_id_str, "orcamento_subfuncao")
        mc.start()
        try:
            log_start = len(self.neo4j_client.query_log)
            result = agent.query(
                analysis_id, date_from, date_to,
                intent_summary=self.working_memory.get("intent_summary"),
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
                "[%s] OrquestradorEstrela: orcamento_subfuncao(%s) (agent_id=%s) returned %d despesas",
                self.agent_id, codigo, agent_id_str, len(result.get("despesas", [])),
            )
            self.working_memory.setdefault("_collectors", []).append(
                (agent_id_str, "orcamento_subfuncao", mc)
            )
        except Exception as exc:
            mc.stop()
            logger.error(
                "[%s] OrquestradorEstrela: orcamento_subfuncao(%s) (agent_id=%s) failed — %s",
                self.agent_id, codigo, agent_id_str, exc,
            )
            self._send_error(f"Agente orcamento_subfuncao({codigo}) falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append(
                (agent_id_str, "orcamento_subfuncao", mc)
            )
            raise ActionFailure(action, str(exc)) from exc

    def _act_cruzar_dados(self, action: dict) -> None:
        """Ação interna (reasoning): deduplica e cruza despesas × indicadores (Req 9.4)."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            unique_despesas = deduplicate_despesas(despesas)
            dados_cruzados = cross_domain_data(unique_despesas, indicadores)
            self.working_memory["despesas"] = unique_despesas
            self.working_memory["dados_cruzados"] = dados_cruzados
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_detectar_gaps(self, action: dict) -> None:
        """Ação interna (reasoning): detecta lacunas de dados para transparência."""
        try:
            despesas = self.working_memory.get("despesas", [])
            indicadores = self.working_memory.get("indicadores", [])
            date_from = self.working_memory["date_from"]
            date_to = self.working_memory["date_to"]
            health_params = self.working_memory.get("health_params", [])

            data_coverage = detect_data_gaps(
                despesas, indicadores, date_from, date_to, health_params
            )
            self.working_memory["data_coverage"] = data_coverage
            if data_coverage["summary"]["has_gaps"]:
                logger.warning(
                    "[%s] OrquestradorEstrela: %d data gaps detected",
                    self.agent_id,
                    data_coverage["summary"]["total_gaps"],
                )
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    def _act_analisar_contexto(self, action: dict) -> None:
        """Ação externa: delega a AgenteContextoOrcamentario (Req 9.4)."""
        ctx_id = f"star-contexto-{uuid.uuid4().hex[:8]}"
        agente_contexto = AgenteContextoOrcamentario(ctx_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando a contexto_orcamentario (agent_id=%s)",
            self.agent_id, ctx_id,
        )
        mc = MetricsCollector(ctx_id, "contexto_orcamentario")
        mc.start()
        try:
            despesas = self.working_memory.get("despesas", [])
            contexto_orcamentario = agente_contexto.analyze_trends(despesas)
            mc.stop()
            self.working_memory["contexto_orcamentario"] = contexto_orcamentario
            logger.info(
                "[%s] OrquestradorEstrela: contexto_orcamentario computed for %d subfunções",
                self.agent_id,
                len(contexto_orcamentario),
            )
            self.working_memory.setdefault("_collectors", []).append(
                (ctx_id, "contexto_orcamentario", mc)
            )
        except Exception as exc:
            mc.stop()
            logger.error(
                "[%s] OrquestradorEstrela: contexto_orcamentario failed — %s", self.agent_id, exc
            )
            self._send_error(f"Agente contexto_orcamentario falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append(
                (ctx_id, "contexto_orcamentario", mc)
            )
            raise ActionFailure(action, str(exc)) from exc

    def _act_analisar(self, action: dict) -> None:
        """Ação externa: delega a AgenteAnalitico — correlação Spearman +
        detecção de anomalias consolidadas num único agente
        (PLANO_NOVO_MODELO_DADOS.md §7 item 6, Req 9.4, 9.5)."""
        an_id = f"star-analitico-{uuid.uuid4().hex[:8]}"
        agente_analitico = AgenteAnalitico(an_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando a analitico (agent_id=%s)", self.agent_id, an_id
        )
        mc = MetricsCollector(an_id, "analitico")
        mc.start()
        try:
            dados_cruzados = self.working_memory.get("dados_cruzados", [])
            resultado = agente_analitico.analisar(dados_cruzados)
            mc.stop()
            self.working_memory["correlacoes"] = resultado["correlacoes"]
            self.working_memory["anomalias"] = resultado["anomalias"]
            logger.info(
                "[%s] OrquestradorEstrela: analitico computed %d correlações, %d anomalias",
                self.agent_id, len(resultado["correlacoes"]), len(resultado["anomalias"]),
            )
            self.working_memory.setdefault("_collectors", []).append((an_id, "analitico", mc))
        except Exception as exc:
            mc.stop()
            logger.error("[%s] OrquestradorEstrela: analitico failed — %s", self.agent_id, exc)
            self._send_error(f"Agente analitico falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((an_id, "analitico", mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_capturar_wallclock(self, action: dict) -> None:
        """Ação interna (bookkeeping): marca o fim do pipeline determinístico.

        Wall-clock mede apenas o pipeline CoALA (domínio → cruzamento →
        contexto → correlação → anomalias), excluindo o sintetizador — a
        geração de texto depende da disponibilidade da API do LLM, não
        reflete a eficiência da arquitetura multiagente em si.
        """
        self.working_memory["_orch_end"] = time.time()

    def _act_priorizar_achados(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): delega a AgentePriorizacaoAnalitica (Etapa 3).

        Roda depois de `capturar_wallclock` — assim como o sintetizador,
        seu tempo (inclui 1 chamada LLM) fica fora do wall-clock "oficial"
        usado para comparar as arquiteturas. Falha aqui nunca propaga: se a
        priorização não rodar, o sintetizador simplesmente usa os dados
        brutos sem reordenação/ênfase (comportamento pré-Etapa-3).
        """
        prior_id = f"star-priorizacao-{uuid.uuid4().hex[:8]}"
        agente_priorizacao = AgentePriorizacaoAnalitica(prior_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando priorização de achados (agent_id=%s)",
            self.agent_id, prior_id,
        )
        mc = MetricsCollector(prior_id, "priorizacao")
        mc.start()
        try:
            correlacoes = self.working_memory.get("correlacoes", [])
            anomalias = self.working_memory.get("anomalias", [])
            contexto_orcamentario = self.working_memory.get("contexto_orcamentario", {})
            intent_summary = self.working_memory.get("intent_summary")
            use_llm = self.working_memory.get("use_llm", True)

            achados_priorizados = agente_priorizacao.prioritize(
                correlacoes, anomalias, contexto_orcamentario, intent_summary, use_llm=use_llm
            )
            mc.stop()
            self.working_memory["achados_priorizados"] = achados_priorizados
            logger.info(
                "[%s] OrquestradorEstrela: achados priorizados via ângulo '%s'",
                self.agent_id, achados_priorizados.get("angulo"),
            )
            self.working_memory.setdefault("_collectors", []).append((prior_id, "priorizacao", mc))
        except Exception as exc:
            mc.stop()
            logger.warning(
                "[%s] OrquestradorEstrela: priorização de achados falhou (%s) — "
                "sintetizador seguirá sem ênfase/reordenação",
                self.agent_id, exc,
            )
            self.working_memory.setdefault("_collectors", []).append((prior_id, "priorizacao", mc))

    def _act_sintetizar_texto(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): gera o texto via TextSynthesizer (Req 9.6)."""
        sint_id = f"star-sintetizador-{uuid.uuid4().hex[:8]}"
        sintetizador = TextSynthesizer(sint_id)
        logger.info(
            "[%s] OrquestradorEstrela: delegando síntese textual (sintetizador_id=%s, use_llm=%s)",
            self.agent_id, sint_id, self.working_memory.get("use_llm", True),
        )
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

            # Correlações/anomalias/ênfase priorizadas (Etapa 3), se disponíveis
            # — nunca substitui os dados brutos usados em Q1, só a ordem/ênfase
            # do que é passado ao prompt.
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

            adapter = StreamingAdapter(ws_queue, analysis_id, "star")

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
                    )
                    texto_analise = adapter.stream_tokens(token_gen)
                    if not texto_analise:
                        # LLM returned empty — use fallback
                        texto_analise = sintetizador.generate_fallback(
                            correlacoes, anomalias, contexto_orcamentario, data_coverage,
                            date_from, date_to,
                        )
                        adapter.stream_text(texto_analise)
                except Exception:
                    logger.warning(
                        "[%s] OrquestradorEstrela: LLM streaming failed, using fallback",
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
                "[%s] OrquestradorEstrela: synthesis complete (%d chars)",
                self.agent_id, len(texto_analise),
            )
            self.working_memory.setdefault("_collectors", []).append((sint_id, "sintetizador", mc))
        except Exception as exc:
            mc.stop()
            logger.error("[%s] OrquestradorEstrela: sintetizador failed — %s", self.agent_id, exc)
            self._send_error(f"Sintetizador falhou: {exc}")
            self.working_memory.setdefault("_collectors", []).append((sint_id, "sintetizador", mc))
            raise ActionFailure(action, str(exc)) from exc

    def _act_verificar_afirmacoes(self, action: dict) -> None:
        """Ação externa (reasoning + grounding): self-check pós-síntese (Etapa 4).

        Opcional (flag `use_self_check`, default False) e só roda quando
        `use_llm=True` — não faz sentido verificar um texto gerado pelo
        fallback determinístico contra os próprios dados que o originaram
        (mesma regra já aplicada ao LLM Judge/Q2+). Falha aqui nunca
        propaga: se a verificação não rodar, o texto sintetizado permanece
        como está (comportamento pré-Etapa-4).
        """
        if not self.working_memory.get("use_self_check", False):
            return
        if not self.working_memory.get("use_llm", True):
            return

        texto_analise = self.working_memory.get("texto_analise", "")
        if not texto_analise:
            return

        verif_id = f"star-verificacao-{uuid.uuid4().hex[:8]}"
        logger.info(
            "[%s] OrquestradorEstrela: iniciando self-check pós-síntese (verif_id=%s)",
            self.agent_id, verif_id,
        )
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
                    "[%s] OrquestradorEstrela: self-check corrigiu o texto "
                    "(%d/%d afirmações não suportadas)",
                    self.agent_id, nao_suportadas, len(resultado["claims"]),
                )
            else:
                logger.info(
                    "[%s] OrquestradorEstrela: self-check concluído sem correções "
                    "(%d afirmações verificadas)",
                    self.agent_id, len(resultado["claims"]),
                )
            self.working_memory.setdefault("_collectors", []).append((verif_id, "verificacao", mc))
        except Exception as exc:
            mc.stop()
            logger.warning(
                "[%s] OrquestradorEstrela: self-check falhou (%s) — texto original mantido",
                self.agent_id, exc,
            )
            self.working_memory.setdefault("_collectors", []).append((verif_id, "verificacao", mc))

    def _act_persistir_metricas(self, action: dict) -> None:
        """Ação externa: persiste métricas de cada agente e emite evento `metric` (Req 9.7, 11.2)."""
        try:
            analysis_id = self.working_memory["analysis_id"]
            collectors: list[tuple[str, str, MetricsCollector]] = self.working_memory.get(
                "_collectors", []
            )

            for agent_id_str, agent_type, mc in collectors:
                try:
                    mc.persist(self.neo4j_client, analysis_id, "star")
                except Exception as exc:
                    logger.error(
                        "[%s] OrquestradorEstrela: failed to persist metrics for %s — %s",
                        self.agent_id,
                        agent_id_str,
                        exc,
                    )

            agent_metrics = []
            workers_time_ms = 0.0
            for _, agent_type, mc in collectors:
                # Exclui sintetizador (serviço LLM, não agente CoALA),
                # priorizacao (Etapa 3) e verificacao (Etapa 4) — todos
                # rodam depois de capturar_wallclock e incluem chamada LLM,
                # mesma lógica de exclusão para não distorcer o wall-clock.
                if agent_type in ("sintetizador", "priorizacao", "verificacao"):
                    continue
                try:
                    m = mc.collect()
                    agent_metrics.append({
                        "agentName": agent_type,
                        "executionTimeMs": m["executionTimeMs"],
                        "cpuPercent": m["cpuPercent"],
                    })
                    workers_time_ms += m["executionTimeMs"]
                except Exception:
                    pass

            orch_start = self.working_memory.get("_orch_start", time.time())
            orch_end = self.working_memory.get("_orch_end", time.time())
            wall_clock_ms = round((orch_end - orch_start) * 1000, 2)
            overhead_ms = round(max(0, wall_clock_ms - workers_time_ms), 2)

            agent_metrics.append({
                "agentName": "orquestrador_estrela",
                "executionTimeMs": overhead_ms,
                "cpuPercent": 0.0,
            })

            ws_queue = self.working_memory["_ws_queue"]
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": "star",
                "type": "metric",
                "payload": {
                    "architecture": "star",
                    "totalExecutionTimeMs": wall_clock_ms,
                    "workersTimeMs": round(workers_time_ms, 2),
                    "overheadTimeMs": overhead_ms,
                    "agentMetrics": agent_metrics,
                },
            })
            ws_queue.put({
                "analysisId": analysis_id,
                "architecture": "star",
                "type": "agent_data",
                "payload": {
                    "architecture": "star",
                    "agents": self.working_memory.get("agent_queries", []),
                },
            })
        except Exception as exc:
            raise ActionFailure(action, str(exc)) from exc

    # -- Helpers --------------------------------------------------------

    def _send_error(self, message: str) -> None:
        """Envia evento de erro via ws_queue (Req 9.8), se disponível."""
        ws_queue = self.working_memory.get("_ws_queue")
        if ws_queue is None:
            return
        ws_queue.put({
            "analysisId": self.working_memory.get("analysis_id"),
            "architecture": "star",
            "type": "error",
            "payload": message,
        })

    # -- Interface pública ------------------------------------------------

    def run(
        self,
        analysis_id: str,
        params: dict[str, Any],
        ws_queue: Queue,
    ) -> dict[str, Any]:
        """Executa o pipeline completo da arquitetura estrela com 8 agentes.

        Configura a working memory com os parâmetros da análise e delega
        ao ciclo CoALA (`run_coala_cycle`), que percorre as macro-ações
        registradas em `procedural_memory` na ordem proposta por
        `propose_actions` (Req 9.1-9.8).

        Args:
            analysis_id: UUID da análise.
            params: Dicionário com date_from, date_to, health_params.
            ws_queue: Fila para streaming de eventos WebSocket.

        Returns:
            Dicionário com resultado completo da análise.
        """
        self.update_working_memory({
            "analysis_id": analysis_id,
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "health_params": params.get("health_params", []),
            "use_llm": params.get("use_llm", True),
            "use_self_check": params.get("use_self_check", False),
            "intent_summary": params.get("intent_summary"),
            "_ws_queue": ws_queue,
            "_orch_start": time.time(),
            "despesas": [],
            "indicadores": [],
            "_collectors": [],
        })

        self.run_coala_cycle()

        result = {
            "despesas": self.working_memory.get("despesas", []),
            "indicadores": self.working_memory.get("indicadores", []),
            "dados_cruzados": self.working_memory.get("dados_cruzados", []),
            "contexto_orcamentario": self.working_memory.get("contexto_orcamentario", {}),
            "correlacoes": self.working_memory.get("correlacoes", []),
            "anomalias": self.working_memory.get("anomalias", []),
            "texto_analise": self.working_memory.get("texto_analise", ""),
            "data_coverage": self.working_memory.get("data_coverage", {}),
            "self_check": self.working_memory.get("self_check"),
            "agent_queries": self.working_memory.get("agent_queries", []),
        }

        self.working_memory["result"] = result
        logger.info("OrquestradorEstrela %s: pipeline complete", self.agent_id)

        return result
