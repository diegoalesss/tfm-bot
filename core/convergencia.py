"""Score de convergencia: cuántas señales apoyan una operación.

Ninguna de las señales que se han ido midiendo sirve por sí sola para
decidir. Pero SUMADAS informan: el resultado medio crece de forma
monótona con el número de señales alineadas, y lo hace en los dos
activos a la vez, que es el listón que este proyecto exige antes de
creerse nada.

Las cinco señales
------------------
Tres cuentan en su sentido natural, porque estar a favor de ellas
mejora el resultado:

``impulso``
    El precio llega al nivel recorriendo al menos ``impulso_minimo_atr``
    en las últimas velas. Llegar disparado es bueno: el precio llega
    sobreextendido y el rebote es más probable (SPEC.md §11).
``divergencia``
    Divergencia del MACD vigente y alineada con la dirección del trade.
``rotacion``
    La entrada NO va contra la rotación del área de valor, es decir, el
    precio no llega al nivel desde dentro del área (SPEC.md §12).

Y dos cuentan INVERTIDAS, porque medidas restan de forma consistente en
los dos activos:

``contra_estructura``
    La operación va CONTRA la estructura semanal. Medido: ir a favor da
    -0.171 R de media y en contra +0.070. Contraintuitivo hasta que se
    recuerda que esto es una estrategia de reversión: se entra contra el
    movimiento, así que la estructura dominante es lo que hay que
    contradecir, no lo que hay que acompañar.
``poca_confluencia``
    Hay menos de dos zonas apiladas alrededor del nivel. Una zona muy
    disputada rinde peor (-0.236 R frente a +0.016), probablemente
    porque tanta estructura junta significa que el precio ya lleva
    tiempo peleando ahí y el nivel está gastado.

Se descartó el funding rate: medido sobre las mismas operaciones, no
separa, y su signo se invierte entre BTC y ONDO (SPEC.md §13).

Nota sobre lookahead bias
--------------------------
Este módulo no lee velas: recibe los valores ya calculados en la vela de
decisión por los módulos correspondientes, todos ellos con su propia
prueba de truncado. Solo combina y cuenta.
"""

from __future__ import annotations

import logging

import numpy as np

from core.osciladores import en_extremo

logger = logging.getLogger(__name__)

SEÑALES = (
    "impulso",
    "divergencia",
    "rotacion",
    "contra_estructura",
    "poca_confluencia",
)

# El estocástico se evalúa y se registra en cada operación, pero NO
# cuenta para el score salvo que se pida expresamente. Medido, separa
# por sí solo (+0.157 R con la señal frente a -0.082 sin ella) pero es
# REDUNDANTE con el impulso: el 94% de las operaciones que lo activan
# activan también el impulso, frente al 44% de las que no. Sumarlo al
# score no añade información y sí la diluye — la correlación del score
# con el resultado cae de +0.231 a +0.076 (SPEC.md §15).
SEÑALES_OPCIONALES = ("estocastico", "fase_ttm")


def señales_activas(
    impulso: float,
    impulso_minimo: float,
    divergencia: str,
    regimen: str,
    nivel: str,
    confluencia: int,
    direccion: int,
    confluencia_maxima: int = 2,
    estocastico: float = float("nan"),
    estocastico_bajo: float = 20.0,
    estocastico_alto: float = 80.0,
    fase: str = "",
) -> dict[str, bool]:
    """Evalúa las cinco señales para una operación concreta.

    Parameters
    ----------
    impulso : float
        ATR recorridos en las últimas velas antes del toque.
    impulso_minimo : float
        Umbral a partir del cual el impulso cuenta como señal.
    divergencia : str
        Divergencia vigente: ``"alcista"``, ``"bajista"`` o ``""``.
    regimen : str
        Estructura de mercado: ``"alcista"``, ``"bajista"`` o
        ``"indefinida"``.
    nivel : str
        Nivel del FRVP tocado: ``"val"``, ``"poc"`` o ``"vah"``.
    confluencia : int
        Zonas apiladas alrededor del precio de entrada.
    direccion : int
        ``1`` long, ``-1`` short.
    confluencia_maxima : int
        A partir de este número de zonas se considera que hay
        demasiada confluencia y la señal deja de contar.
    estocastico : float
        Valor del estocástico en la vela de decisión.
    estocastico_bajo, estocastico_alto : float
        Umbrales de sobreventa y sobrecompra.

    Returns
    -------
    dict[str, bool]
        Una entrada por señal de :data:`SEÑALES`.
    """
    a_favor_estructura = (regimen == "alcista" and direccion > 0) or (
        regimen == "bajista" and direccion < 0
    )
    desde_dentro_del_area = (nivel == "vah" and direccion < 0) or (
        nivel == "val" and direccion > 0
    )

    return {
        "impulso": bool(np.isfinite(impulso) and impulso >= impulso_minimo),
        "divergencia": (
            (divergencia == "alcista" and direccion > 0)
            or (divergencia == "bajista" and direccion < 0)
        ),
        "rotacion": not desde_dentro_del_area,
        "contra_estructura": not a_favor_estructura,
        "poca_confluencia": confluencia < confluencia_maxima,
        # El precio está en el extremo de su rango reciente, en la
        # dirección que le conviene al trade: sobreventa para un long,
        # sobrecompra para un short.
        "estocastico": en_extremo(
            estocastico, direccion, estocastico_bajo, estocastico_alto
        ),
        # El histograma del TTM ACELERANDO, en cualquier dirección. Es
        # lo contrario de la lectura manual habitual —que busca el
        # histograma agotándose— y lo contrario es lo que mide mejor
        # (SPEC.md §17).
        "fase_ttm": bool(fase) and fase.endswith("fuerte"),
    }


def score(activas: dict[str, bool], opcionales: tuple[str, ...] = ()) -> int:
    """Cuenta cuántas señales apoyan la operación.

    Solo cuentan las de :data:`SEÑALES`. Las de
    :data:`SEÑALES_OPCIONALES` se registran igualmente en cada
    operación, para poder medirlas, pero no suman salvo que se pidan
    aquí expresamente.

    Parameters
    ----------
    activas : dict[str, bool]
        Salida de :func:`señales_activas`.
    opcionales : tuple[str, ...]
        Señales opcionales que sí deben contar en esta llamada.

    Returns
    -------
    int
        Entre 0 y el número de señales contadas.
    """
    cuentan = SEÑALES + tuple(
        nombre for nombre in opcionales if nombre in SEÑALES_OPCIONALES
    )
    return int(sum(bool(activas.get(nombre)) for nombre in cuentan))


def multiplicador_tamano(puntuacion: int, escalones: dict) -> float:
    """Traduce el score en un multiplicador del riesgo por operación.

    Es lo que permite arriesgar más donde los datos están alineados y
    menos donde no, en vez de tratar todas las entradas igual.

    Parameters
    ----------
    puntuacion : int
        Score de convergencia.
    escalones : dict
        Multiplicador por score, con claves enteras o convertibles.
        Los scores no listados usan el más cercano por debajo, y si no
        hay ninguno, ``1.0``.

    Returns
    -------
    float
        Multiplicador a aplicar sobre el riesgo base.
    """
    if not escalones:
        return 1.0

    tabla = {int(clave): float(valor) for clave, valor in escalones.items()}
    candidatos = [clave for clave in tabla if clave <= puntuacion]
    if not candidatos:
        return float(tabla[min(tabla)])
    return float(tabla[max(candidatos)])
