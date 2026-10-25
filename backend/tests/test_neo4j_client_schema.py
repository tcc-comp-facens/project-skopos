"""Esquema dos nós gravados pela aplicação (Analise/MetricaExecucao) e save_metrica."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from db.neo4j_client import Neo4jClient


@pytest.fixture
def client_and_session():
    session = MagicMock()
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    with patch("db.neo4j_client.GraphDatabase.driver", return_value=driver):
        client = Neo4jClient(uri="bolt://fake:7687", user="u", password="p")
    return client, session


class TestEnsureSchema:
    def test_cria_constraint_de_unicidade_para_analise_e_metrica(self, client_and_session):
        client, session = client_and_session
        client.ensure_schema()
        statements = [c.args[0] for c in session.run.call_args_list]
        assert any("FOR (a:Analise)" in s and "a.id IS UNIQUE" in s for s in statements)
        assert any("FOR (m:MetricaExecucao)" in s and "m.id IS UNIQUE" in s for s in statements)
        assert all("IF NOT EXISTS" in s for s in statements)

    def test_falha_em_um_statement_nao_impede_os_demais(self, client_and_session):
        client, session = client_and_session
        session.run.side_effect = [RuntimeError("dados duplicados"), MagicMock()]
        client.ensure_schema()  # não levanta
        assert session.run.call_count == len(Neo4jClient.SCHEMA_STATEMENTS)


class TestSaveMetrica:
    def test_match_da_analise_vem_antes_do_merge_da_metrica(self, client_and_session):
        client, session = client_and_session
        client.save_metrica({"id": "m1", "agentType": "sinan"}, "analise-1")
        query = session.run.call_args.args[0]
        assert query.index("MATCH (a:Analise") < query.index("MERGE (m:MetricaExecucao")

    def test_passa_os_parametros_da_metrica(self, client_and_session):
        client, session = client_and_session
        client.save_metrica(
            {"id": "m1", "architecture": "star", "agentId": "a1", "agentType": "sinan",
             "executionTimeMs": 12.5, "cpuPercent": 3.0},
            "analise-1",
        )
        kwargs = session.run.call_args.kwargs
        assert kwargs["id"] == "m1"
        assert kwargs["analysisId"] == "analise-1"
        assert kwargs["executionTimeMs"] == 12.5
