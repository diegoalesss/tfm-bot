"""La IA dimensionando la posición, medida walk-forward (SPEC.md §21).

Aplica `core.calificador_ia` a las operaciones REALES del sistema —las
que pasan los tres filtros— y compara tres formas de decidir el tamaño:

    tamaño constante        todas las operaciones pesan lo mismo
    convergencia (actual)   multiplicador por puntuación de señales
    IA (walk-forward)       multiplicador por probabilidad estimada

El R medio ponderado es la métrica: como `pnl_r` ya viene normalizado
por el riesgo de cada operación, ponderar por el multiplicador da
exactamente el resultado que habría producido operar con esos tamaños.

Uso:
    .venv\\Scripts\\python.exe experiments\\exp_ia_sizing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.calificador_ia import aplicar_walk_forward  # noqa: E402
from core.convergencia import multiplicador_tamano  # noqa: E402
from data.loader import cargar_config  # noqa: E402
from experiments.exp_ia_filtro import ACTIVOS, construir_muestra  # noqa: E402

RESULTADOS = Path(__file__).resolve().parent / "resultados"


def _metricas(r: np.ndarray, peso: np.ndarray) -> dict:
    """R medio, profit factor y acierto ponderados por tamaño."""
    peso = peso / peso.mean()  # normaliza: mismo riesgo medio que la base
    ponderado = r * peso
    ganancias = ponderado[ponderado > 0].sum()
    perdidas = -ponderado[ponderado < 0].sum()
    return {
        "R medio": ponderado.mean(),
        "PF": ganancias / perdidas if perdidas > 0 else np.inf,
        "acierto": float((r > 0).mean()),
        "peso máx": peso.max(),
    }


def main() -> None:
    """Compara las tres formas de dimensionar sobre datos no vistos."""
    config = cargar_config()
    escalones = config["experimento_toques_frvp"]["escalones_tamano_convergencia"]

    print("=" * 84)
    print("LA IA DIMENSIONANDO LA POSICIÓN  (walk-forward, datos no vistos)")
    print("=" * 84)

    resumen = []
    for nombre, symbol in ACTIVOS.items():
        # Muestra con los filtros ACTIVOS: son las operaciones que el
        # bot abre de verdad, que es sobre lo que se decide el tamaño.
        m = construir_muestra(symbol, config, filtrar=True)
        if m.empty:
            continue

        m = m.reset_index(drop=True)
        m["mult_ia"] = aplicar_walk_forward(m, minimo_entreno=40, paso=10)
        m["mult_conv"] = [
            multiplicador_tamano(int(s), escalones) for s in m["score"]
        ]

        # Solo se compara donde la IA ha llegado a opinar: antes del
        # mínimo de entrenamiento su multiplicador es 1 por defecto y no
        # aportaría información a la comparación.
        activo = m.index >= 40
        sub = m[activo]
        if sub.empty:
            continue

        r = sub["pnl_r"].to_numpy(float)
        filas = {
            "tamaño constante": _metricas(r, np.ones(len(sub))),
            "convergencia (actual)": _metricas(r, sub["mult_conv"].to_numpy(float)),
            "IA (walk-forward)": _metricas(r, sub["mult_ia"].to_numpy(float)),
            # Las dos juntas: la convergencia aporta criterio experto y
            # la IA la combinación de variables que aquélla no mira.
            "convergencia × IA": _metricas(
                r,
                sub["mult_conv"].to_numpy(float) * sub["mult_ia"].to_numpy(float),
            ),
        }
        print(f"\n  {nombre}  —  {len(sub)} operaciones evaluadas"
              f" ({sub['ts'].min():%Y-%m-%d} → {sub['ts'].max():%Y-%m-%d})")
        t = pd.DataFrame(filas).T
        print("    " + t.round(3).to_string().replace("\n", "\n    "))

        for criterio, valores in filas.items():
            resumen.append({"activo": nombre, "criterio": criterio, **valores})

        m.to_csv(RESULTADOS / f"ia_sizing_{nombre}.csv", index=False)

    if resumen:
        RESULTADOS.mkdir(exist_ok=True)
        pd.DataFrame(resumen).to_csv(RESULTADOS / "ia_sizing.csv", index=False)
        print("\n  guardado en experiments/resultados/ia_sizing.csv")


if __name__ == "__main__":
    main()
