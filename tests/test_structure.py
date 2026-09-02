"""Pruebas de la estructura de mercado (SPEC.md §10).

Mismo estilo que el resto del proyecto: funciones ``test_*`` con
``assert``, sin pytest::

    .venv\\Scripts\\python.exe tests\\test_structure.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.structure import (  # noqa: E402
    ALCISTA,
    BAJISTA,
    INDEFINIDA,
    estructura_alineada,
    estructura_mercado,
    permite,
)
from data.loader import _ruta_parquet  # noqa: E402

CONFIG = {"estructura_mercado": {"barras_confirmacion_pivote": 2}}
INICIO = pd.Timestamp("2025-01-01", tz="UTC")


def _velas(cierres: list[float]) -> pd.DataFrame:
    """Velas sintéticas a partir de una lista de cierres."""
    return pd.DataFrame(
        {
            "open": cierres,
            "high": [c * 1.01 for c in cierres],
            "low": [c * 0.99 for c in cierres],
            "close": cierres,
            "volume": [1.0] * len(cierres),
        },
        index=pd.date_range(INICIO, periods=len(cierres), freq="4h",
                            name="timestamp"),
    )


def test_escalones_al_alza_son_estructura_alcista() -> None:
    """Máximos y mínimos crecientes: alcista.

    Con R=2, la serie da swing high en 2 (20) y 7 (25), y swing low en
    4 (10) y 9 (13): los dos máximos y los dos mínimos suben.
    """
    velas = _velas([10, 11, 20, 11, 10, 12, 13, 25, 14, 13, 15, 16, 16, 16])
    estructura = estructura_mercado(velas, CONFIG)
    assert ALCISTA in set(estructura["regimen"]), (
        f"regímenes obtenidos: {list(estructura['regimen'])}"
    )


def test_escalones_a_la_baja_son_estructura_bajista() -> None:
    """Máximos y mínimos decrecientes: bajista.

    Simétrica de la anterior: swing low en 2 (20) y 7 (15), swing high
    en 4 (30) y 9 (27).
    """
    velas = _velas([30, 29, 20, 29, 30, 28, 27, 15, 26, 27, 25, 24, 24, 24])
    estructura = estructura_mercado(velas, CONFIG)
    assert BAJISTA in set(estructura["regimen"]), (
        f"regímenes obtenidos: {list(estructura['regimen'])}"
    )


def test_sin_pivotes_suficientes_la_estructura_es_indefinida() -> None:
    """Con menos de dos swings de cada tipo no hay nada que comparar."""
    velas = _velas([10, 11, 12, 13, 14])
    estructura = estructura_mercado(velas, CONFIG)
    assert (estructura["regimen"] == INDEFINIDA).all()


def test_el_filtro_bloquea_lo_que_va_en_contra() -> None:
    """`no_en_contra` deja pasar lo neutro; `a_favor` no."""
    assert permite(BAJISTA, -1, "no_en_contra")
    assert not permite(BAJISTA, 1, "no_en_contra")
    assert permite(INDEFINIDA, 1, "no_en_contra")

    assert permite(ALCISTA, 1, "a_favor")
    assert not permite(INDEFINIDA, 1, "a_favor")

    assert permite(BAJISTA, 1, "ninguno")


def test_estructura_no_usa_el_futuro() -> None:
    """Truncar el histórico no cambia el régimen de las velas previas."""
    ruta = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    velas = pd.read_parquet(ruta)
    config = {"estructura_mercado": {"barras_confirmacion_pivote": 3}}
    completo = estructura_mercado(velas, config)

    for fraccion in (0.5, 0.7, 0.9):
        corte = velas.index[int(len(velas) * fraccion)]
        truncado = estructura_mercado(velas.loc[:corte], config)
        antes = completo.loc[:corte, "regimen"]
        assert (antes.to_numpy() == truncado["regimen"].to_numpy()).all(), (
            f"el régimen cambia al truncar en {fraccion:.0%}"
        )


def test_la_estructura_de_un_timeframe_mayor_no_se_adelanta() -> None:
    """El régimen diario no llega a las 4h antes de cerrar el día."""
    ruta_4h = _ruta_parquet("BTC/USD:USD", "4h")
    ruta_1d = _ruta_parquet("BTC/USD:USD", "1d")
    if not ruta_4h.exists() or not ruta_1d.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    velas_4h = pd.read_parquet(ruta_4h)
    velas_1d = pd.read_parquet(ruta_1d)
    config = {"estructura_mercado": {"barras_confirmacion_pivote": 3}}

    alineada = estructura_alineada(velas_1d, velas_4h.index, config)
    directa = estructura_mercado(velas_1d, config)["regimen"]

    # Para cada vela de 4h, el régimen que se le asigna tiene que venir
    # de una vela diaria YA CERRADA.
    for ts in velas_4h.index[::200]:
        valor = alineada.loc[ts]
        if valor == INDEFINIDA:
            continue
        cerradas = directa[directa.index + pd.Timedelta("1D") <= ts]
        assert not cerradas.empty
        assert cerradas.iloc[-1] == valor, (
            f"en {ts} se usa un régimen que aún no se conocía"
        )


def main() -> int:
    """Ejecuta todas las pruebas del módulo e informa del resultado."""
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
