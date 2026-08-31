"""Testes das métricas determinísticas de core/quality_metrics.py.

Cobre compute_token_cost, compute_communication_volume,
compute_analysis_success e a cobertura de anomalias de
compute_completeness (Q3).

A fidelidade do texto não é mais medida aqui: saiu deste módulo para
core/ragas_metrics.py, que usa a biblioteca RAGAS — ver
tests/test_ragas_metrics.py.
"""

from __future__ import annotations

from unittest.mock import patch

from core.quality_metrics import (
    FAITHFULNESS_TIE_THRESHOLD,
    _decide_winner,
    compute_all_quality_metrics,
    compute_analysis_success,
    compute_communication_volume,
    compute_completeness,
    compute_token_cost,
    generate_comparative_report,
)


class TestComputeTokenCost:
    def test_normalizes_snapshot(self):
        snapshot = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "call_count": 3}
        assert compute_token_cost(snapshot) == snapshot

    def test_none_returns_zeros(self):
        assert compute_token_cost(None) == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }

    def test_empty_dict_returns_zeros(self):
        assert compute_token_cost({}) == {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }

    def test_missing_keys_default_to_zero(self):
        assert compute_token_cost({"prompt_tokens": 5}) == {
            "prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 0, "call_count": 0,
        }


class TestComputeCommunicationVolume:
    def test_star_never_has_lateral_hops(self):
        agent_metrics = [{"agentName": "vigilancia_epidemiologica"}, {"agentName": "correlacao"}]
        result = compute_communication_volume("star", agent_metrics)
        assert result["lateral_hops"] == 0
        assert result["lateral_summaries"] == 0
        assert result["agent_invocations"] == 2
        assert result["message_count"] == 4  # 2 agentes * (1 chamada + 1 retorno)

    def test_hierarchical_always_has_3_lateral_hops(self):
        agent_metrics = [{"agentName": "supervisor_dominio"}, {"agentName": "vigilancia_epidemiologica"}]
        result = compute_communication_volume("hierarchical", agent_metrics)
        assert result["lateral_hops"] == 3
        assert result["lateral_summaries"] == 2
        assert result["message_count"] == 2 * 2 + 3

    def test_payload_records_sums_despesas_e_indicadores(self):
        result = compute_communication_volume(
            "star", [], despesas_count=10, indicadores_count=7
        )
        assert result["payload_records"] == 17

    def test_no_agents_zero_invocations(self):
        result = compute_communication_volume("star", [])
        assert result["agent_invocations"] == 0
        assert result["message_count"] == 0


class TestCompletenessAnomalyCoverage:
    """Q3 — cobertura de anomalias verificada anomalia a anomalia.

    A implementação anterior fazia uma busca GLOBAL por palavra-chave de
    categoria ("ineficiência", "alto gasto", ...): bastava uma ocorrência
    em qualquer lugar do texto para marcar TODAS as anomalias daquele tipo
    como cobertas. Um texto que menciona 1 de 4 anomalias pontuava igual a
    um que menciona as 4.
    """

    ANOMALIAS = [
        {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2019,
         "tipo_anomalia": "alto_gasto_baixo_resultado"},
        {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2020,
         "tipo_anomalia": "alto_gasto_baixo_resultado"},
        {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2021,
         "tipo_anomalia": "alto_gasto_baixo_resultado"},
        {"subfuncao": 305, "tipo_indicador": "dengue", "ano": 2022,
         "tipo_anomalia": "alto_gasto_baixo_resultado"},
    ]

    def test_partial_mention_scores_partial(self):
        texto = "Houve alto gasto com ineficiência em dengue na subfunção 305 em 2019."
        result = compute_completeness([], self.ANOMALIAS, {}, texto)
        assert result["details"]["anomalias"] == {"found": 1, "total": 4}
        assert result["anomalias_coverage"] == 0.25

    def test_all_mentioned_scores_full(self):
        texto = (
            "Anomalias de dengue na subfunção 305 em 2019, 2020, 2021 e 2022 "
            "indicam alto gasto com baixo resultado."
        )
        result = compute_completeness([], self.ANOMALIAS, {}, texto)
        assert result["details"]["anomalias"] == {"found": 4, "total": 4}
        assert result["anomalias_coverage"] == 1.0

    def test_category_keyword_alone_covers_nothing(self):
        """A palavra-chave da categoria, sozinha, não identifica anomalia
        nenhuma — era exatamente o que inflava o score antes."""
        texto = "O relatório aponta ineficiência e alto gasto de forma geral."
        result = compute_completeness([], self.ANOMALIAS, {}, texto)
        assert result["details"]["anomalias"] == {"found": 0, "total": 4}

    def test_year_without_entity_does_not_count(self):
        texto = "Em 2019 o orçamento cresceu."
        result = compute_completeness([], self.ANOMALIAS, {}, texto)
        assert result["details"]["anomalias"]["found"] == 0

    def test_no_anomalies_is_full_coverage(self):
        result = compute_completeness([], [], {}, "qualquer texto")
        assert result["anomalias_coverage"] == 1.0


class TestComputeAnalysisSuccess:
    def _resultado_completo(self, **overrides):
        base = {
            "despesas": [1], "indicadores": [1], "dados_cruzados": [1],
            "correlacoes": [1], "anomalias": [1], "contexto_orcamentario": {"1": {}},
            "texto_analise": "texto",
        }
        base.update(overrides)
        return base

    def test_success_when_r1_complete_and_within_budget(self):
        result = compute_analysis_success(self._resultado_completo(), wall_clock_ms=1000, time_budget_ms=5000)
        assert result["success"] is True
        assert result["r1_complete"] is True
        assert result["within_time_budget"] is True

    def test_fails_when_r1_incomplete(self):
        result = compute_analysis_success(self._resultado_completo(despesas=[]), wall_clock_ms=1000)
        assert result["success"] is False
        assert result["r1_complete"] is False

    def test_fails_when_over_time_budget(self):
        result = compute_analysis_success(
            self._resultado_completo(), wall_clock_ms=10_000, time_budget_ms=5_000,
        )
        assert result["success"] is False
        assert result["within_time_budget"] is False

    def test_zero_wall_clock_disables_budget_check(self):
        result = compute_analysis_success(self._resultado_completo(), wall_clock_ms=0)
        assert result["within_time_budget"] is True

    def test_self_check_not_run_does_not_penalize(self):
        result = compute_analysis_success(self._resultado_completo())
        assert result["self_check_ok"] is True

    def test_self_check_revisado_counts_as_ok(self):
        resultado = self._resultado_completo(self_check={
            "verificado": True, "revisado": True,
            "claims": [{"claim": "x", "suportado": False}],
        })
        result = compute_analysis_success(resultado)
        assert result["self_check_ok"] is True

    def test_self_check_nao_revisado_com_claim_nao_suportada_falha(self):
        resultado = self._resultado_completo(self_check={
            "verificado": True, "revisado": False,
            "claims": [{"claim": "x", "suportado": False}],
        })
        result = compute_analysis_success(resultado)
        assert result["self_check_ok"] is False
        assert result["success"] is False

    def test_self_check_sem_claims_nao_suportadas_ok(self):
        resultado = self._resultado_completo(self_check={
            "verificado": True, "revisado": False,
            "claims": [{"claim": "x", "suportado": True}],
        })
        result = compute_analysis_success(resultado)
        assert result["self_check_ok"] is True


class TestComputeAllQualityMetricsWiring:
    """Confirma que a agregadora inclui as novas seções (cost, communication,
    outcome) e propaga corretamente os snapshots de token."""

    def _resultado(self):
        return {
            "correlacoes": [], "anomalias": [], "texto_analise": "",
            "contexto_orcamentario": {}, "despesas": [1, 2], "indicadores": [3],
            "dados_cruzados": [], "data_coverage": {},
        }

    def test_includes_cost_communication_outcome_sections(self):
        star_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "call_count": 2}
        metrics = compute_all_quality_metrics(
            star_result=self._resultado(),
            hier_result=self._resultado(),
            star_agent_metrics=[{"agentName": "vigilancia_epidemiologica", "executionTimeMs": 10}],
            hier_agent_metrics=[{"agentName": "supervisor_dominio", "executionTimeMs": 10}],
            star_token_usage=star_usage,
        )
        assert metrics["cost"]["star"] == star_usage
        assert metrics["cost"]["hierarchical"]["total_tokens"] == 0
        assert metrics["communication"]["star"]["lateral_hops"] == 0
        assert metrics["communication"]["hierarchical"]["lateral_hops"] == 3
        assert metrics["communication"]["star"]["payload_records"] == 3
        assert "outcome" in metrics
        assert "success" in metrics["outcome"]["star"]

    def test_never_calls_the_llm(self):
        """O agregador é 100% determinístico e gratuito.

        A avaliação que custa LLM (RAGAS) roda à parte, de forma
        assíncrona, em `api/websocket.py` — nenhuma métrica calculada aqui
        pode disparar uma chamada, senão o "caminho rápido" deixa de ser
        rápido e o custo por análise fica imprevisível.
        """
        with patch("core.llm_client.generate") as mock_generate:
            metrics = compute_all_quality_metrics(
                star_result=self._resultado(), hier_result=self._resultado(),
                star_agent_metrics=[], hier_agent_metrics=[],
            )
        mock_generate.assert_not_called()
        assert "faithfulness" not in metrics["quality"]["star"]
        assert "completeness" in metrics["quality"]["star"]


class TestDecideWinner:
    """Ordem lexicográfica: fidelidade (RAGAS) > completude > eficiência.

    O ponto delicado é o `None`: uma fidelidade não medida não pode ser
    tratada como zero, senão a arquitetura cuja medição falhou perde por
    falha de instrumentação, não por qualidade.
    """

    def _quality(self, star_comp=0.5, hier_comp=0.5):
        return {
            "quality": {
                "star": {"completeness": {"score": star_comp}},
                "hierarchical": {"completeness": {"score": hier_comp}},
            }
        }

    def _ragas(self, star=None, hier=None):
        def bloco(score):
            return {"metrics": {"faithfulness": {"score": score}}}

        return {"star": bloco(star), "hierarchical": bloco(hier)}

    def test_diferenca_menor_que_o_limiar_e_empate_tecnico(self):
        """Caso real observado: 0.79 vs 0.80. O juiz é um LLM e reavaliar
        o mesmo texto varia — decidir a topologia vencedora por 0.01 seria
        decidir por ruído, não por arquitetura."""
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.2, hier_comp=0.9),
            self._ragas(star=0.79, hier=0.80),
            star_total=10, hier_total=20,
        )
        assert winner == "hierarchical"  # decidido pela completude, não pelos 0.01
        assert "tecnicamente empatada" in criterio

    def test_diferenca_no_limiar_ja_decide(self):
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.9, hier_comp=0.1),
            self._ragas(star=0.70, hier=0.75),  # exatamente FAITHFULNESS_TIE_THRESHOLD
            star_total=10, hier_total=20,
        )
        assert winner == "hierarchical"
        assert criterio == "fidelidade"

    def test_empate_tecnico_nao_e_confundido_com_nao_medida(self):
        """São dois motivos diferentes para cair na completude, e o
        relatório precisa dizer qual foi."""
        _, tecnico = _decide_winner(
            self._quality(star_comp=0.9, hier_comp=0.1),
            self._ragas(star=0.79, hier=0.80), star_total=10, hier_total=20,
        )
        _, nao_medida = _decide_winner(
            self._quality(star_comp=0.9, hier_comp=0.1),
            self._ragas(star=0.79, hier=None), star_total=10, hier_total=20,
        )
        assert "tecnicamente empatada" in tecnico
        assert "não medida" in nao_medida
        assert tecnico != nao_medida

    def test_fidelidade_decide_quando_ambas_medidas(self):
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.9, hier_comp=0.1),  # completude favorece a estrela
            self._ragas(star=0.4, hier=0.8),              # fidelidade favorece a hierárquica
            star_total=10, hier_total=999,
        )
        assert winner == "hierarchical"
        assert criterio == "fidelidade"

    def test_fidelidade_nao_medida_numa_arquitetura_cai_para_completude(self):
        """Sem esta regra, `None` viraria 0 e a estrela ganharia de graça."""
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.2, hier_comp=0.9),
            self._ragas(star=0.8, hier=None),
            star_total=10, hier_total=20,
        )
        assert winner == "hierarchical"
        assert "fidelidade não medida" in criterio

    def test_sem_ragas_nenhum_usa_completude(self):
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.9, hier_comp=0.3), None,
            star_total=10, hier_total=20,
        )
        assert winner == "star"
        assert "fidelidade não medida" in criterio

    def test_empate_exato_em_fidelidade_desempata_por_completude(self):
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.3, hier_comp=0.7),
            self._ragas(star=0.8, hier=0.8),
            star_total=10, hier_total=20,
        )
        assert winner == "hierarchical"
        assert criterio == "completude"  # empate exato, não "tecnicamente empatada"

    def test_limiar_e_pequeno_o_bastante_para_nao_engolir_diferenca_real(self):
        assert 0 < FAITHFULNESS_TIE_THRESHOLD <= 0.10

    def test_empate_em_qualidade_desempata_por_tempo(self):
        winner, criterio = _decide_winner(
            self._quality(star_comp=0.5, hier_comp=0.5),
            self._ragas(star=0.8, hier=0.8),
            star_total=10, hier_total=20,
        )
        assert winner == "star"
        assert "eficiência" in criterio

    def test_empate_total_e_empate(self):
        winner, _ = _decide_winner(
            self._quality(), self._ragas(star=0.8, hier=0.8),
            star_total=10, hier_total=10,
        )
        assert winner == "tie"

    def test_relatorio_cita_o_criterio_que_decidiu(self):
        relatorio = generate_comparative_report(
            quality={
                **self._quality(star_comp=0.5, hier_comp=0.5),
                "efficiency": {}, "resilience": {}, "cost": {}, "communication": {},
            },
            star_agent_metrics=[], hier_agent_metrics=[],
            ragas=self._ragas(star=0.9, hier=0.2),
        )
        assert "Estrela apresentou melhor desempenho geral" in relatorio
        assert "critério: fidelidade" in relatorio
        assert "Fidelidade (RAGAS)" in relatorio

    def test_relatorio_sem_ragas_marca_fidelidade_como_nao_medida(self):
        relatorio = generate_comparative_report(
            quality={
                **self._quality(star_comp=0.9, hier_comp=0.1),
                "efficiency": {}, "resilience": {}, "cost": {}, "communication": {},
            },
            star_agent_metrics=[], hier_agent_metrics=[],
        )
        assert "fidelidade não medida" in relatorio


class TestTabelaDeAnomalias:
    """As colunas tinham largura fixa (28 para subfunção, 16 para
    indicador) e o conteúdo real estourava: "Suporte Profilático e
    Terapêutico" tem 33 chars e "sifilis_adquirida" tem 17, então as
    colunas colidiam no relatório ("Terapêuticodengue").
    """

    ANOMALIAS = [
        {"subfuncao": 303, "tipo_indicador": "sifilis_adquirida", "ano": 2020,
         "tipo_anomalia": "alto_gasto_baixo_resultado"},
        {"subfuncao": 301, "tipo_indicador": "dengue", "ano": 2019,
         "tipo_anomalia": "baixo_gasto_bom_resultado"},
    ]

    def _linhas(self):
        relatorio = generate_comparative_report(
            quality={"quality": {"star": {"completeness": {"score": 0.5}},
                                 "hierarchical": {"completeness": {"score": 0.5}}},
                     "efficiency": {}, "resilience": {}, "cost": {}, "communication": {}},
            star_agent_metrics=[], hier_agent_metrics=[],
            star_result={"anomalias": self.ANOMALIAS, "correlacoes": []},
            hier_result={"anomalias": self.ANOMALIAS, "correlacoes": []},
        )
        return [l for l in relatorio.split("\n") if "Terapêutico" in l or "Atenção" in l]

    def test_nome_longo_nao_cola_no_indicador(self):
        linha = next(l for l in self._linhas() if "Terapêutico" in l)
        assert "Terapêuticosifilis" not in linha
        assert "Suporte Profilático e Terapêutico" in linha
        assert "sifilis_adquirida" in linha

    def test_indicador_longo_nao_cola_no_diagnostico(self):
        linha = next(l for l in self._linhas() if "Terapêutico" in l)
        assert "sifilis_adquiridaineficiência" not in linha
        assert linha.rstrip().endswith("ineficiência")

    def test_colunas_alinhadas_entre_linhas(self):
        linhas = self._linhas()
        assert len(linhas) == 2
        posicoes = {l.index("sifilis_adquirida") if "sifilis" in l else l.index("dengue")
                    for l in linhas}
        assert len(posicoes) == 1, "a coluna do indicador deve começar na mesma posição"
