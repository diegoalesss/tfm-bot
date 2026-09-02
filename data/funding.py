"""Descarga del histórico de funding rate de perpetuos.

El funding es lo que pagan unos titulares a otros para mantener el
precio del perpetuo pegado al del contado. Cuando es POSITIVO, los
largos pagan a los cortos: hay exceso de posiciones largas. Cuando es
negativo, al revés. Es la medida más directa —y gratuita— del
desequilibrio de apalancamiento del mercado, que es justo el contexto
que le falta a una estrategia que solo mira estructura de precio.

Por qué Binance y no Kraken
----------------------------
Kraken Futures publica su propio funding, pero su histórico vía CCXT
arranca en agosto de 2025 y no cubre los dos años del backtest. Binance
sí llega: BTC desde septiembre de 2024 y ONDO igual.

**Supuesto que hay que declarar en la memoria**: el precio y la
ejecución son de Kraken y el funding es de Binance. Es la misma clase de
mezcla que ya contempla ``volume_source: "aggregated"`` (SPEC.md §2). Se
sostiene porque el desequilibrio de apalancamiento en perpetuos es una
variable de mercado global, no de un exchange concreto —los arbitrajistas
mantienen los funding alineados entre plataformas—, pero no es gratis:
el número exacto de Binance no es el que se cobra en Kraken.

Frecuencias distintas
----------------------
No todos los activos pagan con la misma cadencia: BTC cada 8 h y ONDO
cada 4 h en Binance. Eso lo resuelve la alineación, que propaga el
último pago CONOCIDO hasta que llega el siguiente.

Nota sobre lookahead bias
--------------------------
Este módulo solo descarga y alinea. La regla temporal está en
:func:`alinear_a_velas`: el funding con marca ``T`` se liquida en ``T``,
así que una vela de 4h solo puede usar los pagos con marca anterior o
igual a su CIERRE, nunca el que se liquidará después. Se propaga hacia
adelante con ``ffill``, que por construcción no mira al futuro.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd
import truststore

# Mismo motivo que en data/loader.py: hay entornos con un certificado
# raíz propio que no está en el bundle de certifi.
truststore.inject_into_ssl()

logger = logging.getLogger(__name__)

DIRECTORIO_RAW = Path(__file__).resolve().parent / "raw"

MAX_REGISTROS_POR_PETICION = 1000
MAX_REINTENTOS = 3
ESPERA_REINTENTO_S = 2.0

COLUMNAS = ["funding"]


def _crear_exchange(id_exchange: str) -> ccxt.Exchange:
    """Crea el cliente CCXT del que se toma el funding.

    Parameters
    ----------
    id_exchange : str
        Identificador de CCXT (p. ej. ``"binanceusdm"``).

    Returns
    -------
    ccxt.Exchange
        Cliente con el limitador de tasa activado.

    Raises
    ------
    AttributeError
        Si CCXT no conoce ese exchange.
    """
    try:
        constructor = getattr(ccxt, id_exchange)
    except AttributeError:
        logger.error("CCXT no conoce el exchange '%s'", id_exchange)
        raise
    return constructor({"enableRateLimit": True})


def _ruta_parquet(symbol: str) -> Path:
    """Ruta del parquet de funding de un símbolo.

    Parameters
    ----------
    symbol : str
        Símbolo unificado del exchange de ejecución.

    Returns
    -------
    Path
        Ruta dentro de ``data/raw/``.
    """
    nombre = symbol.replace("/", "-").replace(":", "-")
    return DIRECTORIO_RAW / f"{nombre}_funding.parquet"


def _fetch_con_reintentos(
    exchange: ccxt.Exchange, symbol: str, desde_ms: int
) -> list[dict]:
    """Pide un lote de funding con reintentos ante errores de red.

    Los errores de red son transitorios y se reintentan con espera
    creciente; los del exchange (símbolo inexistente, parámetros mal) no
    lo son y se propagan de inmediato.

    Parameters
    ----------
    exchange : ccxt.Exchange
        Cliente ya inicializado.
    symbol : str
        Símbolo en la nomenclatura del exchange de funding.
    desde_ms : int
        Marca temporal desde la que pedir, en milisegundos.

    Returns
    -------
    list[dict]
        Registros de funding de CCXT.

    Raises
    ------
    ccxt.ExchangeError
        Si el exchange rechaza la petición.
    ccxt.NetworkError
        Si tras agotar los reintentos sigue fallando la conexión.
    """
    ultimo_error: Exception | None = None
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            return exchange.fetch_funding_rate_history(
                symbol, since=desde_ms, limit=MAX_REGISTROS_POR_PETICION
            )
        except ccxt.NetworkError as exc:
            ultimo_error = exc
            logger.warning(
                "Error de red descargando funding de %s (intento %d/%d): %s",
                symbol, intento, MAX_REINTENTOS, exc,
            )
            if intento < MAX_REINTENTOS:
                time.sleep(ESPERA_REINTENTO_S * intento)
        except ccxt.ExchangeError as exc:
            logger.error("El exchange rechazó el funding de %s: %s", symbol, exc)
            raise

    logger.error("Se agotaron los %d reintentos con %s", MAX_REINTENTOS, symbol)
    assert ultimo_error is not None
    raise ultimo_error


def _validar(df: pd.DataFrame, symbol: str) -> None:
    """Comprueba el histórico antes de guardarlo.

    En un bot de trading es preferible fallar ruidosamente a operar con
    datos corruptos, así que esto levanta excepción en vez de avisar.

    Parameters
    ----------
    df : pd.DataFrame
        Histórico descargado.
    symbol : str
        Símbolo, solo para el mensaje de error.

    Raises
    ------
    ValueError
        Si el índice no es único o creciente, o si hay valores
        imposibles.
    """
    if df.empty:
        raise ValueError(f"Histórico de funding vacío para {symbol}")
    if df.index.has_duplicates:
        raise ValueError(f"Funding con marcas duplicadas en {symbol}")
    if not df.index.is_monotonic_increasing:
        raise ValueError(f"Funding desordenado en {symbol}")
    if df["funding"].isna().any():
        raise ValueError(f"Funding con huecos en {symbol}")
    # Un funding del 10% por periodo sería un mercado roto; sirve para
    # detectar un cambio de unidades en la API (fracción vs porcentaje).
    if df["funding"].abs().max() > 0.1:
        raise ValueError(
            f"Funding fuera de rango en {symbol}: "
            f"máximo {df['funding'].abs().max():.4f}"
        )


def descargar_funding(
    symbol: str, config: dict, forzar: bool = False
) -> pd.DataFrame:
    """Obtiene el histórico de funding de un símbolo, con caché.

    Pagina hacia adelante hasta alcanzar el presente, igual que
    :func:`data.loader.descargar_ohlcv`.

    Parameters
    ----------
    symbol : str
        Símbolo unificado del exchange de EJECUCIÓN (Kraken). Se
        traduce al del exchange de funding con la tabla de
        ``config.yaml``.
    config : dict
        Configuración cargada de ``config.yaml``. Se usa la sección
        ``flujo``.
    forzar : bool, optional
        Si es ``True``, vuelve a descargar aunque exista la caché.

    Returns
    -------
    pd.DataFrame
        Índice ``DatetimeIndex`` UTC con la marca de cada liquidación y
        una columna ``funding``.

    Raises
    ------
    KeyError
        Si el símbolo no está en la tabla de equivalencias.
    ValueError
        Si el histórico descargado no supera la validación.
    OSError
        Si falla la lectura o escritura del parquet.
    """
    cfg = config["flujo"]
    equivalencias: dict[str, str] = cfg["simbolos"]

    if symbol not in equivalencias:
        logger.error(
            "No hay equivalencia de funding para %s. Añádela en "
            "config.yaml -> flujo.simbolos", symbol,
        )
        raise KeyError(f"Sin equivalencia de funding para {symbol}")

    DIRECTORIO_RAW.mkdir(parents=True, exist_ok=True)
    ruta = _ruta_parquet(symbol)

    if ruta.exists() and not forzar:
        logger.info("Usando caché de funding para %s: %s", symbol, ruta)
        try:
            return pd.read_parquet(ruta)
        except OSError:
            logger.error("No se pudo leer el parquet de funding: %s", ruta)
            raise

    exchange = _crear_exchange(cfg["exchange"])
    simbolo_remoto = equivalencias[symbol]
    anios: float = config["datos"]["historico_anios"]
    desde = datetime.now(timezone.utc) - timedelta(days=anios * 365)
    desde_ms = int(desde.timestamp() * 1000)

    logger.info(
        "Descargando funding de %s (%s en %s) desde %s...",
        symbol, simbolo_remoto, cfg["exchange"], desde.date(),
    )

    registros: list[dict] = []
    ultima_marca: int | None = None
    while True:
        lote = _fetch_con_reintentos(exchange, simbolo_remoto, desde_ms)
        if not lote:
            break

        registros.extend(lote)
        marca = lote[-1]["timestamp"]
        # Si el exchange no avanza, se corta para no entrar en bucle.
        if ultima_marca is not None and marca <= ultima_marca:
            break
        ultima_marca = marca
        desde_ms = marca + 1

        if len(lote) < MAX_REGISTROS_POR_PETICION:
            break

    if not registros:
        raise ValueError(f"El exchange no devolvió funding para {symbol}")

    df = pd.DataFrame(
        {
            "timestamp": [r["timestamp"] for r in registros],
            "funding": [float(r["fundingRate"]) for r in registros],
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = (
        df.drop_duplicates(subset="timestamp")
        .sort_values("timestamp")
        .set_index("timestamp")
    )

    _validar(df, symbol)
    logger.info(
        "Funding de %s: %d pagos, de %s a %s",
        symbol, len(df), df.index[0].date(), df.index[-1].date(),
    )

    try:
        df.to_parquet(ruta)
    except OSError:
        logger.error("No se pudo guardar el parquet de funding: %s", ruta)
        raise

    return df


def alinear_a_velas(
    funding: pd.DataFrame, indice: pd.DatetimeIndex
) -> pd.Series:
    """Lleva la serie de funding al índice del timeframe de decisión.

    Nota sobre lookahead bias
    --------------------------
    Un pago con marca ``T`` se liquida EN ``T``. La vela de 4h que abre
    en ``t`` cierra en ``t + 4h``, así que puede usar todos los pagos con
    marca ``<= t + 4h``... pero solo *después* de cerrar. Para que el
    valor asignado a la vela ``t`` sea utilizable AL TOMAR LA DECISIÓN en
    su cierre, se asigna el último pago con marca ``<= t``. Es la opción
    conservadora: como mucho se usa un funding ligeramente más viejo,
    nunca uno que todavía no se ha liquidado.

    Parameters
    ----------
    funding : pd.DataFrame
        Salida de :func:`descargar_funding`.
    indice : pd.DatetimeIndex
        Índice de las velas de decisión.

    Returns
    -------
    pd.Series
        Funding vigente en cada vela. ``NaN`` antes del primer pago
        conocido.
    """
    if funding.empty:
        return pd.Series(float("nan"), index=indice, name="funding")

    serie = funding["funding"]
    # `ffill` propaga el último valor conocido hacia adelante y nunca
    # rellena hacia atrás: no puede traer información futura.
    return serie.reindex(
        serie.index.union(indice)
    ).ffill().reindex(indice).rename("funding")
