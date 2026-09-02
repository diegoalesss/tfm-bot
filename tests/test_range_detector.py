"""Pruebas del detector de rango lateral (Filtro 1, SPEC.md §5).

Se escriben como funciones ``test_*`` con ``assert``, así que las
recoge ``pytest`` si algún día se instala, pero el fichero también se
ejecuta tal cual con el intérprete del entorno virtual, que es como
funciona el resto de scripts del proyecto::

    .venv\\Scripts\\python.exe tests\\test_range_detector.py

Las pruebas de datos reales usan la caché de ``data/raw/`` y se
saltan solas si no existe: no descargan nada.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.range_detector import (  # noqa: E402
    _nivel_por_extremo,
    _posiciones_pivotes,
    _primer_inicio_racha,
    detectar_rangos_laterales,
    seleccionar_rangos,
)
from data.loader import _ruta_parquet, cargar_config  # noqa: E402

TOLERANCIA = 1e-9


def _config(
    ventana: int = 60,
    duracion_minima: int = 40,
    barras_confirmacion: int = 3,
) -> dict:
    """Construye una configuración mínima del Filtro 1 para las pruebas
    sintéticas, independiente de ``config.yaml``.

    Parameters
    ----------
    ventana : int
        Tamaño de la ventana móvil, en velas.
    duracion_minima : int
        Duración mínima del rango, en velas.
    barras_confirmacion : int
        Velas exigidas a cada lado de un pivote.

    Returns
    -------
    dict
        Configuración con la sección ``filtro_1_rango_lateral``.
    """
    return {
        "filtro_1_rango_lateral": {
            "ventanas": [{"velas": ventana, "tipo": "secundario"}],
            "fraccion_duracion_minima": duracion_minima / ventana,
            # Separación laxa en las pruebas sintéticas: la oscilación
            # es regular y los toques caen muy seguidos por diseño.
            "fraccion_separacion_toques": 0.02,
            "barras_confirmacion_pivote": barras_confirmacion,
            # Holgado a propósito: las pruebas sintéticas usan mechas
            # fijas de 0.5, que dan un ATR muy pequeño frente a la
            # amplitud de la oscilación.
            "altura_maxima_atr_base": 100.0,
            "altura_calibrada_sobre_velas": ventana,
            "altura_maxima_pct": 100.0,
            "r2_maximo": 0.3,
            "fraccion_pendiente_altura": 0.5,
            "contencion_minima_cierres": 0.85,
            "cruces_minimos_medio": 7,
            "banda_bordes": 0.20,
            "ocupacion_bordes_minima": 0.0,
            "recorridos_minimos_base": 1.0,
            "fraccion_cola_contencion": 0.10,
            "altura_sin_penalizar_pct": 0.30,
            "pct_por_recorrido_extra": 0.10,
            "cierres_fuera_fin_rango": 5,
            "toques_minimos_nivel": 2,
            "anchura_banda_atr": 0.5,
            "atr_periodo": 14,
            "calidad_minima_seleccion": {"principal": 0.40, "secundario": 0.70},
            "solape_maximo_seleccion": 0.15,
        }
    }


def _df_desde_cierres(cierres: np.ndarray, media_mecha: float = 0.5) -> pd.DataFrame:
    """Construye un DataFrame OHLC sintético a partir de una serie de
    cierres, añadiendo mechas simétricas de tamaño fijo.

    Parameters
    ----------
    cierres : np.ndarray
        Serie de cierres.
    media_mecha : float
        Distancia de ``high`` y ``low`` respecto al cierre.

    Returns
    -------
    pd.DataFrame
        Velas con ``open``, ``high``, ``low``, ``close`` y
        ``DatetimeIndex`` de 4h en UTC.
    """
    indice = pd.date_range("2024-01-01", periods=len(cierres), freq="4h", tz="UTC")
    return pd.DataFrame(
        {
            "open": cierres,
            "high": cierres + media_mecha,
            "low": cierres - media_mecha,
            "close": cierres,
        },
        index=indice,
    )


def _oscilacion(
    n_velas: int,
    centro: float = 100.0,
    amplitud: float = 5.0,
    periodo: int = 12,
) -> np.ndarray:
    """Genera una oscilación sinusoidal.

    Con el periodo por defecto de 12 los máximos caen en velas exactas
    (i = 3, 15, 27...) y los mínimos en i = 9, 21, 33..., lo que
    produce swing highs y swing lows limpios y perfectamente
    agrupados. El periodo es parametrizable para poder variar la
    frecuencia de oscilación sin tocar amplitud ni centro.

    Parameters
    ----------
    n_velas : int
        Número de velas a generar.
    centro : float
        Precio central de la oscilación.
    amplitud : float
        Amplitud de la oscilación.
    periodo : int
        Velas por ciclo completo.

    Returns
    -------
    np.ndarray
        Serie de cierres.
    """
    posiciones = np.arange(n_velas, dtype="float64")
    return centro + amplitud * np.sin(2 * np.pi * posiciones / periodo)


# --------------------------------------------------------------------
# Detección de pivotes
# --------------------------------------------------------------------


def test_pivotes_zigzag_simple() -> None:
    """Los swing highs y lows caen donde toca en un zigzag conocido."""
    cierres = pd.Series([1.0, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1])
    altos, bajos = _posiciones_pivotes(cierres, barras_confirmacion=2)

    assert altos.tolist() == [2, 7], altos.tolist()
    assert bajos.tolist() == [4], bajos.tolist()


def test_pivotes_descartan_mesetas() -> None:
    """Una meseta (dos velas al mismo precio) no genera pivote.

    La comparación es estricta por ambos lados, así que un máximo
    repetido no cuenta: evita duplicar el mismo nivel.
    """
    cierres = pd.Series([1.0, 2, 3, 3, 2, 1, 2, 3, 2, 1])
    altos, _ = _posiciones_pivotes(cierres, barras_confirmacion=2)

    assert altos.tolist() == [7], altos.tolist()


def test_pivotes_sin_contexto_completo() -> None:
    """Las velas sin R velas a un lado no pueden ser pivote."""
    cierres = pd.Series([5.0, 4, 3, 1, 3, 4, 5])
    altos, bajos = _posiciones_pivotes(cierres, barras_confirmacion=2)

    # Las velas 0 y 6 son los máximos de la serie, pero les falta
    # contexto a un lado: no se confirman como pivote.
    assert altos.tolist() == [], altos.tolist()
    assert bajos.tolist() == [3], bajos.tolist()


# --------------------------------------------------------------------
# Agrupación de pivotes
# --------------------------------------------------------------------


def test_nivel_es_el_extremo_no_la_moda() -> None:
    """El nivel es el pivote más extremo, aunque la moda esté más adentro.

    Es el caso del lateral de ONDO: cinco toques agrupados en el
    interior y dos en el borde. El borde es el que delimita.
    """
    precios = np.array([100.0, 100.1, 100.2, 100.15, 100.05, 104.6, 105.0])
    posiciones = np.array([0, 20, 40, 60, 80, 100, 140])

    techo, toques = _nivel_por_extremo(
        precios, posiciones, anchura=1.0, toques_minimos=2,
        separacion_minima=10, hacia_arriba=True,
    )

    assert abs(techo - 105.0) < TOLERANCIA, techo
    assert toques == 2, toques


def test_nivel_extremo_por_ambos_lados() -> None:
    """Techo al máximo y suelo al mínimo del mismo conjunto."""
    precios = np.array([100.0, 100.2, 105.0, 105.2])
    posiciones = np.array([0, 40, 80, 120])

    techo, _ = _nivel_por_extremo(
        precios, posiciones, anchura=1.0, toques_minimos=2,
        separacion_minima=10, hacia_arriba=True,
    )
    suelo, _ = _nivel_por_extremo(
        precios, posiciones, anchura=1.0, toques_minimos=2,
        separacion_minima=10, hacia_arriba=False,
    )

    assert abs(techo - 105.2) < TOLERANCIA, techo
    assert abs(suelo - 100.0) < TOLERANCIA, suelo


def test_toques_del_mismo_impulso_no_cuentan_dos_veces() -> None:
    """Dos pivotes de velas contiguas son UN test del nivel, no dos.

    Es el fallo que inflaba la caja de BTC de feb-abr 2026: el techo
    lo confirmaban dos swing highs del 2 y el 3 de febrero, o sea la
    cola de la caída anterior. Con separación, el nivel baja al
    siguiente que sí se haya testeado en ocasiones distintas.
    """
    precios = np.array([110.0, 109.8, 100.0, 100.2])
    # Los dos de 110 son contiguos; los de 100 están bien separados.
    posiciones = np.array([50, 52, 10, 90])

    nivel, toques = _nivel_por_extremo(
        precios, posiciones, anchura=1.0, toques_minimos=2,
        separacion_minima=20, hacia_arriba=True,
    )

    assert abs(nivel - 100.2) < TOLERANCIA, nivel
    assert toques == 2, toques


def test_extremo_aislado_no_confirma_nivel() -> None:
    """Un extremo sin ningún pivote cerca no fija el nivel.

    El nivel baja al siguiente candidato que sí tenga compañía, en vez
    de descartar la ventana entera.
    """
    precios = np.array([100.0, 100.4, 100.2, 110.0])
    posiciones = np.array([0, 40, 80, 120])

    nivel, _ = _nivel_por_extremo(
        precios, posiciones, anchura=1.0, toques_minimos=2,
        separacion_minima=10, hacia_arriba=True,
    )

    assert abs(nivel - 100.4) < TOLERANCIA, nivel


def test_nivel_sin_pivotes_suficientes() -> None:
    """Con menos pivotes que el mínimo de toques no hay nivel."""
    resultado = _nivel_por_extremo(
        np.array([100.0]), np.array([0]), anchura=1.0, toques_minimos=2,
        separacion_minima=10, hacia_arriba=True,
    )

    assert resultado is None


# --------------------------------------------------------------------
# Búsqueda de rachas
# --------------------------------------------------------------------


def test_primer_inicio_racha() -> None:
    """Localiza el comienzo de la primera racha de la longitud pedida."""
    mascara = np.array([False, True, True, False, True, True, True, True, True, False])

    assert _primer_inicio_racha(mascara, 5) == 4
    assert _primer_inicio_racha(mascara, 2) == 1
    assert _primer_inicio_racha(mascara, 6) is None
    assert _primer_inicio_racha(np.array([True, True]), 5) is None


# --------------------------------------------------------------------
# Detección extremo a extremo sobre series sintéticas
# --------------------------------------------------------------------


def test_rango_limpio_y_ruptura() -> None:
    """Una oscilación limpia seguida de ruptura da un rango que termina
    en la vela anterior a la racha, confirmado 5 velas después."""
    cierres = _oscilacion(140)
    cierres[120:] = 115.0 + np.sin(np.arange(20, dtype="float64"))

    df = _df_desde_cierres(cierres)
    rangos = detectar_rangos_laterales(df, _config())

    assert not rangos.empty, "no se detectó ningún rango"

    primero = rangos.iloc[0]
    assert primero["inicio"] == df.index[0]
    assert primero["fin"] == df.index[119], primero["fin"]
    assert primero["declarado_en"] == df.index[59], primero["declarado_en"]
    assert primero["confirmado_en"] == df.index[124], primero["confirmado_en"]
    assert not primero["en_curso"]
    assert abs(primero["techo"] - 105.0) < TOLERANCIA, primero["techo"]
    assert abs(primero["suelo"] - 95.0) < TOLERANCIA, primero["suelo"]
    assert abs(primero["contencion"] - 1.0) < TOLERANCIA


def test_barrido_de_stops_no_termina_el_rango() -> None:
    """Un único cierre fuera, y vuelta dentro, no rompe el rango.

    Además el pico aislado que ese barrido introduce (un swing high a
    107 frente al grupo a 105) no debe mover el techo.
    """
    cierres = _oscilacion(140)
    cierres[60] = 107.0
    cierres[120:] = 115.0 + np.sin(np.arange(20, dtype="float64"))

    df = _df_desde_cierres(cierres)
    rangos = detectar_rangos_laterales(df, _config())

    assert not rangos.empty
    primero = rangos.iloc[0]
    assert abs(primero["techo"] - 105.0) < TOLERANCIA, primero["techo"]
    assert primero["fin"] == df.index[119], primero["fin"]


def test_tendencia_no_es_rango() -> None:
    """Una rampa lineal no produce ningún rango (R² ≈ 1)."""
    cierres = np.linspace(100.0, 200.0, 200)
    df = _df_desde_cierres(cierres)

    rangos = detectar_rangos_laterales(df, _config())

    assert rangos.empty, f"se detectaron {len(rangos)} rangos en una tendencia"


def test_rango_abierto_al_final_del_historico() -> None:
    """Si el rango no ha roto al agotarse el histórico, queda abierto."""
    df = _df_desde_cierres(_oscilacion(140))
    rangos = detectar_rangos_laterales(df, _config())

    assert len(rangos) == 1
    unico = rangos.iloc[0]
    assert unico["en_curso"]
    assert pd.isna(unico["confirmado_en"])
    assert unico["fin"] == df.index[-1]


def test_rectangulo_congelado_no_se_ensancha() -> None:
    """El rectángulo se fija en la declaración y no sigue al precio.

    Tras declararse el rango, la oscilación mantiene su forma pero se
    le añade una deriva lenta hacia arriba. Un rectángulo recalculado
    en cada vela la seguiría y nunca dispararía la regla de los 5
    cierres; congelado en 95-105, la deriva acaba sacando al precio de
    forma sostenida y el rango termina.
    """
    cierres = _oscilacion(200)
    cierres[100:] += np.linspace(0.0, 40.0, 100)

    df = _df_desde_cierres(cierres)
    rangos = detectar_rangos_laterales(df, _config())

    assert not rangos.empty
    primero = rangos.iloc[0]
    assert abs(primero["techo"] - 105.0) < TOLERANCIA, primero["techo"]
    assert abs(primero["suelo"] - 95.0) < TOLERANCIA, primero["suelo"]
    assert not primero["en_curso"], "el rango debería haber roto"
    assert primero["fin"] < df.index[130], primero["fin"]


def test_columnas_y_tipos() -> None:
    """El DataFrame devuelto tiene el contrato de columnas esperado."""
    df = _df_desde_cierres(_oscilacion(140))
    rangos = detectar_rangos_laterales(df, _config())

    esperadas = [
        "ventana", "tipo", "calidad", "inicio", "fin", "techo", "suelo",
        "precio_max", "precio_min", "contencion", "altura_atr", "r2",
        "cruces", "toques_techo", "toques_suelo", "declarado_en",
        "confirmado_en", "en_curso", "grupo_solape",
    ]
    assert list(rangos.columns) == esperadas, list(rangos.columns)
    assert (rangos["tipo"] == "secundario").all()
    assert rangos["calidad"].between(0.0, 1.0).all(), rangos["calidad"].tolist()


def test_tope_de_altura_rechaza_rectangulo_alto() -> None:
    """Un tope de altura por debajo del rectángulo lo descarta.

    El mismo dato que produce un rango con el tope holgado deja de
    producirlo al bajar `altura_maxima_atr`.
    """
    df = _df_desde_cierres(_oscilacion(140))
    assert not detectar_rangos_laterales(df, _config()).empty

    config = _config()
    config["filtro_1_rango_lateral"]["altura_maxima_atr_base"] = 1.0
    rangos = detectar_rangos_laterales(df, config)

    assert rangos.empty, f"el tope no filtró: {len(rangos)} rangos"


def test_pendiente_anclada_a_la_altura_del_rectangulo() -> None:
    """La deriva se mide contra la altura del rectángulo, no contra el
    precio medio.

    Dos series con la MISMA deriva y distinta amplitud: la ancha
    supera el criterio y la estrecha no. Con un umbral anclado al
    precio medio (la versión anterior) ambas darían el mismo
    resultado, porque comparten pendiente y nivel de precio.
    """
    n_velas = 140
    deriva = np.arange(n_velas, dtype="float64") * (3.0 / 60.0)

    ancha = _df_desde_cierres(_oscilacion(n_velas, amplitud=5.0) + deriva)
    estrecha = _df_desde_cierres(_oscilacion(n_velas, amplitud=1.5) + deriva)

    rangos_ancha = detectar_rangos_laterales(ancha, _config())
    rangos_estrecha = detectar_rangos_laterales(estrecha, _config())

    assert not rangos_ancha.empty, "el rectángulo ancho debería absorber la deriva"
    assert rangos_estrecha.empty, (
        f"el rectángulo estrecho no debería absorberla: "
        f"{len(rangos_estrecha)} rangos"
    )


def test_grupo_solape_sobre_datos_reales() -> None:
    """El marcado de solapes es coherente con las fechas de los rangos.

    Dentro de un grupo, cada rango debe empezar antes de que termine
    el anterior; entre grupos consecutivos, no.
    """
    config = cargar_config()
    df = _cargar_cache("BTC/USD:USD", "4h")
    if df is None:
        print("    (saltada: no hay caché en data/raw/)")
        return

    rangos = detectar_rangos_laterales(df, config)
    assert not rangos.empty

    # Un grupo nunca cruza ventanas.
    assert (rangos.groupby("grupo_solape")["ventana"].nunique() == 1).all()

    for _, grupo in rangos.groupby("grupo_solape"):
        orden = grupo.sort_values("inicio")
        assert (orden["inicio"].iloc[1:].to_numpy()
                <= orden["fin"].iloc[:-1].to_numpy()).all()

    # Un rango abre grupo nuevo si empieza después del punto más lejano
    # al que llegó el grupo anterior, no del fin del rango que le
    # precede: el agrupamiento es transitivo. Los dos criterios
    # coinciden solo si `fin` crece de forma monótona, cosa que dejó de
    # ocurrir al recortar las colas mal contenidas.
    for ventana, sub in rangos.groupby("ventana"):
        orden = sub.sort_values("inicio")
        cambio = orden["grupo_solape"].diff() > 0
        sin_solape = orden["inicio"] > orden["fin"].cummax().shift()
        assert cambio[1:].equals(sin_solape[1:]), ventana

    n_solapados = (rangos.groupby("grupo_solape").size() > 1).sum()
    print(f"    ({n_solapados} grupos con más de un rango)")


def test_pocas_travesias_rechazadas() -> None:
    """Pocas travesías del rectángulo se rechazan; muchas se aceptan.

    Aísla el criterio de oscilación: las dos series tienen el mismo
    centro, la misma amplitud y por tanto el mismo rectángulo, y solo
    cambian la frecuencia. La lenta (periodo 24, ~2.5 ciclos en la
    ventana) es el problema estructural de la V —el precio recorre el
    rectángulo muy pocas veces— y la rápida (periodo 12, 5 ciclos) es
    un lateral.

    Se comprueba además que bajando el umbral la lenta sí se detecta,
    para verificar que la bloquea el criterio de oscilación y no otro.
    """
    lenta = _df_desde_cierres(_oscilacion(140, periodo=24))
    rapida = _df_desde_cierres(_oscilacion(140, periodo=12))

    assert not detectar_rangos_laterales(rapida, _config()).empty

    config = _config()
    config["filtro_1_rango_lateral"]["cruces_minimos_medio"] = 1
    sin_filtro = detectar_rangos_laterales(lenta, config)
    assert not sin_filtro.empty, "la serie lenta solo debe caer por oscilación"
    assert (sin_filtro["cruces"] < 7).all(), sin_filtro["cruces"].tolist()

    assert detectar_rangos_laterales(lenta, _config()).empty


def test_umbral_de_cruces_no_escala_con_la_ventana() -> None:
    """El umbral de cruces es el mismo sea cual sea el tamaño de ventana.

    Se probó escalarlo con N y es inviable: con N=250 exigiría 29
    cruces y con N=400, 47, umbrales inalcanzables que anulaban la
    detección en las ventanas largas. Un lateral no cruza el medio más
    veces por ser más largo, sino por ser más oscilante.
    """
    # Oscilación lenta: en 400 velas caben ~6.7 ciclos, o sea del orden
    # de 13 cruces. Supera el umbral fijo de 7, pero quedaría muy por
    # debajo de los 47 que exigiría un umbral escalado con N.
    df = _df_desde_cierres(_oscilacion(900, periodo=60))

    config = _config(ventana=60, duracion_minima=40)
    config["filtro_1_rango_lateral"]["ventanas"] = [
        {"velas": 400, "tipo": "principal"},
    ]
    rangos = detectar_rangos_laterales(df, config)

    assert not rangos.empty, "la ventana larga debería detectar el lateral"
    assert (rangos["cruces"] >= 7).all(), rangos["cruces"].tolist()
    assert (rangos["cruces"] < 47).all(), (
        "el caso no distingue el umbral fijo del escalado: "
        f"{rangos['cruces'].tolist()}"
    )


def test_oscilacion_registrada_en_el_resultado() -> None:
    """Todo rango detectado cumple y reporta el mínimo de cruces."""
    df = _df_desde_cierres(_oscilacion(140))
    rangos = detectar_rangos_laterales(df, _config())

    assert not rangos.empty
    assert (rangos["cruces"] >= 7).all(), rangos["cruces"].tolist()


def test_tipo_invalido_falla_ruidosamente() -> None:
    """Un `tipo` que no sea operativo ni contexto aborta con ValueError."""
    config = _config()
    config["filtro_1_rango_lateral"]["ventanas"][0]["tipo"] = "táctico"
    df = _df_desde_cierres(_oscilacion(140))

    try:
        detectar_rangos_laterales(df, config)
    except ValueError:
        return
    raise AssertionError("debería haber lanzado ValueError")


def test_config_incompleta_falla_ruidosamente() -> None:
    """Un config.yaml sin los parámetros nuevos aborta con KeyError."""
    config = _config()
    del config["filtro_1_rango_lateral"]["toques_minimos_nivel"]
    df = _df_desde_cierres(_oscilacion(140))

    try:
        detectar_rangos_laterales(df, config)
    except KeyError:
        return
    raise AssertionError("debería haber lanzado KeyError")


# --------------------------------------------------------------------
# Datos reales (caché de data/raw/)
# --------------------------------------------------------------------


def _cargar_cache(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Lee un parquet de ``data/raw/`` si existe, sin descargar nada.

    Parameters
    ----------
    symbol : str
        Símbolo unificado de CCXT.
    timeframe : str
        Timeframe de las velas.

    Returns
    -------
    pd.DataFrame | None
        Velas cacheadas, o ``None`` si no hay caché.
    """
    ruta = _ruta_parquet(symbol, timeframe)
    if not ruta.exists():
        return None
    try:
        return pd.read_parquet(ruta)
    except OSError:
        return None


def test_invariantes_sobre_datos_reales() -> None:
    """Todo rango detectado sobre datos reales cumple los invariantes."""
    config = cargar_config()
    cfg = config["filtro_1_rango_lateral"]

    df = _cargar_cache("ONDO/USD:USD", "4h")
    if df is None:
        print("    (saltada: no hay caché en data/raw/)")
        return

    rangos = detectar_rangos_laterales(df, config)
    if rangos.empty:
        print("    (sin rangos detectados: invariantes no evaluables)")
        return

    assert (rangos["techo"] > rangos["suelo"]).all()
    assert rangos["tipo"].isin(("secundario", "principal")).all()
    assert rangos["calidad"].between(0.0, 1.0).all()
    # Un rango más plano, más estrecho y mejor respetado que otro debe
    # puntuar más alto: la nota no puede contradecir sus ingredientes.
    mejores = rangos.nlargest(10, "calidad")
    peores = rangos.nsmallest(10, "calidad")
    assert mejores["altura_atr"].median() < peores["altura_atr"].median()
    assert mejores["r2"].median() < peores["r2"].median()
    assert (rangos["contencion"] >= cfg["contencion_minima_cierres"]).all()
    assert (rangos["toques_techo"] >= cfg["toques_minimos_nivel"]).all()
    assert (rangos["toques_suelo"] >= cfg["toques_minimos_nivel"]).all()
    assert (rangos["fin"] >= rangos["inicio"]).all()
    # El rango nunca se conoce en su primera vela: se declara N-1 velas
    # después, y su fin no se sabe hasta la confirmación de la ruptura.
    assert (rangos["declarado_en"] > rangos["inicio"]).all()
    cerrados = rangos[~rangos["en_curso"]]
    assert (cerrados["confirmado_en"] > cerrados["fin"]).all()


def test_seleccion_reduce_y_conserva_anidados() -> None:
    """La selección deja pocos rangos pero no aplasta los anidados.

    Comprueba las tres propiedades que la hacen útil: reduce mucho,
    conserva ambos tipos (un operativo dentro de uno de contexto no es
    redundante) y ningún par del mismo tipo se solapa por encima del
    umbral.
    """
    config = cargar_config()
    df = _cargar_cache("BTC/USD:USD", "4h")
    if df is None:
        print("    (saltada: no hay caché en data/raw/)")
        return

    rangos = detectar_rangos_laterales(df, config)
    seleccion = seleccionar_rangos(rangos, config)
    cfg = config["filtro_1_rango_lateral"]

    assert len(seleccion) < len(rangos) / 2, (
        f"la selección apenas reduce: {len(rangos)} -> {len(seleccion)}"
    )
    assert set(seleccion["tipo"]) == {"secundario", "principal"}, (
        "la selección debe conservar rangos anidados de ambos tipos"
    )
    for tipo, grupo in seleccion.groupby("tipo"):
        assert (grupo["calidad"] >= cfg["calidad_minima_seleccion"][tipo]).all(), tipo
    assert "_relevancia" not in seleccion.columns

    # Dentro de un mismo tipo, ningún par supera el solape permitido.
    for _, grupo in seleccion.groupby("tipo"):
        filas = list(grupo.itertuples())
        for i, a in enumerate(filas):
            for b in filas[i + 1:]:
                union = (max(a.fin, b.fin) - min(a.inicio, b.inicio)).total_seconds()
                inter = (min(a.fin, b.fin) - max(a.inicio, b.inicio)).total_seconds()
                solape = max(0.0, inter) / union
                assert solape <= cfg["solape_maximo_seleccion"] + 1e-9, (
                    f"{a.inicio.date()} y {b.inicio.date()} se solapan {solape:.2f}"
                )

    n_contexto = (seleccion["tipo"] == "principal").sum()
    print(f"    ({len(rangos)} -> {len(seleccion)} rangos, {n_contexto} de contexto)")


def test_sin_lookahead_sobre_datos_reales() -> None:
    """Truncar el histórico no cambia los rangos ya confirmados.

    Es la prueba fuerte contra el lookahead bias: si algún cálculo
    usara velas futuras, un rango cerrado y confirmado antes del corte
    saldría distinto al recortar la serie por detrás.
    """
    config = cargar_config()
    df_completo = _cargar_cache("ONDO/USD:USD", "4h")
    if df_completo is None:
        print("    (saltada: no hay caché en data/raw/)")
        return

    rangos_completo = detectar_rangos_laterales(df_completo, config)
    # `grupo_solape` queda fuera a propósito: su valor es un contador
    # global que se desplaza según cuántos grupos hayan producido las
    # ventanas anteriores, así que el número absoluto cambia al truncar
    # aunque la agrupación sea idéntica. La coherencia del agrupamiento
    # la verifica `test_grupo_solape_sobre_datos_reales`.
    columnas = ["inicio", "fin", "techo", "suelo", "confirmado_en"]
    comparados = 0

    for fraccion in (0.5, 0.7, 0.9):
        corte = int(len(df_completo) * fraccion)
        df_truncado = df_completo.iloc[:corte]
        ultima_vela = df_truncado.index[-1]

        rangos_truncado = detectar_rangos_laterales(df_truncado, config)
        ya_confirmados = rangos_completo[
            rangos_completo["confirmado_en"].notna()
            & (rangos_completo["confirmado_en"] <= ultima_vela)
        ]

        # Se emparejan por (ventana, declarado_en) y no por posición:
        # con varias ventanas el orden del resultado combinado no tiene
        # por qué coincidir entre la serie completa y la truncada. La
        # vela de declaración identifica un rango de forma única dentro
        # de su ventana, cosa que `inicio` ya no hace desde que los
        # rangos se extienden hacia atrás.
        indexado = rangos_truncado.set_index(["ventana", "declarado_en"])
        for fila in ya_confirmados.itertuples():
            clave = (fila.ventana, fila.declarado_en)
            assert clave in indexado.index, (
                f"corte {fraccion}: falta el rango confirmado {clave}"
            )
            obtenido = indexado.loc[clave]
            for columna in columnas:
                esperado = getattr(fila, columna)
                assert obtenido[columna] == esperado, (
                    f"corte {fraccion}, rango {clave}, columna '{columna}': "
                    f"esperado {esperado!r}, obtenido {obtenido[columna]!r}"
                )
            comparados += 1

    print(f"    ({comparados} rangos confirmados comparados entre cortes)")


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
