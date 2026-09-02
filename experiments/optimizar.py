"""Búsqueda de configuración robusta para la estrategia del FRVP.

El problema de barrer parámetros es que siempre sale un ganador, y casi
siempre es ruido. Este script está construido para hacerlo difícil:

**Se puntúa por el PEOR caso, no por la media.** Cada configuración se
evalúa en cuatro subconjuntos —BTC y ONDO, primera y segunda mitad del
histórico— y se queda con el mínimo. Una configuración que gana mucho
en BTC-2026 y se hunde en ONDO-2025 no puntúa: para ganar hay que
aguantar en los cuatro.

Es deliberadamente conservador. Una regla que sobrevive al peor de
cuatro subconjuntos tiene alguna posibilidad de seguir funcionando
fuera de muestra; una elegida por su media, casi ninguna.

**Se exige un mínimo de operaciones** en cada subconjunto: una
configuración con tres trades puede tener un R medio espectacular y no
significar nada.

Uso:

    .venv\\Scripts\\python.exe experiments\\optimizar.py
"""

from __future__ import annotations

import copy
import itertools
import logging
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from core.imbalances import detectar_imbalances  # noqa: E402
from core.levels import construir_niveles  # noqa: E402
from core.range_detector import detectar_rangos_laterales  # noqa: E402
from data.loader import cargar_config, descargar_ohlcv  # noqa: E402
from execution.backtest import simular  # noqa: E402
from execution.metrics import resumen  # noqa: E402

logger = logging.getLogger(__name__)

# Operaciones mínimas en cada subconjunto para que su resultado cuente.
# Por debajo, el R medio es una anécdota.
OPERACIONES_MINIMAS = 15

# Rejilla de configuraciones. Se mantiene deliberadamente corta: cada
# parámetro que se añade multiplica las combinaciones y con ellas la
# probabilidad de que el ganador sea casualidad.
REJILLA: dict[str, list] = {
    "mult_atr_stop": [1.5, 2.0, 2.5],
    "tope_objetivo_en_r": [None, 1.5, 2.5, 4.0],
    "objetivos_en_r": [None],
    "filtro_estructura": ["ninguno"],
    "distancia_maxima_objetivo_pct": [0.05],
    "max_posiciones_simultaneas": [2],
    "tp1_al_menos_como_el_stop": [False],
}

TIMEFRAMES = ("4h", "1h", "15m", "1d", "1w")


def cargar(symbol: str, config: dict) -> dict:
    """Prepara todo lo que necesita una simulación de un símbolo.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    config : dict
        Configuración base.

    Returns
    -------
    dict
        Velas por timeframe, rejilla de niveles e imbalances.
    """
    por_tf = {tf: descargar_ohlcv(symbol, tf, 2) for tf in TIMEFRAMES}
    return {
        "por_tf": por_tf,
        "niveles": construir_niveles(
            detectar_rangos_laterales(por_tf["4h"], config),
            por_tf["4h"], por_tf, config,
        ),
        "imbalances": detectar_imbalances(por_tf["1w"]),
    }


def evaluar(datos: dict, config: dict) -> list[dict]:
    """Corre una configuración y la separa en dos mitades temporales.

    La partición por mitades es el sustituto barato de un walk-forward:
    no elimina el sobreajuste, pero sí delata a la configuración que
    solo funciona en un tramo del histórico.

    Parameters
    ----------
    datos : dict
        Salida de :func:`cargar`.
    config : dict
        Configuración a evaluar.

    Returns
    -------
    list[dict]
        Un resumen por mitad, con su etiqueta.
    """
    v4 = datos["por_tf"]["4h"]
    trades, _ = simular(
        v4, datos["por_tf"]["15m"], datos["niveles"], config,
        datos["imbalances"], datos["por_tf"][
            config["experimento_toques_frvp"].get("timeframe_estructura", "4h")
        ],
    )

    capital = config["experimento_toques_frvp"]["capital_inicial"]
    corte = v4.index[len(v4) // 2]

    salida = []
    for etiqueta, subconjunto in (
        ("1a mitad", trades[trades["ts_entrada"] <= corte]),
        ("2a mitad", trades[trades["ts_entrada"] > corte]),
    ):
        metricas = resumen(subconjunto, capital)
        metricas["mitad"] = etiqueta
        salida.append(metricas)
    return salida


def main() -> None:
    """Barre la rejilla y ordena por el peor de los cuatro subconjuntos."""
    logging.basicConfig(level=logging.ERROR)
    base = cargar_config()
    simbolos = base["datos"]["simbolos"]

    print("Cargando datos...")
    datos = {s: cargar(s, base) for s in simbolos}

    claves = list(REJILLA)
    combinaciones = list(itertools.product(*(REJILLA[c] for c in claves)))
    print(f"{len(combinaciones)} configuraciones x {len(simbolos)} símbolos"
          f" x 2 mitades\n")

    filas = []
    for numero, valores in enumerate(combinaciones, 1):
        config = copy.deepcopy(base)
        config["experimento_toques_frvp"].update(dict(zip(claves, valores)))

        fila = dict(zip(claves, valores))
        peor, total_ops, suficientes = float("inf"), 0, True

        for symbol in simbolos:
            for metricas in evaluar(datos[symbol], config):
                nombre = f"{symbol.split('/')[0]} {metricas['mitad']}"
                fila[nombre] = round(metricas["r_medio"], 3)
                total_ops += metricas["operaciones"]
                if metricas["operaciones"] < OPERACIONES_MINIMAS:
                    suficientes = False
                peor = min(peor, metricas["r_medio"])

        fila["peor"] = round(peor, 3)
        fila["ops"] = int(total_ops)
        fila["valida"] = suficientes
        filas.append(fila)
        print(f"  {numero}/{len(combinaciones)}  peor caso {peor:+.3f}"
              f"  ({int(total_ops)} ops)")

    tabla = pd.DataFrame(filas).sort_values("peor", ascending=False)
    pd.set_option("display.width", 250)

    print(f"\n{'=' * 100}")
    print("ORDENADO POR EL PEOR DE LOS CUATRO SUBCONJUNTOS")
    print("=" * 100)
    print(tabla.to_string(index=False))

    validas = tabla[tabla["valida"]]
    if validas.empty:
        print("\nNinguna configuración llega al mínimo de operaciones"
              f" ({OPERACIONES_MINIMAS} por subconjunto).")
        return

    mejor = validas.iloc[0]
    print(f"\nMEJOR CONFIGURACIÓN ROBUSTA (peor caso {mejor['peor']:+.3f} R):")
    for clave in claves:
        print(f"  {clave}: {mejor[clave]}")

    ruta = RAIZ / "experiments" / "resultados" / "barrido.csv"
    tabla.to_csv(ruta, index=False)
    print(f"\nTabla completa → {ruta}")


if __name__ == "__main__":
    main()
