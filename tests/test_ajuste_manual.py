"""Ajuste del Filtro 1 a los rangos trazados a mano (CLAUDE.md, SPEC.md §6).

El criterio de verdad del Filtro 1 son los rangos que el autor traza a
mano en TradingView, y lo que importa de ellos son las FECHAS de inicio
y fin, no los niveles de techo y suelo: sobre esas fechas se ancla el
FRVP. Este módulo mide esa coincidencia con el IoU temporal (intersección
partida por unión de los dos intervalos) y **fija la línea de base**:

    ajuste medio 0.900 sobre los rangos SELECCIONADOS, los 7 detectados

Es la medición que CLAUDE.md obliga a repetir antes y después de tocar el
detector. Un cambio que baje la media es un retroceso por muy bien que
arregle un caso suelto, y lo correcto entonces es revertir y anotar la
medición en SPEC.md §7 como alternativa descartada.

**Se mide sobre `seleccionar_rangos`, no sobre los crudos.** Los crudos
dan 0.928, pero es un número engañoso: con 116 candidatos casi siempre
hay uno que encaja, y no es ese el que se lleva al gráfico ni al FRVP. Lo
que se valida es lo que el sistema ELIGE, que son ~20.

No hay lookahead que vigilar aquí: esto no calcula ninguna señal, solo
compara la salida del detector con una lista de fechas fija.

    .venv\\Scripts\\python.exe tests\\test_ajuste_manual.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.range_detector import (  # noqa: E402
    detectar_rangos_laterales,
    seleccionar_rangos,
)
from data.loader import cargar_config, descargar_ohlcv  # noqa: E402

SIMBOLO_REFERENCIA = "BTC/USD:USD"

# Rangos laterales de BTC en 4h trazados a mano en TradingView. Se dieron
# DESPUÉS de fijar los parámetros del detector, que se habían calibrado
# contra otras 9 cajas distintas: funcionan como validación fuera de
# muestra, no como ajuste a medida (SPEC.md §6).
REFERENCIA_BTC: list[tuple[str, str]] = [
    ("2024-11-21", "2025-02-24"),
    ("2025-02-25", "2025-04-22"),
    ("2025-05-09", "2025-07-09"),
    ("2025-11-17", "2026-01-28"),
    ("2026-02-05", "2026-04-13"),
    ("2026-04-14", "2026-05-27"),
    ("2026-06-04", "2026-08-19"),
]

# Línea de base medida el 02/09/2026. La tolerancia absorbe el ruido de
# un histórico que crece con los días; no es margen para empeorar.
AJUSTE_MEDIO_BASE = 0.900
TOLERANCIA_AJUSTE = 0.005

# Por debajo de esto se considera que el rango manual NO se ha detectado.
IOU_MINIMO_DETECTADO = 0.30


def iou_temporal(
    inicio_a: pd.Timestamp,
    fin_a: pd.Timestamp,
    inicio_b: np.ndarray,
    fin_b: np.ndarray,
) -> np.ndarray:
    """Solape temporal de un intervalo contra un vector de intervalos.

    Intersección partida por unión, en segundos. Vale 1 si los dos
    intervalos coinciden exactamente y 0 si son disjuntos.

    Parameters
    ----------
    inicio_a, fin_a : pd.Timestamp
        Extremos del intervalo de referencia.
    inicio_b, fin_b : np.ndarray
        Extremos de los intervalos con los que comparar, como
        ``datetime64[ns]``.

    Returns
    -------
    np.ndarray
        Un IoU por cada intervalo de ``b``, entre 0 y 1.
    """
    a0 = np.datetime64(inicio_a.tz_localize(None))
    a1 = np.datetime64(fin_a.tz_localize(None))

    interseccion = np.minimum(a1, fin_b) - np.maximum(a0, inicio_b)
    union = np.maximum(a1, fin_b) - np.minimum(a0, inicio_b)

    segundos_inter = interseccion / np.timedelta64(1, "s")
    segundos_union = union / np.timedelta64(1, "s")

    return np.where(segundos_inter > 0.0, segundos_inter / segundos_union, 0.0)


def emparejar_con_referencia(
    rangos: pd.DataFrame, referencia: list[tuple[str, str]]
) -> pd.DataFrame:
    """Empareja cada rango trazado a mano con el detectado que más se le
    parece.

    El emparejamiento es por máximo solape, no por orden: un rango manual
    se queda con el candidato de mayor IoU, y un mismo candidato puede
    ganar dos manuales si el detector ha fundido dos cajas en una (es
    justo el fallo que se quiere ver reflejado en la media, no ocultar).

    Parameters
    ----------
    rangos : pd.DataFrame
        Salida de :func:`core.range_detector.seleccionar_rangos`, con
        columnas ``inicio``, ``fin`` y ``tipo``.
    referencia : list[tuple[str, str]]
        Pares ``(inicio, fin)`` en formato ``YYYY-MM-DD``.

    Returns
    -------
    pd.DataFrame
        Una fila por rango de referencia, con las fechas manuales, las
        del mejor candidato, su ``tipo``, el ``iou`` y el desfase en días
        de cada extremo. El candidato es ``NaT`` si no hay ninguno que
        solape.

    Raises
    ------
    ValueError
        Si ``rangos`` no trae las columnas esperadas.
    """
    faltantes = {"inicio", "fin", "tipo"} - set(rangos.columns)
    if faltantes:
        raise ValueError(f"faltan columnas en los rangos: {sorted(faltantes)}")

    # Sin tz para poder operar con datetime64 de numpy: los dos lados son
    # UTC, así que la comparación no cambia.
    inicios = rangos["inicio"].dt.tz_localize(None).to_numpy()
    fines = rangos["fin"].dt.tz_localize(None).to_numpy()

    filas = []
    for inicio_txt, fin_txt in referencia:
        manual_ini = pd.Timestamp(inicio_txt, tz="UTC")
        manual_fin = pd.Timestamp(fin_txt, tz="UTC")

        ious = (
            iou_temporal(manual_ini, manual_fin, inicios, fines)
            if len(rangos)
            else np.zeros(0)
        )
        mejor = int(ious.argmax()) if ious.size and ious.max() > 0.0 else None

        if mejor is None:
            filas.append(
                {
                    "manual_inicio": manual_ini,
                    "manual_fin": manual_fin,
                    "detectado_inicio": pd.NaT,
                    "detectado_fin": pd.NaT,
                    "tipo": "",
                    "iou": 0.0,
                    "desfase_inicio_dias": np.nan,
                    "desfase_fin_dias": np.nan,
                }
            )
            continue

        candidato = rangos.iloc[mejor]
        filas.append(
            {
                "manual_inicio": manual_ini,
                "manual_fin": manual_fin,
                "detectado_inicio": candidato["inicio"],
                "detectado_fin": candidato["fin"],
                "tipo": candidato["tipo"],
                "iou": float(ious[mejor]),
                "desfase_inicio_dias": (
                    candidato["inicio"] - manual_ini
                ).total_seconds() / 86400.0,
                "desfase_fin_dias": (
                    candidato["fin"] - manual_fin
                ).total_seconds() / 86400.0,
            }
        )

    return pd.DataFrame(filas)


def medir_ajuste(config: dict | None = None) -> pd.DataFrame:
    """Corre el Filtro 1 sobre BTC y lo compara con los rangos manuales.

    Lee de la caché en ``data/raw/``: no descarga nada si el parquet ya
    existe, así que medir es barato.

    Parameters
    ----------
    config : dict, optional
        Configuración ya cargada. Si se omite, se lee ``config.yaml``.

    Returns
    -------
    pd.DataFrame
        Salida de :func:`emparejar_con_referencia` sobre los rangos
        seleccionados.

    Raises
    ------
    OSError
        Si falla la lectura de la caché en parquet.
    """
    cfg = config if config is not None else cargar_config()
    velas = descargar_ohlcv(
        SIMBOLO_REFERENCIA, "4h", cfg["datos"]["historico_anios"]
    )
    crudos = detectar_rangos_laterales(velas, cfg)
    return emparejar_con_referencia(seleccionar_rangos(crudos, cfg), REFERENCIA_BTC)


def formatear(comparacion: pd.DataFrame) -> str:
    """Formatea la comparación como tabla para inspección a ojo.

    Parameters
    ----------
    comparacion : pd.DataFrame
        Salida de :func:`emparejar_con_referencia`.

    Returns
    -------
    str
        Tabla lista para imprimir, con la media al pie.
    """
    lineas = [
        "  #  rango trazado a mano       mejor candidato            tipo         IoU  desfase (d)",
        "  " + "-" * 88,
    ]
    for i, fila in enumerate(comparacion.itertuples(), 1):
        if pd.isna(fila.detectado_inicio):
            detectado = "SIN DETECTAR".ljust(25)
            desfase = "        —"
        else:
            detectado = (
                f"{fila.detectado_inicio:%Y-%m-%d} → {fila.detectado_fin:%Y-%m-%d}"
            )
            desfase = (
                f"{fila.desfase_inicio_dias:+.1f} / {fila.desfase_fin_dias:+.1f}"
            )
        lineas.append(
            f"  {i}  {fila.manual_inicio:%Y-%m-%d} → {fila.manual_fin:%Y-%m-%d}"
            f"    {detectado}  {fila.tipo:<11}  {fila.iou:.3f}  {desfase}"
        )

    detectados = int((comparacion["iou"] >= IOU_MINIMO_DETECTADO).sum())
    lineas.append("  " + "-" * 88)
    lineas.append(
        f"  ajuste medio {comparacion['iou'].mean():.3f}"
        f"   ({detectados} de {len(comparacion)} detectados)"
        f"   línea de base {AJUSTE_MEDIO_BASE:.3f}"
    )
    return "\n".join(lineas)


def test_iou_de_dos_intervalos_identicos_es_uno() -> None:
    """Dos intervalos iguales tienen IoU 1."""
    inicio = pd.Timestamp("2025-01-01", tz="UTC")
    fin = pd.Timestamp("2025-02-01", tz="UTC")
    valor = iou_temporal(
        inicio,
        fin,
        np.array([np.datetime64("2025-01-01")]),
        np.array([np.datetime64("2025-02-01")]),
    )
    assert valor[0] == 1.0


def test_iou_de_intervalos_disjuntos_es_cero() -> None:
    """Sin solape, el IoU es 0 y no negativo."""
    valor = iou_temporal(
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2025-02-01", tz="UTC"),
        np.array([np.datetime64("2025-03-01")]),
        np.array([np.datetime64("2025-04-01")]),
    )
    assert valor[0] == 0.0


def test_iou_de_solape_parcial_conocido() -> None:
    """Mitad solapada sobre el doble de longitud: IoU 1/3."""
    valor = iou_temporal(
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2025-01-03", tz="UTC"),
        np.array([np.datetime64("2025-01-02")]),
        np.array([np.datetime64("2025-01-04")]),
    )
    assert abs(valor[0] - 1.0 / 3.0) < 1e-9


def test_intervalo_contenido_da_la_razon_de_longitudes() -> None:
    """Un intervalo dentro de otro: IoU = corto / largo."""
    valor = iou_temporal(
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2025-01-05", tz="UTC"),
        np.array([np.datetime64("2025-01-02")]),
        np.array([np.datetime64("2025-01-03")]),
    )
    assert abs(valor[0] - 0.25) < 1e-9


def test_sin_candidatos_no_revienta_y_marca_sin_detectar() -> None:
    """Con la lista de rangos vacía, todo sale sin detectar."""
    vacio = pd.DataFrame(
        {
            "inicio": pd.Series(dtype="datetime64[ns, UTC]"),
            "fin": pd.Series(dtype="datetime64[ns, UTC]"),
            "tipo": pd.Series(dtype="object"),
        }
    )
    comparacion = emparejar_con_referencia(vacio, REFERENCIA_BTC)
    assert len(comparacion) == len(REFERENCIA_BTC)
    assert (comparacion["iou"] == 0.0).all()


def test_faltar_una_columna_es_un_error() -> None:
    """Prefiere fallar ruidosamente a medir sobre datos incompletos."""
    incompleto = pd.DataFrame({"inicio": [], "fin": []})
    try:
        emparejar_con_referencia(incompleto, REFERENCIA_BTC)
    except ValueError:
        return
    raise AssertionError("debería haber avisado de la columna que falta")


def test_los_siete_rangos_de_referencia_se_detectan() -> None:
    """Los 7 rangos trazados a mano tienen candidato en la selección."""
    comparacion = medir_ajuste()
    sin_detectar = comparacion[comparacion["iou"] < IOU_MINIMO_DETECTADO]
    assert sin_detectar.empty, (
        "rangos manuales sin candidato: "
        + ", ".join(
            f"{f.manual_inicio:%Y-%m-%d} (IoU {f.iou:.3f})"
            for f in sin_detectar.itertuples()
        )
    )


def test_el_ajuste_medio_no_baja_de_la_linea_de_base() -> None:
    """El ajuste medio sigue en 0.900 (CLAUDE.md).

    Si esta prueba falla tras tocar el detector, el cambio es un
    retroceso aunque arregle un caso concreto: revertir y anotar la
    medición en SPEC.md §7. No subir la tolerancia para que pase.
    """
    comparacion = medir_ajuste()
    medio = float(comparacion["iou"].mean())
    assert medio >= AJUSTE_MEDIO_BASE - TOLERANCIA_AJUSTE, (
        f"el ajuste medio ha bajado a {medio:.3f} desde la línea de base "
        f"{AJUSTE_MEDIO_BASE:.3f}"
    )


def main() -> int:
    """Imprime la comparación y ejecuta todas las pruebas del módulo."""
    print(f"\nAjuste del Filtro 1 a los rangos manuales — {SIMBOLO_REFERENCIA}\n")
    print(formatear(medir_ajuste()))
    print()

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
