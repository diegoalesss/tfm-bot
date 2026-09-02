"""Pruebas del motor de backtest y de la rejilla de niveles.

Mismo estilo que ``tests/test_range_detector.py``: funciones ``test_*``
con ``assert``, sin pytest, ejecutables con el intérprete del entorno
virtual::

    .venv\\Scripts\\python.exe tests\\test_backtest.py

Las pruebas sintéticas construyen el camino del precio vela a vela de
15m y derivan de él las velas de 4h, así que las dos series son
coherentes por construcción, igual que en los datos reales.

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

from core.imbalances import detectar_imbalances, puntos_objetivo  # noqa: E402
from core.levels import (  # noqa: E402
    COLUMNAS_NIVEL,
    construir_niveles,
    objetivos_desde,
    seleccionar_causalmente,
)
from core.range_detector import detectar_rangos_laterales  # noqa: E402
from data.loader import _ruta_parquet, cargar_config  # noqa: E402
from execution.backtest import _plan_de_salida, simular  # noqa: E402
from execution.metrics import desglose, resumen  # noqa: E402

TOLERANCIA = 1e-9
INICIO = pd.Timestamp("2025-01-01 00:00", tz="UTC")

# Velas de 15m de calentamiento antes de cualquier operación: el ATR de
# 4h necesita 14 velas cerradas, y el motor no abre posición sin él.
VELAS_CALENTAMIENTO = 16 * 20


def _config_exp(**cambios) -> dict:
    """Configuración mínima del experimento para las pruebas sintéticas.

    Independiente de ``config.yaml``: si mañana se recalibra un
    parámetro real, estas pruebas siguen midiendo lo que dicen medir.

    Parameters
    ----------
    **cambios
        Claves de ``experimento_toques_frvp`` que se quieran sustituir.

    Returns
    -------
    dict
        Configuración completa lista para pasar a ``simular``.
    """
    experimento = {
        "tipos_operables": ["principal"],
        "distancia_minima_nivel_pct": 0.005,
        "mult_atr_stop": 1.0,
        "atr_periodo": 14,
        "reparto_parciales": [1 / 3, 1 / 3, 1 / 3],
        "mover_be_en_tp": 2,
        "usar_imbalances_como_objetivo": True,
        "tp1_al_menos_como_el_stop": True,
        "distancia_maxima_objetivo_pct": 0.05,
        "tope_tambien_desde_la_entrada": False,
        "tp_fallback_pct": 0.05,
        "fallback_escalonado": True,
        "sl_fallback_usa_atr": True,
        "sl_fallback_pct": 0.02,
        "max_posiciones_simultaneas": 1,
        "cooldown_velas_nivel": 6,
        "timeout_velas_4h": None,
        "vigencia_nivel_velas_4h": None,
        "comision_maker_pct": 0.0,
        "comision_taker_pct": 0.0,
        "slippage_stop_pct": 0.0,
        "capital_inicial": 10000.0,
        "repeticiones_baseline": 2,
        "desplazamiento_baseline_pct": [0.03, 0.15],
        "semilla_baseline": 1,
    }
    experimento.update(cambios)
    return {
        "frvp": {"bins": 1000, "value_area_pct": 0.70},
        "datos": {"timeframe_frvp": "15m"},
        "estructura_mercado": {"barras_confirmacion_pivote": 3},
        "experimento_toques_frvp": experimento,
    }


def _velas(camino: list[float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Construye velas de 15m siguiendo un camino de precios, y las de 4h.

    Cada vela va del cierre anterior al precio indicado, con el máximo y
    el mínimo en esos dos extremos: es un camino continuo, sin huecos.

    Parameters
    ----------
    camino : list[float]
        Precio de cierre de cada vela de 15m.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        Velas de 4h y de 15m, con ``DatetimeIndex`` UTC.
    """
    cierres = np.asarray(camino, dtype="float64")
    aperturas = np.concatenate([[cierres[0]], cierres[:-1]])
    df15 = pd.DataFrame(
        {
            "open": aperturas,
            "high": np.maximum(aperturas, cierres),
            "low": np.minimum(aperturas, cierres),
            "close": cierres,
            "volume": np.ones_like(cierres),
        },
        index=pd.date_range(INICIO, periods=len(cierres), freq="15min", name="timestamp"),
    )
    return _agregar_4h(df15), df15


def _agregar_4h(df15: pd.DataFrame) -> pd.DataFrame:
    """Agrega velas de 15m a velas de 4h."""
    return df15.resample("4h").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()


def _zigzag(centro: float, amplitud: float, n: int) -> list[float]:
    """Camino oscilante, para que el ATR de calentamiento no sea cero."""
    return [centro + amplitud * (1 if k % 2 else -1) for k in range(n)]


def _rejilla(precios: list[float], vigente_desde: pd.Timestamp) -> pd.DataFrame:
    """Construye una rejilla de niveles sintética."""
    return pd.DataFrame(
        [
            {
                "rango_id": 0,
                "ventana": 150,
                "tipo": "principal",
                "nivel": nombre,
                "precio": precio,
                "vigente_desde": vigente_desde,
                "inicio": INICIO,
                "fin": vigente_desde,
            }
            for nombre, precio in zip(
                ["val", "poc", "vah", "extra1", "extra2"], precios
            )
        ],
        columns=COLUMNAS_NIVEL,
    )


# --------------------------------------------------------------------
# Elección de objetivos
# --------------------------------------------------------------------


def test_objetivos_desde_toma_los_siguientes_niveles() -> None:
    """En un long, los objetivos son los niveles de encima, en orden."""
    rejilla = np.array([90.0, 102.0, 104.0, 106.0, 110.0])
    objetivos = objetivos_desde(100.0, 1, rejilla, 0.005, 3)
    assert objetivos == [102.0, 104.0, 106.0]


def test_objetivos_desde_en_short_van_hacia_abajo() -> None:
    """En un short, los objetivos son los niveles de debajo."""
    rejilla = np.array([90.0, 94.0, 98.0, 105.0])
    objetivos = objetivos_desde(100.0, -1, rejilla, 0.005, 3)
    assert objetivos == [98.0, 94.0, 90.0]


def test_objetivos_desde_descarta_niveles_pegados() -> None:
    """Dos líneas casi juntas no son dos objetivos distintos."""
    # 100.2 está a un 0.2% de la entrada, por debajo del 0.5% exigido.
    rejilla = np.array([100.2, 104.0, 104.1, 108.0])
    objetivos = objetivos_desde(100.0, 1, rejilla, 0.005, 3)
    assert objetivos == [104.0, 108.0]


def test_objetivos_desde_sin_niveles_por_delante() -> None:
    """Si no hay nada en la dirección del trade, no hay objetivos."""
    rejilla = np.array([90.0, 95.0])
    assert objetivos_desde(100.0, 1, rejilla, 0.005, 3) == []


# --------------------------------------------------------------------
# Plan de salida
# --------------------------------------------------------------------


def test_plan_de_salida_usa_los_tres_niveles() -> None:
    """Con rejilla suficiente: tres objetivos, stop en ATR."""
    cfg = _config_exp()["experimento_toques_frvp"]
    rejilla = np.array([102.0, 104.0, 106.0])
    objetivos, fracciones, stop, fallback = _plan_de_salida(
        100.0, 1, rejilla, 2.0, cfg
    )
    assert objetivos == [102.0, 104.0, 106.0]
    assert abs(sum(fracciones) - 1.0) < TOLERANCIA
    assert abs(stop - 98.0) < TOLERANCIA
    assert not fallback


def test_fallback_escalonado_pone_tres_objetivos_al_5_por_ciento() -> None:
    """Sin ningún nivel por delante: 5%, 5% del anterior, y otro 5%."""
    cfg = _config_exp(fallback_escalonado=True)["experimento_toques_frvp"]
    objetivos, fracciones, stop, fallback = _plan_de_salida(
        100.0, 1, np.array([90.0]), 2.0, cfg
    )
    assert fallback
    assert len(objetivos) == 3
    assert abs(objetivos[0] - 105.0) < 1e-6
    assert abs(objetivos[1] - 110.25) < 1e-6
    assert abs(objetivos[2] - 115.7625) < 1e-6
    assert abs(sum(fracciones) - 1.0) < TOLERANCIA
    # El stop del fallback es el mismo de ATR que el resto: un solo
    # criterio de riesgo en todo el sistema.
    assert abs(stop - 98.0) < TOLERANCIA


def test_fallback_de_objetivo_unico() -> None:
    """Con el escalonado apagado, se cierra todo en el primer 5%."""
    cfg = _config_exp(fallback_escalonado=False)["experimento_toques_frvp"]
    objetivos, fracciones, _, fallback = _plan_de_salida(
        100.0, 1, np.array([90.0]), 2.0, cfg
    )
    assert fallback
    assert objetivos == [105.0]
    assert fracciones == [1.0]


def test_el_fallback_puede_usar_un_stop_porcentual() -> None:
    """`sl_fallback_usa_atr: false` recupera el stop fijo del 2%."""
    cfg = _config_exp(sl_fallback_usa_atr=False)["experimento_toques_frvp"]
    _, _, stop, fallback = _plan_de_salida(
        100.0, 1, np.array([90.0]), 2.0, cfg
    )
    assert fallback
    assert abs(stop - 98.0) < TOLERANCIA  # coincide: 1.5*2.0 ATR = 2%


def test_plan_de_salida_refunde_los_objetivos_que_faltan() -> None:
    """Con un solo nivel por delante, el resto va al objetivo de 5%."""
    cfg = _config_exp()["experimento_toques_frvp"]
    objetivos, fracciones, _, fallback = _plan_de_salida(
        100.0, 1, np.array([102.0]), 2.0, cfg
    )
    assert not fallback
    assert objetivos == [102.0, 105.0]
    assert abs(fracciones[0] - 1 / 3) < TOLERANCIA
    assert abs(fracciones[1] - 2 / 3) < TOLERANCIA


def test_tp1_nunca_queda_mas_cerca_que_el_stop() -> None:
    """Un primer objetivo por debajo de 1R no es objetivo, es ruido."""
    cfg = _config_exp()["experimento_toques_frvp"]
    # ATR 2.0 -> stop en 98, o sea un 2% de riesgo. El nivel de 101
    # está a un 1%: dentro del stop, así que se salta.
    rejilla = np.array([101.0, 104.0, 108.0])
    objetivos, _, stop, _ = _plan_de_salida(100.0, 1, rejilla, 2.0, cfg)

    assert abs(stop - 98.0) < TOLERANCIA
    assert objetivos[0] == 104.0, "TP1 no puede estar más cerca que el stop"


def test_sin_la_regla_el_tp1_puede_quedar_pegado() -> None:
    """Contraste del test anterior: con la regla apagada, 101 sí vale."""
    cfg = _config_exp(tp1_al_menos_como_el_stop=False)["experimento_toques_frvp"]
    rejilla = np.array([101.0, 104.0, 108.0])
    objetivos, _, _, _ = _plan_de_salida(100.0, 1, rejilla, 2.0, cfg)

    assert objetivos[0] == 101.0


def test_el_50_por_ciento_de_un_imbalance_fino_se_descarta() -> None:
    """Dos zonas casi pegadas no son dos objetivos distintos.

    Con el tope de distancia desactivado: aquí se mide la separación
    mínima, no el recorte de tramos largos.
    """
    cfg = _config_exp(distancia_maxima_objetivo_pct=None)["experimento_toques_frvp"]
    # Imbalance fino: borde en 104 y su 50% en 104.2, a un 0.19% del
    # borde, por debajo del 0.5% exigido entre objetivos.
    rejilla = np.array([104.0, 104.2, 110.0])
    objetivos, _, _, _ = _plan_de_salida(100.0, 1, rejilla, 2.0, cfg)

    assert objetivos == [104.0, 110.0], "el 50% del imbalance fino sobra"


def test_las_zonas_de_imbalance_entran_en_la_rejilla() -> None:
    """Un imbalance más cercano que el nivel del FRVP es el TP1.

    Con el tope de distancia desactivado, para que lo único que decida
    el orden sea la cercanía.
    """
    cfg = _config_exp(distancia_maxima_objetivo_pct=None)["experimento_toques_frvp"]
    # 104 y 105.75 vienen de un imbalance; 112 es el nivel del FRVP.
    rejilla = np.array([112.0, 104.0, 105.75])
    objetivos, _, _, _ = _plan_de_salida(100.0, 1, rejilla, 2.0, cfg)

    assert objetivos == [104.0, 105.75, 112.0], (
        "los objetivos se ordenan por cercanía, no por procedencia"
    )


def test_puntos_objetivo_de_un_imbalance() -> None:
    """Cada imbalance vivo aporta su borde y el 50% de lo que le queda."""
    semanales = pd.DataFrame(
        {
            "open": [95.0, 105.0, 115.0],
            "high": [100.0, 115.0, 120.0],
            "low": [90.0, 95.0, 110.0],
            "close": [95.0, 110.0, 115.0],
            "volume": [1.0, 1.0, 1.0],
        },
        index=pd.date_range(INICIO, periods=3, freq="7D", name="timestamp"),
    )
    # Hueco alcista 100-110. Su 50% inicial es 105.
    imbalances = detectar_imbalances(semanales)

    velas = pd.DataFrame(
        {
            "open": [115.0, 115.0],
            "high": [118.0, 118.0],
            "low": [112.0, 106.0],
            "close": [115.0, 115.0],
            "volume": [1.0, 1.0],
        },
        index=pd.date_range(
            INICIO + pd.Timedelta("21D"), periods=2, freq="4h", name="timestamp"
        ),
    )
    bordes, medios = puntos_objetivo(imbalances, velas)

    assert bordes.shape == (1, 2)
    # Primera vela: no ha entrado, el borde sigue en el techo original.
    assert abs(bordes[0, 0] - 110.0) < TOLERANCIA
    assert abs(medios[0, 0] - 105.0) < TOLERANCIA
    # Segunda: baja a 106 y se come un trozo; el 50% se recalcula sobre
    # lo que QUEDA (100-106), no sobre el hueco original.
    assert abs(bordes[0, 1] - 106.0) < TOLERANCIA
    assert abs(medios[0, 1] - 103.0) < TOLERANCIA


def test_un_objetivo_demasiado_lejos_se_acerca() -> None:
    """Si TP2 está lejísimos, se cierra a un 5% de TP1."""
    cfg = _config_exp()["experimento_toques_frvp"]
    # TP1 en 104 (nivel real). El siguiente nivel, 130, está a un 25%
    # de él: se recorta a 104 * 1.05 = 109.2.
    rejilla = np.array([104.0, 130.0, 200.0])
    objetivos, _, _, _ = _plan_de_salida(100.0, 1, rejilla, 2.0, cfg)

    assert abs(objetivos[0] - 104.0) < TOLERANCIA, "TP1 es un nivel real"
    assert abs(objetivos[1] - 109.2) < 1e-6, "TP2 recortado al 5% de TP1"
    # Cascada: TP3 se mide desde el TP2 ya recortado.
    assert abs(objetivos[2] - 109.2 * 1.05) < 1e-6


def test_un_objetivo_cercano_no_se_toca() -> None:
    """El recorte solo actúa sobre tramos largos, no sobre los normales."""
    cfg = _config_exp()["experimento_toques_frvp"]
    rejilla = np.array([104.0, 107.0, 110.0])
    objetivos, _, _, _ = _plan_de_salida(100.0, 1, rejilla, 2.0, cfg)

    assert objetivos == [104.0, 107.0, 110.0]


def test_el_recorte_de_objetivos_funciona_en_short() -> None:
    """En un short los objetivos bajan, y el tope se mide hacia abajo."""
    cfg = _config_exp()["experimento_toques_frvp"]
    rejilla = np.array([96.0, 70.0])
    objetivos, _, _, _ = _plan_de_salida(100.0, -1, rejilla, 2.0, cfg)

    assert abs(objetivos[0] - 96.0) < TOLERANCIA
    assert abs(objetivos[1] - 96.0 * 0.95) < 1e-6


def test_el_tope_desde_la_entrada_es_opcional() -> None:
    """Con el interruptor activado, también se recorta TP1."""
    cfg = _config_exp(tope_tambien_desde_la_entrada=True)["experimento_toques_frvp"]
    objetivos, _, _, _ = _plan_de_salida(100.0, 1, np.array([130.0]), 2.0, cfg)

    assert abs(objetivos[0] - 105.0) < 1e-6


def test_plan_de_salida_descarta_el_fallback_si_queda_corto() -> None:
    """El objetivo de fallback no puede quedar por detrás de un nivel."""
    cfg = _config_exp()["experimento_toques_frvp"]
    # El nivel de 108 ya está más lejos que el fallback del 5% (105).
    objetivos, fracciones, _, _ = _plan_de_salida(
        100.0, 1, np.array([108.0]), 2.0, cfg
    )
    assert objetivos == [108.0]
    assert abs(fracciones[0] - 1.0) < TOLERANCIA


# --------------------------------------------------------------------
# Mecánica del motor
# --------------------------------------------------------------------


# Rejilla de referencia para las pruebas de mecánica: el precio se
# calienta en 110 y el único nivel por debajo es el 100, así que al
# bajar no encuentra ninguna línea intermedia que se lleve el fill
# antes. Los objetivos del long (112, 114, 116) quedan por encima del
# precio de partida, donde el calentamiento no los toca.
REJILLA_LONG = [100.0, 112.0, 114.0, 116.0, 130.0]


def test_long_cuando_el_precio_llega_desde_arriba() -> None:
    """El precio baja hasta el nivel y se abre un long en él."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    camino += [104.0, 100.0, 104.0, 108.0]
    camino += [108.0] * 16
    velas_4h, velas_15m = _velas(camino)

    trades, _ = simular(velas_4h, velas_15m, _rejilla(REJILLA_LONG, INICIO),
                        _config_exp())

    assert len(trades) == 1, "se esperaba exactamente una operación"
    trade = trades.iloc[0]
    assert trade["direccion"] == "long"
    assert abs(trade["entrada"] - 100.0) < TOLERANCIA
    assert trade["nivel"] == "val"


def test_short_cuando_el_precio_llega_desde_abajo() -> None:
    """El precio sube hasta el nivel y se abre un short en él."""
    camino = _zigzag(90.0, 0.5, VELAS_CALENTAMIENTO)
    camino += [96.0, 100.0, 96.0, 92.0]
    camino += [92.0] * 16
    velas_4h, velas_15m = _velas(camino)

    # Simétrico de REJILLA_LONG: nada entre el calentamiento y el 100.
    niveles = _rejilla([100.0, 88.0, 86.0, 84.0, 70.0], INICIO)
    trades, _ = simular(velas_4h, velas_15m, niveles, _config_exp())

    assert len(trades) == 1, "se esperaba exactamente una operación"
    trade = trades.iloc[0]
    assert trade["direccion"] == "short"
    assert abs(trade["entrada"] - 100.0) < TOLERANCIA


def test_parciales_y_break_even_tras_el_segundo_objetivo() -> None:
    """Se cobran TP1 y TP2, el stop sube a BE y ahí cierra el resto."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    # Baja al nivel (100), sube cobrando 112 y 114, y vuelve a 100.
    camino += [104.0, 100.0, 106.0, 112.5, 114.5, 108.0, 100.0, 99.5]
    camino += [99.5] * 16
    velas_4h, velas_15m = _velas(camino)

    trades, _ = simular(velas_4h, velas_15m, _rejilla(REJILLA_LONG, INICIO),
                        _config_exp())

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["objetivos_alcanzados"] == 2
    assert trade["motivo_salida"] == "break_even"
    # 1/3 al 12% + 1/3 al 14% + 1/3 a cero, sin costes en esta config.
    esperado = (1 / 3) * 0.12 + (1 / 3) * 0.14
    assert abs(trade["pnl_pct"] - esperado) < 1e-6


def test_el_stop_gana_si_coincide_con_el_objetivo_en_la_misma_vela() -> None:
    """Convención pesimista: ante la duda dentro de una vela, salta el stop.

    Se construyen a mano velas de 15m con un recorrido amplio, de modo
    que una sola vela contenga el stop y el primer objetivo.
    """
    calentamiento = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    _, df_previo = _velas(calentamiento + [104.0, 100.0])

    # Vela que baja a 100 (entra), luego una que abarca de 90 a 120.
    ancha = pd.DataFrame(
        {
            "open": [100.0],
            "high": [120.0],
            "low": [90.0],
            "close": [110.0],
            "volume": [1.0],
        },
        index=pd.date_range(
            df_previo.index[-1] + pd.Timedelta("15min"),
            periods=1,
            freq="15min",
            name="timestamp",
        ),
    )
    cola = pd.DataFrame(
        {
            "open": 110.0, "high": 110.0, "low": 110.0,
            "close": 110.0, "volume": 1.0,
        },
        index=pd.date_range(
            ancha.index[-1] + pd.Timedelta("15min"),
            periods=32,
            freq="15min",
            name="timestamp",
        ),
    )
    velas_15m = pd.concat([df_previo, ancha, cola])
    velas_4h = _agregar_4h(velas_15m)

    trades, _ = simular(velas_4h, velas_15m, _rejilla(REJILLA_LONG, INICIO),
                        _config_exp())

    assert not trades.empty
    primero = trades.iloc[0]
    assert abs(primero["entrada"] - 100.0) < TOLERANCIA
    assert primero["motivo_salida"] == "stop"
    assert primero["objetivos_alcanzados"] == 0, (
        "la vela contenía el stop y TP1: debe ganar el stop"
    )
    assert primero["pnl_pct"] < 0


def test_el_cooldown_impide_reentrar_en_el_mismo_nivel() -> None:
    """Un nivel que el precio serrucha no genera una entrada por vela."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    # Sierra sobre el nivel 100: entra, sale por stop, vuelve a tocarlo.
    for _ in range(6):
        camino += [100.0, 97.0, 100.5, 97.0]
    camino += [97.0] * 16
    velas_4h, velas_15m = _velas(camino)
    niveles = _rejilla([100.0, 108.0, 116.0, 124.0, 130.0], INICIO)

    sin_cooldown, _ = simular(
        velas_4h, velas_15m, niveles, _config_exp(cooldown_velas_nivel=0)
    )
    con_cooldown, _ = simular(
        velas_4h, velas_15m, niveles, _config_exp(cooldown_velas_nivel=12)
    )
    assert len(con_cooldown) < len(sin_cooldown)


def test_los_costes_empeoran_el_resultado() -> None:
    """Con comisiones y deslizamiento, el mismo camino rinde menos."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    camino += [104.0, 100.0, 106.0, 112.5, 114.5, 116.5]
    camino += [116.5] * 16
    velas_4h, velas_15m = _velas(camino)
    niveles = _rejilla(REJILLA_LONG, INICIO)

    sin_costes, _ = simular(velas_4h, velas_15m, niveles, _config_exp())
    con_costes, _ = simular(
        velas_4h,
        velas_15m,
        niveles,
        _config_exp(
            comision_maker_pct=0.0002,
            comision_taker_pct=0.0005,
            slippage_stop_pct=0.0005,
        ),
    )
    assert len(sin_costes) == len(con_costes)
    assert con_costes["pnl_pct"].sum() < sin_costes["pnl_pct"].sum()
    # El primer trade es el long del nivel: mismo recorrido, menos neto.
    assert con_costes.iloc[0]["pnl_pct"] < sin_costes.iloc[0]["pnl_pct"]


def test_un_nivel_no_opera_antes_de_su_vigencia() -> None:
    """Antes de `confirmado_en`, el nivel no existe y no puede operarse."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    camino += [104.0, 100.0, 106.0, 112.0]
    camino += [112.0] * 16
    velas_4h, velas_15m = _velas(camino)

    vigente = _rejilla([100.0, 102.0, 104.0, 106.0, 130.0], velas_4h.index[0])
    futuro = _rejilla([100.0, 102.0, 104.0, 106.0, 130.0], velas_4h.index[-1])

    con_vigencia, _ = simular(velas_4h, velas_15m, vigente, _config_exp())
    sin_vigencia, _ = simular(velas_4h, velas_15m, futuro, _config_exp())

    assert not con_vigencia.empty
    assert sin_vigencia.empty


def test_una_posicion_a_la_vez() -> None:
    """Con el límite en 1, nunca hay dos posiciones solapadas."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    camino += [104.0, 100.0, 96.0, 92.0, 96.0, 100.0, 104.0]
    camino += [104.0] * 16
    velas_4h, velas_15m = _velas(camino)
    niveles = _rejilla([100.0, 96.0, 92.0, 108.0, 130.0], INICIO)

    trades, _ = simular(velas_4h, velas_15m, niveles, _config_exp())
    if len(trades) < 2:
        return
    ordenados = trades.sort_values("ts_entrada")
    solapes = ordenados["ts_entrada"].iloc[1:].to_numpy() < (
        ordenados["ts_salida"].iloc[:-1].to_numpy()
    )
    assert not solapes.any(), "hay posiciones solapadas con el límite en 1"


def test_las_posiciones_abiertas_se_cierran_al_agotar_el_historico() -> None:
    """Nada queda abierto al final: ocultarlo inflaría el resultado."""
    camino = _zigzag(110.0, 0.5, VELAS_CALENTAMIENTO)
    camino += [104.0, 100.0, 100.5, 101.0]
    velas_4h, velas_15m = _velas(camino)
    niveles = _rejilla([100.0, 106.0, 112.0, 118.0, 130.0], INICIO)

    trades, _ = simular(velas_4h, velas_15m, niveles, _config_exp())
    assert not trades.empty
    assert (trades["motivo_salida"] == "fin_historico").any()


# --------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------


def test_metricas_sobre_operaciones_conocidas() -> None:
    """El resumen cuadra con unos trades construidos a mano."""
    trades = pd.DataFrame(
        {
            "pnl_pct": [0.02, -0.01, 0.03],
            "pnl_r": [2.0, -1.0, 3.0],
            "capital_despues": [10200.0, 10098.0, 10401.0],
            "mae_pct": [-0.005, -0.01, -0.002],
            "mfe_pct": [0.02, 0.001, 0.03],
            "velas_15m": [10, 20, 30],
            "nivel": ["poc", "poc", "vah"],
        }
    )
    metricas = resumen(trades, 10000.0)
    assert metricas["operaciones"] == 3
    assert abs(metricas["acierto"] - 2 / 3) < 1e-6
    assert abs(metricas["r_total"] - 4.0) < TOLERANCIA
    assert abs(metricas["profit_factor"] - 5.0) < 1e-6

    por_nivel = desglose(trades, "nivel")
    assert abs(por_nivel.loc["poc", "r_total"] - 1.0) < TOLERANCIA
    assert abs(por_nivel.loc["vah", "r_total"] - 3.0) < TOLERANCIA


# --------------------------------------------------------------------
# Datos reales
# --------------------------------------------------------------------


def _cargar_reales() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict] | None:
    """Carga BTC de la caché, o ``None`` si no está descargada."""
    config = cargar_config()
    config.setdefault(
        "experimento_toques_frvp",
        _config_exp()["experimento_toques_frvp"],
    )
    symbol = "BTC/USD:USD"
    por_tf = {}
    for timeframe in ("4h", "1h", "15m"):
        ruta = _ruta_parquet(symbol, timeframe)
        if not ruta.exists():
            return None
        por_tf[timeframe] = pd.read_parquet(ruta)
    return por_tf["4h"], por_tf, config


def test_seleccion_causal_no_usa_el_futuro() -> None:
    """Truncar el histórico no cambia los rangos ya seleccionados."""
    datos = _cargar_reales()
    if datos is None:
        print("       (saltada: no hay caché en data/raw/)")
        return
    velas_4h, _, config = datos

    completo = seleccionar_causalmente(
        detectar_rangos_laterales(velas_4h, config), config
    )
    for fraccion in (0.5, 0.7, 0.9):
        corte = velas_4h.index[int(len(velas_4h) * fraccion)]
        truncado = seleccionar_causalmente(
            detectar_rangos_laterales(velas_4h.loc[:corte], config), config
        )
        antes = completo[completo["confirmado_en"] <= corte]
        claves_antes = set(zip(antes["ventana"], antes["inicio"], antes["fin"]))
        claves_truncado = set(
            zip(truncado["ventana"], truncado["inicio"], truncado["fin"])
        )
        assert claves_antes == claves_truncado, (
            f"la selección cambia al truncar en {fraccion:.0%}: "
            f"{len(claves_antes ^ claves_truncado)} rangos distintos"
        )


def test_sin_lookahead_backtest() -> None:
    """Truncar el histórico no cambia ningún trade cerrado antes del corte.

    Es la garantía crítica del MOTOR, equivalente a
    ``test_sin_lookahead_sobre_datos_reales`` en el detector: si falla,
    es que la simulación mira al futuro.

    Se corre con ``seleccion_rangos: "causal"`` a propósito. El modo
    ``"global"`` que usa el experimento elige los rangos con el
    histórico completo, así que truncar cambia la rejilla y la prueba
    no podría distinguir ese sesgo —conocido, declarado y medido en
    SPEC.md §8— de una fuga temporal del motor, que es lo que aquí se
    quiere aislar.
    """
    datos = _cargar_reales()
    if datos is None:
        print("       (saltada: no hay caché en data/raw/)")
        return
    velas_4h, por_tf, config = datos
    config = {**config, "experimento_toques_frvp": {
        **config["experimento_toques_frvp"], "seleccion_rangos": "causal",
    }}
    velas_15m = por_tf["15m"]

    crudos = detectar_rangos_laterales(velas_4h, config)
    niveles = construir_niveles(crudos, velas_4h, por_tf, config)
    completo, _ = simular(velas_4h, velas_15m, niveles, config)

    comparados = 0
    columnas = ["ts_entrada", "ts_salida", "entrada", "pnl_pct", "motivo_salida"]
    for fraccion in (0.5, 0.7, 0.9):
        corte = velas_4h.index[int(len(velas_4h) * fraccion)]
        v4 = velas_4h.loc[:corte]
        v15 = velas_15m.loc[:corte]
        crudos_t = detectar_rangos_laterales(v4, config)
        niveles_t = construir_niveles(crudos_t, v4, {
            tf: df.loc[:corte] for tf, df in por_tf.items()
        }, config)
        truncado, _ = simular(v4, v15, niveles_t, config)

        # Solo se comparan trades cerrados con margen antes del corte:
        # el truncado fuerza el cierre de lo que siguiera abierto.
        margen = corte - pd.Timedelta("4h")
        a = completo[completo["ts_salida"] <= margen][columnas]
        b = truncado[truncado["ts_salida"] <= margen][columnas]
        assert len(a) == len(b), (
            f"corte al {fraccion:.0%}: {len(a)} trades en el completo y "
            f"{len(b)} en el truncado"
        )
        for columna in columnas:
            iguales = a[columna].to_numpy() == b[columna].to_numpy()
            assert iguales.all(), (
                f"corte al {fraccion:.0%}: difiere la columna {columna}"
            )
        comparados += len(a)

    print(f"       ({comparados} trades comparados entre cortes)")


def test_niveles_reales_tienen_sentido() -> None:
    """Los niveles caen dentro del rango de precio de su propio tramo."""
    datos = _cargar_reales()
    if datos is None:
        print("       (saltada: no hay caché en data/raw/)")
        return
    velas_4h, por_tf, config = datos

    crudos = detectar_rangos_laterales(velas_4h, config)
    niveles = construir_niveles(crudos, velas_4h, por_tf, config)

    assert not niveles.empty, "no se construyó ningún nivel sobre datos reales"
    assert (niveles["vigente_desde"] > niveles["fin"]).all(), (
        "algún nivel es operable antes de conocerse el fin de su rango"
    )
    for fila in niveles.itertuples():
        tramo = velas_4h.loc[fila.inicio : fila.fin]
        assert tramo["low"].min() <= fila.precio <= tramo["high"].max(), (
            f"nivel {fila.nivel} fuera del recorrido de su tramo"
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
