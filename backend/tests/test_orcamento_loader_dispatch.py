"""Despacho de arquivos do loader de orçamento.

O CSV de previsto × realizado (`*_previsto_e_empenhado_por_programa_e_secretaria_*`) não é
mais carregado. O nome dele também contém "empenhado", então, se o ramo que o ignora sair da
frente do ramo de empenho, o arquivo passa a ser lido como se fosse de empenhos.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from etl import orcamento_loader as loader

PREVISTO = "x_lai_-_previsto_e_empenhado_por_programa_e_secretaria_(extracao_dados)_abc.csv"
EMPENHO = "x_lai_-_empenhado_por_subfuncao_abc.csv"
DESCONHECIDO = "outro_arquivo.csv"


@pytest.fixture
def loader_isolado(tmp_path, monkeypatch):
    periodo = tmp_path / "2023-2025"
    periodo.mkdir()
    for nome in (PREVISTO, EMPENHO, DESCONHECIDO):
        (periodo / nome).write_text("conteudo irrelevante", encoding="utf-8")

    monkeypatch.setattr(loader, "ORCAMENTO_DIR", tmp_path)
    lidos: list[str] = []
    monkeypatch.setattr(loader, "_read_empenho_csv", lambda path, periodo_origem: lidos.append(path.name) or [])
    monkeypatch.setattr(loader, "_persist_empenhos", lambda session, records: 0)
    for nome in (
        "_ensure_constraints",
        "_compute_despesa_anual",
        "_compute_despesa_por_aplicacao",
        "_compute_despesa_por_natureza",
        "_compute_variacao_anual",
    ):
        monkeypatch.setattr(loader, nome, lambda *args, **kwargs: 0)
    return lidos


def test_so_o_csv_de_empenho_e_lido(loader_isolado):
    loader.load(MagicMock())
    assert loader_isolado == [EMPENHO]


def test_previsto_realizado_nao_gera_contagem_nem_funcao_de_leitura(loader_isolado):
    counts = loader.load(MagicMock())
    assert "orcamento_programa" not in counts
    assert not hasattr(loader, "_read_previsto_realizado_csv")
    assert not hasattr(loader, "_persist_orcamento_programa")
    assert not any("OrcamentoPrograma" in c for c in loader._CONSTRAINTS)
