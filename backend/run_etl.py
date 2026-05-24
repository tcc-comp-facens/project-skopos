"""
ETL completo — executa localmente ANTES do docker compose up.

Este script faz o trabalho pesado (download FTP via PySUS) na sua máquina
local, que tem memória suficiente. Os dados são salvos como cache em
data/datasus/ (parquets) e persistidos no Neo4j.

Quando o container sobe, o entrypoint.sh apenas sincroniza os parquets
já existentes com o Neo4j — sem download, sem PySUS, sem risco de OOM.

Fluxo recomendado:
    1. docker compose up neo4j -d          # sobe só o Neo4j
    2. cd backend && python run_etl.py     # ETL pesado local
    3. docker compose up --build           # sobe tudo (backend lê do cache)

Requer:
    - Neo4j rodando (local ou Docker, porta 7687 acessível)
    - Variáveis em .env (NEO4J_URI=bolt://localhost:7687, NEO4J_USER, NEO4J_PASSWORD)
    - Dependências instaladas (pip install -r requirements.txt)
    - Memória suficiente para downloads PySUS (~1-2GB para SP inteiro)

Flags:
    --skip-download    Pula downloads FTP, usa apenas cache existente
    --year-from YYYY   Ano inicial (default: detecta dos arquivos SIOPS)
    --year-to YYYY     Ano final (default: detecta dos arquivos SIOPS)
"""

import sys
import os
import logging
import argparse
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
    parser = argparse.ArgumentParser(description="ETL completo — DataSUS + SIOPS + Seed")
    parser.add_argument("--skip-download", action="store_true",
                        help="Pula downloads FTP, usa apenas cache existente")
    parser.add_argument("--year-from", type=int, default=None,
                        help="Ano inicial (default: detecta dos arquivos SIOPS)")
    parser.add_argument("--year-to", type=int, default=None,
                        help="Ano final (default: detecta dos arquivos SIOPS)")
    args = parser.parse_args()

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

    # 2. Detectar anos disponíveis
    from etl.detect_years import detect_siops_years
    siops_years_list = detect_siops_years()
    siops_years_set = set(siops_years_list) if siops_years_list else None

    if args.year_from and args.year_to:
        year_from = args.year_from
        year_to = args.year_to
    elif siops_years_list:
        year_from = min(siops_years_list)
        year_to = max(siops_years_list)
    else:
        year_from = 2018
        year_to = 2022

    logger.info("  Anos: %d-%d (SIOPS detectados: %s)", year_from, year_to,
                sorted(siops_years_list) if siops_years_list else "nenhum")

    # 3. Carregar SIOPS (planilhas FNS) — leve
    logger.info("")
    logger.info("=" * 60)
    logger.info("ETAPA 1/3: PLANILHAS SIOPS")
    logger.info("=" * 60)
    data_dir = Path(__file__).parent / "data"
    siops_files = sorted(data_dir.glob("*.xls")) + sorted(data_dir.glob("*.xlsx"))

    if not siops_files:
        logger.warning("  Nenhum arquivo .xls/.xlsx encontrado em %s", data_dir)
    else:
        from etl.siops_loader import load as siops_load
        for f in siops_files:
            try:
                count = siops_load(str(f), client)
                logger.info("  ✓ %s → %d registros", f.name, count)
            except Exception as e:
                logger.error("  ✗ %s → %s", f.name, e)

    # 4. Carregar DataSUS — pesado (download FTP) ou cache-only
    logger.info("")
    logger.info("=" * 60)
    if args.skip_download:
        logger.info("ETAPA 2/3: DATASUS (cache-only, --skip-download)")
    else:
        logger.info("ETAPA 2/3: DATASUS (download FTP + cache)")
    logger.info("=" * 60)

    try:
        from etl.datasus_loader import load as datasus_load, load_from_cache

        if args.skip_download:
            result = load_from_cache(client, year_from=year_from, year_to=year_to)
        else:
            result = datasus_load(
                client,
                year_from=year_from,
                year_to=year_to,
                siops_years=siops_years_set,
            )
        total = sum(result.values())
        logger.info("  ✓ DataSUS concluído: %d registros — %s", total, result)
    except Exception as e:
        logger.error("  ✗ DataSUS falhou: %s", e)

    # 5. Seed de fallback (COVID)
    logger.info("")
    logger.info("=" * 60)
    logger.info("ETAPA 3/3: SEED DE FALLBACK (COVID)")
    logger.info("=" * 60)
    try:
        from etl.seed_data import seed
        result = seed(client)
        logger.info("  ✓ Seed concluído: %s", result)
    except Exception as e:
        logger.error("  ✗ Seed falhou: %s", e)

    # 6. Resumo
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
