"""
ETL — Ingestão de dados DataSUS para Sorocaba (IBGE 355220).

Baixa dados via PySUS, salva em data/datasus/ como cache local.
Na próxima execução, lê do cache se já existir.

Persistência incremental: cada indicador é salvo no Neo4j imediatamente
após ser processado, evitando perda de dados caso o processo seja
interrompido (ex: OOM kill no Docker).

Uso:
    python -m etl.datasus_loader [year_from] [year_to]

Sem argumentos, auto-detecta anos dos arquivos SIOPS em data/.
"""

import gc
import sys
import uuid
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MUNICIPIO_SOROCABA = "355220"
CACHE_DIR = Path(__file__).parent.parent / "data" / "datasus"
# Cache global pré-baixado pelo script download_pysus.py na raiz do projeto
GLOBAL_CACHE_DIR = Path(__file__).parent.parent.parent / "datasus_cache"

_SINAN_MUN_COLS = ["CODMUNRES", "CO_MUN_RES", "MUNIC_RES", "ID_MUNICIP"]
_SIM_MUN_COLS = ["CODMUNRES", "CO_MUN_RES", "MUNIC_RES", "CODMUNOCOR"]
_SIH_MUN_COLS = ["MUNIC_RES", "CO_MUN_RES", "CODMUNRES", "MUNIC_MOV"]
_PNI_MUN_COLS = ["CO_MUN_RES", "CODMUNRES", "MUNIC_RES", "CO_MUNICIPIO_IBGE", "MUNIC"]

# Persistência Neo4j
_MERGE_QUERY = """
MERGE (i:IndicadorDataSUS {sistema: $sistema, tipo: $tipo, ano: $ano})
SET i.id         = COALESCE(i.id, $id),
    i.valor      = $valor,
    i.fonte      = 'datasus',
    i.importedAt = $importedAt
"""


def _find_mun_col(columns: list, candidates: list[str]) -> Optional[str]:
    cols_upper = {c.upper(): c for c in columns}
    for candidate in candidates:
        if candidate.upper() in cols_upper:
            return cols_upper[candidate.upper()]
    return None


def _filter_sorocaba(df: pd.DataFrame, mun_col: str) -> pd.DataFrame:
    col = df[mun_col].astype(str).str.strip().str.lstrip("0")
    target = MUNICIPIO_SOROCABA.lstrip("0")
    return df[col == target]


def _cache_path(sistema: str, tipo: str, year: int) -> Path:
    return CACHE_DIR / f"{sistema}_{tipo}_{year}.parquet"


def _has_cache(sistema: str, tipo: str, year: int) -> bool:
    return _cache_path(sistema, tipo, year).exists()


def _global_cache_path(sistema: str, tipo: str, year: int) -> Path:
    return GLOBAL_CACHE_DIR / f"{sistema}_{tipo}_{year}.parquet"


def _has_global_cache(sistema: str, tipo: str, year: int) -> bool:
    return _global_cache_path(sistema, tipo, year).exists()


def _save_cache(df: pd.DataFrame, sistema: str, tipo: str, year: int) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(sistema, tipo, year)
    df.to_parquet(path, index=False)
    logger.info("Cache salvo: %s (%d linhas)", path.name, len(df))


def _load_cache(sistema: str, tipo: str, year: int) -> pd.DataFrame:
    """Carrega DataFrame do cache local ou global.

    Prioridade: cache local → cache global.
    """
    path = _cache_path(sistema, tipo, year)
    if not path.exists():
        path = _global_cache_path(sistema, tipo, year)
    df = pd.read_parquet(path)
    logger.info("Cache carregado: %s (%d linhas)", path.name, len(df))
    return df


# ---------------------------------------------------------------------------
# Download + cache por sistema
# ---------------------------------------------------------------------------


def _read_pysus_result(result) -> pd.DataFrame:
    """Lê o resultado do PySUS independente do formato retornado.

    O PySUS pode retornar:
      - Um objeto com .path (ParquetSet)
      - Uma lista de objetos com .path
      - Uma lista de caminhos (strings/Path)
      - Um único caminho (string/Path)
    """
    # Caso 1: objeto com .path (ParquetSet)
    if hasattr(result, "path"):
        return pd.read_parquet(result.path)

    # Caso 2: lista
    if isinstance(result, list):
        dfs = []
        for item in result:
            if hasattr(item, "path"):
                dfs.append(pd.read_parquet(item.path))
            elif isinstance(item, (str, Path)):
                dfs.append(pd.read_parquet(item))
            else:
                # Tentar ler diretamente como parquet
                dfs.append(pd.read_parquet(item))
        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)

    # Caso 3: caminho direto
    if isinstance(result, (str, Path)):
        return pd.read_parquet(result)

    # Caso 4: PySUS 2.x retorna DataFrame diretamente
    if isinstance(result, pd.DataFrame):
        return result

    raise TypeError(f"Formato de resultado PySUS não reconhecido: {type(result)}")


def _download_sinan(disease: str, tipo: str, year: int) -> Optional[pd.DataFrame]:
    """Baixa SINAN via PySUS, filtra Sorocaba e cacheia."""
    if _has_cache("sinan", tipo, year) or _has_global_cache("sinan", tipo, year):
        return _load_cache("sinan", tipo, year)

    try:
        import pysus
        logger.info("SINAN %s: baixando ano %d do FTP...", disease, year)
        result = pysus.sinan(disease, year)

        df = _read_pysus_result(result)

        mun_col = _find_mun_col(list(df.columns), _SINAN_MUN_COLS)
        if mun_col is None:
            logger.warning("SINAN %s %d: coluna municipio nao encontrada", disease, year)
            return None

        sorocaba = _filter_sorocaba(df, mun_col)
        _save_cache(sorocaba, "sinan", tipo, year)
        return sorocaba

    except Exception as exc:
        logger.warning("SINAN %s %d: falha — %s", disease, year, exc)
        return None


def _download_sim(year: int) -> Optional[pd.DataFrame]:
    """Baixa SIM via PySUS, filtra Sorocaba e cacheia."""
    if _has_cache("sim", "mortalidade", year) or _has_global_cache("sim", "mortalidade", year):
        return _load_cache("sim", "mortalidade", year)

    try:
        import pysus
        logger.info("SIM: baixando ano %d do FTP...", year)
        result = pysus.sim("SP", year)

        df = _read_pysus_result(result)

        mun_col = _find_mun_col(list(df.columns), _SIM_MUN_COLS)
        if mun_col is None:
            logger.warning("SIM %d: coluna municipio nao encontrada", year)
            return None

        sorocaba = _filter_sorocaba(df, mun_col)
        _save_cache(sorocaba, "sim", "mortalidade", year)
        return sorocaba

    except Exception as exc:
        logger.warning("SIM %d: falha — %s", year, exc)
        return None


def _download_sih(year: int) -> Optional[pd.DataFrame]:
    """Baixa SIH via PySUS, filtra Sorocaba mês a mês e cacheia.

    Estratégia de baixo consumo de memória: cada mês é baixado, filtrado
    para Sorocaba e descartado antes de baixar o próximo. Apenas os registros
    de Sorocaba (~2-3k por mês) são mantidos em memória.
    """
    if _has_cache("sih", "internacoes", year) or _has_global_cache("sih", "internacoes", year):
        return _load_cache("sih", "internacoes", year)

    try:
        import pysus
        logger.info("SIH: baixando ano %d do FTP (mês a mês, filtro incremental)...", year)

        sorocaba_frames: list[pd.DataFrame] = []
        for m in range(1, 13):
            try:
                r = pysus.sih("SP", year, m)
                df_m = _read_pysus_result(r)

                # Filtra Sorocaba IMEDIATAMENTE — descarta SP inteiro
                mun_col = _find_mun_col(list(df_m.columns), _SIH_MUN_COLS)
                if mun_col is not None:
                    filtered = _filter_sorocaba(df_m, mun_col)
                    if len(filtered) > 0:
                        sorocaba_frames.append(filtered)
                        logger.debug("SIH %d/%02d: %d registros Sorocaba", year, m, len(filtered))

                # Libera o DataFrame bruto de SP (~1M+ linhas) imediatamente
                del df_m
                gc.collect()

            except Exception as exc:
                logger.debug("SIH %d/%02d: falha — %s", year, m, exc)

        if not sorocaba_frames:
            logger.warning("SIH %d: nenhum registro de Sorocaba encontrado", year)
            return None

        sorocaba = pd.concat(sorocaba_frames, ignore_index=True)
        _save_cache(sorocaba, "sih", "internacoes", year)
        return sorocaba

    except Exception as exc:
        logger.warning("SIH %d: falha — %s", year, exc)
        return None


def _download_pni(year: int) -> Optional[pd.DataFrame]:
    """Baixa PNI via PySUS, filtra Sorocaba e cacheia."""
    if _has_cache("si_pni", "vacinacao", year) or _has_global_cache("si_pni", "vacinacao", year):
        return _load_cache("si_pni", "vacinacao", year)

    try:
        import pysus
        logger.info("SI-PNI: baixando ano %d do FTP...", year)
        result = pysus.pni("SP", year)

        df = _read_pysus_result(result)

        mun_col = _find_mun_col(list(df.columns), _PNI_MUN_COLS)
        if mun_col is None:
            logger.warning("SI-PNI %d: coluna municipio nao encontrada", year)
            return None

        sorocaba = _filter_sorocaba(df, mun_col)
        _save_cache(sorocaba, "si_pni", "vacinacao", year)
        return sorocaba

    except Exception as exc:
        logger.warning("SI-PNI %d: falha — %s", year, exc)
        return None


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------


def _compute_valor(df: pd.DataFrame, sistema: str) -> float:
    """Calcula o valor do indicador a partir do DataFrame.

    Para SI-PNI (vacinação), soma a coluna QT_DOSE quando disponível,
    pois cada linha pode representar um agregado (imuno × mês × faixa etária)
    e não uma dose individual. Para os demais sistemas, conta linhas
    (cada linha = 1 notificação/internação/óbito).

    Args:
        df: DataFrame filtrado para Sorocaba.
        sistema: Identificador do sistema (sinan, si_pni, sim, sih).

    Returns:
        Valor numérico do indicador (doses aplicadas ou contagem de registros).
    """
    if sistema == "si_pni" and "QT_DOSE" in df.columns:
        try:
            # QT_DOSE pode vir como string com separadores brasileiros
            doses = (
                df["QT_DOSE"]
                .astype(str)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
                .str.strip()
            )
            total = pd.to_numeric(doses, errors="coerce").sum()
            if total > 0:
                return float(total)
        except Exception:
            pass
    return float(len(df))


def _persist_one(neo4j_client, sistema: str, tipo: str, year: int, valor: float) -> None:
    """Persiste um único indicador no Neo4j imediatamente.

    Estratégia de persistência incremental: cada registro é salvo assim que
    processado, garantindo que dados não sejam perdidos em caso de OOM kill.
    """
    imported_at = datetime.now(timezone.utc).isoformat()
    with neo4j_client._driver.session() as session:
        session.run(
            _MERGE_QUERY,
            id=str(uuid.uuid4()),
            sistema=sistema,
            tipo=tipo,
            ano=year,
            valor=valor,
            importedAt=imported_at,
        )


def load(neo4j_client, year_from: int = 2018, year_to: int = 2022,
         siops_years: Optional[set[int]] = None) -> dict:
    """Baixa (ou lê do cache) dados DataSUS e persiste no Neo4j.

    Se siops_years for fornecido, só baixa dados novos do FTP para esses anos.
    Dados já em cache são carregados independentemente.

    Persistência incremental: cada indicador é salvo no Neo4j imediatamente
    após ser processado, evitando perda de dados caso o processo seja
    interrompido (ex: OOM kill no Docker).
    """
    logger.info("DataSUS ETL: Sorocaba (%s), anos %d-%d.", MUNICIPIO_SOROCABA, year_from, year_to)
    if siops_years:
        logger.info("DataSUS ETL: anos SIOPS disponíveis = %s (só baixa novos para estes)", sorted(siops_years))

    counts: dict[str, int] = {}

    # Definição dos indicadores a processar: (sistema, tipo, download_fn_factory)
    # download_fn_factory recebe (year) e retorna DataFrame ou None
    indicators = [
        ("sinan", "dengue", lambda y: _download_sinan("DENG", "dengue", y)),
        ("sinan", "covid", lambda y: _download_sinan("INFL", "covid", y)),
        ("si_pni", "vacinacao", _download_pni),
        ("sim", "mortalidade", _download_sim),
        ("sih", "internacoes", _download_sih),
    ]

    for year in range(year_from, year_to + 1):
        has_siops = siops_years is None or year in siops_years

        for sistema, tipo, download_fn in indicators:
            has_local = _has_cache(sistema, tipo, year)
            has_global = _has_global_cache(sistema, tipo, year)

            # Pula se não tem SIOPS para este ano E não tem cache
            if not has_siops and not has_local and not has_global:
                continue

            # Carrega do cache ou baixa do FTP
            try:
                if has_local or has_global:
                    df = _load_cache(sistema, tipo, year)
                elif has_siops:
                    df = download_fn(year)
                else:
                    df = None
            except Exception as exc:
                logger.warning("%s/%s/%d: falha ao carregar — %s", sistema, tipo, year, exc)
                df = None

            if df is not None and len(df) > 0:
                valor = _compute_valor(df, sistema)
                _persist_one(neo4j_client, sistema, tipo, year, valor)
                counts[sistema] = counts.get(sistema, 0) + 1
                logger.info("Persistido: %s/%s/%d = %.0f", sistema, tipo, year, valor)

            # Libera memória imediatamente — crítico para evitar OOM em containers
            del df
            gc.collect()

    if not counts:
        logger.warning("DataSUS ETL: nenhum registro obtido.")
        return {"sinan": 0, "si_pni": 0, "sim": 0, "sih": 0}

    logger.info("DataSUS ETL concluido: %s", counts)
    return counts


def load_from_cache(neo4j_client, year_from: int = 2018, year_to: int = 2022) -> dict:
    """Persiste indicadores DataSUS no Neo4j usando APENAS cache local.

    Não importa PySUS, não acessa rede, não faz download FTP.
    Consome memória mínima (~poucos MB por parquet de Sorocaba).
    Seguro para rodar dentro de containers com memória limitada.

    Varre todos os parquets em CACHE_DIR e GLOBAL_CACHE_DIR, filtra pelo
    range de anos solicitado, e persiste no Neo4j os que ainda não existem.

    Args:
        neo4j_client: Cliente Neo4j conectado.
        year_from: Ano inicial do período.
        year_to: Ano final do período.

    Returns:
        Dicionário com contagem por sistema (ex: {"sinan": 3, "si_pni": 2}).
    """
    logger.info(
        "DataSUS cache-only: sincronizando parquets %d-%d com Neo4j.",
        year_from, year_to,
    )

    # Descobre o que já existe no Neo4j para evitar writes desnecessários
    with neo4j_client._driver.session() as session:
        result = session.run(
            "MATCH (i:IndicadorDataSUS) "
            "RETURN i.sistema AS s, i.tipo AS t, i.ano AS a"
        )
        existing = {(rec["s"], rec["t"], rec["a"]) for rec in result}

    counts: dict[str, int] = {}

    # Varre ambos os diretórios de cache
    cache_dirs = [CACHE_DIR, GLOBAL_CACHE_DIR]
    seen: set[tuple[str, str, int]] = set()

    for cache_dir in cache_dirs:
        if not cache_dir.exists():
            continue
        for path in sorted(cache_dir.glob("*.parquet")):
            # Parse nome: sistema_tipo_year.parquet
            parts = path.stem.split("_")
            if len(parts) < 3:
                continue
            try:
                year = int(parts[-1])
            except ValueError:
                continue
            tipo = parts[-2]
            sistema = "_".join(parts[:-2])

            # Filtra por range de anos
            if year < year_from or year > year_to:
                continue

            key = (sistema, tipo, year)
            # Pula se já existe no Neo4j ou já processado nesta execução
            if key in existing or key in seen:
                continue
            seen.add(key)

            try:
                df = pd.read_parquet(path)
                if len(df) > 0:
                    valor = _compute_valor(df, sistema)
                    _persist_one(neo4j_client, sistema, tipo, year, valor)
                    counts[sistema] = counts.get(sistema, 0) + 1
                    logger.info("Sincronizado: %s/%s/%d = %.0f", sistema, tipo, year, valor)
                del df
                gc.collect()
            except Exception as exc:
                logger.warning("Cache %s: falha — %s", path.name, exc)

    if not counts:
        logger.info("DataSUS cache-only: Neo4j já possui todos os indicadores do cache.")
        return {"sinan": 0, "si_pni": 0, "sim": 0, "sih": 0}

    logger.info("DataSUS cache-only concluido: %s", counts)
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Parse args: --download habilita FTP, sem flag usa cache-only
    download_mode = "--download" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--download"]

    from etl.detect_years import detect_siops_years
    siops_years_list = detect_siops_years()
    siops_years_set = set(siops_years_list) if siops_years_list else None

    if len(args) >= 2:
        year_from = int(args[0])
        year_to = int(args[1])
    elif len(args) == 1:
        year_from = int(args[0])
        year_to = year_from
    else:
        if siops_years_list:
            year_from = min(siops_years_list)
            year_to = max(siops_years_list)
            print(f"Anos SIOPS detectados: {siops_years_list} -> buscando {year_from}-{year_to}")
        else:
            year_from = 2018
            year_to = 2022
            print(f"Nenhum SIOPS encontrado, usando padrao {year_from}-{year_to}")

    from db.neo4j_client import Neo4jClient

    with Neo4jClient(
        uri=os.environ["NEO4J_URI"],
        user=os.environ["NEO4J_USER"],
        password=os.environ["NEO4J_PASSWORD"],
    ) as client:
        if download_mode:
            print("Modo: DOWNLOAD (FTP + cache + Neo4j)")
            result = load(client, year_from=year_from, year_to=year_to,
                          siops_years=siops_years_set)
        else:
            print("Modo: CACHE-ONLY (sem download, sem PySUS)")
            result = load_from_cache(client, year_from=year_from, year_to=year_to)

        total = sum(result.values())
        print(f"Concluido: {total} registros. Detalhes: {result}")
