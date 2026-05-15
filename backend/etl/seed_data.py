"""
Seed — popula o Neo4j com dados de COVID-19 de Sorocaba como fallback.

Apenas indicadores de COVID são mantidos no seed porque o DataSUS não
disponibiliza esses dados via SINAN/PySUS (foram notificados pelo
e-SUS Notifica / SIVEP-Gripe). Os demais indicadores (dengue, vacinação,
internações, mortalidade) são obtidos diretamente do DataSUS via ETL.

Despesas SIOPS não precisam de seed — são carregadas das planilhas FNS.
"""

import os
import uuid
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Apenas COVID — dados não disponíveis via PySUS/SINAN
# Valores estimados com base em boletins epidemiológicos de Sorocaba
INDICADORES = [
    {"sistema": "sinan", "tipo": "covid", "ano": 2018, "valor": 0.0},
    {"sistema": "sinan", "tipo": "covid", "ano": 2019, "valor": 0.0},
    {"sistema": "sinan", "tipo": "covid", "ano": 2020, "valor": 26320.0},
    {"sistema": "sinan", "tipo": "covid", "ano": 2021, "valor": 60315.0},
    {"sistema": "sinan", "tipo": "covid", "ano": 2022, "valor": 57266.0},
]

_MERGE_INDICADOR = """
MERGE (i:IndicadorDataSUS {sistema: $sistema, tipo: $tipo, ano: $ano})
SET i.id         = COALESCE(i.id, $id),
    i.valor      = $valor,
    i.fonte      = 'datasus',
    i.importedAt = $importedAt
"""


def seed(neo4j_client) -> dict:
    imported_at = datetime.now(timezone.utc).isoformat()
    counts = {"indicadores": 0}

    with neo4j_client._driver.session() as session:
        for i in INDICADORES:
            session.run(
                _MERGE_INDICADOR,
                id=str(uuid.uuid4()),
                sistema=i["sistema"],
                tipo=i["tipo"],
                ano=i["ano"],
                valor=i["valor"],
                importedAt=imported_at,
            )
            counts["indicadores"] += 1

    logger.info("Seed concluido: %s", counts)
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from db.neo4j_client import Neo4jClient

    with Neo4jClient(
        uri=os.environ["NEO4J_URI"],
        user=os.environ["NEO4J_USER"],
        password=os.environ["NEO4J_PASSWORD"],
    ) as client:
        result = seed(client)
        print(f"Seed concluido: {result['indicadores']} indicadores COVID criados/atualizados.")
