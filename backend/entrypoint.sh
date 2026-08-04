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
echo "=== Carregando orçamento (Dados/orcamento/) ==="
python -m etl.orcamento_loader || echo "AVISO: falha ao carregar orçamento"

echo ""
echo "=== Carregando indicadores de saúde (Dados/Sorocaba_DATASUS_2015-2025/) ==="
if python -c "import etl.saude_indicadores_loader" 2>/dev/null; then
    python -m etl.saude_indicadores_loader || echo "AVISO: falha ao carregar indicadores de saúde"
else
    echo "AVISO: etl.saude_indicadores_loader ainda não implementado — pulando."
fi

echo ""
echo "=== Dados sincronizados. Iniciando backend ==="
exec uvicorn main:app --host 0.0.0.0 --port 8000
