"""Perfil de volumen de rango fijo — FRVP (SPEC.md §4).

Construye el histograma de volumen por precio sobre el tramo de un
rango lateral detectado por el Filtro 1, y de él extrae los tres
niveles operativos:

- **POC** (Point of Control): el precio con más volumen negociado.
- **VAH** / **VAL** (Value Area High / Low): los bordes de la zona que
  concentra ``value_area_pct`` del volumen total.

Sobre esos tres niveles se colocan las líneas que se proyectan hacia
la derecha y marcan dónde abrir posición cuando el precio vuelve a
testearlos.

Reparto del volumen de una vela
--------------------------------
El volumen de una vela se reparte UNIFORMEMENTE entre su mínimo y su
máximo, en proporción al solape con cada bin. Es la aproximación
estándar cuando no se dispone del volumen por precio real (que
exigiría datos de tick): dentro de una vela no se sabe a qué precios
se negoció, y repartir uniformemente no introduce sesgo hacia ningún
extremo. La granularidad de las velas usadas (SPEC.md §1) es lo que
acota el error de esta aproximación.

Método de la Value Area
------------------------
Expansión CME desde el POC: se parte del bin del POC y se añade
repetidamente el par de bins (dos por encima o dos por debajo) con más
volumen, hasta cubrir ``value_area_pct`` del total. Es el método que
usa TradingView, así que los niveles coinciden con los del análisis
manual del autor.

Nota sobre lookahead bias
--------------------------
El perfil se calcula sobre un tramo ya cerrado: usa exclusivamente
velas entre ``inicio`` y ``fin``, ambas conocidas. Como el ``fin`` de
un rango no se conoce hasta su ``confirmado_en`` (ver
:mod:`core.range_detector`), el perfil de un rango tampoco existe
antes de ese instante, y el consumidor debe respetarlo: operar sus
niveles antes de ``confirmado_en`` sería lookahead.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = ["high", "low", "volume"]


def _volumen_por_bin(
    maximos: np.ndarray,
    minimos: np.ndarray,
    volumenes: np.ndarray,
    bordes: np.ndarray,
) -> np.ndarray:
    """Reparte el volumen de cada vela entre los bins que abarca.

    Vectorizado: calcula de una vez la matriz de solapes entre cada
    vela y cada bin, sin recorrer velas.

    Parameters
    ----------
    maximos, minimos, volumenes : np.ndarray
        Máximo, mínimo y volumen de cada vela del tramo.
    bordes : np.ndarray
        Bordes de los bins, de longitud ``n_bins + 1``.

    Returns
    -------
    np.ndarray
        Volumen acumulado en cada bin, de longitud ``n_bins``.
    """
    inferior = bordes[:-1][np.newaxis, :]
    superior = bordes[1:][np.newaxis, :]
    bajo = minimos[:, np.newaxis]
    alto = maximos[:, np.newaxis]

    solape = np.clip(
        np.minimum(alto, superior) - np.maximum(bajo, inferior), 0.0, None
    )

    # Una vela sin recorrido (máximo == mínimo) no puede repartirse en
    # proporción: todo su volumen va al bin que la contiene.
    recorrido = (maximos - minimos)[:, np.newaxis]
    sin_recorrido = recorrido <= 0.0
    if sin_recorrido.any():
        indices = np.clip(
            np.searchsorted(bordes, minimos, side="right") - 1, 0, len(bordes) - 2
        )
        solape = np.where(sin_recorrido, 0.0, solape)
        solape[sin_recorrido[:, 0], indices[sin_recorrido[:, 0]]] = 1.0
        recorrido = np.where(sin_recorrido, 1.0, recorrido)

    pesos = solape / recorrido
    return (pesos * volumenes[:, np.newaxis]).sum(axis=0)


def _value_area(
    volumen_bin: np.ndarray, indice_poc: int, value_area_pct: float
) -> tuple[int, int]:
    """Expande la Value Area desde el POC por el método CME.

    Se añade repetidamente el par de bins (dos por encima o dos por
    debajo del tramo ya incluido) que aporte más volumen, hasta cubrir
    la fracción pedida del total.

    Parameters
    ----------
    volumen_bin : np.ndarray
        Volumen de cada bin.
    indice_poc : int
        Índice del bin del POC.
    value_area_pct : float
        Fracción del volumen total que debe cubrir la Value Area.

    Returns
    -------
    tuple[int, int]
        Índices ``(inferior, superior)`` de los bins que delimitan la
        Value Area, ambos incluidos.
    """
    total = volumen_bin.sum()
    objetivo = total * value_area_pct
    n_bins = len(volumen_bin)

    inferior = superior = indice_poc
    acumulado = volumen_bin[indice_poc]

    # Bucle sobre pares de bins, no sobre velas: la expansión CME es
    # secuencial por definición (cada paso depende de dónde llegó el
    # anterior) y como mucho da n_bins/2 vueltas.
    while acumulado < objetivo and (inferior > 0 or superior < n_bins - 1):
        arriba = volumen_bin[superior + 1 : superior + 3].sum()
        abajo = volumen_bin[max(0, inferior - 2) : inferior].sum()

        if superior >= n_bins - 1:
            arriba = -1.0
        if inferior <= 0:
            abajo = -1.0

        if arriba >= abajo:
            superior = min(n_bins - 1, superior + 2)
            acumulado += arriba
        else:
            inferior = max(0, inferior - 2)
            acumulado += abajo

    return inferior, superior


def calcular_frvp(
    df: pd.DataFrame,
    inicio: pd.Timestamp,
    fin: pd.Timestamp,
    config: dict,
) -> dict[str, float | np.ndarray] | None:
    """Calcula el perfil de volumen de un tramo y sus niveles.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con columnas ``high``, ``low`` y ``volume``, con
        ``DatetimeIndex`` ordenado cronológicamente. La granularidad la
        elige el llamante según SPEC.md §1.
    inicio, fin : pd.Timestamp
        Primera y última vela del tramo, ambas incluidas.
    config : dict
        Configuración cargada de ``config.yaml``. Se usa la sección
        ``frvp``.

    Returns
    -------
    dict | None
        ``poc``, ``vah``, ``val`` (precios), ``precios`` y ``volumen``
        (centro y volumen de cada bin, para dibujar el perfil) y
        ``volumen_total``. Devuelve ``None`` si el tramo no tiene velas
        o no tiene volumen.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas requeridas.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas para el FRVP: %s", faltantes)
        raise KeyError(f"Faltan columnas para el FRVP: {faltantes}")

    tramo = df.loc[inicio:fin]
    if tramo.empty:
        logger.warning("Tramo vacío para el FRVP: %s → %s", inicio, fin)
        return None

    maximos = tramo["high"].to_numpy(dtype="float64")
    minimos = tramo["low"].to_numpy(dtype="float64")
    volumenes = tramo["volume"].to_numpy(dtype="float64")

    if volumenes.sum() <= 0:
        logger.warning("Tramo sin volumen para el FRVP: %s → %s", inicio, fin)
        return None

    precio_min = float(minimos.min())
    precio_max = float(maximos.max())
    if precio_max <= precio_min:
        return None

    n_bins: int = config["frvp"]["bins"]
    value_area_pct: float = config["frvp"]["value_area_pct"]

    bordes = np.linspace(precio_min, precio_max, n_bins + 1)
    volumen_bin = _volumen_por_bin(maximos, minimos, volumenes, bordes)
    centros = (bordes[:-1] + bordes[1:]) / 2

    # Desempate del POC: el bin más cercano al centro del rango
    # (SPEC.md §4). `argmin` sobre la distancia al centro se queda con
    # el primero, así que se filtra antes por volumen máximo.
    maximo = volumen_bin.max()
    candidatos = np.flatnonzero(volumen_bin == maximo)
    centro_rango = (precio_min + precio_max) / 2
    indice_poc = int(
        candidatos[np.argmin(np.abs(centros[candidatos] - centro_rango))]
    )

    inferior, superior = _value_area(volumen_bin, indice_poc, value_area_pct)

    return {
        "poc": float(centros[indice_poc]),
        "vah": float(bordes[superior + 1]),
        "val": float(bordes[inferior]),
        "precios": centros,
        "volumen": volumen_bin,
        "volumen_total": float(volumen_bin.sum()),
    }


def timeframe_construccion(n_velas_4h: int, config: dict) -> str:
    """Elige la granularidad con la que construir el perfil.

    Sigue la regla de SPEC.md §1: cuanto más corto el rango, más fina
    la vela, para reducir el error de asignación intra-vela sin
    disparar el coste de cálculo en los rangos largos.

    Parameters
    ----------
    n_velas_4h : int
        Duración del rango en velas de 4h.
    config : dict
        Configuración cargada de ``config.yaml``.

    Returns
    -------
    str
        Timeframe a usar: ``"15m"``, ``"1h"`` o ``"4h"``.
    """
    if n_velas_4h < 60:
        return config["datos"]["timeframe_frvp"]
    if n_velas_4h <= 200:
        return "1h"
    return "4h"
