"""Validación de calidad de velas OHLCV.

Comprueba que los datos descargados por :mod:`data.loader` no tengan
huecos, duplicados ni valores imposibles antes de usarse en el resto
de la estrategia, tal y como exige CLAUDE.md.

Nota sobre lookahead bias
--------------------------
Este módulo es puramente descriptivo: audita velas ya cerradas y no
calcula ningún indicador ni señal de trading, por lo que no introduce
fuga temporal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS_OHLC = ["open", "high", "low", "close"]


@dataclass
class InformeCalidad:
    """Informe de calidad de un DataFrame OHLCV.

    Attributes
    ----------
    symbol : str
        Símbolo validado.
    timeframe : str
        Timeframe de las velas validadas.
    n_velas : int
        Número de velas presentes en los datos.
    inicio : pd.Timestamp or None
        Timestamp de la primera vela (rango temporal real).
    fin : pd.Timestamp or None
        Timestamp de la última vela (rango temporal real).
    n_velas_esperadas : int
        Número de velas que debería haber entre ``inicio`` y ``fin``
        según el timeframe, si no hubiera huecos.
    n_huecos : int
        Número de timestamps esperados que no están presentes en los
        datos.
    cobertura_pct : float
        Porcentaje de velas esperadas que efectivamente están
        presentes.
    n_duplicados : int
        Número de timestamps duplicados (filas sobrantes más allá de
        la primera ocurrencia).
    n_valores_imposibles : int
        Número de filas con algún valor imposible (high < low,
        volumen negativo, OHLC nulo).
    timestamps_huecos : pd.DatetimeIndex
        Timestamps esperados y ausentes.
    filas_imposibles : pd.DataFrame
        Filas con valores imposibles, para inspección.
    """

    symbol: str
    timeframe: str
    n_velas: int
    inicio: pd.Timestamp | None
    fin: pd.Timestamp | None
    n_velas_esperadas: int
    n_huecos: int
    cobertura_pct: float
    n_duplicados: int
    n_valores_imposibles: int
    timestamps_huecos: pd.DatetimeIndex = field(repr=False)
    filas_imposibles: pd.DataFrame = field(repr=False)

    def resumen(self) -> str:
        """Genera un resumen legible en texto del informe.

        Returns
        -------
        str
            Texto de varias líneas con las métricas de calidad.
        """
        rango = (
            f"{self.inicio} → {self.fin}"
            if self.inicio is not None and self.fin is not None
            else "sin datos"
        )
        return (
            f"Informe de calidad — {self.symbol} [{self.timeframe}]\n"
            f"  Rango temporal real : {rango}\n"
            f"  Velas presentes     : {self.n_velas}\n"
            f"  Velas esperadas     : {self.n_velas_esperadas}\n"
            f"  Cobertura           : {self.cobertura_pct:.2f}%\n"
            f"  Huecos              : {self.n_huecos}\n"
            f"  Duplicados          : {self.n_duplicados}\n"
            f"  Valores imposibles  : {self.n_valores_imposibles}"
        )


def _detectar_duplicados(df: pd.DataFrame) -> int:
    """Cuenta los timestamps duplicados en el índice.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame OHLCV con ``DatetimeIndex``.

    Returns
    -------
    int
        Número de filas duplicadas (más allá de la primera
        ocurrencia de cada timestamp).
    """
    return int(df.index.duplicated(keep="first").sum())


def _detectar_valores_imposibles(df: pd.DataFrame) -> pd.DataFrame:
    """Detecta filas con valores OHLCV imposibles.

    Se consideran imposibles: ``high < low``, volumen negativo y
    cualquier valor nulo en open, high, low o close.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame OHLCV con columnas ``open, high, low, close, volume``.

    Returns
    -------
    pd.DataFrame
        Subconjunto de ``df`` con las filas problemáticas.
    """
    high_menor_que_low = df["high"] < df["low"]
    volumen_negativo = df["volume"] < 0
    ohlc_nulo = df[COLUMNAS_OHLC].isna().any(axis=1)

    mascara = high_menor_que_low | volumen_negativo | ohlc_nulo
    return df[mascara]


def _detectar_huecos(
    df: pd.DataFrame, timeframe: str
) -> tuple[pd.DatetimeIndex, int]:
    """Detecta timestamps ausentes dentro del rango temporal de ``df``.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame OHLCV con ``DatetimeIndex`` ordenado.
    timeframe : str
        Timeframe de las velas (p. ej. ``"4h"``, ``"15m"``), usado
        para derivar la periodicidad esperada.

    Returns
    -------
    tuple[pd.DatetimeIndex, int]
        Timestamps esperados y ausentes, y el número total de
        timestamps que deberían existir entre el primero y el último.
    """
    if df.empty:
        return pd.DatetimeIndex([], tz="UTC"), 0

    duracion_s = ccxt.Exchange.parse_timeframe(timeframe)
    esperados = pd.date_range(
        start=df.index.min(), end=df.index.max(), freq=pd.Timedelta(seconds=duracion_s)
    )
    huecos = esperados.difference(df.index)
    return huecos, len(esperados)


def validar_ohlcv(df: pd.DataFrame, symbol: str, timeframe: str) -> InformeCalidad:
    """Valida un DataFrame OHLCV y genera su informe de calidad.

    Comprueba huecos, duplicados y valores imposibles (high < low,
    volumen negativo, OHLC nulo).

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame OHLCV con ``DatetimeIndex`` (UTC) y columnas
        ``open, high, low, close, volume``, tal y como lo devuelve
        :func:`data.loader.descargar_ohlcv`.
    symbol : str
        Símbolo al que corresponden los datos (solo para el informe).
    timeframe : str
        Timeframe de las velas (p. ej. ``"4h"``, ``"15m"``).

    Returns
    -------
    InformeCalidad
        Informe con las métricas de calidad de los datos.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas OHLCV requeridas.
    """
    columnas_requeridas = {*COLUMNAS_OHLC, "volume"}
    faltantes = columnas_requeridas - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas OHLCV en los datos: %s", faltantes)
        raise KeyError(f"Faltan columnas OHLCV: {faltantes}")

    n_duplicados = _detectar_duplicados(df)
    filas_imposibles = _detectar_valores_imposibles(df)
    timestamps_huecos, n_velas_esperadas = _detectar_huecos(df, timeframe)

    n_velas = len(df)
    cobertura_pct = (
        100.0 * n_velas / n_velas_esperadas if n_velas_esperadas > 0 else 0.0
    )

    informe = InformeCalidad(
        symbol=symbol,
        timeframe=timeframe,
        n_velas=n_velas,
        inicio=df.index.min() if not df.empty else None,
        fin=df.index.max() if not df.empty else None,
        n_velas_esperadas=n_velas_esperadas,
        n_huecos=len(timestamps_huecos),
        cobertura_pct=cobertura_pct,
        n_duplicados=n_duplicados,
        n_valores_imposibles=len(filas_imposibles),
        timestamps_huecos=timestamps_huecos,
        filas_imposibles=filas_imposibles,
    )

    if informe.n_duplicados or informe.n_valores_imposibles:
        logger.warning(
            "%s [%s]: %d duplicados, %d valores imposibles",
            symbol, timeframe, informe.n_duplicados, informe.n_valores_imposibles,
        )

    return informe
