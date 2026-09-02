"""Variables de contexto de flujo derivadas del funding rate.

El funding mide el desequilibrio de apalancamiento: cuando es positivo
hay exceso de posiciones largas —los largos pagan a los cortos para
sostener el precio del perpetuo— y cuando es negativo, exceso de
cortos.

Por qué importa para esta estrategia
-------------------------------------
La estrategia entra contra el movimiento cuando el precio toca un nivel.
Ese trade sale bien cuando el movimiento que llevó al precio hasta ahí
se ha quedado sin combustible. El funding extremo señala precisamente
eso: mucha gente posicionada en el mismo lado y pagando por estarlo.
Una masa de largos apalancados por encima del precio es lo que alimenta
una caída cuando empiezan a cerrarse, y viceversa.

Encaja con el filtro de impulso ya medido (SPEC.md §11): una llegada
vertical al nivel **con funding extremo a favor** es la firma de una
cascada de liquidaciones que se agota, no de un movimiento con
convicción detrás.

Las tres medidas
----------------
``funding``
    El último pago conocido. Su magnitud no es comparable entre activos:
    un 0.0007 es extremo en BTC y normal en una alt.
``funding_z``
    El mismo dato en desviaciones típicas sobre su propia ventana. Esto
    SÍ es comparable entre activos, y es lo que se usa para decidir.
``funding_acumulado``
    Suma de los últimos pagos. Distingue una presión sostenida durante
    un día de un pico aislado de una sola liquidación.

Nota sobre lookahead bias
--------------------------
Todas las ventanas son ``rolling`` hacia atrás, nunca centradas, y
parten de una serie ya alineada por
:func:`data.funding.alinear_a_velas`, que solo propaga pagos ya
liquidados. La orientación a favor del trade (:func:`funding_a_favor`)
no usa datos, solo el signo y la dirección de la operación.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS = ["funding", "funding_z", "funding_acumulado"]


def variables_funding(
    funding_alineado: pd.Series, config: dict
) -> pd.DataFrame:
    """Calcula las medidas derivadas del funding, vela a vela.

    Vectorizado con ``rolling``, sin recorrer velas.

    Parameters
    ----------
    funding_alineado : pd.Series
        Funding vigente en cada vela de decisión (ver
        :func:`data.funding.alinear_a_velas`).
    config : dict
        Configuración cargada de ``config.yaml``. Se usa la sección
        ``flujo``.

    Returns
    -------
    pd.DataFrame
        Indexado como la entrada, con las columnas de
        :data:`COLUMNAS`. Las primeras velas de cada ventana salen
        ``NaN``, que es lo correcto: todavía no hay contexto suficiente
        para decir si un funding es extremo.
    """
    cfg = config["flujo"]
    ventana: int = cfg["ventana_zscore"]
    velas_acumulado: int = cfg["velas_acumulado"]

    media = funding_alineado.rolling(ventana, min_periods=ventana // 2).mean()
    desviacion = funding_alineado.rolling(
        ventana, min_periods=ventana // 2
    ).std()

    # Una desviación de cero significa funding constante en toda la
    # ventana: no hay nada que declarar extremo, y dividir daría inf.
    z = (funding_alineado - media) / desviacion.replace(0.0, np.nan)

    acumulado = funding_alineado.rolling(
        velas_acumulado, min_periods=velas_acumulado
    ).sum()

    return pd.DataFrame(
        {
            "funding": funding_alineado,
            "funding_z": z,
            "funding_acumulado": acumulado,
        },
        index=funding_alineado.index,
    )


def funding_a_favor(valor: float, direccion: int) -> float:
    """Orienta una medida de funding a favor de una operación.

    Un funding POSITIVO significa exceso de posiciones largas, que es
    combustible para una caída: juega **a favor de un short**. Un
    funding negativo es lo contrario.

    Devolver el valor con signo, en vez de un booleano, permite usarlo
    tanto de filtro (con umbral) como de peso continuo.

    Parameters
    ----------
    valor : float
        Medida de funding (crudo, z-score o acumulado).
    direccion : int
        ``1`` long, ``-1`` short.

    Returns
    -------
    float
        Positivo si el funding favorece la operación, negativo si va en
        contra. ``NaN`` se propaga.
    """
    if not np.isfinite(valor):
        return float("nan")
    # Short (-1) quiere funding positivo; long (+1), funding negativo.
    return float(-valor * direccion)


def serie_a_favor(valores: np.ndarray, direcciones: np.ndarray) -> np.ndarray:
    """Versión vectorizada de :func:`funding_a_favor`.

    Parameters
    ----------
    valores : np.ndarray
        Medidas de funding.
    direcciones : np.ndarray
        ``1`` long, ``-1`` short, misma longitud.

    Returns
    -------
    np.ndarray
        Las medidas orientadas a favor de cada operación.
    """
    return -np.asarray(valores, dtype="float64") * np.asarray(
        direcciones, dtype="float64"
    )
