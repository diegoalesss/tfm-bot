"""Filtro de operaciones aprendido, contra el filtro por reglas (SPEC.md §20).

PROBLEMA
--------
El filtro actual son tres reglas encadenadas —sin squeeze, ADX < 35,
impulso >= 1.0 ATR— elegidas midiendo una variable cada vez. Ese método
encuentra efectos individuales, pero no puede explorar las
COMBINACIONES: son 15 variables y probarlas a mano es inviable.

Es un problema de clasificación binaria supervisada: dado el estado del
mercado en el momento en que el precio toca un nivel del FRVP, ¿acabará
la operación en ganancia?

DISEÑO CONTRA EL SOBREAJUSTE
----------------------------
La muestra es pequeña (unos cientos de operaciones por activo), así que
todo el diseño va orientado a no engañarse:

1. `walk-forward`: se entrena con el 60% más ANTIGUO y se evalúa con el
   40% más reciente, que el modelo no ha visto. Nunca validación
   cruzada aleatoria, que en series temporales filtra el futuro.
2. `cruzado entre activos`: se entrena en BTC y se evalúa en ONDO, y al
   revés. Es la prueba dura: si el modelo aprendió algo general
   sobrevive, si memorizó BTC se hunde.
3. Modelos deliberadamente pequeños y regularizados, y una regresión
   logística como referencia: si el bosque no bate a un modelo lineal,
   la complejidad no se justifica.
4. Se compara SIEMPRE contra dos referencias en el mismo conjunto de
   prueba: operar todo sin filtrar, y el filtro por reglas actual.

SIN LOOKAHEAD
-------------
Las variables de cada operación se toman de la vela de 4h que estaba
CERRADA cuando se colocó la orden, que es la misma que usa `simular`
para decidir. La etiqueta viene del resultado, que es futuro por
definición, pero solo se usa para entrenar con datos anteriores al
tramo de evaluación.

Uso:
    .venv\\Scripts\\python.exe experiments\\exp_ia_filtro.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from core.imbalances import detectar_imbalances  # noqa: E402
from core.levels import construir_niveles  # noqa: E402
from core.momentum import divergencia_vigente, divergencias, macd  # noqa: E402
from core.osciladores import adx, bollinger, estocastico, fase_ttm, momento_ttm, squeeze  # noqa: E402
from core.range_detector import _atr, detectar_rangos_laterales  # noqa: E402
from data.loader import TIMEFRAMES_FRVP, cargar_config, descargar_ohlcv  # noqa: E402
from execution.backtest import simular  # noqa: E402

ACTIVOS: dict[str, str] = {"BTC": "BTC/USD:USD", "ONDO": "ONDO/USD:USD"}
CORTE_ENTRENO = 0.60  # proporción más antigua que se usa para entrenar
SEMILLA = 7

# Variables que ve el modelo. Todas se calculan con velas ya cerradas.
COLUMNAS: tuple[str, ...] = (
    "impulso", "adx", "di_orientado", "squeeze", "velas_squeeze",
    "estocastico_orientado", "momento_ttm_orientado", "fase_favorable",
    "bb_ancho_pct", "atr_relativo", "divergencia_favor",
    "calidad", "confluencia", "r_potencial", "riesgo_pct",
    "es_long", "es_poc", "es_vah", "es_val",
)


def _sin_filtros(config: dict) -> dict:
    """Copia de la configuración con los filtros de régimen apagados.

    Hace falta para construir el conjunto de entrenamiento: el modelo
    tiene que ver también las operaciones que las reglas descartan, o
    solo aprendería sobre las que ya pasan y no habría nada que comparar.
    """
    cfg = copy.deepcopy(config)
    exp = cfg["experimento_toques_frvp"]
    exp["adx_maximo"] = None
    exp["evitar_squeeze"] = False
    exp["impulso_minimo_atr"] = None
    exp["score_minimo_convergencia"] = None
    # El tope de capital sin apalancamiento limita las posiciones
    # simultáneas. Bajar el riesgo por operación deja abrir más y no
    # altera el resultado en R, que va normalizado por ese mismo riesgo.
    exp["riesgo_por_operacion_pct"] = 0.001
    return cfg


def construir_muestra(
    symbol: str, config: dict, filtrar: bool = False
) -> pd.DataFrame:
    """Genera las operaciones con sus variables y su etiqueta.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    config : dict
        Configuración cargada de ``config.yaml``.
    filtrar : bool, optional
        Si es ``False`` (por defecto) se apagan los filtros de régimen,
        para que el modelo vea también lo que las reglas descartan. Si
        es ``True`` se simula el sistema tal cual opera, que es lo que
        hace falta para decidir el TAMAÑO de posición.

    Returns
    -------
    pd.DataFrame
        Una fila por operación, con las columnas de `COLUMNAS`, el
        resultado en R (`pnl_r`), la etiqueta binaria (`gana`) y las
        marcas necesarias para reproducir el filtro por reglas.
    """
    tfs = set(TIMEFRAMES_FRVP) | {"4h", "15m", config["imbalances"]["timeframe"]}
    por_tf = {tf: descargar_ohlcv(symbol, tf, 2) for tf in tfs}
    v4 = por_tf["4h"]
    exp_original = config["experimento_toques_frvp"]

    crudos = detectar_rangos_laterales(v4, config)
    niveles = construir_niveles(crudos, v4, por_tf, config)
    imbalances = (
        detectar_imbalances(por_tf[config["imbalances"]["timeframe"]])
        if exp_original.get("usar_imbalances_como_objetivo", False)
        else None
    )

    usada = config if filtrar else _sin_filtros(config)
    trades, _ = simular(v4, por_tf["15m"], niveles, usada, imbalances)
    if trades.empty:
        return trades

    # --- indicadores sobre las velas de decisión ---
    atr = _atr(v4, 14)
    ind = pd.DataFrame(index=v4.index)
    ind["impulso"] = (
        v4["close"] - v4["close"].shift(int(exp_original["velas_impulso"]))
    ).abs() / atr
    tabla_adx = adx(v4, 14)
    ind["adx"] = tabla_adx["adx"]
    ind["di_dif"] = tabla_adx["di_mas"] - tabla_adx["di_menos"]
    tabla_sqz = squeeze(v4, 20, 2.0, 1.5)
    ind["squeeze"] = tabla_sqz["activo"].fillna(False).astype(float)
    # Velas que lleva el squeeze en su estado actual: distingue una
    # compresión recién empezada de una a punto de romper.
    ind["velas_squeeze"] = tabla_sqz["velas_en_estado"]
    ind["estocastico"] = estocastico(v4, 14, 3)
    ttm = momento_ttm(v4, 20)
    ind["ttm"] = ttm
    ind["fase"] = fase_ttm(ttm)
    bb = bollinger(v4["close"], 20, 2.0)
    # Percentil de la anchura en su propio histórico: un régimen de
    # volatilidad comparable entre activos con precios muy distintos.
    ind["bb_ancho_pct"] = bb["anchura"].rolling(120, min_periods=30).rank(pct=True)
    ind["atr_relativo"] = atr / v4["close"]
    # Misma llamada que hace `simular`, para que el modelo vea
    # exactamente la señal que ve la estrategia.
    ind["divergencia"] = divergencia_vigente(
        divergencias(v4["close"], macd(v4["close"])["histograma"]),
        int(exp_original.get("velas_vigencia_divergencia", 12)),
    )

    # --- vela de decisión de cada operación, sin mirar al futuro ---
    idx4 = pd.DatetimeIndex(v4.index).values
    entrada_en = np.searchsorted(
        idx4, pd.DatetimeIndex(trades["ts_entrada"]).values, side="right"
    ) - 1
    # La orden se coloca en la vela ANTERIOR a aquella en la que se
    # ejecuta: es la última cerrada cuando se decide.
    decision = np.clip(entrada_en - 1, 0, len(v4) - 1)
    val = ind.iloc[decision].reset_index(drop=True)

    m = pd.DataFrame(index=range(len(trades)))
    signo = np.where(trades["direccion"].to_numpy() == "long", 1.0, -1.0)
    m["impulso"] = val["impulso"].to_numpy()
    m["adx"] = val["adx"].to_numpy()
    # Orientadas al lado de la operación: un DI+ alto favorece al long y
    # perjudica al short, así que el signo tiene que entrar en la variable.
    m["di_orientado"] = val["di_dif"].to_numpy() * signo
    m["squeeze"] = val["squeeze"].to_numpy()
    m["velas_squeeze"] = val["velas_squeeze"].to_numpy()
    m["estocastico_orientado"] = np.where(
        signo > 0, val["estocastico"].to_numpy(), 100.0 - val["estocastico"].to_numpy()
    )
    m["momento_ttm_orientado"] = val["ttm"].to_numpy() * signo
    favorable = np.where(
        signo > 0, val["fase"].to_numpy() == "bajista_debil",
        val["fase"].to_numpy() == "alcista_debil",
    )
    m["fase_favorable"] = favorable.astype(float)
    m["bb_ancho_pct"] = val["bb_ancho_pct"].to_numpy()
    m["atr_relativo"] = val["atr_relativo"].to_numpy()
    esperada = np.where(signo > 0, "alcista", "bajista")
    m["divergencia_favor"] = (val["divergencia"].to_numpy() == esperada).astype(float)

    for col in ("calidad", "confluencia", "r_potencial", "riesgo_pct"):
        m[col] = trades[col].to_numpy(float)
    m["es_long"] = (signo > 0).astype(float)
    for nombre in ("poc", "vah", "val"):
        m[f"es_{nombre}"] = (trades["nivel"].to_numpy() == nombre).astype(float)

    m["ts"] = trades["ts_entrada"].to_numpy()
    m["score"] = trades["score"].to_numpy(int)
    m["pnl_r"] = trades["pnl_r"].to_numpy(float)
    m["gana"] = (m["pnl_r"] > 0).astype(int)
    # Reproduce el filtro por reglas para poder compararlo en el mismo
    # conjunto de prueba.
    m["pasa_reglas"] = (
        (m["squeeze"] == 0)
        & (m["adx"] < float(exp_original["adx_maximo"]))
        & (m["impulso"] >= float(exp_original["impulso_minimo_atr"]))
    )
    return m.sort_values("ts").reset_index(drop=True)


def _modelos() -> dict:
    """Los dos modelos que se comparan, ambos deliberadamente pequeños."""
    return {
        "regresión logística": make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.1, max_iter=2000, random_state=SEMILLA),
        ),
        "gradient boosting": HistGradientBoostingClassifier(
            max_depth=3,
            max_iter=120,
            learning_rate=0.05,
            min_samples_leaf=25,
            l2_regularization=1.0,
            random_state=SEMILLA,
        ),
    }


def _evaluar(prueba: pd.DataFrame, acepta: np.ndarray, etiqueta: str) -> dict:
    """Resume el rendimiento de un criterio de aceptación."""
    sel = prueba[acepta]
    if sel.empty:
        return {"criterio": etiqueta, "ops": 0, "R": np.nan,
                "acierto": np.nan, "PF": np.nan}
    ganancias = sel.loc[sel["pnl_r"] > 0, "pnl_r"].sum()
    perdidas = -sel.loc[sel["pnl_r"] < 0, "pnl_r"].sum()
    return {
        "criterio": etiqueta,
        "ops": len(sel),
        "R": sel["pnl_r"].mean(),
        "acierto": (sel["pnl_r"] > 0).mean(),
        "PF": ganancias / perdidas if perdidas > 0 else np.inf,
    }


def _tabla(filas: list[dict]) -> str:
    t = pd.DataFrame(filas)
    t["R"] = t["R"].map(lambda v: f"{v:+.3f}" if pd.notna(v) else "—")
    t["acierto"] = t["acierto"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "—")
    t["PF"] = t["PF"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
    return "    " + t.to_string(index=False).replace("\n", "\n    ")


def main() -> None:
    """Entrena, valida walk-forward y cruzado, e imprime la comparación."""
    config = cargar_config()
    muestras = {n: construir_muestra(s, config) for n, s in ACTIVOS.items()}

    print("=" * 88)
    print("FILTRO APRENDIDO CONTRA FILTRO POR REGLAS")
    print("=" * 88)
    for nombre, m in muestras.items():
        print(f"  {nombre}: {len(m)} operaciones sin filtrar,"
              f" {m['gana'].mean():.1%} ganadoras, R medio {m['pnl_r'].mean():+.3f}")

    # ---------- 1. walk-forward dentro de cada activo ----------
    print("\n" + "=" * 88)
    print(f"1. WALK-FORWARD  (entrena con el {CORTE_ENTRENO:.0%} más antiguo,"
          f" evalúa con el resto)")
    print("=" * 88)
    for nombre, m in muestras.items():
        corte = int(len(m) * CORTE_ENTRENO)
        entreno, prueba = m.iloc[:corte], m.iloc[corte:]
        print(f"\n  {nombre}   entreno {len(entreno)} ops"
              f" ({entreno['ts'].min():%Y-%m-%d} → {entreno['ts'].max():%Y-%m-%d})"
              f"   prueba {len(prueba)} ops"
              f" ({prueba['ts'].min():%Y-%m-%d} → {prueba['ts'].max():%Y-%m-%d})")

        filas = [
            _evaluar(prueba, np.ones(len(prueba), bool), "sin filtrar"),
            _evaluar(prueba, prueba["pasa_reglas"].to_numpy(), "reglas (actual)"),
        ]
        x_ent = entreno[list(COLUMNAS)].to_numpy(float)
        x_pru = prueba[list(COLUMNAS)].to_numpy(float)
        for etiqueta, modelo in _modelos().items():
            modelo.fit(x_ent, entreno["gana"].to_numpy())
            prob = modelo.predict_proba(x_pru)[:, 1]
            # El umbral se fija en el entrenamiento, no en la prueba: se
            # queda con el mismo porcentaje de operaciones que dejan
            # pasar las reglas, para que la comparación sea a igualdad
            # de selectividad.
            cuota = float(entreno["pasa_reglas"].mean())
            umbral = np.quantile(modelo.predict_proba(x_ent)[:, 1], 1.0 - cuota)
            filas.append(_evaluar(prueba, prob >= umbral, etiqueta))
        print(_tabla(filas))

    # ---------- 2. cruzado entre activos ----------
    print("\n" + "=" * 88)
    print("2. CRUZADO ENTRE ACTIVOS  (entrena en uno, evalúa en el otro entero)")
    print("=" * 88)
    for origen, destino in (("BTC", "ONDO"), ("ONDO", "BTC")):
        entreno, prueba = muestras[origen], muestras[destino]
        print(f"\n  entrena {origen} ({len(entreno)}) → evalúa {destino}"
              f" ({len(prueba)})")
        filas = [
            _evaluar(prueba, np.ones(len(prueba), bool), "sin filtrar"),
            _evaluar(prueba, prueba["pasa_reglas"].to_numpy(), "reglas (actual)"),
        ]
        x_ent = entreno[list(COLUMNAS)].to_numpy(float)
        x_pru = prueba[list(COLUMNAS)].to_numpy(float)
        for etiqueta, modelo in _modelos().items():
            modelo.fit(x_ent, entreno["gana"].to_numpy())
            cuota = float(entreno["pasa_reglas"].mean())
            umbral = np.quantile(modelo.predict_proba(x_ent)[:, 1], 1.0 - cuota)
            filas.append(
                _evaluar(prueba, modelo.predict_proba(x_pru)[:, 1] >= umbral, etiqueta)
            )
        print(_tabla(filas))

    # ---------- 3. qué variables usa el modelo ----------
    print("\n" + "=" * 88)
    print("3. QUÉ MIRA EL MODELO  (coeficientes de la logística, activos juntos)")
    print("=" * 88)
    junto = pd.concat(muestras.values(), ignore_index=True)
    lin = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.1, max_iter=2000, random_state=SEMILLA),
    )
    lin.fit(junto[list(COLUMNAS)].to_numpy(float), junto["gana"].to_numpy())
    coef = pd.Series(
        lin[-1].coef_[0], index=COLUMNAS
    ).sort_values(key=np.abs, ascending=False)
    for var, c in coef.items():
        signo = "favorable" if c > 0 else "adversa"
        print(f"    {var:<24} {c:+.3f}   {signo}")


if __name__ == "__main__":
    main()
