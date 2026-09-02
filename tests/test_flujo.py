"""Pruebas del funding y del score de convergencia.

    .venv\\Scripts\\python.exe tests\\test_flujo.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.convergencia import (  # noqa: E402
    SEÑALES,
    SEÑALES_OPCIONALES,
    multiplicador_tamano,
    score,
    señales_activas,
)
from core.flujo import funding_a_favor, serie_a_favor, variables_funding  # noqa: E402
from data.funding import _ruta_parquet, _validar, alinear_a_velas  # noqa: E402

TOLERANCIA = 1e-9
INICIO = pd.Timestamp("2025-01-01", tz="UTC")

CONFIG = {"flujo": {"ventana_zscore": 10, "velas_acumulado": 3}}


def _funding(valores: list[float], freq: str = "8h") -> pd.DataFrame:
    """Histórico de funding sintético."""
    return pd.DataFrame(
        {"funding": valores},
        index=pd.date_range(INICIO, periods=len(valores), freq=freq,
                            name="timestamp"),
    )


# --------------------------------------------------------------------
# Alineación temporal — donde se puede colar el lookahead
# --------------------------------------------------------------------


def test_la_alineacion_propaga_el_ultimo_pago_conocido() -> None:
    """Entre dos pagos, la vela usa el anterior, nunca el siguiente."""
    # Pagos cada 8h: 00:00 y 08:00.
    funding = _funding([0.001, 0.002])
    velas = pd.date_range(INICIO, periods=3, freq="4h")  # 00, 04, 08

    alineado = alinear_a_velas(funding, velas)
    assert abs(alineado.iloc[0] - 0.001) < TOLERANCIA
    # La vela de las 04:00 NO puede ver el pago de las 08:00.
    assert abs(alineado.iloc[1] - 0.001) < TOLERANCIA
    assert abs(alineado.iloc[2] - 0.002) < TOLERANCIA


def test_antes_del_primer_pago_no_hay_dato() -> None:
    """Sin funding conocido, NaN; nunca se rellena hacia atrás."""
    funding = _funding([0.001], freq="8h")
    velas = pd.date_range(INICIO - pd.Timedelta("8h"), periods=3, freq="4h")

    alineado = alinear_a_velas(funding, velas)
    assert np.isnan(alineado.iloc[0])
    assert np.isnan(alineado.iloc[1])
    assert abs(alineado.iloc[2] - 0.001) < TOLERANCIA


def test_la_alineacion_no_usa_el_futuro() -> None:
    """Truncar el histórico no cambia ninguna vela anterior al corte."""
    funding = _funding(list(np.linspace(0.0001, 0.001, 40)))
    velas = pd.date_range(INICIO, periods=80, freq="4h")

    completo = alinear_a_velas(funding, velas)
    for fraccion in (0.5, 0.7, 0.9):
        corte = velas[int(len(velas) * fraccion)]
        truncado = alinear_a_velas(funding.loc[:corte], velas[velas <= corte])
        a = completo.loc[:corte].to_numpy()
        b = truncado.to_numpy()
        assert np.allclose(a, b, equal_nan=True), (
            f"la alineación cambia al truncar en {fraccion:.0%}"
        )


def test_frecuencias_distintas() -> None:
    """Un activo que paga cada 4h y otro cada 8h se alinean igual."""
    velas = pd.date_range(INICIO, periods=6, freq="4h")
    cada_4h = alinear_a_velas(_funding([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "4h"), velas)
    cada_8h = alinear_a_velas(_funding([1.0, 2.0, 3.0], "8h"), velas)

    assert list(cada_4h) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    # Con pagos cada 8h, cada valor cubre dos velas.
    assert list(cada_8h) == [1.0, 1.0, 2.0, 2.0, 3.0, 3.0]


# --------------------------------------------------------------------
# Variables derivadas
# --------------------------------------------------------------------


def test_el_zscore_marca_lo_extremo() -> None:
    """Un pico aislado sale con z alto; el resto, cerca de cero.

    La serie base lleva ruido a propósito: con un funding perfectamente
    constante la desviación es cero y el z-score no existe, que es lo
    correcto —sin variación previa no hay nada que declarar extremo— y
    lo comprueba `test_sin_variacion_no_hay_zscore`.
    """
    ruido = [0.0001 + 0.00001 * (1 if i % 2 else -1) for i in range(20)]
    valores = ruido + [0.005] + ruido[:5]
    serie = pd.Series(
        valores,
        index=pd.date_range(INICIO, periods=len(valores), freq="4h"),
    )
    tabla = variables_funding(serie, CONFIG)
    assert tabla["funding_z"].max() > 2.0, (
        f"el pico debería destacar: max z = {tabla['funding_z'].max()}"
    )
    assert abs(tabla["funding_z"].iloc[15]) < 2.0


def test_sin_variacion_no_hay_zscore() -> None:
    """Funding constante: no se puede decir que nada sea extremo."""
    serie = pd.Series(
        [0.0001] * 20, index=pd.date_range(INICIO, periods=20, freq="4h")
    )
    tabla = variables_funding(serie, CONFIG)
    assert tabla["funding_z"].isna().all(), (
        "sin desviación no debe inventarse un z-score"
    )


def test_el_acumulado_suma_los_ultimos_pagos() -> None:
    """Es la suma móvil, no la media."""
    serie = pd.Series(
        [1.0] * 10, index=pd.date_range(INICIO, periods=10, freq="4h")
    )
    tabla = variables_funding(serie, CONFIG)
    assert abs(tabla["funding_acumulado"].iloc[-1] - 3.0) < TOLERANCIA


def test_las_ventanas_no_miran_al_futuro() -> None:
    """Truncar no cambia el z-score ni el acumulado ya calculados."""
    serie = pd.Series(
        np.linspace(0.0001, 0.002, 60),
        index=pd.date_range(INICIO, periods=60, freq="4h"),
    )
    completo = variables_funding(serie, CONFIG)
    for fraccion in (0.5, 0.8):
        corte = serie.index[int(len(serie) * fraccion)]
        truncado = variables_funding(serie.loc[:corte], CONFIG)
        for columna in ("funding_z", "funding_acumulado"):
            a = completo.loc[:corte, columna].to_numpy()
            b = truncado[columna].to_numpy()
            assert np.allclose(a, b, equal_nan=True), (
                f"{columna} cambia al truncar en {fraccion:.0%}"
            )


def test_el_funding_se_orienta_al_trade() -> None:
    """Funding positivo (exceso de largos) favorece a un short."""
    assert funding_a_favor(0.001, -1) > 0
    assert funding_a_favor(0.001, 1) < 0
    assert funding_a_favor(-0.001, 1) > 0
    assert np.isnan(funding_a_favor(float("nan"), 1))

    orientada = serie_a_favor(np.array([0.001, -0.001]), np.array([-1, -1]))
    assert orientada[0] > 0 and orientada[1] < 0


def test_la_validacion_rechaza_datos_corruptos() -> None:
    """Fallar ruidosamente antes que operar con basura."""
    for malo, motivo in (
        (pd.DataFrame({"funding": []},
                      index=pd.DatetimeIndex([], name="timestamp")), "vacío"),
        (_funding([0.5]), "fuera de rango"),
    ):
        try:
            _validar(malo, "TEST")
        except ValueError:
            continue
        raise AssertionError(f"no se detectó el caso: {motivo}")

    con_nan = _funding([0.001, 0.002])
    con_nan.iloc[1, 0] = np.nan
    try:
        _validar(con_nan, "TEST")
        raise AssertionError("no se detectaron los huecos")
    except ValueError:
        pass


# --------------------------------------------------------------------
# Convergencia
# --------------------------------------------------------------------


def _señales(**cambios):
    """Evalúa las señales sobre un caso base, con los cambios dados."""
    caso = {
        "impulso": 2.0, "impulso_minimo": 1.0, "divergencia": "alcista",
        "regimen": "bajista", "nivel": "val", "confluencia": 1,
        "direccion": 1,
    }
    caso.update(cambios)
    return señales_activas(**caso)


def test_las_cinco_senales_se_evaluan() -> None:
    """El caso base activa impulso, divergencia y poca confluencia.

    `señales_activas` devuelve también las opcionales —el estocástico—
    aunque estas no cuenten para el score.
    """
    activas = _señales()
    assert set(activas) == set(SEÑALES) | set(SEÑALES_OPCIONALES)
    assert activas["impulso"]
    assert activas["divergencia"]
    assert activas["poca_confluencia"]
    # VAL + long = el precio llega desde dentro del área: rotación NO.
    assert not activas["rotacion"]
    # Régimen bajista + long = va contra la estructura: SÍ cuenta.
    assert activas["contra_estructura"]


def test_el_impulso_necesita_superar_el_umbral() -> None:
    """Por debajo del mínimo, la señal no cuenta."""
    assert _señales(impulso=1.5)["impulso"]
    assert not _señales(impulso=0.5)["impulso"]
    assert not _señales(impulso=float("nan"))["impulso"]


def test_la_divergencia_debe_estar_alineada() -> None:
    """Una divergencia bajista no apoya un long."""
    assert _señales(divergencia="alcista", direccion=1)["divergencia"]
    assert not _señales(divergencia="bajista", direccion=1)["divergencia"]
    assert _señales(divergencia="bajista", direccion=-1)["divergencia"]
    assert not _señales(divergencia="", direccion=1)["divergencia"]


def test_la_rotacion_detecta_la_llegada_desde_dentro() -> None:
    """VAH+short y VAL+long llegan desde dentro del área de valor."""
    assert not _señales(nivel="vah", direccion=-1)["rotacion"]
    assert not _señales(nivel="val", direccion=1)["rotacion"]
    assert _señales(nivel="vah", direccion=1)["rotacion"]
    assert _señales(nivel="poc", direccion=1)["rotacion"]


def test_la_estructura_cuenta_invertida() -> None:
    """La señal se activa cuando la operación va CONTRA la estructura."""
    assert not _señales(regimen="alcista", direccion=1)["contra_estructura"]
    assert _señales(regimen="alcista", direccion=-1)["contra_estructura"]
    # En estructura indefinida no se va a favor de nada: cuenta.
    assert _señales(regimen="indefinida", direccion=1)["contra_estructura"]


def test_la_confluencia_cuenta_invertida() -> None:
    """Demasiadas zonas apiladas apagan la señal."""
    assert _señales(confluencia=1)["poca_confluencia"]
    assert not _señales(confluencia=2)["poca_confluencia"]
    assert not _señales(confluencia=5)["poca_confluencia"]


def test_el_score_cuenta_las_activas() -> None:
    """De 0 a 5."""
    assert score({nombre: True for nombre in SEÑALES}) == 5
    assert score({nombre: False for nombre in SEÑALES}) == 0
    assert score({"impulso": True}) == 1
    assert score({}) == 0


def test_el_multiplicador_usa_el_escalon_inferior() -> None:
    """Un score sin escalón propio hereda el inmediatamente inferior."""
    escalones = {0: 0.5, 3: 1.0, 4: 1.5}
    assert multiplicador_tamano(0, escalones) == 0.5
    assert multiplicador_tamano(2, escalones) == 0.5
    assert multiplicador_tamano(3, escalones) == 1.0
    assert multiplicador_tamano(4, escalones) == 1.5
    assert multiplicador_tamano(5, escalones) == 1.5
    assert multiplicador_tamano(3, {}) == 1.0


def test_el_funding_real_esta_descargado_y_es_coherente() -> None:
    """Sobre la caché: cadencia estable y valores plausibles."""
    ruta = _ruta_parquet("BTC/USD:USD")
    if not ruta.exists():
        print("       (saltada: no hay caché de funding)")
        return

    funding = pd.read_parquet(ruta)
    _validar(funding, "BTC/USD:USD")
    assert funding.index.is_monotonic_increasing
    assert len(funding) > 1000, "dos años de pagos deberían ser más de 1000"
    print(f"       ({len(funding)} pagos, de {funding.index[0].date()} "
          f"a {funding.index[-1].date()})")


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
