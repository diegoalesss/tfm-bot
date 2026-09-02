"""Rejilla de niveles operables a partir del FRVP de cada rango.

Convierte la salida del Filtro 1 (rangos laterales) y del Filtro 2
(FRVP) en la estructura que consume la capa de ejecución: una tabla de
niveles de precio, cada uno con el instante a partir del cual puede
operarse.

Un nivel es un precio (VAH, POC o VAL) de un rango concreto. Sobre esos
precios se colocan las órdenes de entrada y de ellos salen los
objetivos escalonados de las salidas.

Nota sobre lookahead bias
--------------------------
Un nivel NO existe antes del ``confirmado_en`` de su rango. El ``fin``
del rango —y por tanto el tramo sobre el que se calcula su perfil— no
se conoce hasta esa vela (ver :mod:`core.range_detector` y
:mod:`core.frvp`), así que ``vigente_desde`` se fija ahí y nunca en
``inicio`` ni en ``declarado_en``.

Los rangos ``en_curso`` no producen niveles: su ruptura no se ha
confirmado, su ``confirmado_en`` es ``NaT`` y su ``fin`` es
provisional. Usarlos sería mirar al futuro.

**De dónde salen los rangos.** Lo elige
``experimento_toques_frvp.seleccion_rangos`` en ``config.yaml``:

``"global"``
    ``seleccionar_rangos`` sobre el histórico completo. Son
    exactamente los rangos que se dibujan en el gráfico, ni uno más ni
    uno menos: se opera lo que se ve. Es la opción elegida.

``"causal"``
    :func:`seleccionar_causalmente`, que rehace la selección en cada
    instante de confirmación con los rangos conocidos hasta entonces.
    No usa información futura, pero conserva rangos que más tarde
    quedaron suprimidos, y eso triplica la rejilla con variantes casi
    duplicadas de la misma zona.

La opción ``"global"`` tiene un sesgo conocido: la supresión de
solapados es voraz, así que al añadir rangos posteriores puede cambiar
cuál sobrevive entre VECINOS solapados, y esa decisión no podía
tomarse cuando se operó el primero. No afecta a zonas separadas en el
tiempo —esas nunca compiten entre sí—, solo a rangos que se solapan.
El experimento mide las dos variantes y publica la diferencia
(SPEC.md §8), de modo que el sesgo queda cuantificado en vez de
oculto.

La función :func:`objetivos_desde` no consulta velas: opera sobre una
rejilla de precios que el llamante ya ha filtrado por vigencia, de modo
que no hay superficie por la que se cuele información futura.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.frvp import calcular_frvp, timeframe_construccion
from core.range_detector import seleccionar_rangos

logger = logging.getLogger(__name__)

NOMBRES_NIVEL = ("val", "poc", "vah")

COLUMNAS_NIVEL = [
    "rango_id",
    "ventana",
    "tipo",
    "nivel",
    "precio",
    "calidad",
    "vigente_desde",
    "inicio",
    "fin",
]


def seleccionar_causalmente(rangos: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Aplica la selección de rangos operables sin usar el futuro.

    ``seleccionar_rangos`` sobre el histórico completo deja que un rango
    posterior suprima a uno anterior con el que se solapa. Aquí la
    selección se repite en cada instante de confirmación, viendo solo
    los rangos ya confirmados en ese momento, y de cada pasada se
    conservan los rangos que se confirman justo entonces.

    Parameters
    ----------
    rangos : pd.DataFrame
        Salida de ``detectar_rangos_laterales`` (rangos crudos).
    config : dict
        Configuración cargada de ``config.yaml``.

    Returns
    -------
    pd.DataFrame
        Subconjunto de ``rangos`` que sobrevivió a la selección de su
        propio instante, ordenado por ``confirmado_en``.
    """
    if rangos.empty:
        return rangos

    # Un rango en curso no tiene ruptura confirmada: su `fin` es
    # provisional y su perfil todavía no existe (SPEC.md §5).
    confirmados = rangos[rangos["confirmado_en"].notna()]
    descartados = len(rangos) - len(confirmados)
    if descartados:
        logger.info(
            "Descartados %d rangos en curso: su ruptura no está confirmada",
            descartados,
        )
    if confirmados.empty:
        return confirmados

    elegidos: list[pd.DataFrame] = []
    # Bucle sobre instantes de confirmación (unas decenas), no sobre
    # velas: cada pasada rehace la selección con la información
    # disponible entonces, que es justo lo que la hace causal.
    for instante in sorted(confirmados["confirmado_en"].unique()):
        disponibles = confirmados[confirmados["confirmado_en"] <= instante]
        seleccion = seleccionar_rangos(disponibles, config)
        if seleccion.empty:
            continue
        elegidos.append(seleccion[seleccion["confirmado_en"] == instante])

    if not elegidos:
        return confirmados.iloc[0:0]

    return (
        pd.concat(elegidos)
        .sort_values(["confirmado_en", "inicio"])
        .reset_index(drop=True)
    )


def construir_niveles(
    rangos: pd.DataFrame,
    velas_decision: pd.DataFrame,
    velas_por_tf: dict[str, pd.DataFrame],
    config: dict,
) -> pd.DataFrame:
    """Calcula el FRVP de cada rango y devuelve sus niveles operables.

    Parameters
    ----------
    rangos : pd.DataFrame
        Rangos CRUDOS, tal como los devuelve
        ``detectar_rangos_laterales``. La selección de operables se hace
        aquí dentro, con el modo que indique
        ``experimento_toques_frvp.seleccion_rangos``.
    velas_decision : pd.DataFrame
        Velas del timeframe de decisión (4h), usadas solo para medir la
        duración del rango y elegir con ella la granularidad del perfil.
    velas_por_tf : dict[str, pd.DataFrame]
        Velas por timeframe (``"15m"``, ``"1h"``, ``"4h"``) con las que
        se construye el perfil.
    config : dict
        Configuración cargada de ``config.yaml``. Se usan las secciones
        ``frvp`` y ``experimento_toques_frvp``.

    Returns
    -------
    pd.DataFrame
        Una fila por nivel, con las columnas de :data:`COLUMNAS_NIVEL`.
        Ordenado por ``vigente_desde`` y ``precio``.

    Raises
    ------
    KeyError
        Si falta algún timeframe requerido en ``velas_por_tf``.
    ValueError
        Si ``seleccion_rangos`` no es ``"global"`` ni ``"causal"``.
    """
    cfg = config["experimento_toques_frvp"]
    tipos_operables: list[str] = cfg["tipos_operables"]
    modo: str = cfg.get("seleccion_rangos", "global")

    if modo not in ("global", "causal"):
        logger.error("Modo de selección desconocido: %s", modo)
        raise ValueError(f"seleccion_rangos debe ser 'global' o 'causal': {modo}")

    if rangos.empty:
        logger.warning("No hay rangos de los que extraer niveles")
        return pd.DataFrame(columns=COLUMNAS_NIVEL)

    if modo == "causal":
        operables = seleccionar_causalmente(rangos, config)
    else:
        # Los mismos rangos que dibuja el gráfico. Un rango en curso no
        # tiene ruptura confirmada, así que su perfil todavía no existe
        # y no puede operarse (SPEC.md §5).
        operables = seleccionar_rangos(rangos, config)
        operables = operables[operables["confirmado_en"].notna()]

    if operables.empty:
        logger.warning("Ningún rango sobrevivió a la selección '%s'", modo)
        return pd.DataFrame(columns=COLUMNAS_NIVEL)

    operables = operables[operables["tipo"].isin(tipos_operables)]

    filas: list[dict] = []
    # Bucle sobre rangos, no sobre velas: son unas decenas y cada
    # iteración lanza un cálculo de FRVP completo, que ya es vectorizado
    # por dentro.
    for rango_id, rango in enumerate(operables.itertuples()):
        n_velas = len(velas_decision.loc[rango.inicio : rango.fin])
        timeframe = timeframe_construccion(n_velas, config)

        if timeframe not in velas_por_tf:
            logger.error(
                "Falta el timeframe %s para construir el perfil del rango %s",
                timeframe, rango.inicio,
            )
            raise KeyError(f"Falta el timeframe {timeframe} en velas_por_tf")

        perfil = calcular_frvp(
            velas_por_tf[timeframe], rango.inicio, rango.fin, config
        )
        if perfil is None:
            logger.warning(
                "Sin perfil para el rango %s → %s: no genera niveles",
                rango.inicio, rango.fin,
            )
            continue

        for nombre in NOMBRES_NIVEL:
            filas.append(
                {
                    "rango_id": rango_id,
                    "ventana": rango.ventana,
                    "tipo": rango.tipo,
                    "nivel": nombre,
                    "precio": float(perfil[nombre]),
                    "calidad": float(rango.calidad),
                    "vigente_desde": rango.confirmado_en,
                    "inicio": rango.inicio,
                    "fin": rango.fin,
                }
            )

    if not filas:
        logger.warning("Ningún rango produjo niveles operables")
        return pd.DataFrame(columns=COLUMNAS_NIVEL)

    niveles = pd.DataFrame(filas, columns=COLUMNAS_NIVEL)
    return niveles.sort_values(["vigente_desde", "precio"]).reset_index(drop=True)


def objetivos_desde(
    precio_entrada: float,
    direccion: int,
    rejilla: np.ndarray,
    distancia_minima_pct: float,
    n_objetivos: int,
    distancia_minima_primero_pct: float | None = None,
) -> list[float]:
    """Elige las siguientes zonas de la rejilla como objetivos.

    La rejilla mezcla todo lo que puede servir de objetivo —niveles del
    FRVP y puntos de los imbalances semanales— y se recorre alejándose
    de la entrada, aceptando una zona solo si está lo bastante separada
    de la última aceptada. Dos zonas casi pegadas no son dos objetivos
    distintos: cobrar un parcial a un 0.1% de la entrada no aporta
    recorrido y sí paga dos comisiones. Es también lo que descarta solo
    el 50% de un imbalance fino, sin necesidad de una regla aparte.

    El primer objetivo puede exigir más distancia que el resto (ver
    ``distancia_minima_primero_pct``): si TP1 queda más cerca que el
    stop, la operación arriesga más de lo que puede ganar en su primer
    tramo, y eso no es un objetivo, es ruido.

    Parameters
    ----------
    precio_entrada : float
        Precio al que se abre la posición.
    direccion : int
        ``1`` para long (los objetivos quedan por encima), ``-1`` para
        short (por debajo).
    rejilla : np.ndarray
        Precios de todas las zonas vigentes, en cualquier orden. Los
        ``NaN`` se ignoran.
    distancia_minima_pct : float
        Separación mínima entre objetivos consecutivos, como fracción
        del precio.
    n_objetivos : int
        Cuántos objetivos devolver como máximo.
    distancia_minima_primero_pct : float, optional
        Separación mínima exigida solo al PRIMER objetivo respecto de
        la entrada. Si no se indica, se usa ``distancia_minima_pct``.

    Returns
    -------
    list[float]
        Objetivos ordenados de más cercano a más lejano. Puede tener
        menos de ``n_objetivos`` elementos, incluso ninguno, si la
        rejilla no ofrece zonas suficientes por delante.
    """
    if rejilla.size == 0 or n_objetivos <= 0:
        return []

    candidatos = rejilla[np.isfinite(rejilla)]
    if candidatos.size == 0:
        return []

    # Ordena alejándose de la entrada: ascendente en un long,
    # descendente en un short.
    candidatos = np.sort(candidatos)
    if direccion > 0:
        candidatos = candidatos[candidatos > precio_entrada]
    else:
        candidatos = candidatos[candidatos < precio_entrada][::-1]

    if distancia_minima_primero_pct is None:
        distancia_minima_primero_pct = distancia_minima_pct

    objetivos: list[float] = []
    referencia = precio_entrada
    # Bucle sobre candidatos: la aceptación es secuencial por
    # definición (cada zona se mide contra la última aceptada, no
    # contra la entrada), y son unas pocas decenas.
    for precio in candidatos:
        minima = (
            distancia_minima_primero_pct if not objetivos else distancia_minima_pct
        )
        if abs(precio - referencia) < abs(referencia) * minima:
            continue
        objetivos.append(float(precio))
        referencia = float(precio)
        if len(objetivos) == n_objetivos:
            break

    return objetivos
