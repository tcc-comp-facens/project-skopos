"""
ETL completo — executa localmente ANTES do docker compose up.

Lê os dados locais já extraídos em Dados/ (raiz do projeto) e persiste no
Neo4j. Não depende mais de download FTP/PySUS — leitura de arquivo local,
rápida e determinística.

Fluxo recomendado:
    1. docker compose up neo4j -d          # sobe só o Neo4j
    2. cd backend && python run_etl.py     # ETL local
    3. docker compose up --build           # sobe tudo

Requer:
    - Neo4j rodando (local ou Docker, porta 7687 acessível)
    - Variáveis em .env (NEO4J_URI=bolt://localhost:7687, NEO4J_USER, NEO4J_PASSWORD)
    - Dependências instaladas (pip install -r requirements.txt)
"""

import sys
import os
import logging
from pathlib import Path

# Garante que o diretório backend está no path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

# Quando rodando localmente, o hostname "neo4j" (rede Docker) não resolve.
# Substitui "bolt://neo4j:" por "bolt://localhost:" para acesso via porta exposta.
_neo4j_uri = os.environ.get("NEO4J_URI", "")
if "//neo4j:" in _neo4j_uri:
    os.environ["NEO4J_URI"] = _neo4j_uri.replace("//neo4j:", "//localhost:")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    from db.neo4j_client import Neo4jClient

    # 1. Verificar conexão com Neo4j
    logger.info("=" * 60)
    logger.info("VERIFICANDO CONEXÃO COM NEO4J")
    logger.info("=" * 60)
    try:
        client = Neo4jClient()
        logger.info("  ✓ Conectado: %s", os.environ.get("NEO4J_URI"))
    except Exception as e:
        logger.error("  ✗ Falha ao conectar: %s", e)
        logger.error("")
        logger.error("  Dica: suba o Neo4j primeiro com:")
        logger.error("    docker compose up neo4j -d")
        logger.error("")
        logger.error("  E verifique o .env:")
        logger.error("    NEO4J_URI=bolt://localhost:7687")
        sys.exit(1)

    # 2. Orçamento (empenhos + previsto x realizado, extração LAI)
    logger.info("")
    logger.info("=" * 60)
    logger.info("ETAPA 1/2: ORÇAMENTO (Dados/orcamento/)")
    logger.info("=" * 60)
    try:
        from etl.orcamento_loader import load as orcamento_load
        result = orcamento_load(client)
        logger.info("  ✓ Orçamento concluído: %s", result)
    except Exception:
        logger.exception("  ✗ Orçamento falhou")

    # 3. Indicadores de saúde (Dados/Sorocaba_DATASUS_2015-2025/)
    logger.info("")
    logger.info("=" * 60)
    logger.info("ETAPA 2/2: INDICADORES DE SAÚDE (Dados/Sorocaba_DATASUS_2015-2025/)")
    logger.info("=" * 60)
    try:
        from etl.saude_indicadores_loader import load as saude_load
        result = saude_load(client)
        logger.info("  ✓ Indicadores de saúde concluído: %s", result)
    except ModuleNotFoundError:
        logger.warning("  ⚠ etl.saude_indicadores_loader ainda não implementado — pulando.")
    except Exception:
        logger.exception("  ✗ Indicadores de saúde falhou")

    # 4. Resumo
    logger.info("")
    logger.info("=" * 60)
    logger.info("ETL CONCLUÍDO")
    logger.info("=" * 60)
    logger.info("")
    logger.info("  Próximo passo:")
    logger.info("    docker compose up --build")
    logger.info("")

    client.close()


if __name__ == "__main__":
    main()
