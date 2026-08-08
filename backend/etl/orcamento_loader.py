"""
ETL — Ingestão de dados orçamentários a partir da extração LAI/portal de
transparência de Sorocaba (empenho a empenho), substituindo siops_loader.py.

Diferença central em relação ao pipeline antigo: em vez de repasses federais
recebidos (planilhas FNS), este loader lê a despesa municipal efetivamente
EXECUTADA — inclui recurso próprio municipal, transferências estaduais,
operações de crédito e emendas parlamentares, não só repasse federal.

Fontes (ver PLANO_NOVO_MODELO_DADOS.md):
    Dados/orcamento/{2015-2019,2020-2022,2023-2025}/*_empenhado_*.csv
        → nós Empenho (granular, 1 nó por linha) + dimensões
        → agregado DespesaAnual (subfuncao x ano), calculado ao final
    Dados/orcamento/{periodo}/*_previsto_e_empenhado_por_programa_e_secretaria_*.csv
        → nós OrcamentoPrograma (grão: bloco plurianual do arquivo, sem coluna de ano)

Formato dos CSVs: delimitador '|', encoding latin-1.

Uso:
    python -m etl.orcamento_loader

Variáveis de ambiente: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
"""

import math
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Localmente (fora do Docker), Dados/ vive na raiz do repo, 3 níveis acima
# deste arquivo (backend/etl/orcamento_loader.py -> backend/ -> raiz).
# Dentro do container, apenas backend/ é copiado (ver Dockerfile), então
# Dados/ precisa ser montado como volume e apontado via DADOS_DIR
# (ver docker-compose.yml).
DADOS_DIR = Path(os.environ.get("DADOS_DIR", Path(__file__).parent.parent.parent / "Dados"))
ORCAMENTO_DIR = DADOS_DIR / "orcamento"

# Janela temporal oficial do sistema: 01/01/2016 a 31/12/2025 (10 anos).
# Qualquer registro fora desse período é descartado no ETL — vale para todos
# os domínios (orçamento e, futuramente, indicadores de saúde).
PERIODO_ANO_MIN = 2016
PERIODO_ANO_MAX = 2025
CSV_SEP = "|"
CSV_ENCODING = "latin-1"
BATCH_SIZE = 2000

# ---------------------------------------------------------------------------
# Mapeamentos de normalização
# ---------------------------------------------------------------------------

# "Sub Função" (texto na planilha) -> (codigo padrão, nome padrão)
# Códigos da função 10-Saúde (301-306) + 122 (Administração Geral, genérica).
SUBFUNCAO_MAP: dict[str, tuple[int, str]] = {
    "ATENCAO BASICA": (301, "Atenção Básica"),
    "ASSISTENCIA HOSPITALAR E AMBULATORIAL": (302, "Assistência Hospitalar e Ambulatorial"),
    "SUPORTE PROFILATICO E TERAPEUTICO": (303, "Suporte Profilático e Terapêutico"),
    "VIGILANCIA SANITARIA": (304, "Vigilância Sanitária"),
    "VIGILANCIA EPIDEMIOLOGICA": (305, "Vigilância Epidemiológica"),
    "ALIMENTACAO E NUTRICAO": (306, "Alimentação e Nutrição"),
    "ADMINISTRACAO GERAL": (122, "Administração Geral"),
}

# "Fonte Recurso" (texto na planilha, todas as variantes observadas nos 3
# períodos) -> tipoRecurso. Mantém o texto original como chave do nó
# FonteRecurso (não unifica variantes de redação entre anos — "exec.anter."
# marca recurso vinculado de exercício anterior, distinção contábil real).
#
# "Emendas Parlamentares Individuais" fica em categoria própria
# ('emenda_parlamentar'), não forçada em proprio/federal/estadual — decisão
# tomada por incerteza genuína sobre a origem (federal vs. municipal) entre
# períodos (ver PLANO_NOVO_MODELO_DADOS.md, §7 item 3).
FONTE_RECURSO_TIPO: dict[str, str] = {
    "TESOURO": "proprio",
    "REC.PROP.DE FDOS ESPECIAIS DE DESPESA-VINC.-EX.ANT": "proprio",
    "REC.PROP.DE FDOS ESPECIAIS DE DESPESA-VINCULADOS": "proprio",
    "REC. PROP. FUNDOS ESPECIAIS DE DESPESA-VINCULADOS": "proprio",
    "TRANSF.E CONVENIOS FEDERAIS-VINCULADOS-EXEC.ANTER.": "federal",
    "TRANSFERENCIAS E CONVENIOS FEDERAIS - VINCULADOS": "federal",
    "TRANSF.E CONVENIOS ESTADUAIS-VINCULADOS-EXEC.ANT.": "estadual",
    "TRANSFERENCIAS E CONVENIOS ESTADUAIS - VINCULADOS": "estadual",
    "OPERACOES DE CREDITO": "credito",
    "OUTRAS FONTES DE RECURSOS": "outros",
    "OUTRAS FONTES DE RECURSOS - EXERCICIOS ANTERIORES": "outros",
    "EMENDAS PARLAMENTARES INDIVIDUAIS": "emenda_parlamentar",
    "EMENDAS PARLAMENTARES INDIVIDUAIS - LEG. MUNICIPAL": "emenda_parlamentar",
}

_CNPJ_RE = re.compile(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})")
_CPF_RE = re.compile(r"(\d{3}\.\d{3}\.\d{3}-\d{2})")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _normalize(text) -> str:
    return str(text).strip().upper()


def _map_subfuncao(sub_funcao_raw: str) -> Optional[tuple[int, str]]:
    return SUBFUNCAO_MAP.get(_normalize(sub_funcao_raw))


def _map_tipo_recurso(fonte_recurso_raw: str) -> str:
    tipo = FONTE_RECURSO_TIPO.get(_normalize(fonte_recurso_raw))
    if tipo is None:
        logger.warning("Fonte Recurso não mapeada (usando 'outros'): %r", fonte_recurso_raw)
        return "outros"
    return tipo


def _extract_fornecedor(fornecedor_raw: str) -> tuple[str, Optional[str], str]:
    """Extrai (nome_limpo, documento, tipoDocumento) do campo Fornecedor.

    O campo vem com marcação HTML solta (<b>, <br>) e um documento entre
    parênteses no final — CNPJ para pessoa jurídica, CPF para pessoa física
    (comum em prestadores de serviço individuais, ex. home care). Tenta CNPJ
    primeiro, depois CPF.

    Quando o texto tem nome fantasia + razão social concatenados (separados
    por <br>), o nome resultante mantém os dois — não há como distinguir
    com segurança qual é qual a partir do texto puro.
    """
    text = str(fornecedor_raw)

    cnpj_match = _CNPJ_RE.search(text)
    if cnpj_match:
        doc_match, tipo_documento = cnpj_match, "cnpj"
    else:
        doc_match = _CPF_RE.search(text)
        tipo_documento = "pessoa_fisica" if doc_match else "desconhecido"

    documento = doc_match.group(1) if doc_match else None
    sem_doc = text[: doc_match.start()] if doc_match else text
    sem_tags = _HTML_TAG_RE.sub(" ", sem_doc)
    nome = " ".join(sem_tags.split()).strip(" (")
    return nome or "DESCONHECIDO", documento, tipo_documento


def _split_acao(acao_raw: str) -> tuple[Optional[str], str]:
    """Separa código e nome de um campo Ação tipo '2109-Atencao Primaria em Saude'.

    Quando não há hífen, o campo inteiro vira o nome e o código fica nulo.
    """
    text = str(acao_raw).strip()
    if "-" in text:
        codigo, nome = text.split("-", 1)
        codigo = codigo.strip()
        nome = nome.strip()
        if codigo:
            return codigo, nome or text
    return None, text


# ---------------------------------------------------------------------------
# Constraints / índices
# ---------------------------------------------------------------------------

_CONSTRAINTS = [
    "CREATE CONSTRAINT fornecedor_documento IF NOT EXISTS FOR (f:Fornecedor) REQUIRE f.documento IS UNIQUE",
    "CREATE CONSTRAINT subfuncao_codigo IF NOT EXISTS FOR (s:Subfuncao) REQUIRE s.codigo IS UNIQUE",
    "CREATE CONSTRAINT fonte_recurso_nome IF NOT EXISTS FOR (fr:FonteRecurso) REQUIRE fr.nome IS UNIQUE",
    "CREATE CONSTRAINT programa_governo_nome IF NOT EXISTS FOR (p:ProgramaGoverno) REQUIRE p.nome IS UNIQUE",
    "CREATE CONSTRAINT acao_codigo IF NOT EXISTS FOR (a:Acao) REQUIRE a.codigo IS UNIQUE",
    "CREATE CONSTRAINT unidade_orcamentaria_nome IF NOT EXISTS FOR (u:UnidadeOrcamentaria) REQUIRE u.nome IS UNIQUE",
    "CREATE CONSTRAINT aplicacao_nome IF NOT EXISTS FOR (a:Aplicacao) REQUIRE a.nome IS UNIQUE",
    "CREATE CONSTRAINT natureza_nome IF NOT EXISTS FOR (n:Natureza) REQUIRE n.nome IS UNIQUE",
    "CREATE CONSTRAINT empenho_id IF NOT EXISTS FOR (e:Empenho) REQUIRE e.id IS UNIQUE",
    # Índices compostos (não uniqueness constraint composto — evita depender de
    # recurso Enterprise-only; MERGE funciona normalmente, só perde a garantia
    # de unicidade a nível de banco).
    "CREATE INDEX despesa_anual_lookup IF NOT EXISTS FOR (d:DespesaAnual) ON (d.subfuncaoCodigo, d.ano)",
    "CREATE INDEX orcamento_programa_lookup IF NOT EXISTS FOR (o:OrcamentoPrograma) ON (o.periodoOrigem)",
]


def _ensure_constraints(session) -> None:
    for stmt in _CONSTRAINTS:
        session.run(stmt)


# ---------------------------------------------------------------------------
# Leitura — Empenho (granular)
# ---------------------------------------------------------------------------

_EMPENHO_COLS = [
    "Nro Empenho", "Nro Processo", "Fornecedor", "Data Empenho",
    "Descrição Empenho", "Função", "Sub Função", "Fonte Recurso", "Aplicação",
    "Natureza", "Unidade Orçamentária", "Programa", "Ação",
    "Modalidade de Licitação", "Empenho Original", "Estorno Empenho",
    "Saldo Empenho", "Valor Processado", "Valor Pago",
]


def _read_empenho_csv(path: Path, periodo_origem: str) -> list[dict]:
    df = pd.read_csv(path, sep=CSV_SEP, encoding=CSV_ENCODING, dtype=str)
    missing = [c for c in _EMPENHO_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: colunas ausentes: {missing}")

    datas = pd.to_datetime(df["Data Empenho"], format="%d/%m/%Y", errors="coerce")

    valor_cols = ["Empenho Original", "Estorno Empenho", "Saldo Empenho",
                  "Valor Processado", "Valor Pago"]
    for col in valor_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    records: list[dict] = []
    imported_at = datetime.now(timezone.utc).isoformat()

    for idx, row in df.iterrows():
        data_empenho = datas.iloc[idx]
        if pd.isna(data_empenho):
            logger.debug("%s linha %d: Data Empenho inválida (%r), pulando.",
                         path.name, idx, row["Data Empenho"])
            continue

        if not (PERIODO_ANO_MIN <= data_empenho.year <= PERIODO_ANO_MAX):
            logger.debug("%s linha %d: ano %d fora do período %d-%d, descartando.",
                         path.name, idx, data_empenho.year, PERIODO_ANO_MIN, PERIODO_ANO_MAX)
            continue

        subfuncao = _map_subfuncao(row["Sub Função"])
        if subfuncao is None:
            logger.warning("%s linha %d: Sub Função não mapeada: %r",
                           path.name, idx, row["Sub Função"])
            continue
        subfuncao_codigo, subfuncao_nome = subfuncao

        fornecedor_nome, fornecedor_doc, fornecedor_tipo_doc = _extract_fornecedor(row["Fornecedor"])
        acao_codigo, acao_nome = _split_acao(row["Ação"])
        tipo_recurso = _map_tipo_recurso(row["Fonte Recurso"])

        records.append({
            "id": f"{periodo_origem}:{idx}",
            "nroEmpenho": row["Nro Empenho"],
            "nroProcesso": row["Nro Processo"],
            "dataEmpenho": data_empenho.strftime("%Y-%m-%d"),
            "ano": int(data_empenho.year),
            "mes": int(data_empenho.month),
            "descricao": row["Descrição Empenho"],
            "funcao": row["Função"],
            "subfuncaoCodigo": subfuncao_codigo,
            "subfuncaoNome": subfuncao_nome,
            "natureza": row["Natureza"],
            "modalidadeLicitacao": row["Modalidade de Licitação"],
            "valorEmpenhoOriginal": float(row["Empenho Original"]),
            "valorEstorno": float(row["Estorno Empenho"]),
            "saldoEmpenho": float(row["Saldo Empenho"]),
            "valorProcessado": float(row["Valor Processado"]),
            "valorPago": float(row["Valor Pago"]),
            "periodoOrigem": periodo_origem,
            "fonte": "lai_transparencia",
            "importedAt": imported_at,
            "fornecedorNome": fornecedor_nome,
            "fornecedorDocumento": fornecedor_doc or f"SEM_DOC:{fornecedor_nome}",
            "fornecedorTipoDocumento": fornecedor_tipo_doc,
            "fonteRecursoNome": row["Fonte Recurso"],
            "tipoRecurso": tipo_recurso,
            "unidadeNome": row["Unidade Orçamentária"],
            "aplicacaoNome": row["Aplicação"],
            "programaNome": row["Programa"],
            "acaoCodigo": acao_codigo or f"SEM_CODIGO:{acao_nome}",
            "acaoNome": acao_nome,
        })

    return records


_PERSIST_EMPENHO_QUERY = """
UNWIND $rows AS row
MERGE (f:Fornecedor {documento: row.fornecedorDocumento})
  ON CREATE SET f.nome = row.fornecedorNome, f.tipoDocumento = row.fornecedorTipoDocumento
MERGE (s:Subfuncao {codigo: row.subfuncaoCodigo})
  ON CREATE SET s.nome = row.subfuncaoNome
MERGE (fr:FonteRecurso {nome: row.fonteRecursoNome})
  ON CREATE SET fr.tipoRecurso = row.tipoRecurso
MERGE (u:UnidadeOrcamentaria {nome: row.unidadeNome})
MERGE (ap:Aplicacao {nome: row.aplicacaoNome})
MERGE (pg:ProgramaGoverno {nome: row.programaNome})
MERGE (a:Acao {codigo: row.acaoCodigo})
  ON CREATE SET a.nome = row.acaoNome
MERGE (a)-[:PARTE_DE]->(pg)
MERGE (e:Empenho {id: row.id})
SET e.nroEmpenho          = row.nroEmpenho,
    e.nroProcesso         = row.nroProcesso,
    e.dataEmpenho         = date(row.dataEmpenho),
    e.ano                 = row.ano,
    e.mes                 = row.mes,
    e.descricao           = row.descricao,
    e.funcao              = row.funcao,
    e.subfuncaoCodigo     = row.subfuncaoCodigo,
    e.subfuncaoNome       = row.subfuncaoNome,
    e.natureza            = row.natureza,
    e.modalidadeLicitacao = row.modalidadeLicitacao,
    e.valorEmpenhoOriginal = row.valorEmpenhoOriginal,
    e.valorEstorno        = row.valorEstorno,
    e.saldoEmpenho        = row.saldoEmpenho,
    e.valorProcessado     = row.valorProcessado,
    e.valorPago           = row.valorPago,
    e.periodoOrigem       = row.periodoOrigem,
    e.fonte               = row.fonte,
    e.importedAt          = row.importedAt
MERGE (e)-[:DO_FORNECEDOR]->(f)
MERGE (e)-[:CLASSIFICADO_EM]->(s)
MERGE (e)-[:FINANCIADO_POR]->(fr)
MERGE (e)-[:EXECUTA_ACAO]->(a)
MERGE (e)-[:DA_UNIDADE]->(u)
MERGE (e)-[:APLICADO_EM]->(ap)
"""


def _batches(records: list[dict], size: int):
    for i in range(0, len(records), size):
        yield records[i:i + size]


def _persist_empenhos(session, records: list[dict]) -> int:
    count = 0
    for batch in _batches(records, BATCH_SIZE):
        session.run(_PERSIST_EMPENHO_QUERY, rows=batch)
        count += len(batch)
        logger.info("  ... %d/%d empenhos persistidos", count, len(records))
    return count


# ---------------------------------------------------------------------------
# Leitura — Previsto x Realizado (OrcamentoPrograma)
# ---------------------------------------------------------------------------

_PREVISTO_COLS = ["Programa de Governo", "Ação", "Órgão Responsável"]
_PREVISTO_VALOR_COL = "Quanto foi previsto"
_REALIZADO_VALOR_COL_PREFIX = "Quanto já foi realizado"


def _read_previsto_realizado_csv(path: Path, periodo_origem: str) -> list[dict]:
    df = pd.read_csv(path, sep=CSV_SEP, encoding=CSV_ENCODING, dtype=str)

    missing = [c for c in _PREVISTO_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: colunas ausentes: {missing}")

    realizado_col = next(
        (c for c in df.columns if c.startswith(_REALIZADO_VALOR_COL_PREFIX)), None
    )
    if realizado_col is None or _PREVISTO_VALOR_COL not in df.columns:
        raise ValueError(f"{path.name}: colunas de valor previsto/realizado não encontradas")

    previsto = pd.to_numeric(df[_PREVISTO_VALOR_COL], errors="coerce").fillna(0.0)
    realizado = pd.to_numeric(df[realizado_col], errors="coerce").fillna(0.0)

    imported_at = datetime.now(timezone.utc).isoformat()
    records = []
    for idx, row in df.iterrows():
        records.append({
            "programa": row["Programa de Governo"],
            "acao": row["Ação"],
            "orgao": row["Órgão Responsável"],
            "periodoOrigem": periodo_origem,
            "valorPrevisto": float(previsto.iloc[idx]),
            "valorRealizado": float(realizado.iloc[idx]),
            "fonte": "lai_transparencia",
            "importedAt": imported_at,
        })
    return records


_PERSIST_ORCAMENTO_PROGRAMA_QUERY = """
UNWIND $rows AS row
MERGE (o:OrcamentoPrograma {
    programa: row.programa, acao: row.acao,
    orgao: row.orgao, periodoOrigem: row.periodoOrigem
})
SET o.valorPrevisto  = row.valorPrevisto,
    o.valorRealizado = row.valorRealizado,
    o.fonte          = row.fonte,
    o.importedAt     = row.importedAt
"""


def _persist_orcamento_programa(session, records: list[dict]) -> int:
    for batch in _batches(records, BATCH_SIZE):
        session.run(_PERSIST_ORCAMENTO_PROGRAMA_QUERY, rows=batch)
    return len(records)


# ---------------------------------------------------------------------------
# Agregação — DespesaAnual
# ---------------------------------------------------------------------------

_COMPUTE_DESPESA_ANUAL_QUERY = """
MATCH (e:Empenho)-[:CLASSIFICADO_EM]->(s:Subfuncao)
OPTIONAL MATCH (e)-[:FINANCIADO_POR]->(fr:FonteRecurso)
WITH s.codigo AS subfuncaoCodigo, s.nome AS subfuncaoNome, e.ano AS ano,
     sum(e.valorProcessado) AS valorProcessado,
     sum(e.valorPago) AS valorPago,
     sum(e.saldoEmpenho) AS valorEmpenhado,
     sum(CASE WHEN fr.tipoRecurso = 'proprio' THEN e.valorProcessado ELSE 0 END) AS valorProprio,
     sum(CASE WHEN fr.tipoRecurso = 'federal' THEN e.valorProcessado ELSE 0 END) AS valorFederal,
     sum(CASE WHEN fr.tipoRecurso = 'estadual' THEN e.valorProcessado ELSE 0 END) AS valorEstadual,
     sum(CASE WHEN fr.tipoRecurso = 'credito' THEN e.valorProcessado ELSE 0 END) AS valorCredito,
     sum(CASE WHEN fr.tipoRecurso = 'emenda_parlamentar' THEN e.valorProcessado ELSE 0 END) AS valorEmendaParlamentar,
     sum(CASE WHEN fr.tipoRecurso = 'outros' THEN e.valorProcessado ELSE 0 END) AS valorOutros
MERGE (d:DespesaAnual {subfuncaoCodigo: subfuncaoCodigo, ano: ano})
SET d.subfuncaoNome          = subfuncaoNome,
    d.valorProcessado        = valorProcessado,
    d.valorPago               = valorPago,
    d.valorEmpenhado          = valorEmpenhado,
    d.valorProprio            = valorProprio,
    d.valorFederal            = valorFederal,
    d.valorEstadual           = valorEstadual,
    d.valorCredito            = valorCredito,
    d.valorEmendaParlamentar  = valorEmendaParlamentar,
    d.valorOutros             = valorOutros,
    d.fonte                   = 'lai_transparencia',
    d.importedAt              = $importedAt
RETURN count(d) AS total
"""

# NOTA: o relacionamento VARIACAO_ANUAL (encadeamento de DespesaAnual entre
# anos consecutivos com a variação percentual já calculada) foi desenhado e
# aprovado, mas fica para uma execução futura — ver PLANO_NOVO_MODELO_DADOS.md
# §3.1 e §5. Não implementado neste loader.


def _compute_despesa_anual(session) -> int:
    result = session.run(
        _COMPUTE_DESPESA_ANUAL_QUERY,
        importedAt=datetime.now(timezone.utc).isoformat(),
    )
    return result.single()["total"]


# ---------------------------------------------------------------------------
# Agregação — quebra dimensional de DespesaAnual (Fase 3)
#
# Roda depois de _compute_despesa_anual (mesma sessão) — MATCH, não MERGE,
# nos nós DespesaAnual que acabaram de ser calculados. Espelha o padrão já
# usado do lado saúde (IndicadorSaude-[:POR_X {valor}]->(dim)): o valor
# numérico vive na relação, o nó de dimensão é genérico. `db/query_builder.py`
# consome esses reltypes (POR_NATUREZA/POR_APLICACAO) via `dimensao`,
# validado contra `DESPESA_DIMENSOES` antes de qualquer interpolação.
#
# POR_APLICACAO reaproveita o nó Aplicacao já criado por
# _PERSIST_EMPENHO_QUERY (via APLICADO_EM) — MATCH, não MERGE, para nunca
# divergir da instância já existente. POR_NATUREZA cria o único nó
# genuinamente novo desta fase (Natureza não existia antes — natureza era
# só uma propriedade string em Empenho, sem nó próprio).
# ---------------------------------------------------------------------------

_COMPUTE_DESPESA_POR_APLICACAO_QUERY = """
MATCH (e:Empenho)-[:CLASSIFICADO_EM]->(s:Subfuncao)
MATCH (e)-[:APLICADO_EM]->(ap:Aplicacao)
WITH s.codigo AS subfuncaoCodigo, e.ano AS ano, ap AS aplicacao,
     sum(e.valorProcessado) AS valor
MATCH (d:DespesaAnual {subfuncaoCodigo: subfuncaoCodigo, ano: ano})
MERGE (d)-[r:POR_APLICACAO]->(aplicacao)
SET r.valor = valor
RETURN count(r) AS total
"""

_COMPUTE_DESPESA_POR_NATUREZA_QUERY = """
MATCH (e:Empenho)-[:CLASSIFICADO_EM]->(s:Subfuncao)
WITH s.codigo AS subfuncaoCodigo, e.ano AS ano, e.natureza AS natureza,
     sum(e.valorProcessado) AS valor
MATCH (d:DespesaAnual {subfuncaoCodigo: subfuncaoCodigo, ano: ano})
MERGE (n:Natureza {nome: natureza})
MERGE (d)-[r:POR_NATUREZA]->(n)
SET r.valor = valor
RETURN count(r) AS total
"""


def _compute_despesa_por_aplicacao(session) -> int:
    result = session.run(_COMPUTE_DESPESA_POR_APLICACAO_QUERY)
    return result.single()["total"]


def _compute_despesa_por_natureza(session) -> int:
    result = session.run(_COMPUTE_DESPESA_POR_NATUREZA_QUERY)
    return result.single()["total"]


# ---------------------------------------------------------------------------
# Agregação — VARIACAO_ANUAL (desenhada e aprovada em
# PLANO_NOVO_MODELO_DADOS.md §3.1, implementada agora)
#
# (:DespesaAnual {subfuncaoCodigo, ano})-[:VARIACAO_ANUAL {percentual, classificacao}]->
# (:DespesaAnual {subfuncaoCodigo, ano_anterior})
#
# Diferente das agregações acima (CASE-WHEN/GROUP BY puro em Cypher), a
# variação ano-a-ano precisa da mesma fórmula/limiares que
# `AgenteContextoOrcamentario` já usa em tempo de análise — para não ter
# duas lógicas de classificação divergentes, o cálculo é feito em Python
# reaproveitando `compute_yoy_variation`/`classify_trend` literalmente
# (agents/context/contexto_orcamentario.py), não reescrito em Cypher.
# "Anos consecutivos" aqui significa consecutivos na lista de anos que
# de fato têm DespesaAnual para aquela subfunção (mesma semântica do
# `_analyze_trends` do agente, que itera por índice na lista ordenada,
# não por ano civil — uma subfunção sem despesa num ano específico não
# quebra a cadeia, só pula esse ano).
# ---------------------------------------------------------------------------

_MERGE_VARIACAO_ANUAL_QUERY = """
UNWIND $edges AS edge
MATCH (atual:DespesaAnual {subfuncaoCodigo: edge.subfuncaoCodigo, ano: edge.anoAtual})
MATCH (anterior:DespesaAnual {subfuncaoCodigo: edge.subfuncaoCodigo, ano: edge.anoAnterior})
MERGE (atual)-[r:VARIACAO_ANUAL]->(anterior)
SET r.percentual = edge.percentual, r.classificacao = edge.classificacao
RETURN count(r) AS total
"""


def _compute_variacao_anual(session) -> int:
    from agents.context.contexto_orcamentario import classify_trend, compute_yoy_variation

    rows = session.run("""
        MATCH (d:DespesaAnual)
        RETURN d.subfuncaoCodigo AS subfuncaoCodigo, d.ano AS ano,
               d.valorProcessado AS valor
        ORDER BY d.subfuncaoCodigo, d.ano
    """)

    by_subfuncao: dict[int, list[tuple[int, float]]] = {}
    for row in rows:
        by_subfuncao.setdefault(row["subfuncaoCodigo"], []).append((row["ano"], row["valor"]))

    edges = []
    for codigo, pontos in by_subfuncao.items():
        pontos.sort(key=lambda p: p[0])
        for (ano_anterior, valor_anterior), (ano_atual, valor_atual) in zip(pontos, pontos[1:]):
            percentual = compute_yoy_variation(valor_atual, valor_anterior)
            classificacao = classify_trend([percentual])
            edges.append({
                "subfuncaoCodigo": codigo,
                "anoAtual": ano_atual,
                "anoAnterior": ano_anterior,
                "percentual": percentual,
                "classificacao": classificacao,
            })

    if not edges:
        return 0

    result = session.run(_MERGE_VARIACAO_ANUAL_QUERY, edges=edges)
    return result.single()["total"]


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------


def load(neo4j_client) -> dict:
    """Lê todos os CSVs de orçamento em Dados/orcamento/ e persiste no Neo4j."""
    counts = {
        "empenhos": 0,
        "orcamento_programa": 0,
        "despesa_anual": 0,
        "despesa_por_aplicacao": 0,
        "despesa_por_natureza": 0,
        "variacao_anual": 0,
    }

    with neo4j_client._driver.session() as session:
        _ensure_constraints(session)

    if not ORCAMENTO_DIR.exists():
        logger.warning("Pasta não encontrada: %s", ORCAMENTO_DIR)
        return counts

    for periodo_dir in sorted(p for p in ORCAMENTO_DIR.iterdir() if p.is_dir()):
        periodo_origem = periodo_dir.name
        for csv_path in sorted(periodo_dir.glob("*.csv")):
            name_lower = csv_path.name.lower()
            try:
                if "previsto_e_empenhado" in name_lower:
                    records = _read_previsto_realizado_csv(csv_path, periodo_origem)
                    with neo4j_client._driver.session() as session:
                        n = _persist_orcamento_programa(session, records)
                    counts["orcamento_programa"] += n
                    logger.info("%s → %d registros OrcamentoPrograma", csv_path.name, n)
                elif "empenhado" in name_lower:
                    records = _read_empenho_csv(csv_path, periodo_origem)
                    with neo4j_client._driver.session() as session:
                        n = _persist_empenhos(session, records)
                    counts["empenhos"] += n
                    logger.info("%s → %d registros Empenho", csv_path.name, n)
                else:
                    logger.warning("Arquivo não reconhecido, ignorado: %s", csv_path.name)
            except Exception:
                logger.exception("Falha ao processar %s", csv_path)

    with neo4j_client._driver.session() as session:
        counts["despesa_anual"] = _compute_despesa_anual(session)
        counts["despesa_por_aplicacao"] = _compute_despesa_por_aplicacao(session)
        counts["despesa_por_natureza"] = _compute_despesa_por_natureza(session)
        counts["variacao_anual"] = _compute_variacao_anual(session)

    logger.info("Orcamento ETL concluido: %s", counts)
    return counts


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from db.neo4j_client import Neo4jClient

    with Neo4jClient(
        uri=os.environ["NEO4J_URI"],
        user=os.environ["NEO4J_USER"],
        password=os.environ["NEO4J_PASSWORD"],
    ) as client:
        result = load(client)
        print(f"Importação concluída: {result}")
