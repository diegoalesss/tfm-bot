"""Indicadores de momento y divergencias precio-momento.

Aquí viven las medidas que describen CÓMO llega el precio a un nivel,
no dónde está el nivel. Sirven para separar los toques en los que el
precio reacciona de aquellos en los que lo atraviesa sin inmutarse.

Contenido
---------
- **RSI** (Wilder) y **MACD** con su histograma, que es el que dibuja
  los valles y crestas sobre los que se leen las divergencias.
- **Divergencias** entre el precio y cualquier serie de momento: el
  precio marca un mínimo más bajo mientras el momento marca uno más
  alto (divergencia alcista), o al revés.

Qué es una divergencia y por qué importa
-----------------------------------------
Si el precio hace un mínimo más bajo pero el momento hace uno más
alto, la segunda caída se está haciendo con menos fuerza que la
primera: hay menos convicción vendedora de la que sugiere el precio.
No es una señal de compra por sí sola —una divergencia puede
prolongarse mucho—, pero combinada con un nivel que ya tenía sentido
operar es un argumento de peso a favor del rechazo.

Nota sobre lookahead bias
--------------------------
Los tres cálculos usan exclusivamente velas cerradas:

- RSI y MACD son medias móviles exponenciales hacia atrás.
- Los extremos que se comparan en una divergencia son pivotes
  confirmados: un pivote de la vela ``i`` no existe hasta ``i + R``, y
  la divergencia solo se declara en la vela en la que el SEGUNDO
  pivote queda confirmado, nunca en la del propio pivote.

Lo verifica ``test_momentum.py`` truncando el histórico: nada de lo que
se calcula aquí cambia para las velas anteriores al corte.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.range_detector import _posiciones_pivotes

logger = logging.getLogger(__name__)

ALCISTA = "alcista"
BAJISTA = "bajista"


def rsi(cierre: pd.Series, periodo: int = 14) -> pd.Series:
    """Índice de fuerza relativa de Wilder.

    Vectorizado con medias exponenciales, sin recorrer velas.

    Parameters
    ----------
    cierre : pd.Series
        Serie de cierres, ordenada cronológicamente.
    periodo : int
        Número de velas del promedio.

    Returns
    -------
    pd.Series
        RSI entre 0 y 100, alineado con ``cierre``.
    """
    variacion = cierre.diff()
    subidas = variacion.clip(lower=0.0)
    bajadas = (-variacion).clip(lower=0.0)

    # Suavizado de Wilder: equivale a una EMA con alfa = 1 / periodo.
    media_subidas = subidas.ewm(alpha=1.0 / periodo, adjust=False).mean()
    media_bajadas = bajadas.ewm(alpha=1.0 / periodo, adjust=False).mean()

    fuerza = media_subidas / media_bajadas.replace(0.0, np.nan)
    resultado = 100.0 - 100.0 / (1.0 + fuerza)
    # Sin bajadas en toda la ventana, el RSI satura arriba.
    return resultado.fillna(100.0).where(media_bajadas > 0, 100.0)


def macd(
    cierre: pd.Series, rapida: int = 12, lenta: int = 26, señal: int = 9
) -> pd.DataFrame:
    """MACD, su línea de señal y el histograma.

    El histograma es la diferencia entre el MACD y su señal, y es la
    serie sobre la que se leen las divergencias: sus valles y crestas
    son los que se comparan con los del precio.

    Parameters
    ----------
    cierre : pd.Series
        Serie de cierres.
    rapida, lenta, señal : int
        Periodos de las tres medias exponenciales.

    Returns
    -------
    pd.DataFrame
        Columnas ``macd``, ``senal`` e ``histograma``.
    """
    ema_rapida = cierre.ewm(span=rapida, adjust=False).mean()
    ema_lenta = cierre.ewm(span=lenta, adjust=False).mean()
    linea = ema_rapida - ema_lenta
    linea_senal = linea.ewm(span=señal, adjust=False).mean()

    return pd.DataFrame(
        {
            "macd": linea,
            "senal": linea_senal,
            "histograma": linea - linea_senal,
        },
        index=cierre.index,
    )


def divergencias(
    cierre: pd.Series,
    momento: pd.Series,
    barras_confirmacion: int = 3,
    max_separacion: int = 40,
) -> pd.DataFrame:
    """Detecta divergencias entre el precio y una serie de momento.

    Compara pares de pivotes CONSECUTIVOS del precio:

    - **alcista** — el precio marca un mínimo más bajo y el momento uno
      más alto: la caída pierde fuerza.
    - **bajista** — el precio marca un máximo más alto y el momento uno
      más bajo: la subida pierde fuerza.

    La divergencia se declara en la vela en la que el SEGUNDO pivote
    queda confirmado, que es cuando podría operarse, y no en la del
    pivote, que es información que aún no existe.

    Parameters
    ----------
    cierre : pd.Series
        Serie de cierres.
    momento : pd.Series
        Serie de momento (histograma del MACD, RSI...), mismo índice.
    barras_confirmacion : int
        Velas a cada lado para confirmar un pivote (R).
    max_separacion : int
        Velas máximas entre los dos pivotes comparados. Dos extremos
        muy alejados ya no describen el mismo movimiento.

    Returns
    -------
    pd.DataFrame
        Indexado como ``cierre``, con las columnas ``divergencia``
        (``"alcista"``, ``"bajista"`` o ``""``) y ``fuerza`` (cuánto
        difieren precio y momento, normalizado).
    """
    altos, bajos = _posiciones_pivotes(cierre, barras_confirmacion)
    precios = cierre.to_numpy(dtype="float64")
    valores = momento.to_numpy(dtype="float64")

    tipo = np.full(len(cierre), "", dtype=object)
    fuerza = np.zeros(len(cierre), dtype="float64")

    # Bucle sobre pares de pivotes consecutivos (unas decenas), no sobre
    # velas: cada divergencia compara exactamente dos puntos.
    for posiciones, etiqueta, peor in (
        (bajos, ALCISTA, np.less),
        (altos, BAJISTA, np.greater),
    ):
        for anterior, actual in zip(posiciones[:-1], posiciones[1:]):
            if actual - anterior > max_separacion:
                continue
            if not np.isfinite(valores[anterior]) or not np.isfinite(valores[actual]):
                continue

            # El precio va a peor (mínimo más bajo / máximo más alto)
            # mientras el momento va a mejor: eso es la divergencia.
            precio_peor = peor(precios[actual], precios[anterior])
            momento_mejor = (
                valores[actual] > valores[anterior] if etiqueta == ALCISTA
                else valores[actual] < valores[anterior]
            )
            if not (precio_peor and momento_mejor):
                continue

            # Se conoce cuando el segundo pivote queda confirmado.
            declarada = actual + barras_confirmacion
            if declarada >= len(cierre):
                continue

            escala = np.abs(valores[anterior]) + np.abs(valores[actual])
            tipo[declarada] = etiqueta
            fuerza[declarada] = (
                abs(valores[actual] - valores[anterior]) / escala if escala > 0 else 0.0
            )

    return pd.DataFrame(
        {"divergencia": tipo, "fuerza": fuerza}, index=cierre.index
    )


def divergencia_vigente(
    detectadas: pd.DataFrame, velas_vigencia: int
) -> pd.Series:
    """Propaga cada divergencia unas velas hacia adelante.

    Una divergencia no se agota en la vela en que se declara: describe
    un estado del mercado que dura un tiempo. Esto la mantiene vigente
    durante ``velas_vigencia`` velas para poder cruzarla con el toque
    de un nivel, que rara vez cae en la misma vela.

    Parameters
    ----------
    detectadas : pd.DataFrame
        Salida de :func:`divergencias`.
    velas_vigencia : int
        Cuántas velas sigue contando una divergencia declarada.

    Returns
    -------
    pd.Series
        Tipo de divergencia vigente en cada vela, o ``""``.
    """
    tipo = detectadas["divergencia"].to_numpy(dtype=object)
    vigente = np.full(len(tipo), "", dtype=object)

    # Recorrido hacia adelante: cada declaración pisa a la anterior y
    # caduca sola. Solo mira hacia atrás, así que es causal.
    ultima, quedan = "", 0
    for i, valor in enumerate(tipo):
        if valor:
            ultima, quedan = valor, velas_vigencia
        vigente[i] = ultima if quedan > 0 else ""
        quedan = max(0, quedan - 1)

    return pd.Series(vigente, index=detectadas.index, name="divergencia_vigente")
