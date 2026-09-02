"""Script de inspección manual del detector de rango lateral (Filtro 1).

No es una prueba unitaria automática (esas están en
``tests/test_range_detector.py``) ni mide el ajuste a los rangos trazados
a mano (eso es ``tests/test_ajuste_manual.py``): imprime todos los rangos
detectados sobre los símbolos configurados, con sus métricas, para mirar
a ojo qué está devolviendo el detector y cuánto poda la selección.

Usa la caché en ``data/raw/``: no descarga nada si el parquet ya existe.

    .venv\\Scripts\\python.exe tests\\test_range_detector_manual.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.range_detector import (  # noqa: E402
    detectar_rangos_laterales,
    seleccionar_rangos,
)
from data.loader import cargar_config, descargar_ohlcv  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

COLUMNAS_VISTA = [
    "ventana", "tipo", "inicio", "fin", "duracion", "techo", "suelo",
    "altura_pct", "calidad", "contencion", "toques_techo", "toques_suelo",
    "declarado_en", "confirmado_en", "en_curso", "grupo_solape",
]


def _con_metricas(rangos: pd.DataFrame, velas: pd.DataFrame) -> pd.DataFrame:
    """Añade duración en velas y altura porcentual a cada rango.

    Parameters
    ----------
    rangos : pd.DataFrame
        Salida de :func:`core.range_detector.detectar_rangos_laterales`.
    velas : pd.DataFrame
        Velas de 4h sobre las que se detectaron.

    Returns
    -------
    pd.DataFrame
        Copia de ``rangos`` con las columnas ``duracion`` y
        ``altura_pct``.
    """
    vista = rangos.copy()
    # searchsorted sobre el índice evita un .loc por fila: la duración es
    # la diferencia entre las posiciones de `fin` e `inicio`.
    posicion_inicio = velas.index.searchsorted(vista["inicio"])
    posicion_fin = velas.index.searchsorted(vista["fin"])
    vista["duracion"] = posicion_fin - posicion_inicio + 1
    vista["altura_pct"] = (vista["techo"] - vista["suelo"]) / vista["suelo"] * 100
    return vista


def _inspeccionar(symbol: str, config: dict) -> None:
    """Detecta e imprime los rangos laterales de un símbolo.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    config : dict
        Configuración cargada de ``config.yaml``.

    Raises
    ------
    OSError
        Si falla la lectura de la caché en parquet.
    """
    velas = descargar_ohlcv(
        symbol,
        config["datos"]["timeframe_decision"],
        config["datos"]["historico_anios"],
        forzar=False,
    )
    rangos = detectar_rangos_laterales(velas, config)
    elegidos = seleccionar_rangos(rangos, config)

    separador = "=" * 78
    print(f"\n{separador}")
    print(
        f"{symbol}: {len(rangos)} rangos detectados,"
        f" {len(elegidos)} tras la selección"
    )
    print(separador)
    if rangos.empty:
        return

    vista = _con_metricas(rangos, velas)
    with pd.option_context("display.width", 240, "display.max_columns", None):
        print(vista[COLUMNAS_VISTA].to_string(index=False))

    tamanos = vista.groupby("grupo_solape").size()
    print(
        f"\n{(tamanos > 1).sum()} grupos con solape, "
        f"de {len(tamanos)} grupos en total"
    )

    print()
    print(
        vista.groupby("tipo")
        .agg(
            n_rangos=("inicio", "size"),
            duracion_mediana=("duracion", "median"),
            altura_pct_mediana=("altura_pct", "median"),
            calidad_mediana=("calidad", "median"),
            contencion_mediana=("contencion", "median"),
        )
        .to_string()
    )

    print("\n  SELECCIONADOS")
    seleccion = _con_metricas(elegidos, velas)
    print(
        seleccion[["ventana", "tipo", "inicio", "fin", "duracion", "calidad"]]
        .to_string(index=False)
    )


def main() -> None:
    """Inspecciona los rangos laterales de los símbolos configurados."""
    config = cargar_config()
    for symbol in config["datos"]["simbolos"]:
        _inspeccionar(symbol, config)


if __name__ == "__main__":
    main()
