"""Pruebas de la detección de imbalances (SPEC.md §9).

Mismo estilo que el resto del proyecto: funciones ``test_*`` con
``assert``, sin pytest, ejecutables con el intérprete del entorno
virtual::

    .venv\\Scripts\\python.exe tests\\test_imbalances.py

Las pruebas sobre datos reales usan la caché de ``data/raw/`` y se
saltan solas si no existe: no descargan nada.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.imbalances import (  # noqa: E402
    detectar_imbalances,
    evolucion_imbalance,
    imbalances_vivos,
)
from data.loader import _ruta_parquet  # noqa: E402

TOLERANCIA = 1e-9
INICIO = pd.Timestamp("2025-01-01", tz="UTC")


def _velas(filas: list[tuple[float, float]], freq: str = "7D") -> pd.DataFrame:
    """Construye velas a partir de pares (mínimo, máximo).

    Parameters
    ----------
    filas : list[tuple[float, float]]
        Mínimo y máximo de cada vela.
    freq : str
        Separación entre velas.

    Returns
    -------
    pd.DataFrame
        Velas con ``open``, ``high``, ``low``, ``close`` y ``volume``.
    """
    minimos = [f[0] for f in filas]
    maximos = [f[1] for f in filas]
    medios = [(a + b) / 2 for a, b in filas]
    return pd.DataFrame(
        {
            "open": medios,
            "high": maximos,
            "low": minimos,
            "close": medios,
            "volume": [1.0] * len(filas),
        },
        index=pd.date_range(INICIO, periods=len(filas), freq=freq,
                            name="timestamp"),
    )


def test_detecta_imbalance_alcista() -> None:
    """El mínimo de la tercera por encima del máximo de la primera."""
    velas = _velas([(90, 100), (95, 115), (110, 120)])
    imbalances = detectar_imbalances(velas)

    assert len(imbalances) == 1
    fila = imbalances.iloc[0]
    assert fila["tipo"] == "alcista"
    assert abs(fila["suelo"] - 100) < TOLERANCIA
    assert abs(fila["techo"] - 110) < TOLERANCIA


def test_detecta_imbalance_bajista() -> None:
    """El máximo de la tercera por debajo del mínimo de la primera."""
    velas = _velas([(110, 120), (95, 115), (80, 100)])
    imbalances = detectar_imbalances(velas)

    assert len(imbalances) == 1
    fila = imbalances.iloc[0]
    assert fila["tipo"] == "bajista"
    assert abs(fila["suelo"] - 100) < TOLERANCIA
    assert abs(fila["techo"] - 110) < TOLERANCIA


def test_sin_hueco_no_hay_imbalance() -> None:
    """Si la primera y la tercera se solapan, no hay franja sin negociar."""
    velas = _velas([(90, 105), (95, 115), (100, 120)])
    assert detectar_imbalances(velas).empty


def test_el_imbalance_no_existe_antes_de_cerrar_la_tercera_vela() -> None:
    """`confirmado_en` es el CIERRE de la tercera vela, no su apertura."""
    velas = _velas([(90, 100), (95, 115), (110, 120)])
    fila = detectar_imbalances(velas).iloc[0]

    assert fila["formado_en"] == velas.index[2]
    assert fila["confirmado_en"] == velas.index[2] + pd.Timedelta("7D")
    assert fila["confirmado_en"] > fila["formado_en"]


def test_el_relleno_parcial_recorta_la_zona() -> None:
    """El precio muerde el hueco y lo que queda sin visitar sigue vivo."""
    semanales = _velas([(90, 100), (95, 115), (110, 120)])
    # Hueco 100-110. Una vela posterior baja hasta 104: se come la
    # mitad de arriba y quedan vivos los 100-104.
    posteriores = _velas([(104, 118), (112, 119)], freq="7D")
    posteriores.index = posteriores.index + pd.Timedelta("21D")

    fila = detectar_imbalances(semanales).iloc[0]
    _, borde, muerto_en = evolucion_imbalance(fila, posteriores)

    assert muerto_en is None, "no se rellenó del todo: no debe morir"
    assert abs(borde[-1] - 104) < TOLERANCIA


def test_una_mecha_basta_para_rellenar() -> None:
    """El relleno se mide con mechas: no hace falta que la vela cierre dentro."""
    semanales = _velas([(90, 100), (95, 115), (110, 120)])
    # Mínimo en 99 (por debajo del suelo del hueco) pero cierre muy
    # arriba: la mecha ya recorrió toda la franja.
    posteriores = _velas([(99, 130)], freq="7D")
    posteriores.index = posteriores.index + pd.Timedelta("21D")

    fila = detectar_imbalances(semanales).iloc[0]
    _, _, muerto_en = evolucion_imbalance(fila, posteriores)

    assert muerto_en is not None, "la mecha atravesó el hueco entero"


def test_el_borde_solo_avanza_en_una_direccion() -> None:
    """Un imbalance no se 'descome': el borde es monótono."""
    semanales = _velas([(90, 100), (95, 115), (110, 120)])
    posteriores = _velas([(106, 118), (112, 119), (108, 118)], freq="7D")
    posteriores.index = posteriores.index + pd.Timedelta("21D")

    fila = detectar_imbalances(semanales).iloc[0]
    _, borde, _ = evolucion_imbalance(fila, posteriores)

    assert (np.diff(borde) <= TOLERANCIA).all(), "el borde de un alcista solo baja"
    assert abs(borde[-1] - 106) < TOLERANCIA


def test_imbalances_vivos_respeta_la_confirmacion() -> None:
    """Antes de `confirmado_en` un imbalance no existe todavía."""
    velas = _velas([(90, 100), (95, 115), (110, 120), (112, 122), (113, 123)])
    imbalances = detectar_imbalances(velas)
    fila = imbalances.iloc[0]

    antes = imbalances_vivos(imbalances, velas, fila["formado_en"])
    despues = imbalances_vivos(imbalances, velas, velas.index[-1])

    assert antes.empty
    assert len(despues) == 1


def test_imbalances_reales_vivos_no_han_sido_visitados() -> None:
    """Sobre datos reales: lo que sigue vivo no lo ha tocado el precio."""
    ruta_semanal = _ruta_parquet("BTC/USD:USD", "1w")
    ruta_4h = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta_semanal.exists() or not ruta_4h.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    semanales = pd.read_parquet(ruta_semanal)
    velas_4h = pd.read_parquet(ruta_4h)

    imbalances = detectar_imbalances(semanales)
    assert not imbalances.empty, "BTC en 2 años tiene imbalances semanales"

    vivos = imbalances_vivos(imbalances, velas_4h, velas_4h.index[-1])
    for fila in vivos.itertuples():
        posteriores = velas_4h.loc[fila.confirmado_en:]
        if posteriores.empty:
            continue
        if fila.tipo == "alcista":
            assert posteriores["low"].min() > fila.suelo - TOLERANCIA, (
                "un imbalance alcista vivo no puede haber sido recorrido entero"
            )
        else:
            assert posteriores["high"].max() < fila.techo + TOLERANCIA, (
                "un imbalance bajista vivo no puede haber sido recorrido entero"
            )
    print(f"       ({len(imbalances)} imbalances, {len(vivos)} vivos en BTC)")


def test_imbalances_vivos_no_usa_el_futuro() -> None:
    """Truncar el histórico no cambia lo que estaba vivo antes del corte."""
    ruta_semanal = _ruta_parquet("BTC/USD:USD", "1w")
    ruta_4h = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta_semanal.exists() or not ruta_4h.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    semanales = pd.read_parquet(ruta_semanal)
    velas_4h = pd.read_parquet(ruta_4h)
    imbalances = detectar_imbalances(semanales)

    for fraccion in (0.5, 0.7, 0.9):
        corte = velas_4h.index[int(len(velas_4h) * fraccion)]
        completo = imbalances_vivos(imbalances, velas_4h, corte)
        truncado = imbalances_vivos(
            detectar_imbalances(semanales.loc[:corte]),
            velas_4h.loc[:corte],
            corte,
        )
        claves_c = {
            (f.tipo, round(f.techo, 6), round(f.suelo, 6))
            for f in completo.itertuples()
        }
        claves_t = {
            (f.tipo, round(f.techo, 6), round(f.suelo, 6))
            for f in truncado.itertuples()
        }
        assert claves_c == claves_t, (
            f"corte al {fraccion:.0%}: cambian {len(claves_c ^ claves_t)} zonas"
        )


def main() -> int:
    """Ejecuta todas las pruebas del módulo e informa del resultado.

    Returns
    -------
    int
        ``0`` si todas pasan, ``1`` si alguna falla.
    """
    pruebas = [
        (nombre, funcion)
        for nombre, funcion in sorted(globals().items())
        if nombre.startswith("test_") and callable(funcion)
    ]

    fallos = 0
    for nombre, funcion in pruebas:
        try:
            funcion()
        except Exception:
            fallos += 1
            print(f"FALLO  {nombre}")
            traceback.print_exc()
        else:
            print(f"OK     {nombre}")

    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas superadas")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
