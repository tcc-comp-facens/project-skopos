"""
Bateria de cenários que reproduz, fora da UI, o que o sistema faz numa conversa
de chat — e mede com as mesmas métricas que o código produz.

Fluxo (o mesmo de `api/chat_websocket.py` + `api/websocket.py`):

  1. Recusa mensagens acima de `MAX_MESSAGE_LENGTH`, como o chat.
  2. `AgenteInterpretacaoIntencao.parse()` com `TokenBucket`, com a memória
     episódica ligada (a UI a liga; `--no-episodic` desliga).
  3. Se a pergunta é aceita, `run_chat_analysis()` → `dispatch_analysis()`: as
     duas topologias rodam em threads concorrentes, com os runners reais.
  4. Leitura da fila como o WebSocket faz (eventos `metric`, `error`, `done`).
  5. `compute_all_quality_metrics()`, RAGAS (`_run_ragas_evaluation`) e o
     veredito (`_decide_winner`: fidelidade > completude > tempo).

Nada é reimplementado aqui: as métricas do relatório são as do payload de
`/api/analysis/{id}/quality`. Única diferença deliberada: o nó `Analise` não é
gravado no Neo4j (`_SemGravacao`), então o lote não deixa dados nem altera a
memória episódica usada pelo chat. O payload completo de cada análise (qualidade,
RAGAS, relatório comparativo) vai para `<saida>.payloads.jsonl`.

Aviso de custo: cada cenário aceito dispara as duas topologias e o RAGAS
(chamadas LLM reais). Comece com `--limit` ou `--categories`.

Uso (na raiz do backend, com o .env carregado):

    python -m scripts.batch_eval --limit 5
    python -m scripts.batch_eval --categories obvio_explicito,ambiguo_periodo
    python -m scripts.batch_eval --no-ragas --out resultados.xlsx
    python -m scripts.batch_eval --categories falha_injetada --no-ragas   # só resiliência (falhas injetadas)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from queue import Empty
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agents.intent.agente_interpretacao_intencao import (  # noqa: E402
    AgenteInterpretacaoIntencao,
    AnalysisIntent,
)
from agents.analytical.analitico import AgenteAnalitico  # noqa: E402
from agents.context.contexto_orcamentario import AgenteContextoOrcamentario  # noqa: E402
from agents.domain.agente_cnes import AgenteCNES  # noqa: E402
from agents.domain.agente_covid import AgenteCOVID  # noqa: E402
from agents.domain.agente_orcamento import AgenteOrcamentoSubfuncao  # noqa: E402
from agents.domain.agente_sia import AgenteSIA  # noqa: E402
from agents.domain.agente_sih import AgenteSIH  # noqa: E402
from agents.domain.agente_sim import AgenteSIM  # noqa: E402
from agents.domain.agente_sinan import AgenteSINAN  # noqa: E402
from agents.domain.agente_sinasc import AgenteSINASC  # noqa: E402
from agents.domain.agente_sipni import AgenteSIPNI  # noqa: E402
from agents.hierarchical.supervisors import SupervisorAnalitico, SupervisorSaude  # noqa: E402
from api.chat_runner import run_chat_analysis  # noqa: E402
from api.chat_websocket import MAX_MESSAGE_LENGTH  # noqa: E402
from api.dispatch import get_available_year_range  # noqa: E402
from api.state import active_queues, active_results, active_threads  # noqa: E402
from api.websocket import _run_ragas_evaluation  # noqa: E402
import core.llm_client as llm_client_module  # noqa: E402
from core import quality_metrics, ragas_metrics  # noqa: E402
from core.llm_client import PROVIDERS, TokenBucket, has_api_key  # noqa: E402
from scripts import batch_report  # noqa: E402
from db.neo4j_client import Neo4jClient  # noqa: E402


@dataclass
class ModelSpec:
    label: str
    provider: str  # "deepseek" ou "openai" (chaves de core.llm_client.PROVIDERS)
    model: str | None = None  # None = default do provedor


@dataclass
class Scenario:
    scenario_id: str
    category: str
    question: str
    reference_year: int | None = None  # None = ano corrente (ver AgenteInterpretacaoIntencao.parse)
    fault: str | None = None  # id em FAULT_SPECS: falha injetada durante a execução das topologias


# --- Modelos a comparar ----------------------------------------------------

MODELS: list[ModelSpec] = [
    # Modelo de produção: usa o OPENAI_MODEL do .env (hoje gpt-5.6-luna), como a UI.
    ModelSpec("openai-default", "openai"),
    # ModelSpec("deepseek-default", "deepseek"),
    # ModelSpec("openai-mini", "openai", "gpt-5-mini"),
]


# --- Cenários ---------------------------------------------------------------
# 51 perguntas, todas desenhadas para passar da interpretação e gerar
# análise, variando tema, período, objetividade e quão óbvio é qual sistema
# consultar. Os cenários que só exercitavam o critério de admissão (fora de
# escopo, injeção, conversa, datas fora da série e mensagens acima de
# MAX_MESSAGE_LENGTH) foram retirados: não geravam análise das topologias.
# O guardrail continua coberto pelos testes unitários.
#
# Os dados cobrem 2016-2025 e a interpretação exige ano inicial < ano final.
# Perguntas com data relativa ("últimos 5 anos") usam o ano corrente, como a UI:
# com dados só até 2025, elas podem ser recusadas — é o comportamento real.

SCENARIOS: list[Scenario] = [
    # -- objetivas e explícitas: tema e período claros ----------------------
    Scenario(
        "obvio_dengue_periodo", "obvio_explicito",
        "Quantos casos de dengue foram registrados em Sorocaba entre 2020 "
        "e 2023, e como isso se relacionou com o orçamento de vigilância "
        "epidemiológica no mesmo período?",
    ),
    Scenario(
        "obvio_vacinacao_periodo", "obvio_explicito",
        "Compare a cobertura vacinal em Sorocaba entre 2018 e 2022 com o "
        "investimento em atenção básica.",
    ),
    Scenario(
        "obvio_internacoes_periodo", "obvio_explicito",
        "Como evoluiu o número de internações hospitalares entre 2019 e "
        "2021, e isso teve relação com o orçamento de assistência "
        "hospitalar?",
    ),
    Scenario(
        "obvio_mortalidade_periodo", "obvio_explicito",
        "Qual foi a variação da mortalidade em Sorocaba entre 2016 e 2020?",
    ),
    Scenario(
        "obvio_tuberculose_periodo", "obvio_explicito",
        "Como se comportaram os casos de tuberculose entre 2016 e 2022 e o "
        "gasto com vigilância epidemiológica?",
    ),
    Scenario(
        "obvio_covid_periodo", "obvio_explicito",
        "Quantos casos e óbitos de covid ocorreram entre 2020 e 2022 e "
        "como o orçamento de saúde reagiu?",
    ),
    Scenario(
        "obvio_nascidos_vivos_periodo", "obvio_explicito",
        "Os nascidos vivos em Sorocaba aumentaram ou diminuíram entre 2016 "
        "e 2024? Compare com o orçamento da atenção básica.",
    ),
    Scenario(
        "obvio_hepatites_periodo", "obvio_explicito",
        "Analise os casos de hepatites virais entre 2016 e 2023 frente ao "
        "orçamento de vigilância epidemiológica.",
    ),

    # -- ambíguas quanto ao sistema: período claro, tema vago ----------------
    Scenario(
        "ambiguo_sistema_saude_geral", "ambiguo_sistema",
        "Como está a situação da saúde pública em Sorocaba entre 2020 e 2023?",
    ),
    Scenario(
        "ambiguo_sistema_mudou", "ambiguo_sistema",
        "O que mudou na saúde da cidade nos últimos 5 anos?",
    ),
    Scenario(
        "ambiguo_sistema_melhora", "ambiguo_sistema",
        "Dá pra ver alguma melhora nos indicadores de saúde entre 2018 e 2022?",
    ),
    Scenario(
        "ambiguo_sistema_pandemia", "ambiguo_sistema",
        "Como foi o desempenho da saúde pública de Sorocaba durante a "
        "pandemia, entre 2020 e 2021?",
    ),
    Scenario(
        "ambiguo_sistema_mulher_bebe", "ambiguo_sistema",
        "Como evoluiu a saúde da mulher e do recém-nascido em Sorocaba "
        "entre 2018 e 2024?",
    ),
    Scenario(
        "ambiguo_sistema_infecciosas", "ambiguo_sistema",
        "Quais doenças infecciosas pesaram mais na cidade entre 2019 e 2023?",
    ),

    # -- ambíguas quanto ao período: tema claro, período vago ----------------
    Scenario(
        "ambiguo_periodo_vacinacao", "ambiguo_periodo",
        "A cobertura vacinal em Sorocaba tem melhorado?",
    ),
    Scenario(
        "ambiguo_periodo_dengue", "ambiguo_periodo",
        "Os casos de dengue estão diminuindo?",
    ),
    Scenario(
        "ambiguo_periodo_mortalidade", "ambiguo_periodo",
        "Como está a mortalidade na cidade?",
    ),
    Scenario(
        "ambiguo_periodo_leitos", "ambiguo_periodo",
        "O número de leitos hospitalares tem acompanhado o crescimento da cidade?",
    ),
    Scenario(
        "ambiguo_periodo_mortalidade_internacoes", "ambiguo_periodo",
        "A mortalidade e as internações caíram ao longo do tempo?",
    ),

    # -- nem tema nem período especificados ----------------------------------
    Scenario(
        "ambiguo_total_investimento", "ambiguo_total",
        "O dinheiro público está sendo bem investido na saúde?",
    ),
    Scenario(
        "ambiguo_total_melhora", "ambiguo_total",
        "A saúde em Sorocaba está melhorando?",
    ),
    Scenario(
        "ambiguo_total_vale_a_pena", "ambiguo_total",
        "Vale a pena o investimento atual em saúde pública?",
    ),

    # -- data invertida (normalizável) -----------------------------------------
    Scenario(
        "data_invertida", "data_invalida",
        "Compare os casos de dengue entre 2023 e 2019, nessa ordem exata "
        "(do mais recente para o mais antigo).",
    ),

    # -- período curto (2 anos, o mínimo aceito pela interpretação) ----------
    Scenario("data_curta_dengue", "data_estreita", "Como foi a dengue entre 2021 e 2022?"),
    Scenario(
        "data_curta_vacinacao", "data_estreita",
        "O que aconteceu com a cobertura vacinal entre 2020 e 2021?",
    ),
    Scenario(
        "data_curta_covid_pico", "data_estreita",
        "Como a covid evoluiu entre 2021 e 2022, no auge das ondas?",
    ),

    # -- múltiplos sistemas explícitos na mesma pergunta ---------------------
    Scenario(
        "multi_sistema_tres", "multi_sistema_explicito",
        "Compare dengue, cobertura vacinal e leitos hospitalares em "
        "Sorocaba entre 2019 e 2023.",
    ),
    Scenario(
        "multi_sistema_internacao_mortalidade_profissionais", "multi_sistema_explicito",
        "Quero entender a relação entre internações, mortalidade e o "
        "número de profissionais de saúde entre 2017 e 2022.",
    ),
    Scenario(
        "multi_sistema_doencas_negligenciadas", "multi_sistema_explicito",
        "Analise conjuntamente tuberculose, hanseníase e hepatites virais "
        "entre 2016 e 2023.",
    ),
    Scenario(
        "multi_sistema_arboviroses", "multi_sistema_explicito",
        "Compare dengue, chikungunya e covid entre 2020 e 2024.",
    ),
    Scenario(
        "multi_sistema_capacidade", "multi_sistema_explicito",
        "Relacione profissionais, equipes de saúde e leitos entre 2018 e 2024.",
    ),

    # -- indicadores raros/menos comuns (fora dos mais usados) ---------------
    Scenario(
        "sistema_raro_hanseniase", "sistema_raro",
        "Como evoluíram os casos de hanseníase em Sorocaba entre 2016 e 2023?",
    ),
    Scenario(
        "sistema_raro_estabelecimentos", "sistema_raro",
        "Quantos estabelecimentos de saúde por tipo existem em Sorocaba e "
        "como isso mudou frente ao orçamento entre 2018 e 2023?",
    ),
    Scenario(
        "sistema_raro_equipes", "sistema_raro",
        "Como variou o número de equipes de saúde da família entre 2019 e 2023?",
    ),
    Scenario(
        "sistema_raro_sifilis_congenita", "sistema_raro",
        "Os casos de sífilis congênita aumentaram ou diminuíram entre 2018 e 2022?",
    ),
    Scenario(
        "sistema_raro_coqueluche", "sistema_raro",
        "Houve surtos de coqueluche em Sorocaba entre 2016 e 2024?",
    ),
    Scenario(
        "sistema_raro_equipamentos_ocupacoes", "sistema_raro",
        "Como evoluíram os equipamentos de saúde e as ocupações de "
        "profissionais entre 2019 e 2025?",
    ),

    # -- comparações/correlações mais complexas ------------------------------
    Scenario(
        "complexa_pre_pos_pandemia", "comparacao_complexa",
        "Como os indicadores de internação mudaram do período "
        "pré-pandemia (2018-2019) para o pós-pandemia (2022-2023)?",
    ),
    Scenario(
        "complexa_correlacao_orcamento_dengue", "comparacao_complexa",
        "Existe correlação entre o aumento do orçamento de vigilância "
        "epidemiológica e a queda nos casos de dengue entre 2017 e 2023?",
    ),
    Scenario(
        "complexa_variacao_subfuncoes", "comparacao_complexa",
        "Quais subfunções orçamentárias tiveram maior variação percentual "
        "entre 2016 e 2024, e isso se refletiu em algum indicador de saúde?",
    ),
    Scenario(
        "complexa_investimento_vs_atencao_basica", "comparacao_complexa",
        "O investimento em atenção básica cresceu mais rápido que os "
        "indicadores de produção ambulatorial entre 2016 e 2023?",
    ),
    Scenario(
        "complexa_arbovirose_vs_vigilancia", "comparacao_complexa",
        "O gasto com vigilância epidemiológica acompanhou a evolução de "
        "dengue e chikungunya entre 2018 e 2024?",
    ),
    Scenario(
        "complexa_orcamento_vs_mortalidade", "comparacao_complexa",
        "Em que anos o orçamento de saúde cresceu mais e o que aconteceu "
        "com a mortalidade e as internações entre 2016 e 2025?",
    ),

    # -- pouco objetivas: vagas, emocionais, sem pedido claro ----------------
    Scenario(
        "pouco_objetiva_sei_la", "pouco_objetiva",
        "Sei lá, queria entender melhor como anda a saúde por aqui, será "
        "que dá pra dizer alguma coisa?",
    ),
    Scenario(
        "pouco_objetiva_tipo", "pouco_objetiva",
        "Tipo, queria saber se as coisas melhoraram ou pioraram na saúde, "
        "não sei bem o período.",
    ),
    Scenario(
        "pouco_objetiva_preocupado", "pouco_objetiva",
        "Estou preocupado com a saúde pública da minha cidade, pode me "
        "contar alguma coisa relevante?",
    ),
    Scenario(
        "pouco_objetiva_interessante", "pouco_objetiva",
        "Me conta algo interessante sobre a saúde de Sorocaba entre 2019 e 2023.",
    ),

    # -- muito objetivas: curtas, diretas, quase telegráficas ---------------
    Scenario("muito_objetiva_dengue", "muito_objetiva", "Casos de dengue, 2021-2022."),
    Scenario("muito_objetiva_vacinacao_orcamento", "muito_objetiva", "Cobertura vacinal 2020-2021 vs orçamento."),
    Scenario("muito_objetiva_leitos", "muito_objetiva", "Leitos hospitalares 2022-2023."),
    Scenario("muito_objetiva_tuberculose", "muito_objetiva", "Tuberculose 2018-2023."),
]


# --- Falhas injetadas (resiliência) ----------------------------------------
# Cada cenário-base abaixo (BASES_FALHA) é repetido uma vez por falha ativa, com a MESMA pergunta
# (a interpretação é reaproveitada do cenário-base, então os parâmetros
# resolvidos são idênticos e a comparação é pareada). A falha só vale durante
# a execução das topologias, depois da interpretação. As classes de folha
# são as mesmas nas duas topologias, então um patch na classe vale para ambas.
# Falhas marcadas `apenas="hierarchical"` não têm equivalente na estrela.

_SAUDE_CLASSES = (
    AgenteSINAN, AgenteSIH, AgenteSIM, AgenteSIPNI,
    AgenteCOVID, AgenteSINASC, AgenteSIA, AgenteCNES,
)

FAULT_SPECS: dict[str, dict] = {
    "saude_um": {"descricao": "1º agente de saúde invocado falha (os demais seguem)", "apenas": None},
    "saude_todos": {"descricao": "todos os agentes de saúde falham", "apenas": None},
    "orcamento_todos": {"descricao": "todos os agentes orçamentários falham", "apenas": None},
    "contexto": {"descricao": "agente de contexto orçamentário falha", "apenas": None},
    "analitico": {"descricao": "agente analítico (correlação/anomalias) falha", "apenas": None},
    "neo4j_fora": {"descricao": "Neo4j indisponível (consultas lançam erro)", "apenas": None},
    "llm_fora": {"descricao": "LLM indisponível após a interpretação (generate/generate_stream)", "apenas": None},
    "supervisor_saude": {"descricao": "SupervisorSaude inteiro falha", "apenas": "hierarchical"},
    "supervisor_analitico": {"descricao": "SupervisorAnalitico inteiro falha", "apenas": "hierarchical"},
}

# Bateria de falhas enxuta: 1 pergunta-base × 3 falhas escolhidas. O catálogo
# FAULT_SPECS acima tem mais tipos (disponíveis para quem quiser ampliar), mas
# estes três bastam para a comparação:
#   saude_todos          falha de folha (controle: as duas topologias degradam igual)
#   supervisor_analitico falha de ramo (a única que separa as topologias: leva o texto junto)
#   llm_fora             LLM cai depois da interpretação (fallback determinístico)
BASES_FALHA = ("obvio_dengue_periodo",)
FALHAS_ATIVAS = ("saude_todos", "supervisor_analitico", "llm_fora")

_por_id = {s.scenario_id: s for s in SCENARIOS}
SCENARIOS += [
    Scenario(
        f"falha__{fault_id}__{base_id}", "falha_injetada",
        _por_id[base_id].question, _por_id[base_id].reference_year, fault=fault_id,
    )
    for base_id in BASES_FALHA
    for fault_id in FALHAS_ATIVAS
]


def _boom(nome: str):
    def _raise(*args, **kwargs):
        raise RuntimeError(f"falha injetada: {nome}")
    return _raise


def _fault_stack(fault: str) -> ExitStack:
    """ExitStack com os patches da falha `fault`; use como context manager."""
    stack = ExitStack()
    if fault == "saude_um":
        estado = {"falhar": True}
        for cls in _SAUDE_CLASSES:
            original = cls.query

            def _wrapper(self, *args, __orig=original, **kwargs):
                if estado["falhar"]:
                    estado["falhar"] = False
                    raise RuntimeError("falha injetada: saude_um")
                return __orig(self, *args, **kwargs)

            stack.enter_context(mock.patch.object(cls, "query", _wrapper))
    elif fault == "saude_todos":
        for cls in _SAUDE_CLASSES:
            stack.enter_context(mock.patch.object(cls, "query", _boom("saude_todos")))
    elif fault == "orcamento_todos":
        stack.enter_context(mock.patch.object(AgenteOrcamentoSubfuncao, "query", _boom("orcamento_todos")))
    elif fault == "contexto":
        stack.enter_context(mock.patch.object(AgenteContextoOrcamentario, "analyze_trends", _boom("contexto")))
    elif fault == "analitico":
        stack.enter_context(mock.patch.object(AgenteAnalitico, "analisar", _boom("analitico")))
    elif fault == "neo4j_fora":
        for nome in ("get_despesas_por_subfuncao", "get_indicadores_por_sistema", "get_variacao_anual"):
            stack.enter_context(mock.patch.object(Neo4jClient, nome, _boom("neo4j_fora")))
    elif fault == "llm_fora":
        stack.enter_context(mock.patch.object(llm_client_module, "generate", _boom("llm_fora")))
        stack.enter_context(mock.patch.object(llm_client_module, "generate_stream", _boom("llm_fora")))
    elif fault == "supervisor_saude":
        stack.enter_context(mock.patch.object(SupervisorSaude, "run", _boom("supervisor_saude")))
    elif fault == "supervisor_analitico":
        stack.enter_context(mock.patch.object(SupervisorAnalitico, "run", _boom("supervisor_analitico")))
    else:
        raise ValueError(f"falha desconhecida: {fault}")
    return stack


# ---------------------------------------------------------------------------


_MODEL_ENV_ORIGINAL = {p.model_env: os.environ.get(p.model_env, "") for p in PROVIDERS.values()}


def _set_model_env(spec: ModelSpec) -> None:
    """Troca o provedor/modelo ativo via env var — lido a cada chamada por
    `core.llm_client.get_provider()`/`resolve_model()`, então basta setar
    antes de cada `.parse()`/`.run()`; os agentes chamam
    `llm_client.generate()` sem passar provider/model explícito, então o
    env var é o único lever."""
    os.environ["LLM_PROVIDER"] = spec.provider
    provider_cfg = PROVIDERS[spec.provider]
    if spec.model:
        os.environ[provider_cfg.model_env] = spec.model
    elif _MODEL_ENV_ORIGINAL.get(provider_cfg.model_env):
        # Sem override no ModelSpec: usa o modelo do .env, como a UI faz.
        os.environ[provider_cfg.model_env] = _MODEL_ENV_ORIGINAL[provider_cfg.model_env]
    else:
        os.environ.pop(provider_cfg.model_env, None)


def _run_intent(
    scenario: Scenario, min_year: int | None, max_year: int | None,
    neo4j_client: Neo4jClient | None,
) -> dict:
    """Interpretação igual à do chat (`api/chat_websocket.py`): mesmo agente,
    mesmo `TokenBucket` ao redor de `parse()`."""
    interpreter = AgenteInterpretacaoIntencao(
        agent_id=f"batch-intent-{uuid.uuid4().hex[:8]}",
        min_year=min_year, max_year=max_year,
        neo4j_client=neo4j_client,
    )
    bucket = TokenBucket()
    inicio = time.time()
    with bucket:
        result = interpreter.parse(scenario.question, reference_year=scenario.reference_year)
    return {
        "interpreter": interpreter,
        "result": result,
        "elapsed_ms": (time.time() - inicio) * 1000,
        "token_usage": bucket.snapshot(),
    }


class _SemGravacao:
    """Cliente que `dispatch_analysis` usa só para gravar o nó Analise.

    Todo o resto do fluxo (threads, runners, TokenBucket, active_results) é o
    real. Sem a Analise no banco, a gravação de métricas e o
    `_persist_topology_result` viram no-op (MATCH vazio): o lote não deixa nada
    no Neo4j nem contamina a memória episódica do chat.
    """

    def save_analise(self, *args, **kwargs) -> None:
        return None

    def close(self) -> None:
        return None


def _consumir_fila(analysis_id: str, timeout_s: float) -> tuple[dict, dict, dict]:
    """Mesma leitura da fila que `api/websocket.py` faz: captura o evento
    `metric` de cada topologia e espera os dois `done`."""
    ws_queue = active_queues[analysis_id]
    metricas: dict[str, list] = {"star": [], "hierarchical": []}
    relogio: dict[str, float] = {"star": 0, "hierarchical": 0}
    erros: dict[str, int] = {"star": 0, "hierarchical": 0}
    done = 0
    limite = time.time() + timeout_s
    while done < 2:
        if time.time() > limite:
            raise TimeoutError(f"análise {analysis_id[:8]} não terminou em {timeout_s:.0f}s")
        try:
            evento = ws_queue.get(timeout=1.0)
        except Empty:
            continue
        tipo, arq, payload = evento.get("type"), evento.get("architecture"), evento.get("payload")
        if tipo == "metric" and isinstance(payload, dict) and payload.get("architecture") in metricas:
            arch = payload["architecture"]
            metricas[arch] = payload.get("agentMetrics", [])
            relogio[arch] = payload.get("totalExecutionTimeMs", 0)
        elif tipo == "error" and arq in erros:
            erros[arq] += 1
        elif tipo == "done":
            done += 1
    return metricas, relogio, erros


def _score_ragas(ragas: dict | None, arch: str, nome: str):
    metrica = (((ragas or {}).get(arch) or {}).get("metrics") or {}).get(nome) or {}
    score = metrica.get("score")
    return score if isinstance(score, (int, float)) else None


def _run_analysis_like_ui(
    scenario: Scenario, intent_run: dict, args: argparse.Namespace, fault: str | None,
) -> dict:
    """Dispara a análise pelo caminho do chat (`run_chat_analysis` →
    `dispatch_analysis` → threads concorrentes de estrela e hierárquica) e
    calcula as métricas com as mesmas funções do handler `api/websocket.py`:
    `compute_all_quality_metrics`, RAGAS (`_run_ragas_evaluation`) e o veredito
    (`_decide_winner`, via `generate_comparative_report`)."""
    resultado = intent_run["result"]
    with ExitStack() as stack:
        stack.enter_context(mock.patch("api.dispatch.get_neo4j_client", return_value=_SemGravacao()))
        if fault:
            stack.enter_context(_fault_stack(fault))
        analysis_id, _confirmacao = run_chat_analysis(
            params=resultado.params,
            use_llm=True,
            source_question=scenario.question,
            interpreted_via=resultado.interpreted_via,
            interpreter=intent_run["interpreter"],
            use_self_check=args.use_self_check,
            intent_token_usage=intent_run["token_usage"],
        )
        try:
            metricas, relogio, erros = _consumir_fila(analysis_id, args.timeout)
        finally:
            for th in active_threads.get(analysis_id, []):
                th.join(timeout=30)

    resultados = active_results.get(analysis_id, {})
    star_result = resultados.get("star", {})
    hier_result = resultados.get("hierarchical", {})
    saida = {"analysis_id": analysis_id, "erros": erros, "relogio": relogio, "resultados": resultados}
    try:
        if not (star_result and hier_result):
            # A UI também não calcula métricas quando falta o resultado de uma topologia.
            saida["sem_resultado"] = [t for t, r in (("star", star_result), ("hierarchical", hier_result)) if not r]
            return saida

        quality = quality_metrics.compute_all_quality_metrics(
            star_result=star_result,
            hier_result=hier_result,
            star_agent_metrics=metricas["star"],
            hier_agent_metrics=metricas["hierarchical"],
            star_wall_clock_ms=relogio["star"],
            hier_wall_clock_ms=relogio["hierarchical"],
            star_token_usage=resultados.get("star_token_usage"),
            hier_token_usage=resultados.get("hier_token_usage"),
            intent_token_usage=resultados.get("intent_token_usage"),
        )
        ragas = None
        if not args.no_ragas:
            _texto_ragas, ragas = asyncio.run(_run_ragas_evaluation(
                analysis_id=analysis_id, results=resultados,
                star_result=star_result, hier_result=hier_result,
            ))
        star_total = relogio["star"] if relogio["star"] > 0 else quality["efficiency"]["star"]["latency_breakdown"]["total_ms"]
        hier_total = relogio["hierarchical"] if relogio["hierarchical"] > 0 else quality["efficiency"]["hierarchical"]["latency_breakdown"]["total_ms"]
        vencedor, criterio = quality_metrics._decide_winner(quality, ragas, star_total, hier_total)
        relatorio = quality_metrics.generate_comparative_report(
            quality=quality,
            star_agent_metrics=metricas["star"],
            hier_agent_metrics=metricas["hierarchical"],
            data_coverage=star_result.get("data_coverage"),
            star_wall_clock_ms=relogio["star"],
            hier_wall_clock_ms=relogio["hierarchical"],
            star_result=star_result,
            hier_result=hier_result,
            ragas=ragas,
        )
        saida.update({
            "quality": quality, "ragas": ragas, "vencedor": vencedor,
            "criterio": criterio, "relatorio": relatorio,
        })
        return saida
    finally:
        active_queues.pop(analysis_id, None)
        active_threads.pop(analysis_id, None)
        active_results.pop(analysis_id, None)


def _linhas_da_analise(base_row: dict, analise: dict) -> list[dict]:
    """Uma linha por topologia, só com campos que o código do sistema produz."""
    rows = []
    quality, ragas, resultados = analise.get("quality"), analise.get("ragas"), analise["resultados"]
    for topo in ("star", "hierarchical"):
        row = dict(base_row)
        row["topology"] = topo
        row["error_events"] = analise["erros"][topo]
        if quality is None:
            row["run_error"] = f"sem resultado da topologia: {','.join(analise.get('sem_resultado', []))}"
            row.update({"r1_score": 0.0, "r1_completed": 0, "texto_gerado": 0})
            rows.append(row)
            continue
        eff = quality["efficiency"][topo]
        overhead, lat = eff["coordination_overhead"], eff["latency_breakdown"]
        comp = quality["quality"][topo]["completeness"]
        r1, cost = quality["resilience"][topo], quality["cost"][topo]
        com, out = quality["communication"][topo], quality["outcome"][topo]
        texto = (resultados.get(topo) or {}).get("texto_analise", "") or ""
        row.update({
            "wall_clock_ms": round(analise["relogio"][topo], 1),
            "overhead_ms": overhead["supervisor_time_ms"],
            "overhead_percent": overhead["overhead_percent"],
            "fase_dominio_ms": lat["dominio"]["time_ms"],
            "fase_analitico_ms": lat["analitico"]["time_ms"],
            "fase_contexto_ms": lat["contexto"]["time_ms"],
            "fase_supervisores_ms": lat["supervisores"]["time_ms"],
            "completude": comp["score"],
            "completude_correlacoes": comp["correlacoes_coverage"],
            "completude_anomalias": comp["anomalias_coverage"],
            "completude_contexto": comp["contexto_coverage"],
            "ragas_faithfulness": _score_ragas(ragas, topo, "faithfulness"),
            "ragas_answer_relevancy": _score_ragas(ragas, topo, "answer_relevancy"),
            "ragas_context_relevance": _score_ragas(ragas, topo, "context_relevance"),
            "r1_score": r1["score"],
            "r1_completed": r1["completed"],
            "texto_gerado": 1 if r1["components"].get("texto_analise") else 0,
            "prompt_tokens": cost["prompt_tokens"],
            "completion_tokens": cost["completion_tokens"],
            "total_tokens": cost["total_tokens"],
            "llm_call_count": cost["call_count"],
            "agent_invocations": com["agent_invocations"],
            "message_count": com["message_count"],
            "payload_records": com["payload_records"],
            "sucesso": 1 if out["success"] else 0,
            "dentro_do_orcamento_de_tempo": 1 if out["within_time_budget"] else 0,
            "consistencia_identica": 1 if quality["quality"]["deterministic_consistency"]["all_identical"] else 0,
            "vencedor": analise["vencedor"],
            "criterio_veredito": analise["criterio"],
            "texto_analise_len": len(texto),
        })
        rows.append(row)
    return rows


def _select_scenarios(args: argparse.Namespace) -> list[Scenario]:
    scenarios = SCENARIOS
    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",") if c.strip()}
        scenarios = [s for s in scenarios if s.category in wanted]
    if args.scenario_ids:
        wanted_ids = {s.strip() for s in args.scenario_ids.split(",") if s.strip()}
        scenarios = [s for s in scenarios if s.scenario_id in wanted_ids]
    if args.limit:
        scenarios = scenarios[: args.limit]
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-ragas", action="store_true", help="pula a avaliação RAGAS (mais rápido/barato)")
    parser.add_argument(
        "--intent-only", action="store_true",
        help="roda só a interpretação de intenção (barato, ~2s por cenário), sem as topologias — "
             "útil para conferir quantos cenários passam antes da bateria completa",
    )
    parser.add_argument("--use-self-check", action="store_true", help="liga a verificação pós-síntese (Etapa 4)")
    parser.add_argument(
        "--no-episodic", action="store_true",
        help="não passa o Neo4jClient à interpretação (a UI passa, habilitando a memória episódica "
             "de análises anteriores); use para cenários independentes do histórico do banco.",
    )
    parser.add_argument("--timeout", type=float, default=900, help="segundos máximos por análise (default 900)")
    parser.add_argument("--categories", default=None, help="filtra por categoria(s), separadas por vírgula")
    parser.add_argument("--scenario-ids", default=None, help="filtra por scenario_id(s), separados por vírgula")
    parser.add_argument("--limit", type=int, default=None, help="roda só os N primeiros cenários (após filtros)")
    parser.add_argument(
        "--out", default=None,
        help="arquivo de saída: .xlsx (relatório com tabelas e gráficos; grava também um .jsonl "
             "com as linhas e um .payloads.jsonl com o payload completo de qualidade/RAGAS/relatório "
             "de cada análise) ou .csv",
    )
    args = parser.parse_args()

    scenarios = _select_scenarios(args)
    if not scenarios:
        print("Nenhum cenário selecionado — confira --categories/--scenario-ids.")
        return 1

    year_range = get_available_year_range()
    min_year, max_year = year_range if year_range else (None, None)
    print(f"Intervalo de dados disponível: {min_year}-{max_year}")

    modelos = []
    for spec in MODELS:
        if has_api_key(PROVIDERS[spec.provider]):
            modelos.append(spec)
        else:
            print(f"AVISO: {PROVIDERS[spec.provider].api_key_env} ausente — modelo '{spec.label}' ignorado.")
    if not modelos:
        print("Nenhum modelo com chave de API configurada.")
        return 1
    print(f"Rodando {len(scenarios)} cenário(s) × {len(modelos)} modelo(s); estrela e hierárquica concorrentes, como na UI\n")

    neo4j_client = Neo4jClient()  # só para a memória episódica da interpretação (leitura)
    rows: list[dict] = []
    jsonl_path = payloads_path = None
    if args.out:
        base = os.path.splitext(args.out)[0]
        jsonl_path, payloads_path = base + ".jsonl", base + ".payloads.jsonl"
        open(jsonl_path, "w", encoding="utf-8").close()
        open(payloads_path, "w", encoding="utf-8").close()

    def registrar(row: dict) -> None:
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2))
        if jsonl_path:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    for model_spec in modelos:
        _set_model_env(model_spec)
        from core.llm_client import get_provider, resolve_model
        print(f"Modelo efetivo: provedor={get_provider().name} modelo={resolve_model()}")
        intent_cache: dict[tuple, dict] = {}
        for scenario in scenarios:
            print(f"=== {model_spec.label} | {scenario.scenario_id} ({scenario.category}) ===")
            base_row: dict = {
                "model": model_spec.label,
                "scenario": scenario.scenario_id,
                "category": scenario.category,
                "question_len": len(scenario.question),
                "question": scenario.question[:500],
                "fault": scenario.fault or "nenhuma",
                "fault_base": scenario.scenario_id in BASES_FALHA,
            }

            if len(scenario.question) > MAX_MESSAGE_LENGTH:
                # A UI recusa antes de qualquer chamada ao LLM (api/chat_websocket.py).
                base_row.update({
                    "topology": "-", "intent_success": False, "intent_missing": "rejeitada_tamanho_ui",
                    "clarification_message": f"Mensagem muito longa (máximo {MAX_MESSAGE_LENGTH} caracteres).",
                })
                registrar(base_row)
                continue

            cache_key = (scenario.question, scenario.reference_year)
            intent_reused = cache_key in intent_cache
            if intent_reused:
                intent_run = intent_cache[cache_key]
            else:
                intent_run = _run_intent(
                    scenario, min_year, max_year,
                    None if args.no_episodic else neo4j_client,
                )
                intent_cache[cache_key] = intent_run
            intent_result = intent_run["result"]
            intent_cost = quality_metrics.compute_token_cost(intent_run["token_usage"])
            base_row.update({
                "intent_success": intent_result.success,
                "intent_missing": ",".join(intent_result.missing),
                "intent_interpreted_via": intent_result.interpreted_via,
                "intent_elapsed_ms": round(intent_run["elapsed_ms"], 1),
                "intent_tokens": intent_cost["total_tokens"],
                "intent_reused": intent_reused,
            })

            if not intent_result.success or intent_result.params is None:
                base_row["topology"] = "-"
                base_row["clarification_message"] = intent_result.clarification_message[:200]
                registrar(base_row)
                continue

            p = intent_result.params
            base_row.update({
                "health_params_inferred": p.health_params_inferred,
                "date_range_inferred": p.date_range_inferred,
                "resolved_date_from": p.date_from,
                "resolved_date_to": p.date_to,
                "resolved_health_params": ",".join(p.health_params),
                "resolved_health_params_count": len(p.health_params),
            })
            if args.intent_only:
                base_row["topology"] = "-"
                registrar(base_row)
                continue

            try:
                analise = _run_analysis_like_ui(scenario, intent_run, args, scenario.fault)
            except Exception as exc:  # noqa: BLE001 — um cenário que quebra não derruba a bateria
                for topo in ("star", "hierarchical"):
                    registrar({**base_row, "topology": topo,
                               "run_error": f"{type(exc).__name__}: {exc}"[:300],
                               "r1_score": 0.0, "r1_completed": 0, "texto_gerado": 0})
                continue

            if payloads_path and analise.get("quality") is not None:
                with open(payloads_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "model": model_spec.label, "scenario": scenario.scenario_id, "fault": scenario.fault,
                        "quality": analise["quality"], "ragas": analise["ragas"],
                        "vencedor": analise["vencedor"], "criterio": analise["criterio"],
                        "relatorio": analise["relatorio"],
                    }, ensure_ascii=False, default=str) + "\n")
            for row in _linhas_da_analise(base_row, analise):
                registrar(row)

    if args.out:
        if args.out.lower().endswith(".xlsx"):
            batch_report.write_xlsx(rows, args.out, meta={
                "models": [m.label for m in modelos],
                "faults": [{"id": k, **FAULT_SPECS[k]} for k in FALHAS_ATIVAS],
                "scenarios": [
                    {"id": s.scenario_id, "category": s.category, "question": s.question, "len": len(s.question)}
                    for s in scenarios
                ],
            })
        else:
            fieldnames = sorted({k for row in rows for k in row})
            with open(args.out, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        print(f"\n{len(rows)} linhas salvas em {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
