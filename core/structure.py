"""Estructura de mercado: alcista, bajista o indefinida.

Lo que en análisis técnico se llama *market structure*: la sucesión de
máximos y mínimos de swing que define hacia dónde está trabajando el
precio.

- **Alcista** — cada máximo supera al anterior y cada mínimo también
  (higher high + higher low). El precio construye escalones hacia
  arriba.
- **Bajista** — cada máximo queda por debajo del anterior y cada mínimo
  también (lower high + lower low). Escalones hacia abajo.
- **Indefinida** — máximos y mínimos que no van en el mismo sentido
  (por ejemplo máximo más alto pero mínimo más bajo): el precio se
  está ensanchando o consolidando, y no hay sesgo que respetar.

Además se marca la **ruptura de estructura**: el instante en que el
precio cierra más allá del último swing confirmado en contra. Es la
señal temprana —lo que otros llaman BOS o CHoCH— y llega antes que el
cambio de régimen, porque no espera a que se formen dos pivotes
nuevos.

Para qué sirve aquí
--------------------
Medido en SPEC.md §8, las operaciones contra la dirección de la
estructura son las que hunden el resultado: vender el VAH acierta un
4-6% de las veces en los dos activos. La estructura es el filtro que
permite decir «con el precio construyendo escalones a la baja, no
compro rebotes».

Pivotes, no medias
-------------------
Los swings se calculan sobre CIERRES y con confirmación a ambos lados,
reutilizando :func:`core.range_detector._posiciones_pivotes`. Es la
misma definición de swing que ya usa el Filtro 1: tener dos nociones
distintas de «máximo relevante» en el mismo sistema sería pedir que se
contradigan.

Nota sobre lookahead bias
--------------------------
Un pivote en la vela ``i`` no existe hasta la vela ``i + R``: hacen
falta R velas a su derecha para confirmarlo. En la vela ``t`` solo se
usan pivotes con ``i + R <= t``, igual que en el detector de rangos.
El régimen de la vela ``t`` se calcula, por tanto, con información
estrictamente pasada.

Lo verifica ``test_estructura_no_usa_el_futuro`` en
``tests/test_structure.py``: truncar el histórico no cambia el régimen
de ninguna vela anterior al corte.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.range_detector import _posiciones_pivotes

logger = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = ["high", "low", "close"]

ALCISTA = "alcista"
BAJISTA = "bajista"
INDEFINIDA = "indefinida"


def _ultimos_confirmados(
    posiciones: np.ndarray, n_velas: int, confirmacion: int
) -> tuple[np.ndarray, np.ndarray]:
    """Índices del último y el penúltimo pivote confirmado en cada vela.

    Un pivote de la vela ``i`` se conoce en ``i + confirmacion``, así
    que en la vela ``t`` solo cuentan los que cumplen
    ``i + confirmacion <= t``.

    Parameters
    ----------
    posiciones : np.ndarray
        Posiciones de los pivotes dentro de la serie.
    n_velas : int
        Longitud de la serie.
    confirmacion : int
        Velas necesarias a la derecha para confirmar un pivote (R).

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Para cada vela, la posición del último y del penúltimo pivote
        confirmado, o ``-1`` si no existe.
    """
    if posiciones.size == 0:
        vacio = np.full(n_velas, -1, dtype="int64")
        return vacio, vacio.copy()

    # Vela en la que cada pivote pasa a ser conocido.
    conocidos_en = posiciones + confirmacion
    # `side="right"` incluye el pivote que se confirma en esta misma vela.
    cuantos = np.searchsorted(conocidos_en, np.arange(n_velas), side="right")

    ultimo = np.where(cuantos >= 1, posiciones[np.clip(cuantos - 1, 0, None)], -1)
    penultimo = np.where(cuantos >= 2, posiciones[np.clip(cuantos - 2, 0, None)], -1)
    return ultimo, penultimo


def estructura_mercado(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Clasifica cada vela como estructura alcista, bajista o indefinida.

    Vectorizado: los pivotes se localizan de una vez y la comparación
    entre el último y el penúltimo de cada tipo se resuelve con
    operaciones sobre arrays, sin recorrer velas.

    Parameters
    ----------
    df : pd.DataFrame
        Velas del timeframe de decisión, con ``high``, ``low`` y
        ``close``, e índice ``DatetimeIndex`` ordenado.
    config : dict
        Configuración cargada de ``config.yaml``. Se usa la sección
        ``estructura_mercado``.

    Returns
    -------
    pd.DataFrame
        Indexado como ``df``, con las columnas:

        ``regimen``
            ``"alcista"``, ``"bajista"`` o ``"indefinida"``.
        ``swing_alto`` / ``swing_bajo``
            Último máximo y mínimo de swing confirmados.
        ``ruptura``
            ``1`` si el cierre supera el último swing alto, ``-1`` si
            pierde el último swing bajo, ``0`` si no rompe nada.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas requeridas.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas para la estructura: %s", faltantes)
        raise KeyError(f"Faltan columnas para la estructura: {faltantes}")

    cfg = config["estructura_mercado"]
    confirmacion: int = cfg["barras_confirmacion_pivote"]

    n_velas = len(df)
    cierres = df["close"].to_numpy(dtype="float64")

    # `_posiciones_pivotes` trabaja sobre la SERIE de cierres, no sobre
    # el DataFrame: pasarle el marco entero devuelve índices de una
    # matriz aplanada y el régimen sale mal.
    altos, bajos = _posiciones_pivotes(df["close"], confirmacion)
    ultimo_alto, penultimo_alto = _ultimos_confirmados(altos, n_velas, confirmacion)
    ultimo_bajo, penultimo_bajo = _ultimos_confirmados(bajos, n_velas, confirmacion)

    def precio(indices: np.ndarray) -> np.ndarray:
        """Precio del pivote, o NaN si todavía no hay ninguno."""
        valores = np.where(indices >= 0, cierres[np.clip(indices, 0, None)], np.nan)
        return valores

    alto_1, alto_2 = precio(ultimo_alto), precio(penultimo_alto)
    bajo_1, bajo_2 = precio(ultimo_bajo), precio(penultimo_bajo)

    # Escalones hacia arriba: máximo y mínimo, los dos, superan al
    # anterior. Exigir ambos es lo que distingue una tendencia de un
    # simple tramo ancho.
    sube = (alto_1 > alto_2) & (bajo_1 > bajo_2)
    baja = (alto_1 < alto_2) & (bajo_1 < bajo_2)

    regimen = np.full(n_velas, INDEFINIDA, dtype=object)
    regimen[sube] = ALCISTA
    regimen[baja] = BAJISTA

    # Ruptura de estructura: el cierre deja atrás el último swing
    # confirmado. Llega antes que el cambio de régimen, que necesita
    # dos pivotes nuevos.
    ruptura = np.zeros(n_velas, dtype="int64")
    ruptura[np.nan_to_num(cierres - alto_1, nan=-1.0) > 0] = 1
    ruptura[np.nan_to_num(cierres - bajo_1, nan=1.0) < 0] = -1

    return pd.DataFrame(
        {
            "regimen": regimen,
            "swing_alto": alto_1,
            "swing_bajo": bajo_1,
            "ruptura": ruptura,
        },
        index=df.index,
    )


def estructura_alineada(
    velas_tf: pd.DataFrame, indice_destino: pd.DatetimeIndex, config: dict
) -> pd.Series:
    """Calcula la estructura en un timeframe y la lleva a otro.

    Permite juzgar la estructura en una escala mayor (diaria o semanal)
    y aplicarla a las decisiones de 4h, que es como se lee un gráfico:
    la dirección la marca el timeframe grande.

    Nota sobre lookahead bias
    --------------------------
    El régimen de una vela no se conoce hasta que esa vela CIERRA. El
    índice se desplaza una vela entera antes de propagar hacia adelante
    (``ffill``), de modo que una vela de 4h nunca ve el régimen de la
    vela diaria o semanal que todavía la contiene.

    Parameters
    ----------
    velas_tf : pd.DataFrame
        Velas del timeframe en el que se juzga la estructura.
    indice_destino : pd.DatetimeIndex
        Índice al que se lleva el resultado (el de decisión, 4h).
    config : dict
        Configuración cargada de ``config.yaml``.

    Returns
    -------
    pd.Series
        Régimen para cada vela de ``indice_destino``. Las velas
        anteriores al primer cierre disponible quedan como
        ``"indefinida"``.
    """
    estructura = estructura_mercado(velas_tf, config)

    if len(velas_tf) > 1:
        duracion = velas_tf.index[1] - velas_tf.index[0]
    else:
        duracion = pd.Timedelta(0)

    # El régimen de la vela que abre en t se conoce en t + duración.
    conocido = estructura["regimen"].copy()
    conocido.index = velas_tf.index + duracion

    return conocido.reindex(indice_destino, method="ffill").fillna(INDEFINIDA)


def permite(regimen: str, direccion: int, modo: str) -> bool:
    """Decide si la estructura deja abrir una operación.

    Parameters
    ----------
    regimen : str
        ``"alcista"``, ``"bajista"`` o ``"indefinida"``.
    direccion : int
        ``1`` long, ``-1`` short.
    modo : str
        ``"ninguno"``
            No filtra: se opera en cualquier estructura.
        ``"a_favor"``
            Solo a favor de la estructura. En indefinida no se opera:
            es el más restrictivo.
        ``"no_en_contra"``
            Se prohíbe únicamente lo que va contra una estructura
            clara; en indefinida se opera igual.

    Returns
    -------
    bool
        Si la operación puede abrirse.
    """
    if modo == "ninguno":
        return True

    a_favor = (regimen == ALCISTA and direccion > 0) or (
        regimen == BAJISTA and direccion < 0
    )
    if modo == "a_favor":
        return a_favor

    en_contra = (regimen == ALCISTA and direccion < 0) or (
        regimen == BAJISTA and direccion > 0
    )
    return not en_contra
