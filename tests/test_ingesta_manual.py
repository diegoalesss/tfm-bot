"""Script de prueba manual del módulo de ingesta de datos.

No es una prueba unitaria automática (pytest no ejecuta ``main()``
al recolectar este fichero): descarga datos reales de Kraken Futures
y puede tardar varios minutos, por lo que se ejecuta a mano::

    .venv\\Scripts\\python.exe tests\\test_ingesta_manual.py

Descarga ONDO/USD:USD y BTC/USD:USD en los timeframes de decisión
(4h) y de construcción del FRVP (15m), los valida y muestra el
informe de calidad de cada combinación símbolo/timeframe.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.loader import cargar_config, descargar_watchlist  # noqa: E402
from data.validator import validar_ohlcv  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


def main() -> None:
    """Descarga, valida e imprime el informe de calidad de la watchlist.

    Raises
    ------
    ccxt.BaseError
        Si alguna descarga falla de forma no recuperable.
    """
    config = cargar_config()
    datos = descargar_watchlist(config)

    for symbol, por_timeframe in datos.items():
        for timeframe, df in por_timeframe.items():
            informe = validar_ohlcv(df, symbol, timeframe)
            print(informe.resumen())
            print()


if __name__ == "__main__":
    main()
