"""Pruebas de los indicadores de momento y las divergencias.

    .venv\\Scripts\\python.exe tests\\test_momentum.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.momentum import (  # noqa: E402
    ALCISTA,
    BAJISTA,
    divergencia_vigente,
    divergencias,
    macd,
    rsi,
)
from data.loader import _ruta_parquet  # noqa: E402

INICIO = pd.Timestamp("2025-01-01", tz="UTC")


def _serie(valores: list[float]) -> pd.Series:
    """Serie con índice de 4h."""
    return pd.Series(
        valores,
        index=pd.date_range(INICIO, periods=len(valores), freq="4h",
                            name="timestamp"),
    )


def test_rsi_satura_en_una_subida_continua() -> None:
    """Sin bajadas, el RSI se va a 100."""
    resultado = rsi(_serie([float(x) for x in range(1, 40)]), 14)
    assert resultado.iloc[-1] > 99.0


def test_rsi_satura_en_una_bajada_continua() -> None:
    """Sin subidas, el RSI se va a 0."""
    resultado = rsi(_serie([float(x) for x in range(40, 1, -1)]), 14)
    assert resultado.iloc[-1] < 1.0


def test_rsi_se_queda_en_el_medio_si_alterna() -> None:
    """Subidas y bajadas iguales dejan el RSI cerca de 50."""
    valores = [100.0 + (1.0 if i % 2 else -1.0) for i in range(60)]
    resultado = rsi(_serie(valores), 14)
    assert 40.0 < resultado.iloc[-1] < 60.0


def test_el_histograma_del_macd_es_la_diferencia() -> None:
    """El histograma es MACD menos su señal, por definición."""
    serie = _serie(list(np.linspace(100, 130, 80)))
    tabla = macd(serie)
    diferencia = (tabla["macd"] - tabla["senal"] - tabla["histograma"]).abs()
    assert diferencia.max() < 1e-9


def test_divergencia_alcista() -> None:
    """Precio con mínimo más bajo y momento con mínimo más alto."""
    # Dos valles en el precio: el segundo más profundo.
    precio = _serie([20, 18, 10, 18, 20, 22, 20, 18, 8, 18, 20, 22, 22, 22])
    # El momento hace lo contrario: su segundo valle es menos profundo.
    momento = _serie([-1, -2, -8, -2, -1, 0, -1, -2, -4, -2, -1, 0, 0, 0])

    tabla = divergencias(precio, momento, barras_confirmacion=2)
    assert ALCISTA in set(tabla["divergencia"]), (
        f"no se detectó: {list(tabla['divergencia'])}"
    )


def test_divergencia_bajista() -> None:
    """Precio con máximo más alto y momento con máximo más bajo."""
    precio = _serie([10, 12, 20, 12, 10, 8, 10, 12, 30, 12, 10, 8, 8, 8])
    momento = _serie([1, 2, 8, 2, 1, 0, 1, 2, 4, 2, 1, 0, 0, 0])

    tabla = divergencias(precio, momento, barras_confirmacion=2)
    assert BAJISTA in set(tabla["divergencia"]), (
        f"no se detectó: {list(tabla['divergencia'])}"
    )


def test_sin_divergencia_cuando_van_de_la_mano() -> None:
    """Si precio y momento caen juntos, no hay divergencia alcista."""
    precio = _serie([20, 18, 10, 18, 20, 22, 20, 18, 8, 18, 20, 22, 22, 22])
    momento = _serie([-1, -2, -4, -2, -1, 0, -1, -2, -9, -2, -1, 0, 0, 0])

    tabla = divergencias(precio, momento, barras_confirmacion=2)
    assert ALCISTA not in set(tabla["divergencia"])


def test_la_divergencia_se_declara_tras_confirmar_el_pivote() -> None:
    """Nunca en la vela del pivote: hace falta confirmarlo."""
    precio = _serie([20, 18, 10, 18, 20, 22, 20, 18, 8, 18, 20, 22, 22, 22])
    momento = _serie([-1, -2, -8, -2, -1, 0, -1, -2, -4, -2, -1, 0, 0, 0])

    tabla = divergencias(precio, momento, barras_confirmacion=2)
    posiciones = np.flatnonzero(tabla["divergencia"].to_numpy() != "")
    # El segundo valle del precio está en la posición 8.
    assert (posiciones >= 10).all(), (
        f"declarada antes de confirmar el pivote: {posiciones}"
    )


def test_la_vigencia_caduca() -> None:
    """Una divergencia dura lo que se le diga, y luego se apaga."""
    tabla = pd.DataFrame(
        {"divergencia": ["", ALCISTA, "", "", "", ""], "fuerza": [0.0] * 6},
        index=pd.date_range(INICIO, periods=6, freq="4h"),
    )
    vigente = divergencia_vigente(tabla, velas_vigencia=3)
    assert list(vigente) == ["", ALCISTA, ALCISTA, ALCISTA, "", ""]


def test_momentum_no_usa_el_futuro() -> None:
    """Truncar el histórico no cambia nada de lo ya calculado."""
    ruta = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    velas = pd.read_parquet(ruta)
    cierre = velas["close"]

    completo_rsi = rsi(cierre)
    completo_macd = macd(cierre)["histograma"]
    completo_div = divergencias(cierre, completo_macd)

    for fraccion in (0.5, 0.7, 0.9):
        corte = velas.index[int(len(velas) * fraccion)]
        parcial = cierre.loc[:corte]

        assert np.allclose(
            completo_rsi.loc[:corte].to_numpy(), rsi(parcial).to_numpy()
        ), f"el RSI cambia al truncar en {fraccion:.0%}"

        histograma = macd(parcial)["histograma"]
        assert np.allclose(
            completo_macd.loc[:corte].to_numpy(), histograma.to_numpy()
        ), f"el MACD cambia al truncar en {fraccion:.0%}"

        # Las divergencias del tramo final pueden faltar en el truncado
        # porque su pivote aún no está confirmado; las anteriores no
        # pueden cambiar.
        margen = corte - pd.Timedelta("4h") * 10
        a = completo_div.loc[:margen, "divergencia"].to_numpy()
        b = divergencias(parcial, histograma).loc[:margen, "divergencia"].to_numpy()
        assert (a == b).all(), (
            f"las divergencias cambian al truncar en {fraccion:.0%}"
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
