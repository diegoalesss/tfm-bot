"""Experimento: ¿tienen valor operativo los niveles del FRVP por sí solos?

Mide la regla más simple que se puede construir sobre el Filtro 2: el
precio llega a un nivel (VAH, POC o VAL) desde arriba → long; desde
abajo → short. Salida escalonada en los tres niveles siguientes, con el
stop a break-even al alcanzar el segundo.

NO es una propuesta de estrategia ni modifica SPEC.md §5: es una
medición previa para decidir, con datos, si merece la pena seguir
apilando capas (líneas de tendencia, mapa de liquidaciones, SQZ, RSI)
sobre estos niveles o si el problema está antes.

Tres cosas se miden a la vez, y las tres importan:

1. **La regla acordada**, con su desglose por nivel, dirección, motivo
   de salida y año. El desglose es lo que dirá dónde está el problema.
2. **Un baseline de control**: el mismo motor con los niveles
   desplazados a precios sin significado. Si la regla no lo bate, lo
   que se está midiendo es la deriva del mercado, no el FRVP.
3. **La variante «todo en TP1»**, para saber si escalonar la salida
   aporta o resta frente a cerrar entero en el primer objetivo.

Uso:

    .venv\\Scripts\\python.exe experiments\\exp_toques_frvp.py
"""

from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.imbalances import detectar_imbalances  # noqa: E402
from core.levels import construir_niveles, seleccionar_causalmente  # noqa: E402
from core.range_detector import (  # noqa: E402
    detectar_rangos_laterales,
    seleccionar_rangos,
)
from data.loader import TIMEFRAMES_FRVP, cargar_config, descargar_ohlcv  # noqa: E402
from execution.backtest import simular  # noqa: E402
from execution.metrics import (  # noqa: E402
    desglose,
    formatear_desglose,
    formatear_resumen,
    resumen,
)

logger = logging.getLogger(__name__)

DIRECTORIO_RESULTADOS = Path(__file__).resolve().parent / "resultados"


def cargar_velas(
    symbol: str, config: dict
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Carga de caché las velas necesarias para un símbolo.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    config : dict
        Configuración cargada de ``config.yaml``.

    Returns
    -------
    tuple[pd.DataFrame, dict[str, pd.DataFrame]]
        Velas del timeframe de decisión y velas por timeframe de
        construcción del perfil.

    Raises
    ------
    OSError
        Si falla la lectura de la caché en parquet.
    """
    anios = config["datos"]["historico_anios"]
    tf_decision = config["datos"]["timeframe_decision"]
    tf_imbalances = config["imbalances"]["timeframe"]

    por_tf = {
        tf: descargar_ohlcv(symbol, tf, anios)
        for tf in dict.fromkeys([tf_decision, *TIMEFRAMES_FRVP, tf_imbalances])
    }
    return por_tf[tf_decision], por_tf


def niveles_desplazados(
    niveles: pd.DataFrame, cfg: dict, rng: np.random.Generator
) -> pd.DataFrame:
    """Devuelve la rejilla con cada nivel movido a un precio sin sentido.

    Conserva el número de niveles, sus fechas de vigencia y el orden de
    magnitud del precio, y destruye lo único que se está poniendo a
    prueba: que el nivel esté DONDE dice el perfil de volumen. Es el
    control que separa «el FRVP aporta» de «el mercado subió».

    Parameters
    ----------
    niveles : pd.DataFrame
        Rejilla real (ver :func:`core.levels.construir_niveles`).
    cfg : dict
        Sección ``experimento_toques_frvp`` de la configuración.
    rng : np.random.Generator
        Generador ya sembrado, para que el control sea reproducible.

    Returns
    -------
    pd.DataFrame
        Copia de la rejilla con la columna ``precio`` desplazada.
    """
    minimo, maximo = cfg["desplazamiento_baseline_pct"]
    magnitud = rng.uniform(minimo, maximo, size=len(niveles))
    signo = rng.choice([-1.0, 1.0], size=len(niveles))

    falsos = niveles.copy()
    falsos["precio"] = niveles["precio"].to_numpy() * (1.0 + signo * magnitud)
    return falsos


def correr_baseline(
    velas_4h: pd.DataFrame,
    velas_15m: pd.DataFrame,
    niveles: pd.DataFrame,
    config: dict,
    imbalances: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Repite la simulación con niveles desplazados y resume cada pasada.

    Parameters
    ----------
    velas_4h, velas_15m : pd.DataFrame
        Velas de decisión y de ejecución.
    niveles : pd.DataFrame
        Rejilla real, que se desplaza en cada repetición.
    config : dict
        Configuración cargada de ``config.yaml``.
    imbalances : pd.DataFrame, optional
        Los mismos imbalances que usa la regla real: el control debe
        diferenciarse solo en los NIVELES, no en las zonas de salida.

    Returns
    -------
    pd.DataFrame
        Una fila por repetición con sus métricas.
    """
    cfg = config["experimento_toques_frvp"]
    rng = np.random.default_rng(cfg["semilla_baseline"])

    filas = []
    for repeticion in range(int(cfg["repeticiones_baseline"])):
        falsos = niveles_desplazados(niveles, cfg, rng)
        trades, _ = simular(velas_4h, velas_15m, falsos, config, imbalances)
        metricas = resumen(trades, cfg["capital_inicial"])
        metricas["repeticion"] = repeticion
        filas.append(metricas)

    return pd.DataFrame(filas)


def informe_simbolo(symbol: str, config: dict) -> pd.DataFrame:
    """Corre el experimento completo sobre un símbolo y lo imprime.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    config : dict
        Configuración cargada de ``config.yaml``.

    Returns
    -------
    pd.DataFrame
        Los trades de la regla acordada, para volcarlos a disco.
    """
    cfg = config["experimento_toques_frvp"]
    capital = cfg["capital_inicial"]

    velas_4h, por_tf = cargar_velas(symbol, config)
    crudos = detectar_rangos_laterales(velas_4h, config)
    # La rejilla se construye desde los rangos CRUDOS: `construir_niveles`
    # aplica por dentro el modo de selección que diga la configuración.
    niveles = construir_niveles(crudos, velas_4h, por_tf, config)

    # Zonas de objetivo que aportan los imbalances semanales (§9). Solo
    # para salir: las entradas siguen saliendo del FRVP.
    imbalances = None
    if cfg.get("usar_imbalances_como_objetivo", False):
        imbalances = detectar_imbalances(por_tf[config["imbalances"]["timeframe"]])

    seleccion = cfg.get("seleccion_rangos", "global")
    elegidos = (
        seleccionar_causalmente(crudos, config) if seleccion == "causal"
        else seleccionar_rangos(crudos, config)
    )
    operables = elegidos[elegidos["tipo"].isin(cfg["tipos_operables"])]
    print(f"\n{'=' * 72}\n{symbol}\n{'=' * 72}")
    print(
        f"  {len(crudos)} rangos detectados, {len(elegidos)} seleccionados"
        f" (modo «{seleccion}»), {len(operables)} operables,"
        f" {len(niveles)} niveles"
        + (f", {len(imbalances)} imbalances" if imbalances is not None else "")
    )
    print(
        f"  histórico {velas_4h.index[0]:%Y-%m-%d} → {velas_4h.index[-1]:%Y-%m-%d}"
        f"  ({len(velas_4h)} velas de 4h, {len(por_tf['15m'])} de 15m)"
    )

    velas_15m = por_tf["15m"]
    trades, _ = simular(velas_4h, velas_15m, niveles, config, imbalances)

    print(f"\n{formatear_resumen('REGLA ACORDADA (escalonada 1/3)', resumen(trades, capital))}")

    if not trades.empty:
        print()
        print(formatear_desglose("por nivel", desglose(trades, "nivel")))
        print()
        print(formatear_desglose("por dirección", desglose(trades, "direccion")))
        print()
        print(
            formatear_desglose(
                "por motivo de salida", desglose(trades, "motivo_salida")
            )
        )
        print()
        por_anio = trades.assign(anio=trades["ts_salida"].dt.year)
        print(formatear_desglose("por año", desglose(por_anio, "anio")))

    # Variante de control: cerrar entero en el primer objetivo.
    config_tp1 = copy.deepcopy(config)
    config_tp1["experimento_toques_frvp"]["reparto_parciales"] = [1.0, 0.0, 0.0]
    trades_tp1, _ = simular(velas_4h, velas_15m, niveles, config_tp1, imbalances)
    print(f"\n{formatear_resumen('VARIANTE: todo en TP1', resumen(trades_tp1, capital))}")

    # Cuantifica el sesgo de selección: la misma regla sobre la otra
    # rejilla. La diferencia entre ambas es lo que aporta (o quita)
    # haber elegido los rangos con el histórico completo.
    otro = "causal" if seleccion == "global" else "global"
    config_otro = copy.deepcopy(config)
    config_otro["experimento_toques_frvp"]["seleccion_rangos"] = otro
    niveles_otro = construir_niveles(crudos, velas_4h, por_tf, config_otro)
    trades_otro, _ = simular(
        velas_4h, velas_15m, niveles_otro, config_otro, imbalances
    )
    print(
        f"\n{formatear_resumen(f'CONTRASTE: seleccion «{otro}» ({len(niveles_otro)} niveles)', resumen(trades_otro, capital))}"
    )

    # Control: los mismos niveles, descolocados.
    base = correr_baseline(velas_4h, velas_15m, niveles, config, imbalances)
    print(
        f"\n  BASELINE (niveles descolocados, {len(base)} repeticiones)\n"
        f"    operaciones      {base['operaciones'].mean():.0f} de media\n"
        f"    acierto          {base['acierto'].mean():.1%}"
        f"  [{base['acierto'].min():.1%} … {base['acierto'].max():.1%}]\n"
        f"    R medio          {base['r_medio'].mean():+.3f}"
        f"  [{base['r_medio'].min():+.3f} … {base['r_medio'].max():+.3f}]\n"
        f"    R total          {base['r_total'].mean():+.1f}"
        f"  [{base['r_total'].min():+.1f} … {base['r_total'].max():+.1f}]"
    )

    if not trades.empty:
        r_real = resumen(trades, capital)["r_total"]
        mejores = int((base["r_total"] >= r_real).sum())
        print(
            f"    veredicto        {mejores} de {len(base)} pasadas con niveles"
            f" descolocados igualan o baten a la regla"
        )

    return trades


def main() -> None:
    """Corre el experimento sobre todos los símbolos configurados."""
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    config = cargar_config()
    DIRECTORIO_RESULTADOS.mkdir(parents=True, exist_ok=True)

    for symbol in config["datos"]["simbolos"]:
        try:
            trades = informe_simbolo(symbol, config)
        except (OSError, KeyError, ValueError):
            logger.exception("Falló el experimento sobre %s", symbol)
            raise

        nombre = symbol.replace("/", "-").replace(":", "-")
        ruta = DIRECTORIO_RESULTADOS / f"trades_{nombre}.csv"
        trades.to_csv(ruta, index=False)
        print(f"\n  trades → {ruta}")

    print(
        "\nRecordatorio: todos los parámetros de `experimento_toques_frvp`"
        "\nson valores de partida SIN MEDIR. Lo que salga de aquí se anota"
        "\nen SPEC.md §8 como medición, con fecha y parámetros."
    )


if __name__ == "__main__":
    main()
