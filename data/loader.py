"""Descarga de velas OHLCV desde Kraken Futures vía CCXT.

Descarga el histórico configurado (por defecto 2 años) para el
timeframe de decisión (4h) y el de construcción del FRVP (15m),
paginando hacia atrás en el tiempo, y lo guarda en parquet en
``data/raw/``. Si el fichero ya existe no se vuelve a descargar,
salvo que se fuerce explícitamente.

Nota sobre lookahead bias
--------------------------
Este módulo solo descarga y persiste velas ya cerradas: la última
vela devuelta por el exchange puede estar todavía en curso (su
timestamp de cierre es posterior al instante actual), por lo que se
descarta antes de guardar. No se calcula ningún indicador ni señal
aquí, así que no hay más superficie de fuga temporal que esa.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd
import truststore
import yaml

# Algunos entornos (p. ej. con Avast u otro antivirus interceptando
# TLS) instalan un certificado raíz propio que no está en el bundle
# de certifi. truststore delega la verificación en el almacén de
# certificados del sistema operativo, que sí lo conoce.
truststore.inject_into_ssl()

logger = logging.getLogger(__name__)

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
RUTA_CONFIG = RAIZ_PROYECTO / "config.yaml"
DIRECTORIO_RAW = Path(__file__).resolve().parent / "raw"

# Granularidades con las que se puede construir el FRVP según la
# duración del rango (SPEC.md §1). Deben coincidir con las que devuelve
# `core.frvp.timeframe_construccion`.
TIMEFRAMES_FRVP = ("15m", "1h", "4h")

MAX_VELAS_POR_PETICION = 2000
COLUMNAS_OHLCV = ["open", "high", "low", "close", "volume"]
MAX_REINTENTOS = 3
ESPERA_REINTENTO_S = 2.0


def cargar_config(ruta: Path = RUTA_CONFIG) -> dict:
    """Carga la configuración del proyecto desde ``config.yaml``.

    Parameters
    ----------
    ruta : Path
        Ruta al fichero de configuración YAML.

    Returns
    -------
    dict
        Diccionario con la configuración cargada.

    Raises
    ------
    FileNotFoundError
        Si el fichero de configuración no existe.
    yaml.YAMLError
        Si el fichero no es un YAML válido.
    """
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("No se encontró el fichero de configuración: %s", ruta)
        raise
    except yaml.YAMLError:
        logger.error("El fichero de configuración no es un YAML válido: %s", ruta)
        raise


def _crear_exchange() -> ccxt.krakenfutures:
    """Crea el cliente CCXT de Kraken Futures para datos públicos.

    Activa el limitador de tasa integrado de CCXT (``enableRateLimit``)
    para respetar los límites de la API pública del exchange.

    Returns
    -------
    ccxt.krakenfutures
        Instancia del exchange lista para consultar OHLCV.
    """
    return ccxt.krakenfutures({"enableRateLimit": True})


def _ruta_parquet(symbol: str, timeframe: str) -> Path:
    """Calcula la ruta del parquet para un símbolo y timeframe.

    Sustituye los caracteres ``/`` y ``:`` del símbolo unificado de
    CCXT (p. ej. ``"ONDO/USD:USD"``), no válidos en nombres de
    fichero de Windows, por guiones.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    timeframe : str
        Timeframe de las velas (p. ej. ``"4h"``, ``"15m"``).

    Returns
    -------
    Path
        Ruta al fichero parquet dentro de ``data/raw/``.
    """
    nombre_seguro = symbol.replace("/", "-").replace(":", "-")
    return DIRECTORIO_RAW / f"{nombre_seguro}_{timeframe}.parquet"


def _fetch_ohlcv_con_reintentos(
    exchange: ccxt.krakenfutures,
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """Llama a ``fetch_ohlcv`` con reintentos ante errores de red.

    Los errores de red (caídas de conexión, timeouts) son
    transitorios y se reintentan con espera creciente. Los errores
    del exchange (símbolo inválido, parámetros incorrectos) no son
    transitorios y se propagan de inmediato.

    Parameters
    ----------
    exchange : ccxt.krakenfutures
        Cliente CCXT ya inicializado.
    symbol : str
        Símbolo unificado de CCXT.
    timeframe : str
        Timeframe de las velas.
    since_ms : int
        Timestamp en milisegundos desde el que pedir velas.
    limit : int
        Número máximo de velas a solicitar.

    Returns
    -------
    list[list[float]]
        Lista de velas ``[timestamp, open, high, low, close, volume]``.

    Raises
    ------
    ccxt.ExchangeError
        Si el exchange rechaza la petición (no se reintenta).
    ccxt.NetworkError
        Si tras agotar los reintentos sigue fallando la conexión.
    """
    ultimo_error: Exception | None = None
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            return exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=since_ms, limit=limit
            )
        except ccxt.NetworkError as exc:
            ultimo_error = exc
            logger.warning(
                "Error de red descargando %s %s (intento %d/%d): %s",
                symbol, timeframe, intento, MAX_REINTENTOS, exc,
            )
            if intento < MAX_REINTENTOS:
                time.sleep(ESPERA_REINTENTO_S * intento)
        except ccxt.ExchangeError as exc:
            logger.error(
                "Error del exchange descargando %s %s: %s", symbol, timeframe, exc
            )
            raise

    logger.error(
        "Se agotaron los %d reintentos descargando %s %s",
        MAX_REINTENTOS, symbol, timeframe,
    )
    assert ultimo_error is not None
    raise ultimo_error


def _descargar_ohlcv_paginado(
    exchange: ccxt.krakenfutures,
    symbol: str,
    timeframe: str,
    desde: datetime,
) -> pd.DataFrame:
    """Descarga OHLCV paginando hacia adelante desde ``desde`` hasta
    el instante actual, cubriendo así el histórico solicitado.

    Parameters
    ----------
    exchange : ccxt.krakenfutures
        Cliente CCXT ya inicializado.
    symbol : str
        Símbolo unificado de CCXT (p. ej. ``"BTC/USD:USD"``).
    timeframe : str
        Timeframe de las velas (p. ej. ``"4h"``, ``"15m"``).
    desde : datetime
        Instante (UTC) a partir del cual empezar a descargar.

    Returns
    -------
    pd.DataFrame
        DataFrame con índice ``DatetimeIndex`` (UTC, nombre
        ``"timestamp"``) y columnas ``open, high, low, close, volume``,
        ordenado cronológicamente, sin la vela final si todavía está
        en curso.

    Raises
    ------
    ccxt.BaseError
        Si la descarga falla de forma no recuperable (ver
        ``_fetch_ohlcv_con_reintentos``).
    """
    duracion_s = exchange.parse_timeframe(timeframe)
    duracion_ms = duracion_s * 1000

    since_ms = int(desde.timestamp() * 1000)
    velas: list[list[float]] = []
    ultimo_timestamp_visto: int | None = None

    while True:
        ahora_ms = exchange.milliseconds()
        if since_ms > ahora_ms:
            break

        lote = _fetch_ohlcv_con_reintentos(
            exchange, symbol, timeframe, since_ms, MAX_VELAS_POR_PETICION
        )
        if not lote:
            break

        velas.extend(lote)
        nuevo_ultimo_timestamp = lote[-1][0]

        # Si el exchange no avanza (misma última vela que en la
        # iteración anterior), se corta para evitar un bucle infinito.
        if ultimo_timestamp_visto is not None and (
            nuevo_ultimo_timestamp <= ultimo_timestamp_visto
        ):
            break
        ultimo_timestamp_visto = nuevo_ultimo_timestamp

        since_ms = nuevo_ultimo_timestamp + duracion_ms

        if len(lote) < MAX_VELAS_POR_PETICION:
            break

    if not velas:
        logger.warning("No se obtuvieron velas para %s %s", symbol, timeframe)
        return pd.DataFrame(columns=COLUMNAS_OHLCV).set_index(
            pd.DatetimeIndex([], name="timestamp", tz="UTC")
        )

    df = pd.DataFrame(velas, columns=["timestamp", *COLUMNAS_OHLCV])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    df = df.set_index("timestamp")

    # Excluye velas que todavía no han cerrado (evita fuga temporal:
    # una vela en curso puede cambiar de valor tras guardarla).
    ahora = pd.Timestamp.now(tz="UTC")
    cierre = df.index + pd.Timedelta(seconds=duracion_s)
    df = df[cierre <= ahora]

    return df.astype({col: "float64" for col in COLUMNAS_OHLCV})


def descargar_ohlcv(
    symbol: str,
    timeframe: str,
    historico_anios: float,
    exchange: ccxt.krakenfutures | None = None,
    forzar: bool = False,
) -> pd.DataFrame:
    """Obtiene el histórico OHLCV de un símbolo y timeframe, usando
    caché en parquet cuando está disponible.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT (p. ej. ``"ONDO/USD:USD"``).
    timeframe : str
        Timeframe de las velas (p. ej. ``"4h"``, ``"15m"``).
    historico_anios : float
        Años de histórico a cubrir hacia atrás desde hoy.
    exchange : ccxt.krakenfutures, optional
        Cliente CCXT ya inicializado. Si no se indica, se crea uno.
    forzar : bool, optional
        Si es ``True``, vuelve a descargar aunque exista el parquet.

    Returns
    -------
    pd.DataFrame
        DataFrame OHLCV con índice ``DatetimeIndex`` (UTC).

    Raises
    ------
    ccxt.BaseError
        Si la descarga falla de forma no recuperable.
    OSError
        Si falla la lectura o escritura del fichero parquet.
    """
    DIRECTORIO_RAW.mkdir(parents=True, exist_ok=True)
    ruta = _ruta_parquet(symbol, timeframe)

    if ruta.exists() and not forzar:
        logger.info("Usando caché existente para %s %s: %s", symbol, timeframe, ruta)
        try:
            return pd.read_parquet(ruta)
        except OSError:
            logger.error("No se pudo leer el parquet existente: %s", ruta)
            raise

    if exchange is None:
        exchange = _crear_exchange()

    desde = datetime.now(timezone.utc) - timedelta(days=historico_anios * 365)
    logger.info(
        "Descargando %s %s desde %s...", symbol, timeframe, desde.isoformat()
    )
    df = _descargar_ohlcv_paginado(exchange, symbol, timeframe, desde)
    logger.info("Descargadas %d velas de %s %s", len(df), symbol, timeframe)

    try:
        df.to_parquet(ruta)
    except OSError:
        logger.error("No se pudo guardar el parquet: %s", ruta)
        raise

    return df


def descargar_watchlist(
    config: dict, forzar: bool = False
) -> dict[str, dict[str, pd.DataFrame]]:
    """Descarga (o carga de caché) todos los timeframes necesarios
    para todos los símbolos de la watchlist definida en la
    configuración: el de decisión (4h, sobre el que corre el Filtro 1)
    y los de construcción del FRVP (15m, 1h y 4h, ver
    :data:`TIMEFRAMES_FRVP`).

    Parameters
    ----------
    config : dict
        Configuración cargada de ``config.yaml`` (ver
        :func:`cargar_config`).
    forzar : bool, optional
        Si es ``True``, vuelve a descargar aunque exista caché.

    Returns
    -------
    dict[str, dict[str, pd.DataFrame]]
        Diccionario ``{symbol: {timeframe: DataFrame}}``.

    Raises
    ------
    ccxt.BaseError
        Si alguna descarga falla de forma no recuperable.
    """
    datos_cfg = config["datos"]
    simbolos: list[str] = datos_cfg["simbolos"]
    historico_anios: float = datos_cfg["historico_anios"]

    # La detección corre solo sobre el timeframe de decisión (4h), con
    # varias ventanas. El FRVP, en cambio, usa velas más finas cuanto
    # más corto es el rango (SPEC.md §1), así que hay que traerse
    # también las intermedias.
    # `dict.fromkeys` deduplica preservando el orden: el timeframe de
    # decisión suele estar ya entre los del FRVP.
    timeframes = list(
        dict.fromkeys(
            [
                datos_cfg["timeframe_decision"],
                datos_cfg["timeframe_frvp"],
                *TIMEFRAMES_FRVP,
            ]
        )
    )

    exchange = _crear_exchange()
    resultado: dict[str, dict[str, pd.DataFrame]] = {}

    for symbol in simbolos:
        resultado[symbol] = {}
        for timeframe in timeframes:
            resultado[symbol][timeframe] = descargar_ohlcv(
                symbol, timeframe, historico_anios, exchange=exchange, forzar=forzar
            )

    return resultado
