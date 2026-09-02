"""Pruebas de los osciladores de rango.

    .venv\\Scripts\\python.exe tests\\test_osciladores.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.convergencia import SEÑALES, SEÑALES_OPCIONALES, score  # noqa: E402
from core.osciladores import (  # noqa: E402
    adx,
    bollinger,
    en_extremo,
    estocastico,
    fase_a_favor,
    fase_ttm,
    momento_ttm,
    squeeze,
)
from data.loader import _ruta_parquet  # noqa: E402

TOLERANCIA = 1e-9
INICIO = pd.Timestamp("2025-01-01", tz="UTC")


def _velas(cierres: list[float], amplitud: float = 1.0) -> pd.DataFrame:
    """Velas sintéticas alrededor de una lista de cierres."""
    return pd.DataFrame(
        {
            "open": cierres,
            "high": [c + amplitud for c in cierres],
            "low": [c - amplitud for c in cierres],
            "close": cierres,
            "volume": [1.0] * len(cierres),
        },
        index=pd.date_range(INICIO, periods=len(cierres), freq="4h",
                            name="timestamp"),
    )


def test_el_estocastico_satura_arriba_en_maximos() -> None:
    """Cerrando en lo alto de su rango, el %K se va a 100."""
    velas = _velas([float(x) for x in range(1, 30)], amplitud=0.0)
    resultado = estocastico(velas, 14, 1)
    assert resultado.iloc[-1] > 99.0


def test_el_estocastico_satura_abajo_en_minimos() -> None:
    """Cerrando en lo bajo de su rango, el %K se va a 0."""
    velas = _velas([float(x) for x in range(30, 1, -1)], amplitud=0.0)
    resultado = estocastico(velas, 14, 1)
    assert resultado.iloc[-1] < 1.0


def test_el_estocastico_en_el_centro() -> None:
    """Cierre justo en mitad del rango: 50.

    El rango de las últimas 14 velas va de 90 a 110 y la última cierra
    en 100, o sea exactamente en el medio.
    """
    valores = [90.0 + 20.0 * (i % 2) for i in range(29)] + [100.0]
    velas = _velas(valores, amplitud=0.0)
    resultado = estocastico(velas, 14, 1)
    assert abs(resultado.iloc[-1] - 50.0) < 1e-6, (
        f"debería ser 50, es {resultado.iloc[-1]}"
    )


def test_un_rango_plano_no_tiene_estocastico() -> None:
    """Sin recorrido no hay posición relativa que medir."""
    velas = _velas([100.0] * 20, amplitud=0.0)
    resultado = estocastico(velas, 14, 1)
    assert resultado.iloc[-1] != resultado.iloc[-1]  # NaN


def test_el_suavizado_promedia() -> None:
    """Con suavizado 3, el valor es la media de los tres %K crudos."""
    valores = [float(x) for x in range(1, 30)]
    velas = _velas(valores, amplitud=0.0)
    crudo = estocastico(velas, 14, 1)
    suave = estocastico(velas, 14, 3)
    esperado = crudo.iloc[-3:].mean()
    assert abs(suave.iloc[-1] - esperado) < 1e-9


def test_el_extremo_se_orienta_al_trade() -> None:
    """Un long quiere sobreventa; un short, sobrecompra."""
    assert en_extremo(15.0, 1, 20.0, 80.0)
    assert not en_extremo(15.0, -1, 20.0, 80.0)
    assert en_extremo(85.0, -1, 20.0, 80.0)
    assert not en_extremo(85.0, 1, 20.0, 80.0)
    assert not en_extremo(50.0, 1, 20.0, 80.0)
    assert not en_extremo(float("nan"), 1, 20.0, 80.0)


def test_el_estocastico_no_cuenta_en_el_score_por_defecto() -> None:
    """Es opcional: se registra pero no suma salvo que se pida."""
    assert "estocastico" not in SEÑALES
    assert "estocastico" in SEÑALES_OPCIONALES

    todas = {nombre: True for nombre in SEÑALES}
    todas["estocastico"] = True
    assert score(todas) == len(SEÑALES)
    assert score(todas, ("estocastico",)) == len(SEÑALES) + 1
    # Una señal que no está declarada como opcional se ignora.
    assert score(todas, ("inventada",)) == len(SEÑALES)


def test_el_adx_sube_en_tendencia() -> None:
    """Una subida sostenida da ADX alto; un serrucho, bajo."""
    tendencia = _velas([float(x) for x in range(1, 80)])
    lateral = _velas([100.0 + (2.0 if i % 2 else -2.0) for i in range(80)])

    adx_tendencia = adx(tendencia).iloc[-1]["adx"]
    adx_lateral = adx(lateral).iloc[-1]["adx"]
    assert adx_tendencia > 40.0, f"tendencia clara: {adx_tendencia}"
    assert adx_lateral < 30.0, f"serrucho: {adx_lateral}"
    assert adx_tendencia > adx_lateral


def test_el_adx_no_dice_la_direccion() -> None:
    """Subida y bajada simétricas dan ADX parecido; los DI se invierten."""
    sube = adx(_velas([float(x) for x in range(1, 80)])).iloc[-1]
    baja = adx(_velas([float(x) for x in range(80, 1, -1)])).iloc[-1]

    assert abs(sube["adx"] - baja["adx"]) < 15.0
    assert sube["di_mas"] > sube["di_menos"]
    assert baja["di_menos"] > baja["di_mas"]


def test_las_bandas_de_bollinger_encierran_al_precio() -> None:
    """La inferior por debajo de la media y la superior por encima."""
    serie = pd.Series(
        [100.0 + np.sin(i) * 5 for i in range(60)],
        index=pd.date_range(INICIO, periods=60, freq="4h"),
    )
    bb = bollinger(serie)
    ultima = bb.iloc[-1]
    assert ultima["inferior"] < ultima["media"] < ultima["superior"]
    assert 0.0 <= ultima["pct_b"] <= 1.0
    assert ultima["anchura"] > 0


def test_el_squeeze_se_activa_al_comprimirse() -> None:
    """Con la dispersión de cierres muy baja frente al recorrido de las
    velas, Bollinger cabe dentro de Keltner."""
    # Cierres casi idénticos pero velas con mecha amplia: Bollinger se
    # estrecha (poca dispersión de cierres) y Keltner no (mucho ATR).
    comprimido = _velas([100.0 + 0.01 * (i % 2) for i in range(60)], amplitud=5.0)
    resultado = squeeze(comprimido)
    assert bool(resultado["activo"].iloc[-1]), "debería detectar compresión"

    # Al revés: cierres muy dispersos y velas estrechas. La rampa hace
    # que la desviación de los cierres sea grande mientras el recorrido
    # de cada vela es pequeño, así que Bollinger se sale de Keltner.
    disperso = _velas(list(np.linspace(100.0, 200.0, 60)), amplitud=0.1)
    assert not bool(squeeze(disperso)["activo"].iloc[-1]), (
        "con los cierres dispersos no debería haber compresión"
    )


def test_las_velas_en_estado_cuentan_la_racha() -> None:
    """El contador crece mientras el estado no cambia."""
    velas = _velas([100.0 + 0.01 * (i % 2) for i in range(60)], amplitud=5.0)
    resultado = squeeze(velas)
    racha = resultado["velas_en_estado"]
    assert racha.iloc[-1] > racha.iloc[-2]
    assert (racha > 0).all()


def test_el_momento_ttm_mide_aceleracion_no_pendiente() -> None:
    """En una rampa perfectamente lineal el momento es CERO.

    Es la propiedad que lo distingue de una media móvil, y conviene
    tenerla clara: mide cuánto se ALEJA el precio de su base, no si
    sube o baja. Una subida a ritmo constante mantiene esa distancia,
    así que no hay momento que señalar.
    """
    rampa = momento_ttm(_velas([float(x) for x in range(1, 60)]))
    assert abs(rampa.iloc[-1]) < 1e-6, f"rampa lineal: {rampa.iloc[-1]}"


def test_el_momento_ttm_sigue_a_la_aceleracion() -> None:
    """Positivo cuando el precio se acelera al alza; negativo a la baja."""
    acelera = [float(i) ** 2 / 10.0 for i in range(60)]
    sube = momento_ttm(_velas(acelera))
    baja = momento_ttm(_velas([-x for x in acelera]))
    assert sube.iloc[-1] > 0, f"aceleración alcista: {sube.iloc[-1]}"
    assert baja.iloc[-1] < 0, f"aceleración bajista: {baja.iloc[-1]}"


def test_las_cuatro_fases_del_ttm() -> None:
    """Signo del histograma cruzado con su tendencia."""
    momento = pd.Series(
        [np.nan, 1.0, 2.0, 1.5, -0.5, -2.0, -1.0],
        index=pd.date_range(INICIO, periods=7, freq="4h"),
    )
    fases = list(fase_ttm(momento))

    assert fases[0] == ""              # sin dato
    assert fases[1] == ""              # sin vela previa con la que comparar
    assert fases[2] == "alcista_fuerte"   # positivo y subiendo
    assert fases[3] == "alcista_debil"    # positivo y bajando
    assert fases[4] == "bajista_fuerte"   # negativo y bajando
    assert fases[5] == "bajista_fuerte"   # sigue hundiéndose
    assert fases[6] == "bajista_debil"    # negativo pero girando


def test_la_fase_a_favor_de_una_reversion() -> None:
    """Un long quiere el impulso bajista agotándose; un short, el alcista."""
    assert fase_a_favor("bajista_debil", 1)
    assert not fase_a_favor("bajista_fuerte", 1)
    assert fase_a_favor("alcista_debil", -1)
    assert not fase_a_favor("alcista_debil", 1)
    assert not fase_a_favor("", 1)


def test_la_fase_no_usa_el_futuro() -> None:
    """La tendencia se mide contra la vela anterior, nunca la siguiente."""
    ruta = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    velas = pd.read_parquet(ruta)
    completo = fase_ttm(momento_ttm(velas))
    for fraccion in (0.5, 0.7, 0.9):
        corte = velas.index[int(len(velas) * fraccion)]
        truncado = fase_ttm(momento_ttm(velas.loc[:corte]))
        a = completo.loc[:corte].to_numpy()
        b = truncado.to_numpy()
        assert (a == b).all(), f"la fase cambia al truncar en {fraccion:.0%}"


def test_los_indicadores_de_regimen_no_usan_el_futuro() -> None:
    """Truncar el histórico no cambia nada de lo ya calculado."""
    ruta = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    velas = pd.read_parquet(ruta)
    completos = {
        "adx": adx(velas)["adx"],
        "bollinger": bollinger(velas["close"])["anchura"],
        "squeeze": squeeze(velas)["activo"].astype("float64"),
        "momento_ttm": momento_ttm(velas),
    }

    for fraccion in (0.5, 0.7, 0.9):
        corte = velas.index[int(len(velas) * fraccion)]
        parcial = velas.loc[:corte]
        truncados = {
            "adx": adx(parcial)["adx"],
            "bollinger": bollinger(parcial["close"])["anchura"],
            "squeeze": squeeze(parcial)["activo"].astype("float64"),
            "momento_ttm": momento_ttm(parcial),
        }
        for nombre, completo in completos.items():
            a = completo.loc[:corte].to_numpy(dtype="float64")
            b = truncados[nombre].to_numpy(dtype="float64")
            assert np.allclose(a, b, equal_nan=True), (
                f"{nombre} cambia al truncar en {fraccion:.0%}"
            )


def test_el_estocastico_no_usa_el_futuro() -> None:
    """Truncar el histórico no cambia lo ya calculado."""
    ruta = _ruta_parquet("BTC/USD:USD", "4h")
    if not ruta.exists():
        print("       (saltada: no hay caché en data/raw/)")
        return

    velas = pd.read_parquet(ruta)
    completo = estocastico(velas)
    for fraccion in (0.5, 0.7, 0.9):
        corte = velas.index[int(len(velas) * fraccion)]
        truncado = estocastico(velas.loc[:corte])
        assert np.allclose(
            completo.loc[:corte].to_numpy(), truncado.to_numpy(), equal_nan=True
        ), f"el estocástico cambia al truncar en {fraccion:.0%}"


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
