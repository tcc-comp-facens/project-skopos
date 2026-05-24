#!/bin/bash
set -e

echo "=== Aguardando Neo4j ficar pronto ==="
MAX_RETRIES=30
RETRY=0
until python -c "
from neo4j import GraphDatabase
import os
d = GraphDatabase.driver(os.environ['NEO4J_URI'], auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']))
d.verify_connectivity()
d.close()
print('Neo4j conectado.')
" 2>/dev/null; do
    RETRY=$((RETRY + 1))
    if [ $RETRY -ge $MAX_RETRIES ]; then
        echo "Neo4j não respondeu após $MAX_RETRIES tentativas."
        exit 1
    fi
    echo "Neo4j não está pronto (tentativa $RETRY/$MAX_RETRIES)..."
    sleep 2
done

echo ""
echo "=== Sincronizando dados com Neo4j (cache-only, sem download FTP) ==="

# SIOPS — carrega todos os .xls/.xlsx da pasta data/ (leve, ~300 linhas por planilha)
for f in data/*.xls data/*.xlsx; do
    if [ -f "$f" ]; then
        echo "Carregando SIOPS: $f"
        python -m etl.siops_loader "$f" || echo "AVISO: falha ao carregar $f"
    fi
done

# DataSUS — persiste APENAS dados já em cache (parquets locais)
# Não faz download FTP, não importa PySUS, zero risco de OOM
echo "Sincronizando indicadores DataSUS do cache local..."
python -c "
import sys, gc, uuid
sys.path.insert(0, '.')
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from db.neo4j_client import Neo4jClient

CACHE_DIR = Path('data/datasus')
MERGE_QUERY = '''
MERGE (i:IndicadorDataSUS {sistema: \$sistema, tipo: \$tipo, ano: \$ano})
SET i.id         = COALESCE(i.id, \$id),
    i.valor      = \$valor,
    i.fonte      = \"datasus\",
    i.importedAt = \$importedAt
'''

if not CACHE_DIR.exists():
    print('Nenhum cache DataSUS encontrado em data/datasus/. Execute run_etl.py localmente primeiro.')
    sys.exit(0)

client = Neo4jClient()

# Descobre o que já existe no Neo4j
with client._driver.session() as s:
    r = s.run('MATCH (i:IndicadorDataSUS) RETURN i.sistema AS s, i.tipo AS t, i.ano AS a')
    existing = {(rec['s'], rec['t'], rec['a']) for rec in r}

synced = 0
for path in sorted(CACHE_DIR.glob('*.parquet')):
    parts = path.stem.split('_')
    if len(parts) < 3:
        continue
    year = int(parts[-1])
    tipo = parts[-2]
    sistema = '_'.join(parts[:-2])

    if (sistema, tipo, year) in existing:
        continue

    df = pd.read_parquet(path)
    if len(df) > 0:
        with client._driver.session() as s:
            s.run(MERGE_QUERY, id=str(uuid.uuid4()), sistema=sistema, tipo=tipo,
                  ano=year, valor=float(len(df)),
                  importedAt=datetime.now(timezone.utc).isoformat())
        synced += 1
        print(f'  + {sistema}/{tipo}/{year} = {len(df)}')
    del df
    gc.collect()

if synced:
    print(f'Sincronizados: {synced} indicador(es) do cache para Neo4j.')
else:
    print('Neo4j ja possui todos os indicadores do cache.')
client.close()
" || echo "AVISO: falha na sincronizacao de cache DataSUS"

# Seed de fallback — garante dados mínimos de COVID (não disponível via PySUS)
echo "Executando seed de dados de fallback..."
python -m etl.seed_data || echo "AVISO: falha no seed"

echo ""
echo "=== Dados sincronizados. Iniciando backend ==="
exec uvicorn main:app --host 0.0.0.0 --port 8000
