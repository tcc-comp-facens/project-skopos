"""Linhas da bateria: modelos fixos e custo do juiz RAGAS por topologia."""

from scripts import batch_eval


def _quality(custo_ragas=None):
    topo = {
        "completeness": {"score": 1.0, "correlacoes_coverage": 1.0,
                         "anomalias_coverage": 1.0, "contexto_coverage": 1.0},
        "numeric_fidelity": {"score": 1.0, "numeros_no_texto": 3, "nao_verificados": []},
    }
    eff = {
        "coordination_overhead": {"supervisor_time_ms": 10.0, "overhead_percent": 50.0,
                                  "deliberation_time_ms": 9.0, "structural_time_ms": 1.0},
        "latency_breakdown": {f: {"time_ms": 1.0} for f in ("dominio", "analitico", "contexto", "supervisores")},
    }
    q = {
        "efficiency": {"star": eff, "hierarchical": eff},
        "quality": {"star": topo, "hierarchical": topo, "deterministic_consistency": {"all_identical": True}},
        "resilience": {t: {"score": 1.0, "completed": 7, "components": {"texto_analise": True}}
                       for t in ("star", "hierarchical")},
        "cost": {t: {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "call_count": 1}
                 for t in ("star", "hierarchical")},
        "communication": {t: {"agent_invocations": 1, "message_count": 2, "payload_records": 3}
                          for t in ("star", "hierarchical")},
        "outcome": {t: {"success": True, "within_time_budget": True} for t in ("star", "hierarchical")},
    }
    if custo_ragas is not None:
        q["cost"]["ragas"] = custo_ragas
    return q


def _analise(quality):
    return {
        "quality": quality, "ragas": None, "gabarito": None,
        "erros": {"star": 0, "hierarchical": 0},
        "relogio": {"star": 100.0, "hierarchical": 200.0},
        "resultados": {"star": {"texto_analise": "x"}, "hierarchical": {"texto_analise": "y"}},
    }


def test_custo_do_juiz_vai_para_cada_topologia():
    custo = {
        "star": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "call_count": 6},
        "hierarchical": {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340, "call_count": 6},
    }
    linhas = batch_eval._linhas_da_analise({"model": "m"}, _analise(_quality(custo)))
    por_topo = {l["topology"]: l for l in linhas}
    assert por_topo["star"]["ragas_total_tokens"] == 120
    assert por_topo["hierarchical"]["ragas_prompt_tokens"] == 300
    assert por_topo["hierarchical"]["ragas_completion_tokens"] == 40


def test_sem_ragas_os_tokens_do_juiz_ficam_vazios():
    linhas = batch_eval._linhas_da_analise({"model": "m"}, _analise(_quality()))
    assert all(l["ragas_total_tokens"] is None for l in linhas)


def test_payload_guarda_o_texto_final_de_cada_topologia():
    analise = _analise(_quality())
    analise.update({"achados": {}, "ragas": None})
    cenario = batch_eval.Scenario("c1", "cat", "pergunta?")
    payload = batch_eval._payload_da_analise(cenario, analise, "gpt-5-mini")
    assert payload["texto_analise"] == {"star": "x", "hierarchical": "y"}
    assert payload["ragas_juiz"] == "gpt-5-mini" and payload["scenario"] == "c1"
    assert set(payload) >= {"plano", "redacao", "textos_parciais", "revisao", "quality"}


def test_bateria_nao_tem_mais_lista_de_modelos():
    assert not hasattr(batch_eval, "MODELS")
    assert not hasattr(batch_eval, "ModelSpec")
    assert batch_eval.MODELO_ANALISE == "gpt-5.6-luna"
