"""Osciladores de rango y volatilidad.

Complementa a :mod:`core.momentum` (RSI y MACD, que miden fuerza) con
los indicadores que miden POSICIÓN dentro de un rango y RÉGIMEN de
volatilidad.

Por qué el régimen importa aquí
--------------------------------
La estrategia es de REVERSIÓN: entra contra el movimiento cuando el
precio toca un nivel. Un nivel tocado en tendencia fuerte se rompe;
tocado en régimen lateral se respeta. Si eso es cierto, el ADX —que
mide fuerza de tendencia sin decir hacia dónde— debería separar las
operaciones, y con signo negativo.

Es la hipótesis más falsable de todas las que quedan, y por eso vale la
pena medirla: si el ADX no separa, la idea de que el régimen importa
queda seriamente tocada.

ADX (Wilder)
------------
Mide cuánto se impone una dirección sobre la otra, promediado. Por
encima de 25 se considera tendencia; por debajo de 20, rango. Sus dos
componentes, DI+ y DI−, sí tienen dirección y pueden orientarse a favor
o en contra de la operación.

Squeeze (TTM Squeeze de Carter)
--------------------------------
Cuando las bandas de Bollinger se meten DENTRO de los canales de
Keltner, la volatilidad está comprimida: el mercado acumula antes de
expandirse. Es el «SQZ» del indicador que el autor usa a mano.

El momento del TTM es la regresión lineal del precio respecto a la
media entre la base de Donchian y la media móvil: son las «montañitas»
del histograma.

Estocástico
-----------
Dónde cierra el precio dentro de su rango de las últimas N velas: 0 si
cierra en el mínimo, 100 si cierra en el máximo. Es distinto del RSI, y
la diferencia importa: el RSI compara la magnitud de las subidas con la
de las bajadas, mientras que el estocástico dice literalmente **en qué
parte de su rango reciente está el precio ahora**.

Para calificar el toque de un nivel eso es lo relevante, y se nota:
medido sobre 272 operaciones, un estocástico extremo en la dirección
correcta —por debajo de 20 para un long, por encima de 80 para un
short— rinde +0.157 R frente a -0.082 sin él, y el signo se mantiene en
los dos activos. El RSI extremo, en las mismas operaciones, no separa
(SPEC.md §15).

Nota sobre lookahead bias
--------------------------
Todo son ventanas ``rolling`` hacia atrás sobre velas cerradas. El
suavizado de %K es una media móvil simple, también hacia atrás.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = ["high", "low", "close"]


def estocastico(
    df: pd.DataFrame, periodo: int = 14, suavizado: int = 3
) -> pd.Series:
    """Calcula el %K suavizado del estocástico.

    Vectorizado con ``rolling``, sin recorrer velas.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con ``high``, ``low`` y ``close``, ordenadas
        cronológicamente.
    periodo : int
        Velas del rango sobre el que se mide la posición del cierre.
    suavizado : int
        Velas de la media móvil que suaviza el %K crudo. Con 1 se
        devuelve sin suavizar.

    Returns
    -------
    pd.Series
        Valores entre 0 y 100. ``NaN`` mientras no hay ventana
        completa, y también si el rango del periodo es plano: sin
        recorrido no hay posición relativa que medir.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas requeridas.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas para el estocástico: %s", faltantes)
        raise KeyError(f"Faltan columnas para el estocástico: {faltantes}")

    minimo = df["low"].rolling(periodo).min()
    maximo = df["high"].rolling(periodo).max()

    # Un rango plano no tiene "posición relativa": dividir daría inf.
    recorrido = (maximo - minimo).replace(0.0, np.nan)
    crudo = 100.0 * (df["close"] - minimo) / recorrido

    if suavizado <= 1:
        return crudo.rename("estocastico")
    return crudo.rolling(suavizado).mean().rename("estocastico")


def adx(df: pd.DataFrame, periodo: int = 14) -> pd.DataFrame:
    """Índice direccional medio de Wilder, con sus componentes.

    Mide la FUERZA de la tendencia, no su dirección: un ADX alto dice
    que el precio se está imponiendo en algún sentido, y son DI+ y DI−
    los que dicen en cuál.

    Vectorizado con medias exponenciales, sin recorrer velas.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con ``high``, ``low`` y ``close``.
    periodo : int
        Velas del suavizado de Wilder.

    Returns
    -------
    pd.DataFrame
        Columnas ``adx``, ``di_mas`` y ``di_menos``, en escala 0-100.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas requeridas.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas para el ADX: %s", faltantes)
        raise KeyError(f"Faltan columnas para el ADX: {faltantes}")

    subida = df["high"].diff()
    bajada = -df["low"].diff()

    # Solo cuenta el movimiento direccional que SUPERA al contrario:
    # una vela que se extiende por los dos lados no es direccional.
    dm_mas = pd.Series(
        np.where((subida > bajada) & (subida > 0), subida, 0.0), index=df.index
    )
    dm_menos = pd.Series(
        np.where((bajada > subida) & (bajada > 0), bajada, 0.0), index=df.index
    )

    cierre_previo = df["close"].shift(1)
    rango_verdadero = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - cierre_previo).abs(),
            (df["low"] - cierre_previo).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Suavizado de Wilder: EMA con alfa = 1 / periodo.
    alfa = 1.0 / periodo
    atr_suave = rango_verdadero.ewm(alpha=alfa, adjust=False).mean()
    seguro = atr_suave.replace(0.0, np.nan)

    di_mas = 100.0 * dm_mas.ewm(alpha=alfa, adjust=False).mean() / seguro
    di_menos = 100.0 * dm_menos.ewm(alpha=alfa, adjust=False).mean() / seguro

    suma = (di_mas + di_menos).replace(0.0, np.nan)
    dx = 100.0 * (di_mas - di_menos).abs() / suma

    return pd.DataFrame(
        {
            "adx": dx.ewm(alpha=alfa, adjust=False).mean(),
            "di_mas": di_mas,
            "di_menos": di_menos,
        },
        index=df.index,
    )


def bollinger(
    cierre: pd.Series, periodo: int = 20, desviaciones: float = 2.0
) -> pd.DataFrame:
    """Bandas de Bollinger y las dos medidas que se derivan de ellas.

    Parameters
    ----------
    cierre : pd.Series
        Serie de cierres.
    periodo : int
        Velas de la media y la desviación.
    desviaciones : float
        Anchura de las bandas en desviaciones típicas.

    Returns
    -------
    pd.DataFrame
        ``media``, ``superior``, ``inferior``, ``anchura`` (la distancia
        entre bandas relativa a la media, que mide el régimen de
        volatilidad) y ``pct_b`` (dónde está el precio entre las bandas:
        0 en la inferior, 1 en la superior).
    """
    media = cierre.rolling(periodo).mean()
    desviacion = cierre.rolling(periodo).std()

    superior = media + desviaciones * desviacion
    inferior = media - desviaciones * desviacion
    recorrido = (superior - inferior).replace(0.0, np.nan)

    return pd.DataFrame(
        {
            "media": media,
            "superior": superior,
            "inferior": inferior,
            "anchura": (superior - inferior) / media.replace(0.0, np.nan),
            "pct_b": (cierre - inferior) / recorrido,
        },
        index=cierre.index,
    )


def keltner(
    df: pd.DataFrame, periodo: int = 20, multiplicador: float = 1.5
) -> pd.DataFrame:
    """Canales de Keltner: media exponencial más un múltiplo del ATR.

    A diferencia de Bollinger, que se ensancha con la DISPERSIÓN de los
    cierres, Keltner se ensancha con el RECORRIDO real de las velas. Esa
    diferencia es justo lo que hace útil compararlos.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con ``high``, ``low`` y ``close``.
    periodo : int
        Velas de la media y del ATR.
    multiplicador : float
        Múltiplos de ATR a cada lado.

    Returns
    -------
    pd.DataFrame
        ``media``, ``superior`` e ``inferior``.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas para Keltner: %s", faltantes)
        raise KeyError(f"Faltan columnas para Keltner: {faltantes}")

    media = df["close"].ewm(span=periodo, adjust=False).mean()

    cierre_previo = df["close"].shift(1)
    rango_verdadero = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - cierre_previo).abs(),
            (df["low"] - cierre_previo).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = rango_verdadero.rolling(periodo).mean()

    return pd.DataFrame(
        {
            "media": media,
            "superior": media + multiplicador * atr,
            "inferior": media - multiplicador * atr,
        },
        index=df.index,
    )


def squeeze(
    df: pd.DataFrame,
    periodo: int = 20,
    desviaciones: float = 2.0,
    multiplicador_keltner: float = 1.5,
) -> pd.DataFrame:
    """Detecta la compresión de volatilidad del TTM Squeeze.

    Hay *squeeze* cuando las bandas de Bollinger caben DENTRO de los
    canales de Keltner: la dispersión de los cierres se ha estrechado
    más que el recorrido de las velas, lo que señala acumulación antes
    de una expansión.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con ``high``, ``low`` y ``close``.
    periodo : int
        Velas de ambos indicadores.
    desviaciones : float
        Anchura de las bandas de Bollinger.
    multiplicador_keltner : float
        Anchura de los canales de Keltner.

    Returns
    -------
    pd.DataFrame
        ``activo`` (bool, si hay compresión) y ``velas_en_estado``
        (cuántas velas lleva en el estado actual, comprimido o no).
    """
    bb = bollinger(df["close"], periodo, desviaciones)
    kc = keltner(df, periodo, multiplicador_keltner)

    activo = (bb["superior"] < kc["superior"]) & (bb["inferior"] > kc["inferior"])
    # Sin ventana completa no hay estado que declarar.
    activo = activo.where(bb["superior"].notna() & kc["superior"].notna())

    # Velas que lleva en el estado actual: se cuenta desde el último
    # cambio. `cumsum` sobre los cambios agrupa cada racha, y `cumcount`
    # numera dentro de ella. Solo mira hacia atrás.
    booleano = activo.fillna(False).astype(bool)
    cambios = booleano.ne(booleano.shift(1)).cumsum()
    velas = booleano.groupby(cambios).cumcount() + 1

    return pd.DataFrame(
        {"activo": activo, "velas_en_estado": velas}, index=df.index
    )


def momento_ttm(df: pd.DataFrame, periodo: int = 20) -> pd.Series:
    """Histograma de momento del TTM Squeeze.

    Es la regresión lineal, sobre las últimas ``periodo`` velas, de la
    distancia entre el cierre y una base que promedia el centro del
    canal de Donchian con la media móvil. Son las «montañitas» que se
    ven bajo el precio en TradingView: positivas y crecientes en impulso
    alcista, negativas y decrecientes en el bajista.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con ``high``, ``low`` y ``close``.
    periodo : int
        Velas del canal, de la media y de la regresión.

    Returns
    -------
    pd.Series
        Valor del histograma en cada vela.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas para el momento TTM: %s", faltantes)
        raise KeyError(f"Faltan columnas para el momento TTM: {faltantes}")

    centro_donchian = (
        df["high"].rolling(periodo).max() + df["low"].rolling(periodo).min()
    ) / 2.0
    media = df["close"].rolling(periodo).mean()
    desviacion = df["close"] - (centro_donchian + media) / 2.0

    # Pendiente de la regresión lineal sobre una ventana móvil. Con el
    # eje x fijo (0..periodo-1) la fórmula se reduce a una covarianza
    # entre la serie y ese eje, dividida por su varianza, que es
    # constante. Así se resuelve con `rolling` en vez de ajustar una
    # recta por vela.
    x = np.arange(periodo, dtype="float64")
    x_centrado = x - x.mean()
    denominador = (x_centrado ** 2).sum()

    def pendiente(ventana: np.ndarray) -> float:
        """Pendiente de la recta que mejor ajusta la ventana."""
        return float((x_centrado * (ventana - ventana.mean())).sum() / denominador)

    # `raw=True` pasa arrays de numpy: es el camino rápido de `rolling`.
    return (
        desviacion.rolling(periodo)
        .apply(pendiente, raw=True)
        .rename("momento_ttm")
    )


def fase_ttm(momento: pd.Series) -> pd.Series:
    """Clasifica el histograma del TTM en sus cuatro fases clásicas.

    Es la lectura de colores que se usa en la práctica, y dice algo que
    el nivel del histograma por sí solo no dice: **hacia dónde va** el
    momento, no solo dónde está.

    ==================  ==========  ==========  =========================
    fase                histograma  tendencia   qué significa
    ==================  ==========  ==========  =========================
    ``alcista_fuerte``  positivo    subiendo    impulso alcista acelerando
    ``alcista_debil``   positivo    bajando     impulso alcista agotándose
    ``bajista_fuerte``  negativo    bajando     impulso bajista acelerando
    ``bajista_debil``   negativo    subiendo    impulso bajista agotándose
    ==================  ==========  ==========  =========================

    Para una estrategia de REVERSIÓN las fases interesantes son las
    «débiles»: ``bajista_debil`` es el momento de comprar —el histograma
    sigue en negativo pero ya está girando— y ``alcista_debil`` el de
    vender. Son los «valles rojos» y «valles verdes desarrollados» de la
    lectura manual.

    Parameters
    ----------
    momento : pd.Series
        Salida de :func:`momento_ttm`.

    Returns
    -------
    pd.Series
        Una de las cuatro fases, o ``""`` mientras no haya dato
        suficiente para clasificar.

    Notes
    -----
    Sin lookahead: la tendencia se mide contra la vela ANTERIOR, con
    ``diff()``, que solo mira hacia atrás.
    """
    variacion = momento.diff()
    positivo = momento > 0
    subiendo = variacion > 0

    fase = pd.Series("", index=momento.index, dtype=object)
    fase[positivo & subiendo] = "alcista_fuerte"
    fase[positivo & ~subiendo] = "alcista_debil"
    fase[~positivo & ~subiendo] = "bajista_fuerte"
    fase[~positivo & subiendo] = "bajista_debil"

    # Sin histograma o sin vela previa no hay fase que declarar.
    fase[momento.isna() | variacion.isna()] = ""
    return fase.rename("fase_ttm")


def fase_a_favor(fase: str, direccion: int) -> bool:
    """Dice si la fase del TTM apoya una operación de reversión.

    Un long quiere el impulso bajista agotándose; un short, el alcista.

    Parameters
    ----------
    fase : str
        Salida de :func:`fase_ttm` para esa vela.
    direccion : int
        ``1`` long, ``-1`` short.

    Returns
    -------
    bool
    """
    if direccion > 0:
        return fase == "bajista_debil"
    return fase == "alcista_debil"


def en_extremo(valor: float, direccion: int, bajo: float, alto: float) -> bool:
    """Dice si el oscilador está en zona de agotamiento A FAVOR del trade.

    Un long quiere sobreventa (el precio en la parte baja de su rango) y
    un short sobrecompra. Estar en el extremo contrario no es neutro: es
    una mala señal, pero eso lo decide quien llama.

    Parameters
    ----------
    valor : float
        Valor del oscilador, de 0 a 100.
    direccion : int
        ``1`` long, ``-1`` short.
    bajo, alto : float
        Umbrales de sobreventa y sobrecompra.

    Returns
    -------
    bool
        ``False`` si el valor no existe todavía.
    """
    if not np.isfinite(valor):
        return False
    return bool(valor <= bajo) if direccion > 0 else bool(valor >= alto)
