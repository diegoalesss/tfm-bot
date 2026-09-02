"""Motor de backtest para el experimento de toques del FRVP.

Simula la regla acordada: se coloca una orden limit permanente en cada
nivel vigente y, cuando el precio la alcanza, se abre posición contra
el movimiento (llega desde arriba → long, desde abajo → short). La
salida es escalonada en los tres niveles siguientes de la rejilla, con
el stop del remanente subido a break-even al alcanzar el segundo.

Esta capa es intercambiable y no conoce la estrategia: recibe la
rejilla de niveles ya construida por :mod:`core.levels` y se limita a
ejecutarla sobre las velas.

Dos timeframes, dos trabajos
-----------------------------
La DECISIÓN se toma sobre velas de 4h cerradas: qué niveles están
vigentes y de qué lado del nivel está el precio, que es lo que fija si
la orden es de compra o de venta. La EJECUCIÓN se resuelve sobre velas
de 15m: dentro de una vela de 4h no se sabe si saltó antes el stop o el
primer objetivo, y con salidas escalonadas y break-even ese orden
decide el resultado.

Ambigüedad residual: si una misma vela de 15m contiene el stop y un
objetivo, se asume que saltó el stop. Es la convención pesimista
estándar cuando no se dispone de ticks, y sesga el resultado a la
baja de forma conocida.

Nota sobre lookahead bias
--------------------------
Verificado explícitamente, punto por punto:

1. Un nivel no entra en la rejilla antes del ``confirmado_en`` de su
   rango (lo garantiza :mod:`core.levels`), y aquí se vuelve a filtrar
   vela a vela: nunca se usa la tabla completa de una vez.
2. El lado de cada orden se decide con el CIERRE de la vela de 4h ``i``
   y solo puede ejecutarse en las velas de 15m de la vela de 4h
   ``i + 1``, es decir, estrictamente después.
3. Los objetivos y el stop se congelan en el instante del fill, con la
   rejilla y el ATR conocidos en ese momento. No se recalculan nunca
   con información posterior.
4. El recorrido en 15m avanza hacia adelante vela a vela; ninguna
   decisión consulta una vela futura.
5. El ATR usa ``rolling`` hacia atrás sobre velas cerradas (se reutiliza
   la implementación del detector, ver ``_atr``).

Lo verifica ``test_sin_lookahead_backtest`` en
``tests/test_backtest.py``: truncar el histórico no puede cambiar
ningún trade ya cerrado antes del corte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.convergencia import multiplicador_tamano, score, señales_activas
from core.imbalances import puntos_objetivo
from core.levels import objetivos_desde
from core.momentum import divergencia_vigente, divergencias, macd
from core.osciladores import adx, estocastico, fase_ttm, momento_ttm, squeeze
from core.structure import estructura_alineada, estructura_mercado, permite
# Se reutiliza deliberadamente el ATR del detector en vez de escribir
# otro: dos definiciones de volatilidad en el mismo sistema acaban
# divergiendo, y esta ya está probada y documentada.
from core.range_detector import _atr as calcular_atr

logger = logging.getLogger(__name__)

N_OBJETIVOS = 3

# Radio dentro del cual dos zonas se consideran la misma para contar
# confluencia, como fracción del precio. Es del orden de la separación
# mínima entre objetivos: por debajo de eso son la misma zona.
RADIO_CONFLUENCIA_PCT = 0.01

# Recorrido hasta el primer objetivo, en múltiplos del riesgo, a partir
# del cual la nota de recorrido satura. Con 3R de margen ya no importa
# que haya más: la operación tiene sitio de sobra.
R_POTENCIAL_MAXIMO = 3.0

COLUMNAS_TRADE = [
    "ts_entrada",
    "ts_salida",
    "direccion",
    "rango_id",
    "nivel",
    "calidad",
    "score",
    "senales",
    "confluencia",
    "r_potencial",
    "regimen",
    "entrada",
    "stop_inicial",
    "tp1",
    "tp2",
    "tp3",
    "objetivos_alcanzados",
    "motivo_salida",
    "modo_fallback",
    "pnl_pct",
    "pnl_r",
    "riesgo_pct",
    "mae_pct",
    "mfe_pct",
    "velas_15m",
    "capital_despues",
]


@dataclass
class _Posicion:
    """Estado de una posición abierta durante la simulación."""

    direccion: int
    entrada: float
    ts_entrada: pd.Timestamp
    j_entrada: int
    rango_id: int
    nivel: str
    indice_nivel: int
    calidad: float
    score: int
    senales: str
    confluencia: int
    r_potencial: float
    regimen: str
    stop: float
    stop_inicial: float
    objetivos: list[float]
    fracciones: list[float]
    modo_fallback: bool
    notional: float
    restante: float = 1.0
    alcanzados: int = 0
    pnl_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    be_pendiente: bool = False
    extremos: list[float] = field(default_factory=list)


def _nota_operacion(
    entrada: float,
    objetivos: list[float],
    stop: float,
    rejilla: np.ndarray,
    calidad_rango: float,
    regimen: str,
    direccion: int,
) -> tuple[float, int, float]:
    """Puntúa una operación de 0 a 1 en el momento de abrirla.

    Es el equivalente de la nota de calidad de los rangos: no decide si
    se opera —eso lo hacen los filtros— sino que permite ordenar y, si
    la nota separa de verdad, quedarse solo con las mejores.

    Los cuatro factores se eligen POR RAZONAMIENTO, antes de mirar
    resultados, para que medir si funcionan siga significando algo:

    confluencia
        Cuántas zonas distintas se apilan alrededor del nivel de
        entrada. Un precio al que apuntan tres cosas a la vez es una
        zona defendida por más gente que uno al que apunta una sola.
    recorrido
        Cuánto R hay hasta el primer objetivo. Una operación con 3R de
        margen puede permitirse fallar más veces que una con 0.5R.
    calidad del rango
        La nota del lateral del que sale el nivel. Un perfil trazado
        sobre un rango limpio y largo da niveles más fiables.
    estructura
        A favor, indefinida o en contra de la estructura de mercado.

    Parameters
    ----------
    entrada, stop : float
        Precios de entrada y de stop inicial.
    objetivos : list[float]
        Objetivos ya calculados.
    rejilla : np.ndarray
        Todas las zonas vigentes, para contar la confluencia.
    calidad_rango : float
        Nota del rango del que procede el nivel (0 a 1).
    regimen : str
        Estructura de mercado en la vela de decisión.
    direccion : int
        ``1`` long, ``-1`` short.

    Returns
    -------
    tuple[float, int, float]
        La nota (0 a 1), el número de zonas en confluencia y el
        recorrido hasta el primer objetivo en múltiplos de R.
    """
    riesgo = abs(entrada - stop)
    if riesgo <= 0:
        return 0.0, 0, 0.0

    cercanas = rejilla[np.isfinite(rejilla)]
    confluencia = int(
        np.count_nonzero(
            np.abs(cercanas - entrada) <= abs(entrada) * RADIO_CONFLUENCIA_PCT
        )
    )
    # Tres zonas apiladas ya es mucha confluencia; a partir de ahí satura.
    nota_confluencia = min(1.0, max(0, confluencia - 1) / 2.0)

    r_potencial = abs(objetivos[0] - entrada) / riesgo if objetivos else 0.0
    nota_recorrido = min(1.0, r_potencial / R_POTENCIAL_MAXIMO)

    a_favor = (regimen == "alcista" and direccion > 0) or (
        regimen == "bajista" and direccion < 0
    )
    en_contra = (regimen == "alcista" and direccion < 0) or (
        regimen == "bajista" and direccion > 0
    )
    nota_estructura = 1.0 if a_favor else (0.0 if en_contra else 0.5)

    nota = (
        nota_confluencia + nota_recorrido + float(calidad_rango) + nota_estructura
    ) / 4.0
    return round(float(np.clip(nota, 0.0, 1.0)), 3), confluencia, round(r_potencial, 2)


def _recortar_objetivos_lejanos(
    objetivos: list[float], entrada: float, direccion: int, cfg: dict
) -> list[float]:
    """Acerca los objetivos que quedan demasiado lejos del anterior.

    Si el siguiente nivel de la rejilla está a más de
    ``distancia_maxima_objetivo_pct`` del objetivo anterior, no se
    espera hasta él: ese tramo se cierra a esa distancia. Es la regla
    del autor —«si tras TP1 el siguiente nivel está lejísimos, cierro
    TP2 a un 5% de TP1»— y va en la misma dirección que lo ya medido en
    SPEC.md §8, donde los objetivos a distancia fija rinden mejor que
    los anclados a una estructura remota.

    Se aplica en cascada: si TP2 se recorta, TP3 se mide desde el TP2
    ya recortado.

    Parameters
    ----------
    objetivos : list[float]
        Objetivos que salieron de la rejilla, de más cercano a más
        lejano.
    entrada : float
        Precio de entrada, referencia del primer tramo.
    direccion : int
        ``1`` long, ``-1`` short.
    cfg : dict
        Sección ``experimento_toques_frvp`` de la configuración.

    Returns
    -------
    list[float]
        Los objetivos, con los tramos largos recortados.
    """
    tope = cfg.get("distancia_maxima_objetivo_pct")
    if tope is None or not objetivos:
        return objetivos

    desde_la_entrada = cfg.get("tope_tambien_desde_la_entrada", False)

    recortados: list[float] = []
    referencia = entrada
    for indice, objetivo in enumerate(objetivos):
        # El primer tramo solo se recorta si se pide expresamente: el
        # autor planteó la regla de TP1 en adelante.
        if indice > 0 or desde_la_entrada:
            limite = referencia * (1.0 + direccion * tope)
            if (objetivo - limite) * direccion > 0:
                objetivo = limite
        recortados.append(float(objetivo))
        referencia = float(objetivo)

    return recortados


def _plan_de_salida(
    entrada: float,
    direccion: int,
    rejilla: np.ndarray,
    atr: float,
    cfg: dict,
) -> tuple[list[float], list[float], float, bool]:
    """Congela objetivos, fracciones y stop en el momento del fill.

    Reglas acordadas:

    - Los objetivos son las tres siguientes zonas de la rejilla en la
      dirección del trade, con la separación mínima exigida. La rejilla
      mezcla los niveles del FRVP y los puntos de los imbalances
      semanales, ordenados por cercanía: TP1 es lo primero que el
      precio se encuentra, venga de donde venga.
    - **TP1 nunca queda más cerca que el stop** (si
      ``tp1_al_menos_como_el_stop``): un primer objetivo por debajo de
      1R haría que el primer tercio arriesgara más de lo que puede
      ganar. Las zonas que caigan dentro de esa distancia se saltan.
    - Si la rejilla no ofrece los tres, los que falten se refunden en un
      único objetivo de fallback (``tp_fallback_pct`` desde la entrada)
      y sus fracciones se suman a él. Si ese fallback no queda por
      delante del último objetivo real, se descarta y las fracciones
      sobrantes se acumulan en el último objetivo real.
    - Si no hay NINGUNA zona utilizable por delante, se entra en modo
      fallback completo: objetivo único al ``tp_fallback_pct``, stop al
      ``sl_fallback_pct``, sin escalonado ni break-even.

    Parameters
    ----------
    entrada : float
        Precio de entrada.
    direccion : int
        ``1`` long, ``-1`` short.
    rejilla : np.ndarray
        Precios de las zonas vigentes en el instante del fill.
    atr : float
        ATR de la última vela de 4h cerrada.
    cfg : dict
        Sección ``experimento_toques_frvp`` de la configuración.

    Returns
    -------
    tuple[list[float], list[float], float, bool]
        Objetivos, fracción de la posición asignada a cada uno, precio
        del stop inicial y si se está en modo fallback completo.
    """
    reparto: list[float] = list(cfg["reparto_parciales"])

    # El stop se calcula ANTES que los objetivos porque su distancia es
    # el listón que debe superar TP1.
    stop = entrada - direccion * cfg["mult_atr_stop"] * atr
    riesgo = abs(entrada - stop) / abs(entrada)

    # Modo alternativo: objetivos a múltiplos fijos del riesgo, sin
    # mirar la estructura. La evidencia lo pedía (SPEC.md §8): las
    # operaciones con el objetivo a 1-2R rinden mejor que las que lo
    # tienen a 3R o más, y el fallback fijo del 5% batía a los
    # objetivos de la rejilla.
    multiplos = cfg.get("objetivos_en_r")
    if multiplos:
        objetivos = [
            entrada * (1.0 + direccion * riesgo * m) for m in multiplos
        ]
        return objetivos, reparto[: len(objetivos)], float(stop), False

    minima_primero = cfg["distancia_minima_nivel_pct"]
    if cfg.get("tp1_al_menos_como_el_stop", True):
        minima_primero = max(minima_primero, riesgo)

    objetivos = objetivos_desde(
        entrada,
        direccion,
        rejilla,
        cfg["distancia_minima_nivel_pct"],
        N_OBJETIVOS,
        distancia_minima_primero_pct=minima_primero,
    )

    if not objetivos:
        # Sin ninguna zona utilizable por delante, los tres objetivos se
        # escalonan a `tp_fallback_pct` uno del otro, en vez de cerrar
        # todo de golpe en el primero: la operación conserva su
        # estructura de tercios aunque no haya niveles que la guíen.
        paso = 1.0 + direccion * cfg["tp_fallback_pct"]
        cuantos = N_OBJETIVOS if cfg.get("fallback_escalonado", True) else 1
        objetivos, referencia = [], entrada
        for _ in range(cuantos):
            referencia *= paso
            objetivos.append(float(referencia))
        if cuantos == 1:
            # Objetivo único: se cierra la posición entera en él.
            if not cfg.get("sl_fallback_usa_atr", True):
                stop = entrada * (1.0 - direccion * cfg["sl_fallback_pct"])
            return objetivos, [1.0], float(stop), True

        if not cfg.get("sl_fallback_usa_atr", True):
            stop = entrada * (1.0 - direccion * cfg["sl_fallback_pct"])
        return objetivos, reparto[: len(objetivos)], float(stop), True

    # Tope específico del PRIMER objetivo. Muchas operaciones recorren
    # un 3% a favor y vuelven a morir en el stop sin haber cobrado
    # nada: si el nivel que toca como TP1 está más lejos que esto, se
    # cobra antes y el resto sigue corriendo.
    objetivos = _recortar_objetivos_lejanos(objetivos, entrada, direccion, cfg)

    # El tope del PRIMER objetivo se aplica DESPUÉS del recorte en
    # cascada, y a propósito: hacerlo antes arrastraba a TP2 y TP3, que
    # se medían desde el TP1 ya acortado y dejaban de ser los niveles
    # del FRVP. Solo se acorta TP1; TP2 y TP3 siguen siendo estructura.
    #
    # El tope puede darse en porcentaje del precio o en múltiplos de
    # ATR. En ATR es preferible: el mismo 3% es "lejos" en un activo
    # tranquilo y "cerca" en uno agitado, mientras que el ATR mide la
    # distancia en la unidad en la que se mueve cada mercado.
    limite_tp1 = None
    if cfg.get("tp1_maximo_atr") is not None:
        limite_tp1 = entrada + direccion * cfg["tp1_maximo_atr"] * atr
    elif cfg.get("tp1_maximo_pct") is not None:
        limite_tp1 = entrada * (1.0 + direccion * cfg["tp1_maximo_pct"])

    if limite_tp1 is not None and objetivos:
        if (objetivos[0] - limite_tp1) * direccion > 0:
            objetivos[0] = float(limite_tp1)

    # Tope en R: ningún objetivo puede pedir más recorrido que este
    # múltiplo del riesgo. Es el mismo remedio que el tope en
    # porcentaje, pero medido en la unidad que de verdad importa y
    # adaptándose solo a la volatilidad de cada momento.
    tope_r = cfg.get("tope_objetivo_en_r")
    if tope_r is not None:
        limite = entrada * (1.0 + direccion * riesgo * tope_r)
        objetivos = [
            limite if (objetivo - limite) * direccion > 0 else objetivo
            for objetivo in objetivos
        ]
        # Los que chocan contra el tope colapsan en el mismo precio: se
        # deduplican para no cobrar dos parciales en el mismo sitio.
        unicos: list[float] = []
        for objetivo in objetivos:
            if not unicos or abs(objetivo - unicos[-1]) > 1e-9:
                unicos.append(objetivo)
        objetivos = unicos

    fracciones = reparto[: len(objetivos)]

    faltan = sum(reparto[len(objetivos) :])
    if faltan > 0:
        tp_fallback = entrada * (1.0 + direccion * cfg["tp_fallback_pct"])
        mas_lejos = (tp_fallback - objetivos[-1]) * direccion > 0
        separado = abs(tp_fallback - objetivos[-1]) >= (
            abs(objetivos[-1]) * cfg["distancia_minima_nivel_pct"]
        )
        if mas_lejos and separado:
            objetivos = objetivos + [tp_fallback]
            fracciones = fracciones + [faltan]
        else:
            fracciones = fracciones[:-1] + [fracciones[-1] + faltan]

    return objetivos, fracciones, float(stop), False


def _cerrar_parcial(
    pos: _Posicion, precio: float, fraccion: float, coste_pct: float
) -> None:
    """Acumula en la posición el resultado de cerrar una fracción.

    El coste se resta del retorno bruto de esa fracción: las comisiones
    se pagan sobre el nominal negociado, así que escalan igual que el
    retorno.
    """
    bruto = pos.direccion * (precio - pos.entrada) / pos.entrada
    pos.pnl_pct += fraccion * (bruto - coste_pct)
    pos.restante -= fraccion


def simular(
    velas_4h: pd.DataFrame,
    velas_15m: pd.DataFrame,
    niveles: pd.DataFrame,
    config: dict,
    imbalances: pd.DataFrame | None = None,
    velas_estructura: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Ejecuta la simulación completa sobre un símbolo.

    Parameters
    ----------
    velas_4h : pd.DataFrame
        Velas del timeframe de decisión, con ``DatetimeIndex`` UTC.
    velas_15m : pd.DataFrame
        Velas de ejecución, mismo periodo y mismo huso.
    niveles : pd.DataFrame
        Rejilla de niveles (ver :func:`core.levels.construir_niveles`).
    config : dict
        Configuración cargada de ``config.yaml``.
    imbalances : pd.DataFrame, optional
        Imbalances semanales (ver
        :func:`core.imbalances.detectar_imbalances`). Si se pasan, sus
        zonas se suman a la rejilla de OBJETIVOS —nunca a la de
        entradas: se entra en niveles del FRVP y se sale también por
        imbalances.
    velas_estructura : pd.DataFrame, optional
        Velas del timeframe en el que juzgar la estructura de mercado
        (``timeframe_estructura``). Si no se pasan, la estructura se
        calcula sobre las propias velas de decisión.

    Returns
    -------
    tuple[pd.DataFrame, pd.Series]
        Los trades cerrados (columnas de :data:`COLUMNAS_TRADE`) y la
        curva de capital indexada por fecha de cierre.

    Raises
    ------
    ValueError
        Si las velas están vacías o mal ordenadas.
    """
    if velas_4h.empty or velas_15m.empty:
        logger.error("No hay velas con las que simular")
        raise ValueError("No hay velas con las que simular")
    if not velas_4h.index.is_monotonic_increasing:
        logger.error("Las velas de 4h no están ordenadas cronológicamente")
        raise ValueError("Las velas de 4h no están ordenadas")

    cfg = config["experimento_toques_frvp"]
    capital = float(cfg["capital_inicial"])
    # `null` = sin límite de concurrencia. Con el sizing por riesgo fijo
    # el freno real no es este número, sino el capital disponible.
    limite = cfg["max_posiciones_simultaneas"]
    max_posiciones = len(niveles) if limite is None else int(limite)

    modo_sizing = cfg.get("modo_sizing", "fraccion_capital")
    riesgo_objetivo = float(cfg.get("riesgo_por_operacion_pct", 0.005))

    coste_maker = float(cfg["comision_maker_pct"])
    coste_taker = float(cfg["comision_taker_pct"])
    slippage = float(cfg["slippage_stop_pct"])
    # Entrada y objetivos son limit (maker); el stop es a mercado
    # (taker) y además desliza, que es justo cuando el libro está peor.
    coste_objetivo = coste_maker + coste_maker
    coste_stop = coste_maker + coste_taker + slippage

    cooldown = int(cfg["cooldown_velas_nivel"])
    mover_be_en = int(cfg["mover_be_en_tp"])
    timeout_4h = cfg["timeout_velas_4h"]
    vigencia = cfg["vigencia_nivel_velas_4h"]

    if niveles.empty:
        logger.warning("Sin niveles: la simulación no puede abrir ninguna posición")
        return pd.DataFrame(columns=COLUMNAS_TRADE), pd.Series(dtype="float64")

    idx4 = velas_4h.index
    close4 = velas_4h["close"].to_numpy(dtype="float64")
    atr4 = calcular_atr(velas_4h, int(cfg["atr_periodo"])).to_numpy(dtype="float64")

    o15 = velas_15m["open"].to_numpy(dtype="float64")
    h15 = velas_15m["high"].to_numpy(dtype="float64")
    l15 = velas_15m["low"].to_numpy(dtype="float64")
    c15 = velas_15m["close"].to_numpy(dtype="float64")
    idx15 = velas_15m.index

    precios_nivel = niveles["precio"].to_numpy(dtype="float64")
    # Vela de 4h a partir de la cual cada nivel puede operarse. El nivel
    # no existe antes: su rango no tiene ruptura confirmada.
    # `DatetimeIndex` y no `Series.to_numpy()`: sobre una columna con
    # zona horaria, esto último devuelve objetos `Timestamp` que numpy
    # no puede comparar con `datetime64`.
    alta_nivel = np.searchsorted(
        idx4.values, pd.DatetimeIndex(niveles["vigente_desde"]).values, side="left"
    )
    rango_de_nivel = niveles["rango_id"].to_numpy()
    nombre_de_nivel = niveles["nivel"].to_numpy()
    calidad_de_nivel = (
        niveles["calidad"].to_numpy(dtype="float64")
        if "calidad" in niveles.columns
        else np.zeros(len(niveles))
    )
    nota_minima = float(cfg.get("nota_minima_operacion", 0.0))

    # Estructura de mercado por vela, para el filtro direccional. Puede
    # juzgarse en un timeframe mayor que el de decisión: la dirección la
    # marca el gráfico grande.
    # Impulso de aproximación: cuánto ATR ha recorrido el precio en las
    # últimas velas antes de tocar el nivel. Mide si el precio llega
    # "disparado" o arrastrándose (SPEC.md §11).
    velas_impulso = int(cfg.get("velas_impulso", 6))
    impulso = (
        (velas_4h["close"] - velas_4h["close"].shift(velas_impulso)).abs()
        / calcular_atr(velas_4h, int(cfg["atr_periodo"]))
    ).to_numpy()
    impulso_minimo = cfg.get("impulso_minimo_atr")

    # Divergencias del MACD, una de las cinco señales de convergencia.
    # Se calculan siempre: son baratas y forman parte del registro de
    # cada operación aunque no se filtre por ellas.
    divergencia_por_vela = divergencia_vigente(
        divergencias(velas_4h["close"], macd(velas_4h["close"])["histograma"]),
        int(cfg.get("velas_vigencia_divergencia", 12)),
    ).to_numpy()

    # Alejamiento mínimo del nivel, en fracción del rango de la vela,
    # para dar por confirmado el rechazo. `null` = entrada limit directa.
    confirmar_rechazo = cfg.get("confirmar_rechazo")

    # Régimen de volatilidad (SPEC.md §16). La estrategia es de
    # reversión: un nivel tocado en tendencia fuerte se rompe, y uno
    # tocado con la volatilidad comprimida no da recorrido para llegar
    # a ningún objetivo.
    adx_maximo = cfg.get("adx_maximo")
    evitar_squeeze = bool(cfg.get("evitar_squeeze", False))
    if adx_maximo is not None or evitar_squeeze:
        adx_por_vela = adx(velas_4h, int(cfg.get("adx_periodo", 14)))[
            "adx"
        ].to_numpy()
        squeeze_por_vela = squeeze(
            velas_4h,
            int(cfg.get("squeeze_periodo", 20)),
            float(cfg.get("squeeze_desviaciones", 2.0)),
            float(cfg.get("squeeze_multiplicador_keltner", 1.5)),
        )["activo"].fillna(False).to_numpy().astype(bool)
    else:
        adx_por_vela = np.full(len(idx4), np.nan)
        squeeze_por_vela = np.zeros(len(idx4), dtype=bool)

    # Estocástico: dónde cierra el precio en su rango reciente. Es la
    # sexta señal del score (SPEC.md §15).
    estocastico_por_vela = estocastico(
        velas_4h,
        int(cfg.get("estocastico_periodo", 14)),
        int(cfg.get("estocastico_suavizado", 3)),
    ).to_numpy()
    estocastico_bajo = float(cfg.get("estocastico_bajo", 20.0))
    estocastico_alto = float(cfg.get("estocastico_alto", 80.0))
    señales_opcionales = tuple(cfg.get("senales_opcionales_en_score") or ())

    # Fase del histograma del TTM: si el momento acelera o se agota.
    fase_por_vela = fase_ttm(
        momento_ttm(velas_4h, int(cfg.get("squeeze_periodo", 20)))
    ).to_numpy()

    score_minimo = cfg.get("score_minimo_convergencia")
    escalones = cfg.get("escalones_tamano_convergencia") or {}
    confluencia_maxima = int(cfg.get("confluencia_maxima_senal", 2))

    # Se calcula siempre, aunque el filtro esté apagado: forma parte de
    # la nota de cada operación y hay que poder medirla.
    modo_estructura = cfg.get("filtro_estructura", "ninguno")
    tf_estructura = cfg.get("timeframe_estructura", "4h")
    if velas_estructura is not None and tf_estructura != "4h":
        regimen_por_vela = estructura_alineada(
            velas_estructura, idx4, config
        ).to_numpy()
    else:
        regimen_por_vela = estructura_mercado(velas_4h, config)["regimen"].to_numpy()

    # Zonas que aportan los imbalances semanales: su borde de entrada y
    # el 50% de lo que les queda sin rellenar, precalculados vela a
    # vela para no repetir el cálculo en cada operación.
    if imbalances is not None and not imbalances.empty:
        bordes_imb, medios_imb = puntos_objetivo(imbalances, velas_4h)
        zonas_imb = np.vstack([bordes_imb, medios_imb])
    else:
        zonas_imb = np.empty((0, len(idx4)), dtype="float64")

    # Primer índice de 15m de cada vela de 4h. Las velas de 15m de la
    # vela de 4h `i` son [bordes[i], bordes[i+1]).
    bordes = np.searchsorted(idx15.values, idx4.values, side="left")

    proximo_permitido = np.zeros(len(niveles), dtype="int64")
    abiertas: list[_Posicion] = []
    trades: list[dict] = []

    velas_timeout = None if timeout_4h is None else int(timeout_4h) * 16

    def registrar(pos: _Posicion, ts: pd.Timestamp, j: int, motivo: str) -> None:
        """Cierra la contabilidad de una posición y la anota."""
        nonlocal capital
        objetivos = list(pos.objetivos) + [np.nan] * N_OBJETIVOS
        riesgo = abs(pos.entrada - pos.stop_inicial) / pos.entrada
        capital += pos.notional * pos.pnl_pct
        trades.append(
            {
                "ts_entrada": pos.ts_entrada,
                "ts_salida": ts,
                "direccion": "long" if pos.direccion > 0 else "short",
                "rango_id": pos.rango_id,
                "nivel": pos.nivel,
                "calidad": pos.calidad,
                "score": pos.score,
                "senales": pos.senales,
                "confluencia": pos.confluencia,
                "r_potencial": pos.r_potencial,
                "regimen": pos.regimen,
                "entrada": pos.entrada,
                "stop_inicial": pos.stop_inicial,
                "tp1": objetivos[0],
                "tp2": objetivos[1],
                "tp3": objetivos[2],
                "objetivos_alcanzados": pos.alcanzados,
                "motivo_salida": motivo,
                "modo_fallback": pos.modo_fallback,
                "pnl_pct": pos.pnl_pct,
                "pnl_r": pos.pnl_pct / riesgo if riesgo > 0 else np.nan,
                "riesgo_pct": riesgo,
                "mae_pct": pos.mae_pct,
                "mfe_pct": pos.mfe_pct,
                "velas_15m": j - pos.j_entrada,
                "capital_despues": capital,
            }
        )

    # Bucle secuencial sobre velas: el estado de cada vela depende del
    # resultado de la anterior (posición abierta, stop movido a
    # break-even, cooldown de cada nivel), así que no es vectorizable.
    # Es la excepción justificada que contempla CLAUDE.md.
    for i in range(len(idx4) - 1):
        atr = atr4[i]
        cierre = close4[i]

        vigentes = alta_nivel <= i
        if vigencia is not None:
            vigentes &= (i - alta_nivel) < int(vigencia)

        # Rejilla de OBJETIVOS: niveles del FRVP vigentes más las zonas
        # que ofrecen los imbalances todavía sin rellenar.
        rejilla = precios_nivel[vigentes]
        if zonas_imb.shape[0]:
            rejilla = np.concatenate([rejilla, zonas_imb[:, i]])

        # Órdenes que quedan colocadas para la vela de 4h SIGUIENTE. El
        # lado lo fija el cierre de la vela actual, ya cerrada: si el
        # precio está por encima del nivel llegará desde arriba (long) y
        # si está por debajo llegará desde abajo (short).
        disponibles = vigentes & (proximo_permitido <= i)
        idx_ordenes = np.flatnonzero(disponibles & (precios_nivel != cierre))
        precio_orden = precios_nivel[idx_ordenes]
        dir_orden = np.where(cierre > precio_orden, 1, -1)

        # Filtro de rotación del área de valor (SPEC.md §12). Dentro del
        # área de valor el precio ROTA entre sus extremos: si viene del
        # POC hacia el VAH, lo normal es que lo alcance, no que rebote
        # antes. Operar contra esa rotación es apostar contra el
        # comportamiento típico del perfil.
        #   VAH + short  -> el precio llega desde abajo, o sea de dentro
        #   VAL + long   -> llega desde arriba, también de dentro
        if cfg.get("filtro_rotacion_area_valor", False) and idx_ordenes.size:
            nombres = nombre_de_nivel[idx_ordenes]
            desde_dentro = (
                ((nombres == "vah") & (dir_orden < 0))
                | ((nombres == "val") & (dir_orden > 0))
            )
            idx_ordenes = idx_ordenes[~desde_dentro]
            precio_orden = precio_orden[~desde_dentro]
            dir_orden = dir_orden[~desde_dentro]

        # Filtro de régimen: fuera las velas en las que la tendencia es
        # demasiado fuerte para una reversión, o en las que la
        # volatilidad está comprimida y no hay recorrido que cobrar.
        if evitar_squeeze and squeeze_por_vela[i]:
            idx_ordenes = idx_ordenes[:0]
            precio_orden = precio_orden[:0]
            dir_orden = dir_orden[:0]
        elif adx_maximo is not None and not (adx_por_vela[i] < adx_maximo):
            idx_ordenes = idx_ordenes[:0]
            precio_orden = precio_orden[:0]
            dir_orden = dir_orden[:0]

        # Filtro de impulso: no operar niveles a los que el precio llega
        # arrastrándose. Medido, esas entradas son las que peor rinden
        # (SPEC.md §11).
        if impulso_minimo is not None and not (impulso[i] >= impulso_minimo):
            idx_ordenes = idx_ordenes[:0]
            precio_orden = precio_orden[:0]
            dir_orden = dir_orden[:0]

        # Filtro direccional por estructura: comprar rebotes mientras el
        # precio construye escalones a la baja es la operación que peor
        # rinde de todo el experimento (SPEC.md §8).
        if modo_estructura != "ninguno" and idx_ordenes.size:
            regimen = regimen_por_vela[i]
            permitidas = np.array(
                [permite(regimen, int(d), modo_estructura) for d in dir_orden]
            )
            idx_ordenes = idx_ordenes[permitidas]
            precio_orden = precio_orden[permitidas]
            dir_orden = dir_orden[permitidas]

        ini = bordes[i + 1]
        fin = bordes[i + 2] if i + 2 < len(bordes) else len(o15)

        for j in range(ini, fin):
            apertura, maximo, minimo = o15[j], h15[j], l15[j]

            # --- gestión de las posiciones ya abiertas ---
            for pos in list(abiertas):
                excursion = pos.direccion * (
                    (maximo if pos.direccion > 0 else minimo) - pos.entrada
                ) / pos.entrada
                adversa = pos.direccion * (
                    (minimo if pos.direccion > 0 else maximo) - pos.entrada
                ) / pos.entrada
                pos.mfe_pct = max(pos.mfe_pct, excursion)
                pos.mae_pct = min(pos.mae_pct, adversa)

                toca_stop = (
                    minimo <= pos.stop if pos.direccion > 0 else maximo >= pos.stop
                )
                if toca_stop:
                    # Si la vela abre ya del lado malo del stop, se
                    # ejecuta en la apertura: el hueco no se puede
                    # operar al precio del stop.
                    peor = (
                        min(apertura, pos.stop)
                        if pos.direccion > 0
                        else max(apertura, pos.stop)
                    )
                    _cerrar_parcial(pos, peor, pos.restante, coste_stop)
                    motivo = "break_even" if pos.stop == pos.entrada else "stop"
                    registrar(pos, idx15[j], j, motivo)
                    abiertas.remove(pos)
                    continue

                while pos.alcanzados < len(pos.objetivos):
                    objetivo = pos.objetivos[pos.alcanzados]
                    alcanzado = (
                        maximo >= objetivo
                        if pos.direccion > 0
                        else minimo <= objetivo
                    )
                    if not alcanzado:
                        break
                    fraccion = min(pos.fracciones[pos.alcanzados], pos.restante)
                    _cerrar_parcial(pos, objetivo, fraccion, coste_objetivo)
                    pos.alcanzados += 1
                    if pos.alcanzados == mover_be_en and not pos.modo_fallback:
                        pos.be_pendiente = True

                if pos.restante <= 1e-9:
                    registrar(pos, idx15[j], j, f"tp{pos.alcanzados}")
                    abiertas.remove(pos)
                    continue

                if velas_timeout is not None and (j - pos.j_entrada) >= velas_timeout:
                    _cerrar_parcial(pos, c15[j], pos.restante, coste_stop)
                    registrar(pos, idx15[j], j, "timeout")
                    abiertas.remove(pos)
                    continue

                # El break-even surte efecto en la vela siguiente: dentro
                # de la misma vela no se conoce el orden de los sucesos,
                # y aplicarlo aquí podría cerrar en un stop que todavía
                # no existía cuando el precio pasó por ahí.
                if pos.be_pendiente:
                    pos.stop = pos.entrada
                    pos.be_pendiente = False

            # --- fills de las órdenes pendientes ---
            if len(abiertas) >= max_posiciones or idx_ordenes.size == 0:
                continue
            if not np.isfinite(atr) or atr <= 0:
                continue

            alcanzadas = np.where(
                dir_orden > 0, minimo <= precio_orden, maximo >= precio_orden
            )
            # Un nivel ya ocupado por otra posición no vuelve a llenarse.
            for pos in abiertas:
                alcanzadas &= idx_ordenes != pos.indice_nivel
            if not alcanzadas.any():
                continue

            # Si la vela alcanza varios niveles, el precio encontró
            # primero el más cercano a su apertura.
            candidatas = np.flatnonzero(alcanzadas)
            elegida = candidatas[
                np.argmin(np.abs(precio_orden[candidatas] - apertura))
            ]
            k = int(idx_ordenes[elegida])
            entrada = float(precio_orden[elegida])
            direccion = int(dir_orden[elegida])

            # ENTRADA POR CONFIRMACIÓN (SPEC.md §14). En vez de llenar
            # la orden al tocar el nivel, se espera al CIERRE de la vela
            # que lo tocó y solo se entra si el precio se alejó del
            # nivel: eso es un rechazo, frente a un cierre pegado o
            # pasado de largo, que es aceptación.
            #
            # Cuesta un peor precio de entrada —el precio ya se ha
            # movido— pero descarta los toques que atraviesan. Las dos
            # medidas que lo justifican son, medidas, las señales más
            # fuertes encontradas.
            if confirmar_rechazo is not None:
                recorrido = maximo - minimo
                if recorrido <= 0:
                    continue
                # Cuánto cerró la vela al otro lado del nivel, en
                # fracción de su propio rango.
                alejamiento = (c15[j] - entrada) * direccion / recorrido
                if alejamiento < confirmar_rechazo:
                    continue
                # Se entra al cierre de la vela, no en el nivel. El
                # stop se calcula después sobre este precio, y la
                # comprobación de que queda del lado correcto ya está
                # más abajo.
                entrada = float(c15[j])

            objetivos, fracciones, stop, fallback = _plan_de_salida(
                entrada, direccion, rejilla, float(atr), cfg
            )
            # Un stop que la propia vela de entrada ya deja atrás
            # significa que el nivel no da margen: no se abre.
            if (entrada - stop) * direccion <= 0:
                continue

            nota, confluencia, r_potencial = _nota_operacion(
                entrada, objetivos, stop, rejilla,
                float(calidad_de_nivel[k]), str(regimen_por_vela[i]), direccion,
            )
            if nota < nota_minima:
                continue

            # --- convergencia de señales ---
            activas = señales_activas(
                impulso=float(impulso[i]),
                impulso_minimo=float(impulso_minimo or 1.0),
                divergencia=str(divergencia_por_vela[i]),
                regimen=str(regimen_por_vela[i]),
                nivel=str(nombre_de_nivel[k]),
                confluencia=confluencia,
                direccion=direccion,
                confluencia_maxima=confluencia_maxima,
                estocastico=float(estocastico_por_vela[i]),
                estocastico_bajo=estocastico_bajo,
                estocastico_alto=estocastico_alto,
                fase=str(fase_por_vela[i]),
            )
            puntuacion = score(activas, señales_opcionales)
            if score_minimo is not None and puntuacion < int(score_minimo):
                continue

            # --- tamaño de la posición ---
            riesgo_pct = abs(entrada - stop) / abs(entrada)
            if modo_sizing == "riesgo_fijo":
                # Se decide cuánto se está dispuesto a PERDER, y el
                # tamaño sale de la distancia al stop. Así todas las
                # operaciones pesan lo mismo pase lo que pase con el
                # ATR, en vez de que una con el stop lejos arriesgue el
                # triple que otra con el stop cerca.
                #
                # El riesgo se escala con la convergencia: más margen
                # donde los datos están alineados, menos donde no.
                riesgo = riesgo_objetivo * multiplicador_tamano(
                    puntuacion, escalones
                )
                nominal = capital * riesgo / riesgo_pct
            else:
                nominal = capital / max_posiciones

            # Sin apalancamiento: no se puede comprometer más capital
            # del que queda libre.
            comprometido = sum(p.notional for p in abiertas)
            nominal = min(nominal, max(0.0, capital - comprometido))
            if nominal <= 0:
                continue

            abiertas.append(
                _Posicion(
                    direccion=direccion,
                    entrada=entrada,
                    ts_entrada=idx15[j],
                    j_entrada=j,
                    rango_id=int(rango_de_nivel[k]),
                    nivel=str(nombre_de_nivel[k]),
                    indice_nivel=k,
                    calidad=nota,
                    score=puntuacion,
                    senales="+".join(n for n, v in activas.items() if v),
                    confluencia=confluencia,
                    r_potencial=r_potencial,
                    regimen=str(regimen_por_vela[i]),
                    stop=stop,
                    stop_inicial=stop,
                    objetivos=objetivos,
                    fracciones=fracciones,
                    modo_fallback=fallback,
                    notional=nominal,
                )
            )
            proximo_permitido[k] = i + cooldown
            # La orden recién ejecutada deja de estar pendiente en esta
            # misma vela de 4h.
            mascara = idx_ordenes != k
            idx_ordenes = idx_ordenes[mascara]
            precio_orden = precio_orden[mascara]
            dir_orden = dir_orden[mascara]

    # Al agotarse el histórico, lo que siga abierto se cierra al último
    # cierre conocido: dejarlo fuera del recuento inflaría el resultado
    # ocultando las posiciones perdedoras que aún no habían saltado.
    for pos in list(abiertas):
        _cerrar_parcial(pos, float(c15[-1]), pos.restante, coste_stop)
        registrar(pos, idx15[-1], len(c15) - 1, "fin_historico")
        abiertas.remove(pos)

    if not trades:
        logger.warning("La simulación no produjo ninguna operación")
        return pd.DataFrame(columns=COLUMNAS_TRADE), pd.Series(dtype="float64")

    df_trades = pd.DataFrame(trades, columns=COLUMNAS_TRADE)
    equity = pd.Series(
        df_trades["capital_despues"].to_numpy(),
        index=pd.DatetimeIndex(df_trades["ts_salida"], name="timestamp"),
        name="capital",
    )
    return df_trades, equity
