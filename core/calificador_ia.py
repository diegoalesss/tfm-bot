"""Calificador aprendido: convierte el contexto en un tamaño de posición.

QUÉ HACE
--------
Un modelo de aprendizaje automático estima la probabilidad de que una
operación termine en ganancia, y esa probabilidad decide **cuánto se
arriesga**, no si se opera.

POR QUÉ COMO TAMAÑO Y NO COMO FILTRO
------------------------------------
Se probaron las dos formas (SPEC.md §20 y §21). Como FILTRO el modelo
pierde contra las tres reglas del sistema en las cuatro validaciones:
con unas pocas decenas de operaciones de entrenamiento no hay muestra
para decidir un sí o un no.

Como TAMAÑO el planteamiento cambia por completo, y es lo que lo hace
viable: si el modelo se equivoca, la operación se dimensiona mal, pero
NO se cancela una buena ni se abre una mala. El coste de un error deja
de ser el resultado entero de la operación y pasa a ser una fracción de
él. Es la forma prudente de usar un modelo con poca muestra, y es el
mismo criterio por el que la puntuación de convergencia se usa ya como
multiplicador y no como filtro.

SIN LOOKAHEAD
-------------
Dos garantías, y las dos son imprescindibles:

1. Las variables salen de la vela de 4h **ya cerrada** cuando se coloca
   la orden, la misma que usa `simular` para decidir el lado.
2. El modelo se entrena **solo con operaciones anteriores** a aquellas
   sobre las que se aplica (`aplicar_walk_forward`). Nunca se evalúa un
   modelo sobre datos que ha visto.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline

SEMILLA = 7

#: Variables que ve el modelo, todas calculadas con velas ya cerradas.
COLUMNAS: tuple[str, ...] = (
    "impulso", "adx", "di_orientado", "squeeze", "velas_squeeze",
    "estocastico_orientado", "momento_ttm_orientado", "fase_favorable",
    "bb_ancho_pct", "atr_relativo", "divergencia_favor",
    "calidad", "confluencia", "r_potencial", "riesgo_pct",
    "es_long", "es_poc", "es_vah", "es_val",
)


def entrenar(muestra: pd.DataFrame, columnas: tuple[str, ...] = COLUMNAS) -> Pipeline:
    """Entrena el clasificador de probabilidad de ganancia.

    Parameters
    ----------
    muestra : pd.DataFrame
        Operaciones de entrenamiento, con las columnas de `columnas` y
        la etiqueta binaria ``gana``.
    columnas : tuple of str, optional
        Variables que se le pasan al modelo.

    Returns
    -------
    Pipeline
        Modelo ajustado, listo para `probabilidades`.

    Notes
    -----
    El modelo es deliberadamente pequeño —profundidad 3, hojas de 25
    ejemplos como mínimo, regularización L2— porque la muestra es de
    unas pocas decenas de operaciones. Con esa cantidad de datos, un
    modelo grande memoriza en vez de aprender.
    """
    modelo = HistGradientBoostingClassifier(
        max_depth=3,
        max_iter=120,
        learning_rate=0.05,
        min_samples_leaf=25,
        l2_regularization=1.0,
        random_state=SEMILLA,
    )
    modelo.fit(muestra[list(columnas)].to_numpy(float), muestra["gana"].to_numpy())
    return modelo


def probabilidades(
    modelo: Pipeline, muestra: pd.DataFrame, columnas: tuple[str, ...] = COLUMNAS
) -> np.ndarray:
    """Probabilidad estimada de ganancia de cada operación."""
    return modelo.predict_proba(muestra[list(columnas)].to_numpy(float))[:, 1]


def multiplicador(
    probabilidad: np.ndarray,
    referencia: float,
    minimo: float = 0.5,
    maximo: float = 2.0,
    sensibilidad: float = 12.0,
) -> np.ndarray:
    """Traduce probabilidad en multiplicador de tamaño.

    Parameters
    ----------
    probabilidad : np.ndarray
        Probabilidad estimada de ganancia, entre 0 y 1.
    referencia : float
        Probabilidad que corresponde a tamaño normal (multiplicador 1).
        Se toma la media del ENTRENAMIENTO, nunca la de la prueba: es
        una constante conocida antes de operar.
    minimo, maximo : float, optional
        Suelo y techo del multiplicador.
    sensibilidad : float, optional
        Cuánto se amplifica la desviación respecto a la referencia.

    Returns
    -------
    np.ndarray
        Multiplicador por operación, acotado entre `minimo` y `maximo`.

    Notes
    -----
    La respuesta es **continua y acotada**, no una decisión binaria. Una
    operación que el modelo ve un poco mejor que la media sube un poco
    de tamaño, no el doble. Los topes impiden que un fallo del modelo
    concentre el riesgo en una sola operación, que es el modo de fallo
    que hay que evitar por encima de cualquier otro.
    """
    escala = 1.0 + sensibilidad * (probabilidad - referencia)
    return np.clip(escala, minimo, maximo)


def aplicar_walk_forward(
    muestra: pd.DataFrame,
    minimo_entreno: int = 60,
    paso: int = 10,
    columnas: tuple[str, ...] = COLUMNAS,
) -> pd.Series:
    """Multiplicador de cada operación, entrenando solo con su pasado.

    Recorre las operaciones en orden temporal. Cada bloque de `paso`
    operaciones se califica con un modelo entrenado únicamente con las
    anteriores, y el modelo se vuelve a ajustar al avanzar. Es la única
    forma de medir un modelo sobre una serie temporal sin engañarse.

    Parameters
    ----------
    muestra : pd.DataFrame
        Operaciones ORDENADAS POR FECHA, con variables y etiqueta.
    minimo_entreno : int, optional
        Operaciones mínimas antes de empezar a calificar. Por debajo de
        ese número el multiplicador es 1, es decir, tamaño normal.
    paso : int, optional
        Cada cuántas operaciones se reentrena.
    columnas : tuple of str, optional
        Variables que ve el modelo.

    Returns
    -------
    pd.Series
        Multiplicador por operación, alineado con `muestra`.
    """
    mult = np.ones(len(muestra))
    for inicio in range(minimo_entreno, len(muestra), paso):
        entreno = muestra.iloc[:inicio]
        # Con una sola clase presente no hay nada que aprender todavía.
        if entreno["gana"].nunique() < 2:
            continue
        bloque = muestra.iloc[inicio : inicio + paso]
        modelo = entrenar(entreno, columnas)
        referencia = float(probabilidades(modelo, entreno, columnas).mean())
        mult[inicio : inicio + paso] = multiplicador(
            probabilidades(modelo, bloque, columnas), referencia
        )
    return pd.Series(mult, index=muestra.index, name="multiplicador_ia")
