"""Métricas y desgloses sobre los trades de un backtest.

Separado del motor a propósito: el motor decide qué pasa y esto solo
resume, así que las mismas métricas valen para el experimento, para el
baseline de control y para cualquier variante que venga después.

La métrica principal es **R**, el resultado en múltiplos del riesgo
inicial de cada operación (la distancia de la entrada a su stop). Es
independiente del tamaño de posición y del capital, así que compara
operaciones entre sí sin que el sizing contamine la lectura.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def resumen(trades: pd.DataFrame, capital_inicial: float) -> dict[str, float]:
    """Calcula las métricas globales de un conjunto de operaciones.

    Parameters
    ----------
    trades : pd.DataFrame
        Salida de :func:`execution.backtest.simular`.
    capital_inicial : float
        Capital con el que arrancó la simulación, para la curva de
        capital y el drawdown.

    Returns
    -------
    dict[str, float]
        Número de operaciones, tasa de acierto, resultado total, media y
        mediana en R, esperanza, factor de beneficio, máximo drawdown y
        excursiones medias.
    """
    if trades.empty:
        return {
            "operaciones": 0.0,
            "acierto": np.nan,
            "retorno_total_pct": 0.0,
            "r_medio": np.nan,
            "r_mediano": np.nan,
            "r_total": 0.0,
            "profit_factor": np.nan,
            "max_drawdown_pct": 0.0,
            "mae_medio_pct": np.nan,
            "mfe_medio_pct": np.nan,
            "duracion_mediana_h": np.nan,
        }

    pnl = trades["pnl_pct"].to_numpy(dtype="float64")
    r = trades["pnl_r"].to_numpy(dtype="float64")
    ganancias = pnl[pnl > 0].sum()
    perdidas = -pnl[pnl < 0].sum()

    capital = np.concatenate(
        [[capital_inicial], trades["capital_despues"].to_numpy(dtype="float64")]
    )
    maximo = np.maximum.accumulate(capital)
    drawdown = (capital - maximo) / maximo

    return {
        "operaciones": float(len(trades)),
        "acierto": float((pnl > 0).mean()),
        "retorno_total_pct": float(capital[-1] / capital_inicial - 1.0),
        "r_medio": float(np.nanmean(r)),
        "r_mediano": float(np.nanmedian(r)),
        "r_total": float(np.nansum(r)),
        "profit_factor": float(ganancias / perdidas) if perdidas > 0 else np.inf,
        "max_drawdown_pct": float(drawdown.min()),
        "mae_medio_pct": float(trades["mae_pct"].mean()),
        "mfe_medio_pct": float(trades["mfe_pct"].mean()),
        "duracion_mediana_h": float(trades["velas_15m"].median() * 0.25),
    }


def desglose(trades: pd.DataFrame, por: str) -> pd.DataFrame:
    """Agrupa las operaciones por una columna y resume cada grupo.

    Es lo que responde a la pregunta operativa: qué nivel, qué lado y
    qué escala aportan y cuáles restan.

    Parameters
    ----------
    trades : pd.DataFrame
        Salida de :func:`execution.backtest.simular`.
    por : str
        Columna por la que agrupar (``"nivel"``, ``"direccion"``,
        ``"motivo_salida"``, ``"rango_id"``...).

    Returns
    -------
    pd.DataFrame
        Una fila por grupo con operaciones, acierto, R medio y R total,
        ordenada por R total descendente.
    """
    if trades.empty:
        return pd.DataFrame(columns=["operaciones", "acierto", "r_medio", "r_total"])

    if por not in trades.columns:
        logger.error("No existe la columna de desglose: %s", por)
        raise KeyError(f"No existe la columna de desglose: {por}")

    agrupado = trades.groupby(por, dropna=False).apply(
        lambda g: pd.Series(
            {
                "operaciones": float(len(g)),
                "acierto": float((g["pnl_pct"] > 0).mean()),
                "r_medio": float(g["pnl_r"].mean()),
                "r_total": float(g["pnl_r"].sum()),
            }
        ),
        include_groups=False,
    )
    return agrupado.sort_values("r_total", ascending=False)


def formatear_resumen(nombre: str, metricas: dict[str, float]) -> str:
    """Compone el bloque de texto de un resumen para el informe.

    Parameters
    ----------
    nombre : str
        Título del bloque.
    metricas : dict[str, float]
        Salida de :func:`resumen`.

    Returns
    -------
    str
        Texto listo para imprimir.
    """
    return "\n".join(
        [
            f"  {nombre}",
            f"    operaciones      {metricas['operaciones']:.0f}",
            f"    acierto          {metricas['acierto']:.1%}",
            f"    retorno total    {metricas['retorno_total_pct']:+.1%}",
            f"    R medio          {metricas['r_medio']:+.3f}",
            f"    R total          {metricas['r_total']:+.1f}",
            f"    profit factor    {metricas['profit_factor']:.2f}",
            f"    max drawdown     {metricas['max_drawdown_pct']:.1%}",
            f"    MAE / MFE medio  {metricas['mae_medio_pct']:+.2%}"
            f" / {metricas['mfe_medio_pct']:+.2%}",
            f"    duracion mediana {metricas['duracion_mediana_h']:.1f} h",
        ]
    )


def formatear_desglose(titulo: str, tabla: pd.DataFrame) -> str:
    """Compone el bloque de texto de un desglose para el informe.

    Parameters
    ----------
    titulo : str
        Encabezado del bloque.
    tabla : pd.DataFrame
        Salida de :func:`desglose`.

    Returns
    -------
    str
        Texto listo para imprimir.
    """
    if tabla.empty:
        return f"  {titulo}\n    (sin operaciones)"

    lineas = [f"  {titulo}", "    grupo             ops   acierto   R medio   R total"]
    for clave, fila in tabla.iterrows():
        lineas.append(
            f"    {str(clave):<16}{fila['operaciones']:>5.0f}"
            f"{fila['acierto']:>10.1%}{fila['r_medio']:>10.3f}"
            f"{fila['r_total']:>10.1f}"
        )
    return "\n".join(lineas)
