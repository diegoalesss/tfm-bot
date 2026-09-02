"""Imbalances (Fair Value Gaps) del timeframe semanal.

Un imbalance es una franja de precio que el mercado atravesó tan
deprisa que no llegó a negociarse: el impulso saltó por encima de ella.
Se detecta con el patrón de tres velas:

- **alcista** — el mínimo de la tercera vela queda por ENCIMA del
  máximo de la primera. El hueco es esa franja, y queda por debajo del
  precio: actúa como soporte y como objetivo cuando el precio cae.
- **bajista** — el máximo de la tercera vela queda por DEBAJO del
  mínimo de la primera. El hueco queda por encima del precio: actúa
  como resistencia y como objetivo cuando el precio sube.

En cripto no hay huecos de apertura como en acciones —el mercado no
cierra—, así que el imbalance de tres velas es la única forma real de
hueco y por eso es la que se busca.

Relleno progresivo
-------------------
Un imbalance no es un interruptor. Conforme el precio vuelve a entrar
en la franja, la parte visitada deja de ser hueco y se recorta; lo que
queda sin visitar sigue vivo, aunque sea un 10% del original. Cuando el
precio lo recorre entero, el imbalance muere y deja de dibujarse.

El relleno se mide **con mechas**: si el precio pasó por ahí, esa
franja ya se negoció, aunque la vela cerrara fuera. Es coherente con
definir el hueco por máximos y mínimos.

Nota sobre lookahead bias
--------------------------
Un imbalance no existe hasta que CIERRA la tercera vela de su patrón:
antes de eso no se conoce ni su mínimo ni su máximo. Esa marca viaja en
``confirmado_en`` y es lo que debe respetar el consumidor, igual que el
``confirmado_en`` de los rangos.

El recorte usa exclusivamente velas anteriores o iguales al instante
consultado (``np.minimum.accumulate`` / ``maximum``, que solo miran
hacia atrás), así que la altura viva de un imbalance en la vela ``t``
nunca depende de lo que pase después.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = ["high", "low"]
COLUMNAS_IMBALANCE = [
    "tipo",
    "techo",
    "suelo",
    "formado_en",
    "confirmado_en",
]


def detectar_imbalances(velas: pd.DataFrame) -> pd.DataFrame:
    """Encuentra los imbalances de tres velas de una serie.

    Vectorizado: compara la serie desplazada consigo misma, sin
    recorrer velas.

    Parameters
    ----------
    velas : pd.DataFrame
        Velas del timeframe donde se buscan los huecos (semanal, en la
        operativa actual), con columnas ``high`` y ``low`` e índice
        ``DatetimeIndex`` ordenado.

    Returns
    -------
    pd.DataFrame
        Una fila por imbalance, con las columnas de
        :data:`COLUMNAS_IMBALANCE`. ``formado_en`` es la apertura de la
        tercera vela del patrón y ``confirmado_en`` su cierre, que es
        cuando el hueco pasa a conocerse.

    Raises
    ------
    KeyError
        Si a ``velas`` le faltan columnas requeridas.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(velas.columns)
    if faltantes:
        logger.error("Faltan columnas para detectar imbalances: %s", faltantes)
        raise KeyError(f"Faltan columnas para detectar imbalances: {faltantes}")

    if len(velas) < 3:
        logger.warning("Menos de 3 velas: no puede haber imbalances")
        return pd.DataFrame(columns=COLUMNAS_IMBALANCE)

    maximos = velas["high"].to_numpy(dtype="float64")
    minimos = velas["low"].to_numpy(dtype="float64")

    # Patrón de tres velas: la primera en i-2, la tercera en i.
    primera_max = maximos[:-2]
    primera_min = minimos[:-2]
    tercera_max = maximos[2:]
    tercera_min = minimos[2:]

    alcista = tercera_min > primera_max
    bajista = tercera_max < primera_min

    indices = np.arange(2, len(velas))
    # La vela con timestamp t cubre [t, t + paso): el patrón no se
    # conoce hasta que esa tercera vela cierra.
    paso = velas.index[1] - velas.index[0] if len(velas) > 1 else pd.Timedelta(0)

    filas = []
    for marca, tipo, techo, suelo in (
        (alcista, "alcista", tercera_min, primera_max),
        (bajista, "bajista", primera_min, tercera_max),
    ):
        posiciones = indices[marca]
        for pos, alto, bajo in zip(
            posiciones, techo[marca], suelo[marca]
        ):
            filas.append(
                {
                    "tipo": tipo,
                    "techo": float(alto),
                    "suelo": float(bajo),
                    "formado_en": velas.index[pos],
                    "confirmado_en": velas.index[pos] + paso,
                }
            )

    if not filas:
        logger.info("No se encontró ningún imbalance")
        return pd.DataFrame(columns=COLUMNAS_IMBALANCE)

    return (
        pd.DataFrame(filas, columns=COLUMNAS_IMBALANCE)
        .sort_values("confirmado_en")
        .reset_index(drop=True)
    )


def evolucion_imbalance(
    imbalance: pd.Series, velas: pd.DataFrame
) -> tuple[pd.DatetimeIndex, np.ndarray, pd.Timestamp | None]:
    """Calcula cómo se va comiendo un imbalance vela a vela.

    De un imbalance alcista el precio muerde por arriba (el techo
    baja); de uno bajista, por abajo (el suelo sube). El borde
    resultante es monótono, así que basta un mínimo (o máximo)
    acumulado, que solo mira hacia atrás.

    Parameters
    ----------
    imbalance : pd.Series
        Una fila de :func:`detectar_imbalances`.
    velas : pd.DataFrame
        Velas posteriores con las que se mide el relleno. Pueden ser de
        cualquier granularidad: al usar máximos y mínimos, el resultado
        es el mismo y solo cambia el detalle temporal.

    Returns
    -------
    tuple[pd.DatetimeIndex, np.ndarray, pd.Timestamp | None]
        Instantes evaluados, posición del borde móvil en cada uno, y el
        instante en que el imbalance queda rellenado del todo (``None``
        si sigue vivo al final de la serie).
    """
    posteriores = velas.loc[imbalance["confirmado_en"]:]
    if posteriores.empty:
        return posteriores.index, np.array([]), None

    if imbalance["tipo"] == "alcista":
        # El precio entra desde arriba: el techo del hueco va bajando.
        borde = np.minimum.accumulate(posteriores["low"].to_numpy(dtype="float64"))
        borde = np.clip(borde, imbalance["suelo"], imbalance["techo"])
        agotado = borde <= imbalance["suelo"]
    else:
        # El precio entra desde abajo: el suelo del hueco va subiendo.
        borde = np.maximum.accumulate(posteriores["high"].to_numpy(dtype="float64"))
        borde = np.clip(borde, imbalance["suelo"], imbalance["techo"])
        agotado = borde >= imbalance["techo"]

    muerto_en = (
        posteriores.index[int(np.argmax(agotado))] if agotado.any() else None
    )
    return posteriores.index, borde, muerto_en


def puntos_objetivo(
    imbalances: pd.DataFrame, velas: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray]:
    """Precalcula, vela a vela, los precios que cada imbalance ofrece
    como objetivo.

    De cada imbalance vivo salen DOS objetivos (SPEC.md §9):

    - el **borde de entrada**, por el que el precio lo toca primero;
    - el **50% de lo que queda sin rellenar**, no del hueco original:
      si ya se comió la mitad, el punto medio se recalcula sobre el
      resto.

    Un imbalance fino no necesita tratamiento especial: su 50% queda a
    menos de la separación mínima del borde y lo descarta
    :func:`core.levels.objetivos_desde`, que es donde vive esa regla.

    Se devuelve la matriz completa para no repetir el cálculo en cada
    operación del backtest: son unas decenas de imbalances por unos
    miles de velas, y cabe de sobra en memoria.

    Parameters
    ----------
    imbalances : pd.DataFrame
        Salida de :func:`detectar_imbalances`.
    velas : pd.DataFrame
        Velas del timeframe de decisión sobre las que se evalúa.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Dos matrices ``(n_imbalances, n_velas)``: el borde de entrada y
        el 50% del resto. Las posiciones en las que el imbalance
        todavía no existe, o ya está rellenado del todo, valen ``NaN``.
    """
    n_velas = len(velas)
    if imbalances.empty or n_velas == 0:
        vacio = np.empty((0, n_velas), dtype="float64")
        return vacio, vacio.copy()

    bordes = np.full((len(imbalances), n_velas), np.nan)
    medios = np.full((len(imbalances), n_velas), np.nan)

    # Bucle sobre imbalances (unas decenas): cada iteración resuelve
    # todas las velas de golpe con un acumulado vectorizado.
    for k, (_, fila) in enumerate(imbalances.iterrows()):
        instantes, borde, muerto_en = evolucion_imbalance(fila, velas)
        if borde.size == 0:
            continue

        desde = velas.index.searchsorted(instantes[0], side="left")
        hasta = desde + len(borde)
        if muerto_en is not None:
            # A partir del relleno total el imbalance deja de existir.
            hasta = min(hasta, velas.index.searchsorted(muerto_en, side="left"))
            borde = borde[: hasta - desde]
        if hasta <= desde:
            continue

        fijo = fila["suelo"] if fila["tipo"] == "alcista" else fila["techo"]
        bordes[k, desde:hasta] = borde
        medios[k, desde:hasta] = (borde + fijo) / 2.0

    return bordes, medios


def imbalances_vivos(
    imbalances: pd.DataFrame, velas: pd.DataFrame, instante: pd.Timestamp
) -> pd.DataFrame:
    """Devuelve los imbalances no rellenados del todo en un instante.

    Es la función que consumirá la capa de ejecución: da, para la vela
    ``instante``, qué franjas siguen sin negociarse y con qué altura,
    ya recortada por lo que el precio se haya comido hasta entonces.

    Parameters
    ----------
    imbalances : pd.DataFrame
        Salida de :func:`detectar_imbalances`.
    velas : pd.DataFrame
        Velas con las que se mide el relleno.
    instante : pd.Timestamp
        Momento en el que se consulta. Solo se usan velas hasta aquí.

    Returns
    -------
    pd.DataFrame
        Subconjunto de ``imbalances`` con ``techo`` y ``suelo``
        actualizados a ese instante. Los ya confirmados pero rellenados
        del todo no aparecen; los no confirmados todavía, tampoco.
    """
    if imbalances.empty:
        return imbalances

    hasta = velas.loc[:instante]
    vivos = []
    # Bucle sobre imbalances (unas decenas), no sobre velas: cada
    # iteración hace su propio cálculo vectorizado por dentro.
    for _, fila in imbalances[
        imbalances["confirmado_en"] <= instante
    ].iterrows():
        _, borde, muerto_en = evolucion_imbalance(fila, hasta)
        if borde.size == 0:
            vivos.append(fila)
            continue
        if muerto_en is not None:
            continue

        actualizado = fila.copy()
        if fila["tipo"] == "alcista":
            actualizado["techo"] = float(borde[-1])
        else:
            actualizado["suelo"] = float(borde[-1])
        vivos.append(actualizado)

    if not vivos:
        return imbalances.iloc[0:0]

    return pd.DataFrame(vivos).reset_index(drop=True)
