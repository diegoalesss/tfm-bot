"""Calidad de la ENTRADA, aislada de la gestión de salida (SPEC.md §18).

Todo lo medido en los experimentos anteriores usa `pnl_r`, que mezcla
la entrada con el stop y los objetivos. Este script separa las dos
cosas: partiendo del precio de entrada y SIN stop ni objetivos, sigue
el precio N velas de 4h y anota

    MFE          máxima excursión a favor
    MAE          máxima excursión en contra
    eficiencia   MFE / (MFE + MAE),  0.5 = moneda al aire

Todo en unidades de ATR de la vela de entrada, para que activos con
volatilidad muy distinta sean comparables.

El valor absoluto del MFE no informa —crece solo con el horizonte—, así
que se compara con dos controles:

  aleatorio   fechas al azar de la misma época, mismo reparto long/short
  rechazado   toques de los mismos niveles que los filtros tumbaron

El segundo es el control bueno: aísla lo que aportan los filtros de lo
que aporta el nivel del FRVP.

Sin lookahead: la medición es DESCRIPTIVA y posterior al backtest. El
MFE mira velas futuras a propósito, porque es un techo teórico con el
que comparar lo capturado; ninguna decisión de la estrategia lo usa.

Uso:
    .venv\\Scripts\\python.exe experiments\\exp_calidad_entrada.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.levels import construir_niveles
from core.osciladores import adx, squeeze
from core.range_detector import _atr, detectar_rangos_laterales
from data.loader import cargar_config, descargar_ohlcv

HORIZONTES: tuple[int, ...] = (6, 12, 24, 48)
HORIZONTE_PRINCIPAL = 24
REMUESTREOS = 400
ACTIVOS: dict[str, str] = {"BTC": "BTC/USD:USD", "ONDO": "ONDO/USD:USD"}
RESULTADOS = Path(__file__).resolve().parent / "resultados"


def excursiones(
    high: np.ndarray,
    low: np.ndarray,
    atr: np.ndarray,
    posiciones: np.ndarray,
    signo: np.ndarray,
    precio: np.ndarray,
    horizonte: int = HORIZONTE_PRINCIPAL,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calcula MFE, MAE y eficiencia en unidades de ATR.

    Parameters
    ----------
    high, low : np.ndarray
        Máximos y mínimos de las velas de decisión.
    atr : np.ndarray
        ATR de esas mismas velas, usado como unidad de medida.
    posiciones : np.ndarray
        Índice de la vela en la que arranca cada medición.
    signo : np.ndarray
        +1 para long, -1 para short.
    precio : np.ndarray
        Precio de referencia desde el que se miden las excursiones.
    horizonte : int
        Número de velas que se siguen hacia delante.

    Returns
    -------
    tuple of np.ndarray
        MFE, MAE y eficiencia, con un elemento por posición.

    Notes
    -----
    El bucle recorre operaciones, no filas de un DataFrame: cada una
    necesita una ventana de longitud distinta según dónde caiga, y son
    unos cientos de elementos. Es la excepción que contempla CLAUDE.md.
    """
    mfe = np.full(len(posiciones), np.nan)
    mae = np.full(len(posiciones), np.nan)
    for n, (p, s, x) in enumerate(zip(posiciones, signo, precio)):
        tramo_alto = high[p : p + horizonte]
        tramo_bajo = low[p : p + horizonte]
        if tramo_alto.size == 0:
            continue
        mfe[n] = (tramo_alto.max() - x) if s > 0 else (x - tramo_bajo.min())
        mae[n] = (x - tramo_bajo.min()) if s > 0 else (tramo_alto.max() - x)

    unidad = atr[np.clip(posiciones, 0, len(atr) - 1)]
    mfe, mae = mfe / unidad, mae / unidad
    eficiencia = mfe / np.maximum(mfe + np.maximum(mae, 0.0), 1e-9)
    return mfe, mae, eficiencia


def _contexto(symbol: str, cfg: dict) -> dict:
    """Carga velas, niveles e indicadores de régimen de un activo."""
    por_tf = {tf: descargar_ohlcv(symbol, tf, 2) for tf in ("4h", "1h", "15m", "1w")}
    v4 = por_tf["4h"]
    niveles = construir_niveles(detectar_rangos_laterales(v4, cfg), v4, por_tf, cfg)
    exp = cfg["experimento_toques_frvp"]
    atr = _atr(v4, 14)
    return {
        "velas": v4,
        "niveles": niveles,
        "atr": atr.to_numpy(),
        "high": v4["high"].to_numpy(),
        "low": v4["low"].to_numpy(),
        "close": v4["close"].to_numpy(),
        "impulso": (
            (v4["close"] - v4["close"].shift(int(exp["velas_impulso"]))).abs() / atr
        ).to_numpy(),
        "adx": adx(v4, 14)["adx"].to_numpy(),
        "squeeze": squeeze(v4, 20, 2.0, 1.5)["activo"].fillna(False).to_numpy(bool),
        "exp": exp,
    }


def toques_posibles(ctx: dict) -> pd.DataFrame:
    """Enumera todo toque de un nivel vigente, pase o no los filtros.

    Returns
    -------
    pd.DataFrame
        Columnas ``pos`` (vela del toque), ``signo``, ``precio`` y
        ``pasa`` (si superaba los filtros de régimen e impulso).
    """
    v4, exp = ctx["velas"], ctx["exp"]
    idx4 = pd.DatetimeIndex(v4.index).values
    alta = np.searchsorted(
        idx4, pd.DatetimeIndex(ctx["niveles"]["vigente_desde"]).values
    )
    precios = ctx["niveles"]["precio"].to_numpy(float)
    high, low, close = ctx["high"], ctx["low"], ctx["close"]

    filas: list[tuple[int, int, float, bool]] = []
    for i in range(len(v4) - 1):
        # El lado lo fija el cierre de la vela YA CERRADA, igual que en
        # el backtest: si el precio está por encima del nivel llegará
        # desde arriba (long) y si está por debajo, desde abajo (short).
        pasa = bool(
            (not ctx["squeeze"][i])
            and (ctx["adx"][i] < exp["adx_maximo"])
            and (ctx["impulso"][i] >= exp["impulso_minimo_atr"])
        )
        for k in np.flatnonzero(alta <= i):
            p = precios[k]
            if p == close[i] or not (low[i + 1] <= p <= high[i + 1]):
                continue
            filas.append((i + 1, 1 if close[i] > p else -1, float(p), pasa))

    return pd.DataFrame(filas, columns=["pos", "signo", "precio", "pasa"])


def calidad_de_las_ejecutadas(ctx: dict, ruta: Path) -> pd.DataFrame:
    """MFE/MAE por horizonte de las operaciones realmente abiertas."""
    trades = pd.read_csv(ruta, parse_dates=["ts_entrada"])
    idx4 = pd.DatetimeIndex(ctx["velas"].index).values
    pos = np.searchsorted(idx4, pd.DatetimeIndex(trades["ts_entrada"]).values)
    signo = np.where(trades["direccion"].to_numpy() == "long", 1, -1)
    entrada = trades["entrada"].to_numpy(float)

    salida = trades[["ts_entrada", "direccion", "nivel", "score", "pnl_r"]].copy()
    for h in HORIZONTES:
        mfe, mae, ef = excursiones(
            ctx["high"], ctx["low"], ctx["atr"], pos, signo, entrada, h
        )
        salida[f"mfe_{h}"], salida[f"mae_{h}"], salida[f"ef_{h}"] = mfe, mae, ef
    return salida


def _linea(etiqueta: str, n: int, mfe, mae, ef) -> str:
    return (f"  {etiqueta:<30} n={n:>5}  MFE {np.nanmean(mfe):>5.2f}"
            f"  MAE {np.nanmean(mae):>5.2f}  eficiencia {np.nanmean(ef):>6.3f}")


def main() -> None:
    """Ejecuta la medición completa sobre todos los activos."""
    cfg = cargar_config()
    rng = np.random.default_rng(7)

    for nombre, symbol in ACTIVOS.items():
        ctx = _contexto(symbol, cfg)
        toques = toques_posibles(ctx)
        aceptados = toques[toques["pasa"]]
        rechazados = toques[~toques["pasa"]]

        print("\n" + "=" * 92)
        print(f"{nombre}  —  horizonte {HORIZONTE_PRINCIPAL} velas de 4h")
        print("=" * 92)

        for etiqueta, sub in (
            ("ACEPTADO por los filtros", aceptados),
            ("RECHAZADO por los filtros", rechazados),
        ):
            mfe, mae, ef = excursiones(
                ctx["high"], ctx["low"], ctx["atr"],
                sub["pos"].to_numpy(), sub["signo"].to_numpy(),
                sub["precio"].to_numpy(),
            )
            print(_linea(etiqueta, len(sub), mfe, mae, ef))

        # Control aleatorio: misma época y mismo reparto de lados.
        prop_long = float((aceptados["signo"] > 0).mean())
        validos = np.arange(20, len(ctx["velas"]) - HORIZONTE_PRINCIPAL)
        muestras = np.empty((REMUESTREOS, 3))
        for m in range(REMUESTREOS):
            pos = rng.choice(validos, size=len(aceptados), replace=True)
            signo = np.where(rng.random(len(pos)) < prop_long, 1, -1)
            mfe, mae, ef = excursiones(
                ctx["high"], ctx["low"], ctx["atr"], pos, signo, ctx["close"][pos]
            )
            muestras[m] = (np.nanmean(mfe), np.nanmean(mae), np.nanmean(ef))
        print(_linea(f"ALEATORIO ({REMUESTREOS} remuestreos)", len(aceptados),
                     muestras[:, 0], muestras[:, 1], muestras[:, 2]))

        _, _, ef_real = excursiones(
            ctx["high"], ctx["low"], ctx["atr"],
            aceptados["pos"].to_numpy(), aceptados["signo"].to_numpy(),
            aceptados["precio"].to_numpy(),
        )
        pct = float((muestras[:, 2] < np.nanmean(ef_real)).mean())
        print(f"  {'':<30}       supera al {pct:.1%} de los remuestreos")

        # --- operaciones realmente ejecutadas ---
        fichero = RESULTADOS / f"trades_{symbol.replace('/', '-').replace(':', '-')}.csv"
        if not fichero.exists():
            print(f"\n  (sin {fichero.name}: ejecuta antes exp_toques_frvp.py)")
            continue
        t = calidad_de_las_ejecutadas(ctx, fichero)
        h = HORIZONTE_PRINCIPAL
        print(f"\n  EJECUTADAS  n={len(t)}  MFE {t[f'mfe_{h}'].mean():.2f}"
              f"  MAE {t[f'mae_{h}'].mean():.2f}"
              f"  eficiencia {t[f'ef_{h}'].mean():.3f}"
              f"  R real {t['pnl_r'].mean():+.3f}")

        # El stop está a 1.5 ATR por construcción, así que MFE/1.5 es el
        # R máximo que la operación llegó a ofrecer.
        print("\n  cuánto se deja sobre la mesa:")
        for hz in (12, 24, 48):
            ofrecido = (t[f"mfe_{hz}"] / 1.5).mean()
            captura = t["pnl_r"].mean() / max(ofrecido, 1e-9)
            print(f"    h={hz:>2}  ofrecido {ofrecido:>5.2f} R"
                  f"   capturado {t['pnl_r'].mean():>5.2f} R   ({captura:>5.1%})")

        print("\n  distribución del recorrido ofrecido:")
        for umbral in (0.5, 1.0, 1.5, 2.0, 3.0):
            cuota = float(((t[f"mfe_{h}"] / 1.5) >= umbral).mean())
            print(f"    >= {umbral:>3.1f} R : {cuota:>6.1%}")

        for clave in ("direccion", "nivel", "score"):
            g = t.groupby(clave).agg(
                n=("pnl_r", "size"),
                mfe=(f"mfe_{h}", "mean"),
                mae=(f"mae_{h}", "mean"),
                eficiencia=(f"ef_{h}", "mean"),
                r_real=("pnl_r", "mean"),
            )
            print(f"\n  por {clave}:")
            print("    " + g.round(3).to_string().replace("\n", "\n    "))


if __name__ == "__main__":
    main()
