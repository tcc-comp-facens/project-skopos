"""
ETL — Ingestão de indicadores de saúde a partir dos extratos TabNet/DATASUS
já filtrados para Sorocaba, substituindo datasus_loader.py + PySUS.

Diferença central em relação ao pipeline antigo: leitura local de planilhas
já prontas (sem FTP, sem .dbc, sem PySUS), com granularidade dimensional
(faixa etária, capítulo CID-10, vacina, tipo de estabelecimento etc.) que o
pipeline antigo não tinha — só extraía um total por (sistema, tipo, ano).

Fonte: Dados/Sorocaba_DATASUS_2015-2025/*.xlsx (ver PLANO_NOVO_MODELO_DADOS.md)

8 sistemas, 1 função de carga por sistema (`_load_sim`, `_load_sih`, ...),
mapeando para os 8 agentes de saúde definidos na arquitetura (1 agente por
Sistema de Informação). `Sorocaba_Bases_Indisponiveis_2015-2025.xlsx` não é
carregado — contém só notas sobre cubos TabNet descontinuados, não dado.

Janela temporal: 01/01/2016-31/12/2025 (10 anos) — qualquer ano fora disso é
descartado no ETL (mesma regra do orcamento_loader.py).

Decisões de modelagem (ver PLANO_NOVO_MODELO_DADOS.md §7.1/§7):
  - FaixaEtaria NÃO é unificada entre sistemas — cada sistema tem seu próprio
    espaço de faixas (chave = "{sistema}:{nome}"), porque os bins não batem
    entre si e SINASC mede idade da mãe, não da pessoa do registro.
  - CapituloCID10 É unificado entre SIM (numeração romana) e SIH (numeração
    decimal) — chave = código canônico 1-21, é o ponto de correlação real
    entre os dois sistemas.
  - Sub-cubos mensais do CNES (Estabelecimentos, Equipes de Saude, Tipo
    Atendimento, Rec.Fisicos-*) são agregados para o snapshot de dezembro,
    consistente com o resto do CNES (que já é anual/Dez nativo).
  - COVID mantém granularidade mensal (além da anual) — único caso onde
    IndicadorSaude.mes é populado; os demais sistemas são só anuais (mes=null).

Uso:
    python -m etl.saude_indicadores_loader

Variáveis de ambiente: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, DADOS_DIR (opcional)
"""

import os
import re
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DADOS_DIR = Path(os.environ.get("DADOS_DIR", Path(__file__).parent.parent.parent / "Dados"))
DATASUS_DIR = DADOS_DIR / "Sorocaba_DATASUS_2015-2025"
BATCH_SIZE = 2000

# Janela temporal oficial do sistema (mesma regra do orcamento_loader.py).
PERIODO_ANO_MIN = 2016
PERIODO_ANO_MAX = 2025

# ---------------------------------------------------------------------------
# Capítulo CID-10 — mapeamento canônico (unifica SIM [romano] e SIH [decimal])
# ---------------------------------------------------------------------------

CID10_CAPITULOS: dict[int, str] = {
    1: "Doenças infecciosas e parasitárias",
    2: "Neoplasias",
    3: "Doenças do sangue e órgãos hematopoéticos",
    4: "Doenças endócrinas, nutricionais e metabólicas",
    5: "Transtornos mentais e comportamentais",
    6: "Doenças do sistema nervoso",
    7: "Doenças do olho e anexos",
    8: "Doenças do ouvido e da apófise mastóide",
    9: "Doenças do aparelho circulatório",
    10: "Doenças do aparelho respiratório",
    11: "Doenças do aparelho digestivo",
    12: "Doenças da pele e do tecido subcutâneo",
    13: "Doenças osteomusculares e do tecido conjuntivo",
    14: "Doenças do aparelho geniturinário",
    15: "Gravidez, parto e puerpério",
    16: "Afecções originadas no período perinatal",
    17: "Malformações congênitas",
    18: "Sintomas, sinais e achados anormais",
    19: "Lesões, envenenamentos e causas externas",
    20: "Causas externas de morbidade e mortalidade",
    21: "Contato com serviços de saúde",
}

_ROMAN_TO_INT: dict[str, int] = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
    "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
    "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20, "XXI": 21,
}

_CAPITULO_RE = re.compile(r"Cap\s*([IVXLCDM]+|\d+)\(")

_MES_ABREV: dict[str, int] = {
    "Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4, "Mai": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Set": 9, "Out": 10, "Nov": 11, "Dez": 12,
}


def _capitulo_cid_from_categorias(categorias: dict) -> dict:
    """Converte {'Cap I(Infecciosas)': 198, 'Cap09(Circulatorio)': 1115, ...}
    em {codigo_canonico: (nome_canonico, valor)}."""
    result = {}
    for header, valor in categorias.items():
        m = _CAPITULO_RE.match(header)
        if not m:
            continue
        numeral = m.group(1)
        codigo = int(numeral) if numeral.isdigit() else _ROMAN_TO_INT.get(numeral)
        if codigo is None:
            logger.warning("Capítulo CID não reconhecido: %r", header)
            continue
        result[codigo] = (CID10_CAPITULOS.get(codigo, header), valor)
    return result


# ---------------------------------------------------------------------------
# Leitores genéricos de planilha TabNet
# ---------------------------------------------------------------------------


def _parse_ano_mes(raw) -> Optional[tuple[int, int]]:
    """Parseia 'YYYY/Mon' (ex. '2015/Jan') -> (ano, mes)."""
    text = str(raw).strip()
    if "/" not in text:
        return None
    ano_str, mes_str = text.split("/", 1)
    mes = _MES_ABREV.get(mes_str.strip())
    if mes is None:
        return None
    try:
        return int(ano_str), mes
    except ValueError:
        return None


def _read_wide_sheet(path: Path, sheet: str, mensal: bool = False) -> list[dict]:
    """Lê aba no formato padrão TabNet: título (linha 0), vazia (linha 1),
    cabeçalho (linha 2), dados (linha 3+). Primeira coluna = Ano ou Ano/Mês,
    demais colunas = categorias, com 'Total' opcional.

    Quando mensal=True, espera chave 'Ano/Mês' e retorna só as linhas de
    Dezembro (decisão: sub-cubos mensais do CNES agregados para snapshot
    de dezembro — ver docstring do módulo).

    Retorna lista de {"ano": int, "categorias": {nome: valor}, "total": float|None}.
    """
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    headers = df.iloc[2]

    categorias_cols = [
        (i, str(headers.iloc[i]).strip())
        for i in range(1, len(headers))
        if str(headers.iloc[i]) != "nan" and str(headers.iloc[i]).strip().lower() != "total"
    ]
    total_col = next(
        (i for i in range(1, len(headers)) if str(headers.iloc[i]).strip().lower() == "total"),
        None,
    )

    records = []
    for row_idx in range(3, len(df)):
        key_raw = df.iloc[row_idx, 0]
        if pd.isna(key_raw):
            continue

        if mensal:
            parsed = _parse_ano_mes(key_raw)
            if parsed is None or parsed[1] != 12:
                continue
            ano = parsed[0]
        else:
            try:
                ano = int(float(str(key_raw).strip()))
            except (ValueError, TypeError):
                continue

        if not (PERIODO_ANO_MIN <= ano <= PERIODO_ANO_MAX):
            continue

        categorias = {}
        for col_idx, nome in categorias_cols:
            val = df.iloc[row_idx, col_idx]
            if pd.notna(val):
                categorias[nome] = float(val)

        total = None
        if total_col is not None:
            val = df.iloc[row_idx, total_col]
            if pd.notna(val):
                total = float(val)

        records.append({"ano": ano, "categorias": categorias, "total": total})

    return records


def _read_single_valor_sheet(path: Path, sheet: str) -> list[dict]:
    """Lê aba com Ano + 1 única coluna de valor (sem quebra dimensional)."""
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    records = []
    for row_idx in range(3, len(df)):
        key_raw = df.iloc[row_idx, 0]
        if pd.isna(key_raw):
            continue
        try:
            ano = int(float(str(key_raw).strip()))
        except (ValueError, TypeError):
            continue
        if not (PERIODO_ANO_MIN <= ano <= PERIODO_ANO_MAX):
            continue
        val = df.iloc[row_idx, 1]
        if pd.isna(val):
            continue
        records.append({"ano": ano, "valor": float(val)})
    return records


def _read_vacina_transposta(path: Path, sheet: str) -> list[dict]:
    """Lê aba do SI-PNI no formato transposto: linhas = vacina, colunas = ano
    (mais colunas auxiliares como 'Media_Periodo_SemRotulo' ou 'Total', que
    são ignoradas por não parsearem como ano)."""
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    header_row = df.iloc[2]

    records = []
    for col_idx in range(1, len(header_row)):
        try:
            ano = int(float(header_row.iloc[col_idx]))
        except (ValueError, TypeError):
            continue
        if not (PERIODO_ANO_MIN <= ano <= PERIODO_ANO_MAX):
            continue
        for row_idx in range(3, len(df)):
            vacina = df.iloc[row_idx, 0]
            if pd.isna(vacina):
                continue
            valor = df.iloc[row_idx, col_idx]
            if pd.isna(valor):
                continue
            records.append({"vacina": str(vacina).strip(), "ano": ano, "valor": float(valor)})

    return records


# ---------------------------------------------------------------------------
# Conversão para linhas de persistência (IndicadorSaude + quebras dimensionais)
# ---------------------------------------------------------------------------


def _indicador_chave(sistema: str, subtipo: str, ano: int, mes: Optional[int] = None) -> str:
    return f"{sistema}:{subtipo}:{ano}:{mes or 0}"


def _indicador_row(sistema: str, subtipo: str, ano: int, mes: Optional[int],
                    valor_total: Optional[float], imported_at: str,
                    valor_acumulado: Optional[float] = None) -> dict:
    return {
        "chave": _indicador_chave(sistema, subtipo, ano, mes),
        "sistema": sistema, "subtipo": subtipo, "ano": ano, "mes": mes,
        "valorTotal": valor_total, "valorAcumulado": valor_acumulado,
        "fonte": "datasus_tabnet", "importedAt": imported_at,
    }


def _wide_to_rows(records: list[dict], sistema: str, subtipo: str, imported_at: str,
                   scoped_by_sistema: bool = False) -> tuple[list[dict], list[dict]]:
    """Converte saída de _read_wide_sheet em (linhas IndicadorSaude, linhas de quebra).

    scoped_by_sistema=True escopa a chave do nó de dimensão por sistema
    (usado por FaixaEtaria, que não é unificada entre sistemas — ver
    docstring do módulo).
    """
    indicador_rows = []
    breakdown_rows = []
    for rec in records:
        ano = rec["ano"]
        indicador_rows.append(_indicador_row(sistema, subtipo, ano, None, rec["total"], imported_at))
        ind_chave = _indicador_chave(sistema, subtipo, ano)
        for nome, valor in rec["categorias"].items():
            chave = f"{sistema}:{nome}" if scoped_by_sistema else nome
            row = {"indicadorChave": ind_chave, "chave": chave, "nome": nome, "valor": valor}
            if scoped_by_sistema:
                row["sistema"] = sistema
            breakdown_rows.append(row)
    return indicador_rows, breakdown_rows


def _wide_to_rows_capitulo(records: list[dict], sistema: str, subtipo: str,
                            imported_at: str) -> tuple[list[dict], list[dict]]:
    indicador_rows = []
    breakdown_rows = []
    for rec in records:
        ano = rec["ano"]
        indicador_rows.append(_indicador_row(sistema, subtipo, ano, None, rec["total"], imported_at))
        ind_chave = _indicador_chave(sistema, subtipo, ano)
        for codigo, (nome, valor) in _capitulo_cid_from_categorias(rec["categorias"]).items():
            breakdown_rows.append({
                "indicadorChave": ind_chave, "chave": f"{codigo:02d}",
                "codigo": f"{codigo:02d}", "nome": nome, "valor": valor,
            })
    return indicador_rows, breakdown_rows


def _vacina_to_rows(records: list[dict], sistema: str, subtipo: str, imported_at: str,
                     agregacao: str) -> tuple[list[dict], list[dict]]:
    """agregacao: 'soma' (doses aplicadas) ou 'media' (cobertura, é percentual)."""
    por_ano = defaultdict(list)
    for r in records:
        por_ano[r["ano"]].append(r)

    indicador_rows = []
    breakdown_rows = []
    for ano, recs in por_ano.items():
        valores = [r["valor"] for r in recs]
        total = sum(valores) if agregacao == "soma" else sum(valores) / len(valores)
        indicador_rows.append(_indicador_row(sistema, subtipo, ano, None, total, imported_at))
        ind_chave = _indicador_chave(sistema, subtipo, ano)
        for r in recs:
            breakdown_rows.append({
                "indicadorChave": ind_chave, "chave": r["vacina"], "nome": r["vacina"], "valor": r["valor"],
            })
    return indicador_rows, breakdown_rows


def _read_covid_anual(path: Path) -> list[dict]:
    df = pd.read_excel(path, sheet_name="COVID Casos-Obitos Anual", header=None)
    records = []
    for row_idx in range(3, len(df)):
        try:
            ano = int(float(str(df.iloc[row_idx, 0]).strip()))
        except (ValueError, TypeError):
            continue
        if not (PERIODO_ANO_MIN <= ano <= PERIODO_ANO_MAX):
            continue
        casos_novos, obitos_novos, casos_acum, obitos_acum = (
            df.iloc[row_idx, 1], df.iloc[row_idx, 2], df.iloc[row_idx, 3], df.iloc[row_idx, 4]
        )
        records.append({
            "ano": ano, "mes": None,
            "casos_novos": float(casos_novos) if pd.notna(casos_novos) else None,
            "obitos_novos": float(obitos_novos) if pd.notna(obitos_novos) else None,
            "casos_acumulado": float(casos_acum) if pd.notna(casos_acum) else None,
            "obitos_acumulado": float(obitos_acum) if pd.notna(obitos_acum) else None,
        })
    return records


def _read_covid_mensal(path: Path) -> list[dict]:
    df = pd.read_excel(path, sheet_name="COVID Casos-Obitos Mensal", header=None)
    records = []
    for row_idx in range(3, len(df)):
        text = str(df.iloc[row_idx, 0]).strip()
        if "-" not in text:
            continue
        try:
            ano_str, mes_str = text.split("-", 1)
            ano, mes = int(ano_str), int(mes_str)
        except ValueError:
            continue
        if not (PERIODO_ANO_MIN <= ano <= PERIODO_ANO_MAX):
            continue
        casos_novos, obitos_novos, casos_acum, obitos_acum = (
            df.iloc[row_idx, 1], df.iloc[row_idx, 2], df.iloc[row_idx, 3], df.iloc[row_idx, 4]
        )
        records.append({
            "ano": ano, "mes": mes,
            "casos_novos": float(casos_novos) if pd.notna(casos_novos) else None,
            "obitos_novos": float(obitos_novos) if pd.notna(obitos_novos) else None,
            "casos_acumulado": float(casos_acum) if pd.notna(casos_acum) else None,
            "obitos_acumulado": float(obitos_acum) if pd.notna(obitos_acum) else None,
        })
    return records


def _covid_to_indicador_rows(records: list[dict], imported_at: str) -> list[dict]:
    rows = []
    for r in records:
        rows.append(_indicador_row("covid", "casos", r["ano"], r["mes"],
                                    r["casos_novos"], imported_at, r["casos_acumulado"]))
        rows.append(_indicador_row("covid", "obitos", r["ano"], r["mes"],
                                    r["obitos_novos"], imported_at, r["obitos_acumulado"]))
    return rows


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

_CONSTRAINTS = [
    "CREATE CONSTRAINT indicador_saude_chave IF NOT EXISTS FOR (i:IndicadorSaude) REQUIRE i.chave IS UNIQUE",
    "CREATE CONSTRAINT faixa_etaria_chave IF NOT EXISTS FOR (f:FaixaEtaria) REQUIRE f.chave IS UNIQUE",
    "CREATE CONSTRAINT capitulo_cid10_codigo IF NOT EXISTS FOR (c:CapituloCID10) REQUIRE c.codigo IS UNIQUE",
    "CREATE CONSTRAINT sexo_chave IF NOT EXISTS FOR (s:Sexo) REQUIRE s.chave IS UNIQUE",
    "CREATE CONSTRAINT faixa_peso_chave IF NOT EXISTS FOR (f:FaixaPeso) REQUIRE f.chave IS UNIQUE",
    "CREATE CONSTRAINT vacina_chave IF NOT EXISTS FOR (v:Vacina) REQUIRE v.chave IS UNIQUE",
    "CREATE CONSTRAINT tipo_estabelecimento_chave IF NOT EXISTS FOR (t:TipoEstabelecimento) REQUIRE t.chave IS UNIQUE",
    "CREATE CONSTRAINT ocupacao_profissional_chave IF NOT EXISTS FOR (o:OcupacaoProfissional) REQUIRE o.chave IS UNIQUE",
    "CREATE CONSTRAINT tipo_equipe_chave IF NOT EXISTS FOR (t:TipoEquipe) REQUIRE t.chave IS UNIQUE",
    "CREATE CONSTRAINT tipo_atendimento_chave IF NOT EXISTS FOR (t:TipoAtendimento) REQUIRE t.chave IS UNIQUE",
    "CREATE CONSTRAINT tipo_leito_consultorio_chave IF NOT EXISTS FOR (t:TipoLeitoConsultorio) REQUIRE t.chave IS UNIQUE",
    "CREATE CONSTRAINT tipo_equipamento_chave IF NOT EXISTS FOR (t:TipoEquipamento) REQUIRE t.chave IS UNIQUE",
]


def _ensure_constraints(session) -> None:
    for stmt in _CONSTRAINTS:
        session.run(stmt)


# ---------------------------------------------------------------------------
# Persistência genérica
# ---------------------------------------------------------------------------


def _batches(records: list[dict], size: int):
    for i in range(0, len(records), size):
        yield records[i:i + size]


_PERSIST_INDICADOR_QUERY = """
UNWIND $rows AS row
MERGE (i:IndicadorSaude {chave: row.chave})
SET i.sistema        = row.sistema,
    i.subtipo        = row.subtipo,
    i.ano            = row.ano,
    i.mes            = row.mes,
    i.valorTotal     = row.valorTotal,
    i.valorAcumulado = row.valorAcumulado,
    i.fonte          = row.fonte,
    i.importedAt     = row.importedAt
"""


def _persist_indicadores(session, rows: list[dict]) -> None:
    for batch in _batches(rows, BATCH_SIZE):
        session.run(_PERSIST_INDICADOR_QUERY, rows=batch)


def _persist_breakdown(session, rows: list[dict], rel_type: str, node_label: str,
                        key_prop: str = "chave", extra_props: Optional[list[str]] = None) -> None:
    if not rows:
        return
    extra_props = extra_props or []
    set_clause = ""
    if extra_props:
        assigns = ", ".join(f"d.{p} = row.{p}" for p in extra_props)
        set_clause = f"ON CREATE SET {assigns}"
    # rel_type/node_label vêm sempre de constantes fixas definidas neste
    # módulo (nunca de texto de planilha) — seguro interpolar diretamente.
    query = f"""
    UNWIND $rows AS row
    MATCH (i:IndicadorSaude {{chave: row.indicadorChave}})
    MERGE (d:{node_label} {{{key_prop}: row.chave}})
      {set_clause}
    MERGE (i)-[r:{rel_type}]->(d)
    SET r.valor = row.valor
    """
    for batch in _batches(rows, BATCH_SIZE):
        session.run(query, rows=batch)


# ---------------------------------------------------------------------------
# Carga por sistema
# ---------------------------------------------------------------------------


def _load_sim(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_Mortalidade_SIM_2015-2025.xlsx"
    total = 0

    recs = _read_wide_sheet(path, "Óbitos por Faixa Etária")
    ind_rows, faixa_rows = _wide_to_rows(recs, "sim", "mortalidade", imported_at, scoped_by_sistema=True)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, faixa_rows, "POR_FAIXA_ETARIA", "FaixaEtaria", extra_props=["nome", "sistema"])
    total += len(ind_rows)

    recs = _read_wide_sheet(path, "Óbitos por Capítulo CID-10")
    ind_rows, cap_rows = _wide_to_rows_capitulo(recs, "sim", "mortalidade", imported_at)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, cap_rows, "POR_CAPITULO_CID", "CapituloCID10",
                        key_prop="codigo", extra_props=["nome", "codigo"])

    return total


def _load_sih(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_Internacoes_SIH_2015-2025.xlsx"
    recs = _read_wide_sheet(path, "Internações por Capítulo CID-10")
    ind_rows, cap_rows = _wide_to_rows_capitulo(recs, "sih", "internacoes", imported_at)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, cap_rows, "POR_CAPITULO_CID", "CapituloCID10",
                        key_prop="codigo", extra_props=["nome", "codigo"])
    return len(ind_rows)


def _load_sinasc(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_Nascidos_Vivos_SINASC_2015-2025.xlsx"
    total = 0

    recs = _read_wide_sheet(path, "Nascimentos por Idade da Mãe")
    ind_rows, faixa_rows = _wide_to_rows(recs, "sinasc", "nascidos_vivos", imported_at, scoped_by_sistema=True)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, faixa_rows, "POR_FAIXA_ETARIA", "FaixaEtaria", extra_props=["nome", "sistema"])
    total += len(ind_rows)

    recs = _read_wide_sheet(path, "Nascimentos por Peso ao Nascer")
    ind_rows, peso_rows = _wide_to_rows(recs, "sinasc", "nascidos_vivos", imported_at)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, peso_rows, "POR_FAIXA_PESO", "FaixaPeso", extra_props=["nome"])

    return total


_SINAN_SHEETS = [
    ("Dengue por Faixa Etária", "dengue", "faixa"),
    ("Chikungunya por Faixa Etária", "chikungunya", "faixa"),
    ("Sífilis Adquirida", "sifilis_adquirida", "faixa"),
    ("Sífilis em Gestante", "sifilis_gestante", "faixa"),
    ("Sífilis Congênita", "sifilis_congenita", "faixa"),
    ("Coqueluche por Faixa Etária", "coqueluche", "faixa"),
    ("Hepatites Virais", "hepatites_virais", "faixa"),
    ("Tuberculose por Sexo", "tuberculose", "sexo"),
    ("Hanseníase por Sexo", "hanseniase", "sexo"),
]


def _load_sinan(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_SINAN_Doencas_Notificacao_2015-2025.xlsx"
    total = 0
    for sheet, subtipo, dimensao in _SINAN_SHEETS:
        recs = _read_wide_sheet(path, sheet)
        ind_rows, dim_rows = _wide_to_rows(
            recs, "sinan", subtipo, imported_at, scoped_by_sistema=(dimensao == "faixa")
        )
        _persist_indicadores(session, ind_rows)
        if dimensao == "faixa":
            _persist_breakdown(session, dim_rows, "POR_FAIXA_ETARIA", "FaixaEtaria",
                                extra_props=["nome", "sistema"])
        else:
            _persist_breakdown(session, dim_rows, "POR_SEXO", "Sexo", extra_props=["nome"])
        total += len(ind_rows)
    return total


def _load_sipni(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_SIPNI_Cobertura_Vacinal_2015-2026.xlsx"
    total = 0

    cobertura = (_read_vacina_transposta(path, "Cobertura 2015-2022")
                 + _read_vacina_transposta(path, "Cobertura 2023-2025"))
    ind_rows, vac_rows = _vacina_to_rows(cobertura, "sipni", "cobertura_vacinal", imported_at, agregacao="media")
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, vac_rows, "COBERTURA_VACINAL", "Vacina", extra_props=["nome"])
    total += len(ind_rows)

    doses = (_read_vacina_transposta(path, "Doses 2015-2022")
             + _read_vacina_transposta(path, "Doses 2023-2026"))
    ind_rows, vac_rows = _vacina_to_rows(doses, "sipni", "doses_aplicadas", imported_at, agregacao="soma")
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, vac_rows, "DOSES_APLICADAS", "Vacina", extra_props=["nome"])
    total += len(ind_rows)

    return total


def _load_sia(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_SIA_Producao_Ambulatorial_2015-2025.xlsx"
    recs = _read_single_valor_sheet(path, "Producao Ambulatorial por Ano")
    rows = [_indicador_row("sia", "producao_ambulatorial", r["ano"], None, r["valor"], imported_at)
            for r in recs]
    _persist_indicadores(session, rows)
    return len(rows)


def _load_covid(session, imported_at: str) -> int:
    path = DATASUS_DIR / "Sorocaba_COVID19_Casos_Obitos_2020-2023.xlsx"
    registros = _read_covid_anual(path) + _read_covid_mensal(path)
    rows = _covid_to_indicador_rows(registros, imported_at)
    _persist_indicadores(session, rows)
    return len(rows)


# "Tipo Atendimento (SUS)" e "Rec.Fisicos-Leitos e Consult" não têm coluna
# "Total" na planilha fonte — decisão do próprio DATASUS, não lacuna da
# ETL — porque não existe uma soma válida das categorias em nenhum dos
# dois casos:
#   - Tipo Atendimento: colunas (Ambulatorio_SUS, Internacao_SUS,
#     SADT_SUS, Urgencia_SUS, Farmacia_Cooperativa_SUS) contam
#     ESTABELECIMENTOS, e um mesmo estabelecimento aparece em várias
#     colunas ao mesmo tempo (ex.: um hospital que atende ambulatório E
#     internação E urgência) — somar duplicaria contagem.
#   - Leitos e Consultórios: mistura unidades físicas diferentes
#     (leitos vs. consultórios) em categorias como
#     Consultorios_ClinicaBasica_Ambul/Leitos_Repouso_Pediatrico_Hosp —
#     somar produz um número sem significado (nem "total de leitos" nem
#     "total de consultórios").
# `_wide_to_rows` grava `valorTotal=None` para esses dois quando não há
# coluna "Total" (ver `_read_wide_sheet`) — propositalmente, não por
# omissão. `detect_data_gaps` (agents/data_crossing.py) filtra valores
# None antes de contar cobertura, então esse gap real aparece no
# relatório em vez de ser reportado como "100% completo". A quebra por
# categoria (POR_TIPO_ATENDIMENTO/POR_TIPO_LEITO_CONSULTORIO) continua
# disponível via consulta dimensional — só o agregado plano é que não
# existe.
_CNES_MENSAIS = [
    ("Equipes de Saude", "equipes_saude", "POR_TIPO_EQUIPE", "TipoEquipe"),
    ("Tipo Atendimento (SUS)", "tipo_atendimento", "POR_TIPO_ATENDIMENTO", "TipoAtendimento"),
    ("Rec.Fisicos-Leitos e Consult", "leitos_consultorios", "POR_TIPO_LEITO_CONSULTORIO", "TipoLeitoConsultorio"),
    ("Rec.Fisicos-Equipamentos", "equipamentos", "POR_TIPO_EQUIPAMENTO", "TipoEquipamento"),
]

_CNES_ESTABELECIMENTOS_METRICAS = {
    "Nivel_Atencao_Qtd_Geral": "estabelecimentos_nivel_atencao",
    "Servico_Classificacao_a_partir_Mar2008": "estabelecimentos_servico_classificacao",
    "Habilitacao_Qtd_Estab_Habilitados": "estabelecimentos_habilitacao",
    "Vigilancia_Epidem_Sanitaria_Qtd": "estabelecimentos_vigilancia_epidemiologica",
}


def _load_cnes(session, imported_at: str) -> int:
    total = 0

    recursos_path = DATASUS_DIR / "Sorocaba_CNES_Recursos_2015-2025.xlsx"
    for sheet, subtipo in [("Leitos de Internacao (Dez)", "leitos"),
                            ("Profissionais de Saude (Dez)", "profissionais")]:
        recs = _read_single_valor_sheet(recursos_path, sheet)
        rows = [_indicador_row("cnes", subtipo, r["ano"], None, r["valor"], imported_at) for r in recs]
        _persist_indicadores(session, rows)
        total += len(rows)

    rede_path = DATASUS_DIR / "Sorocaba_Rede_Assistencial_CNES_2015-2025.xlsx"
    recs = _read_wide_sheet(rede_path, "Estabelecimentos por Tipo")
    ind_rows, tipo_rows = _wide_to_rows(recs, "cnes", "estabelecimentos_por_tipo", imported_at)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, tipo_rows, "POR_TIPO_ESTABELECIMENTO", "TipoEstabelecimento",
                        extra_props=["nome"])
    total += len(ind_rows)

    adicionais_path = DATASUS_DIR / "Sorocaba_CNES_Dados_Adicionais_2015-2025.xlsx"

    recs = _read_wide_sheet(adicionais_path, "Rec.Humanos-Ocupacoes(Dez)")
    ind_rows, ocup_rows = _wide_to_rows(recs, "cnes", "ocupacoes", imported_at)
    _persist_indicadores(session, ind_rows)
    _persist_breakdown(session, ocup_rows, "POR_OCUPACAO", "OcupacaoProfissional", extra_props=["nome"])
    total += len(ind_rows)

    for sheet, subtipo, rel_type, node_label in _CNES_MENSAIS:
        recs = _read_wide_sheet(adicionais_path, sheet, mensal=True)
        ind_rows, dim_rows = _wide_to_rows(recs, "cnes", subtipo, imported_at)
        _persist_indicadores(session, ind_rows)
        _persist_breakdown(session, dim_rows, rel_type, node_label, extra_props=["nome"])
        total += len(ind_rows)

    # "Estabelecimentos" (sub-cubos adicionais) — 4 métricas independentes,
    # sem quebra dimensional (não são categorias de uma mesma grandeza).
    recs = _read_wide_sheet(adicionais_path, "Estabelecimentos", mensal=True)
    rows = []
    for rec in recs:
        for col_nome, subtipo in _CNES_ESTABELECIMENTOS_METRICAS.items():
            valor = rec["categorias"].get(col_nome)
            if valor is not None:
                rows.append(_indicador_row("cnes", subtipo, rec["ano"], None, valor, imported_at))
    _persist_indicadores(session, rows)
    total += len(rows)

    return total


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

_LOADERS = [
    ("sim", _load_sim), ("sih", _load_sih), ("sinan", _load_sinan),
    ("sipni", _load_sipni), ("sinasc", _load_sinasc), ("sia", _load_sia),
    ("covid", _load_covid), ("cnes", _load_cnes),
]


def load(neo4j_client) -> dict:
    counts: dict[str, int] = {}

    with neo4j_client._driver.session() as session:
        _ensure_constraints(session)

    if not DATASUS_DIR.exists():
        logger.warning("Pasta não encontrada: %s", DATASUS_DIR)
        return counts

    imported_at = datetime.now(timezone.utc).isoformat()
    for nome, fn in _LOADERS:
        try:
            with neo4j_client._driver.session() as session:
                n = fn(session, imported_at)
            counts[nome] = n
            logger.info("%s → %d registros IndicadorSaude", nome, n)
        except Exception:
            logger.exception("Falha ao carregar sistema %s", nome)

    logger.info("Indicadores de saude ETL concluido: %s", counts)
    return counts


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from db.neo4j_client import Neo4jClient

    with Neo4jClient(
        uri=os.environ["NEO4J_URI"],
        user=os.environ["NEO4J_USER"],
        password=os.environ["NEO4J_PASSWORD"],
    ) as client:
        result = load(client)
        print(f"Importação concluída: {result}")
