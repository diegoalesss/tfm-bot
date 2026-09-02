"""Detección de rango lateral — Filtro 1 (SPEC.md §5).

Metodología: caja de Darvas con techo y suelo en el pivote confirmado
más extremo de cada lado. El rango lateral se modela como un
rectángulo derivado de la ESTRUCTURA del precio (pivotes de swing
confirmados), y solo después se contrasta contra los cierres. Esa
separación entre "cómo se construye el rectángulo" y "cómo se valida"
es deliberada: si el rectángulo se derivase de los mismos cierres que
luego se cuentan (percentiles, media ± k·σ, área de valor de
cierres), el criterio de contención se cumpliría por construcción y
no filtraría nada.

La detección se corre SOLO sobre velas de 4h, con VARIAS VENTANAS de
distinto tamaño (``filtro_1_rango_lateral.ventanas`` en
``config.yaml``), para captar rangos anidados de distinta escala sin
elegir una a priori.

Desviación respecto a Darvas clásico
-------------------------------------
La caja de Darvas original fija el techo en UN máximo confirmado y el
suelo en el mínimo de las barras siguientes. Aquí el nivel se busca
desde el pivote más extremo hacia dentro y se acepta el primero
confirmado por varios toques separados en el tiempo. Es una variante
robustecida, no Darvas literal, y se documenta como tal (también en
SPEC.md §5).

Flujo de uso
-------------
1. :func:`detectar_rangos_laterales` devuelve TODOS los rangos que
   cumplen los criterios, con la misma consolidación vista por varias
   ventanas a la vez (del orden de 100 sobre 2 años).
2. :func:`seleccionar_rangos` se queda con los operables, unos 20,
   descartando los redundantes y los de peor nota.

Criterios de declaración de rango
----------------------------------
Sobre la ventana móvil de N velas que termina en la vela ``t``, se
declara rango lateral si se cumplen a la vez:

1. R² < ``r2_maximo``: el precio rebota dentro de la ventana, no
   sigue una recta.
2. Existe rectángulo: hay al menos ``toques_minimos_nivel`` swing
   highs agrupados (techo) y otros tantos swing lows agrupados
   (suelo), con techo > suelo.
3. Altura: ``techo - suelo`` no supera ``altura_maxima_atr`` veces el
   ATR de la vela de declaración (ver "Tope de altura").
4. Deriva: la recta de regresión no se desplaza, a lo largo de la
   ventana, más de ``fraccion_pendiente_altura`` veces la altura del
   rectángulo (ver "Criterio de pendiente").
5. Contención: al menos ``contencion_minima_cierres`` de los cierres
   de la ventana quedan dentro del rectángulo. Las mechas NO cuentan:
   solo los cierres.
6. Estabilidad: dentro de la ventana no hay ninguna racha de
   ``cierres_fuera_fin_rango`` cierres consecutivos fuera del
   rectángulo (ver más abajo).
7. Oscilación: el precio cruza el punto medio del rectángulo al menos
   ``cruces_minimos_medio`` veces dentro de la ventana (ver
   "Oscilación").

Tipo de rango: principal frente a secundario
---------------------------------------------
Cada ventana declara en ``config.yaml`` un ``tipo``, que describe la
ESCALA del rectángulo, no su calidad, y se propaga a la columna
``tipo`` del resultado. **Ambos son operables**: sobre los dos se
traza un FRVP y de los dos salen niveles de entrada.

- ``principal`` (N = 150, 250, 400; de 1 a 3 meses): laterales de
  estructura mayor. En la operativa del autor son LOS MÁS OPERABLES:
  cuanto más tiempo pasa el precio construyendo el rango, más volumen
  acumula el perfil y más fiables son su VAH, POC y VAL.
- ``secundario`` (N = 40, 60; de 1 a 2 semanas): rangos anidados
  dentro de los principales, también operables pero de menor peso.

La distinción tuvo antes otro nombre —``contexto`` y ``operativo``—
heredado de la fase multi-timeframe, en la que se suponía que solo
los rectángulos pequeños generaban entradas. Era engañoso: los
grandes no delimitan estructura para que otros operen dentro, son
ellos mismos el mejor sitio donde operar.

Tope de altura
---------------
``altura_maxima_atr`` acota ``techo - suelo`` en múltiplos del ATR,
medido en la vela de declaración. Se expresa en ATR y no en
porcentaje del precio porque el porcentaje no es invariante de
escala: fue justo el problema del criterio de contención que este
módulo sustituyó.

El tope escala con la RAÍZ del tamaño de ventana, que es como crece
el recorrido de un paseo aleatorio::

    tope = altura_maxima_atr_base * sqrt(N / altura_calibrada_sobre_velas)

Resultante: N=40 -> 6.5, N=60 -> 8.0, N=150 -> 12.6, N=250 -> 16.3,
N=400 -> 20.7 ATR. Un tope fijo partía los rangos largos en
fragmentos: los laterales de 3 meses de BTC miden entre 7 y 12.6 ATR
y quedaban sistemáticamente rechazados por el tope de 5 de la fase
anterior.

Criterio de pendiente
----------------------
    |pendiente| * N  <  fraccion_pendiente_altura * (techo - suelo)

La deriva total que la recta de regresión proyecta a lo largo de la
ventana no puede consumir más de esa fracción de la altura del
rectángulo. Con 0.5: si la deriva igualara la altura completa, el
precio habría recorrido el rectángulo de suelo a techo a lo largo de
la ventana, que es un canal inclinado y no un rango; limitándola a la
mitad, la componente tendencial se queda como mucho con medio
rectángulo y el resto tiene que ser oscilación.

El ancla es estructural: la escala la pone el propio rectángulo. La
versión anterior usaba un porcentaje fijo del precio medio
(``deriva_maxima_pct = 0.075``), heredado del criterio de contención
ya eliminado. Como el umbral depende ahora del rectángulo, este
criterio no se puede evaluar hasta haberlo construido, y por eso sale
del prefiltro vectorizado.

Se conserva el factor ``N`` (en rigor la deriva a lo largo de la
ventana es ``pendiente * (N - 1)``) por continuidad con el criterio
anterior; la diferencia es inferior al 2% del umbral.

Definición del pivote (sobre cierres, no sobre mechas)
-------------------------------------------------------
Un swing high en la vela ``i`` exige que ``close[i]`` sea
estrictamente mayor que los ``R`` cierres anteriores y que los ``R``
cierres posteriores (``R`` = ``barras_confirmacion_pivote``, propio de
cada timeframe). Simétrico para el swing low. Se usa el mismo número
de barras a izquierda y derecha: un ``L`` distinto de ``R`` sería un
grado de libertad extra sin justificación.

Los niveles se definen sobre cierres, siguiendo la metodología de
Darvas: reduce las señales falsas por picos intradía.

Nivel por extremo, no por moda
-------------------------------
El techo es el swing high confirmado MÁS ALTO de la ventana; el suelo,
el swing low confirmado más bajo. No la moda de la distribución de
pivotes.

Motivo: el rectángulo y el FRVP tienen trabajos distintos. El
rectángulo DELIMITA la estructura; el FRVP encuentra los niveles
relevantes DENTRO de ella. Si el rectángulo buscara además dónde se
agrupa el precio, duplicaría el trabajo del FRVP, y peor, porque
contaría toques en vez de medir volumen.

Y para la regla de ruptura el extremo es lo correcto: superar el
máximo confirmado previo significa algo; superar la moda no, porque
el precio ya estuvo por encima varias veces sin que el rango muriera.

El riesgo de que un valor atípico defina el nivel está acotado por
construcción, no por la agrupación: los pivotes se calculan sobre
CIERRES y exigen confirmación a ambos lados, así que una mecha
intradía no puede fijar un extremo. El tope de altura en ATR queda
como salvaguarda adicional.

Los toques se siguen contando —los pivotes que caen dentro de
``anchura_banda_atr * ATR`` por debajo del extremo, o por encima para
el suelo— pero solo para exigir un mínimo: un extremo aislado, sin
ningún otro pivote cerca, no confirma nivel. Esa tolerancia usa la
misma unidad y el mismo orden de magnitud que SPEC.md §4 fija para el
test de robustez del anclaje, así que no introduce una escala nueva.

Descartado: nivel por moda (banda deslizante de anchura fija con más
toques, nivel = mediana de la banda ganadora). Medido sobre el lateral
de feb-may 2026 de ONDO en 1d, el nivel modal daba techo 0.2699 con 8
toques frente a un extremo de 0.2943 con 2, es decir, un rectángulo
cortado muy por debajo de la estructura real: el interior del rango
se visita más que su borde, por definición.

Nota deliberada sobre el ATR y las mechas: el ATR se calcula con
``high``/``low``, es decir, con mechas, mientras que los niveles se
definen solo con cierres. No hay contradicción: aquí el ATR NO define
ningún nivel de precio, solo la UNIDAD DE TOLERANCIA con la que se
decide si dos pivotes "están en el mismo sitio". Es una medida de
volatilidad, no un borde del rectángulo.

Congelación del rectángulo y fin del rango
-------------------------------------------
En el momento en que se declara el rango, el rectángulo se CONGELA.
A partir de ahí el rango solo puede terminar por acumular
``cierres_fuera_fin_rango`` cierres consecutivos fuera del
rectángulo congelado. Motivos:

- Si el rectángulo se recalculara en cada vela, se ensancharía para
  absorber los cierres que se salen y la regla de los cierres
  consecutivos no llegaría a dispararse nunca.
- Un rectángulo móvil hace el backtest irreproducible.

Una vela que sale y vuelve es un barrido de stops, no una ruptura:
por eso se exigen cierres CONSECUTIVOS. El rango termina en la vela
anterior a la primera de la racha; la racha de ruptura no pertenece a
ningún rango, y la detección se reanuda después de ella.

El criterio 5 (estabilidad dentro de la ventana) es una consecuencia
necesaria de esto, no una regla añadida: sin él, una ventana podría
superar el umbral de contención teniendo ya dentro una racha completa
de cierres fuera, es decir, un rango que habría terminado antes de
declararse. Aplicar la misma regla dentro y fuera de la ventana lo
resuelve.

Si al agotarse el histórico el rango todavía no ha roto, se devuelve
con ``en_curso=True`` y ``fin`` en la última vela disponible: es un
rango abierto, no un rango terminado.

La columna ``contencion`` mide la ventana de DECLARACIÓN, no el tramo
completo. Sobre el tramo extendido la contención no está garantizada:
el rango solo termina por racha, así que cierres fuera alternados no
lo cierran aunque bajen del umbral. Medido sobre ONDO y BTC (2 años,
1w/1d/4h), la contención del tramo completo queda por encima de la de
declaración en mediana, y solo 6 de 67 rangos bajan del umbral, el
peor a 0.82. Es una desviación menor, pero es real y conviene tenerla
presente al usar ``contencion`` como filtro aguas abajo.

N, duración mínima y barras de confirmación por timeframe
----------------------------------------------------------
SPEC.md §5 solo fija N y duración mínima para 4h (N=60, mínimo 40).
Al pasar a tres timeframes reales se escalan por el significado
calendario de cada uno::

    4h:  N=60  (~10 días)   mínimo 40 (~6.7 días)   R=3  (12 h)
    1d:  N=60  (~2 meses)   mínimo 30 (~1 mes)      R=3  (3 días)
    1w:  N=26  (~6 meses)   mínimo 13 (~1 trimestre) R=2  (2 semanas)

El criterio del pivote es de forma, no de escala, así que en
principio ``R`` debería ser igual en los tres (mismo argumento que
justifica un R² y una contención comunes). Lo que rompe la simetría
es el RECUENTO de pivotes disponibles: con ``L=R=3`` la probabilidad
de que una vela sea el máximo de 7 consecutivas es ≈1/7, así que en
una ventana de N velas salen del orden de ``N/7`` swing highs: ~8-9
con N=60, pero solo ~3-4 con N=26. Con tres puntos no se puede hablar
de agrupación. Bajar a ``R=2`` en 1w recupera ~5 pivotes por lado.
``R=2`` es además el fractal de Williams clásico (5 barras), no un
valor inventado.

Son valores de partida razonados, no calibrados empíricamente, igual
que los que sustituyen en SPEC.md.

Oscilación: distinguir un lateral de una formación en V
--------------------------------------------------------
Una V —el precio entra por un extremo del rectángulo, lo recorre
hasta el opuesto y vuelve— pasa todos los criterios anteriores: su
pendiente neta es casi nula, su R² es bajo y queda contenida dentro
del rectángulo. Pero no es lateralidad, es un movimiento direccional
de ida y vuelta.

Lo que las separa es la OSCILACIÓN: en un lateral el precio recorre
el rectángulo de arriba abajo varias veces; en una V lo hace una
sola. Se cuenta como cruces del punto medio del rectángulo dentro de
la ventana, y se exige un mínimo de ``cruces_minimos_medio``.

El umbral de 7 es el más bajo que elimina las siete formas en V
confirmadas visualmente sobre ONDO y BTC en 4h. Con 6 sobrevive ONDO
2026-05-12, que es el caso que motivó el criterio.

Se mide sobre la ventana de declaración y no sobre el tramo completo
del rango: el tramo completo no se conoce hasta que el rango termina,
así que filtrar con él sería lookahead.

El umbral se escala con el tamaño de la ventana de cada timeframe,
conservando la tasa calibrada en 4h::

    umbral = round(cruces_minimos_medio * N / cruces_calibrados_sobre_velas)

es decir, 7/60 ≈ 0.117 cruces por vela: 7 en 4h y 1d (N=60), 3 en 1w
(N=26). Sin escalar, el mismo número absoluto sobre una ventana más
corta exige proporcionalmente más oscilación; medido, los 7 cruces
sobre las 26 velas de 1w dejaban a BTC sin ningún rango semanal, que
es justo la capa de contexto del diseño.

Descartado: usar el número de toques del nivel como medida de
oscilación. Tenía sentido cuando el nivel era la moda (los toques
contaban el racimo denso), pero con el nivel por extremo cuentan solo
los pivotes pegados al extremo, que en un lateral son dos o tres por
mucho que el precio oscile. Medido: subir ``toques_minimos_nivel`` de
2 a 3 reduce la detección en 4h de 48 a 6 rangos, sigue dejando pasar
una V y elimina el lateral de feb-may 2026 de ONDO.

Descartado: contar recorridos completos entre bandas del 25% superior
e inferior. Satura —laterales buenos se quedan en 2 recorridos— y aun
así deja pasar una V.

Rangos solapados
-----------------
Dos rangos consecutivos del mismo timeframe pueden solaparse en el
tiempo: ``inicio`` se sitúa N-1 velas antes de la vela de
declaración, así que un rango declarado tras una ruptura arrastra su
ventana sobre el tramo del rango anterior.

Este módulo NO los resuelve: los devuelve todos y los marca con
``grupo_solape``, un identificador compartido por los rangos que se
encadenan. La resolución (quedarse con uno, y con cuál) se delega al
Filtro 3, que ya tiene pendiente en SPEC.md elegir el rango previo
"más importante" entre varios candidatos; resolverlo dos veces con
criterios distintos sería incoherente.

El marcado es causalmente seguro. La detección se reanuda tras
confirmar la ruptura del rango anterior, de modo que al declararse un
rango el anterior ya está cerrado y su ``fin`` es conocido: decidir
si hay solape no requiere información futura. El grupo de un rango se
fija en su declaración y no se revisa después, aunque más tarde se le
unan otros; por eso el marcado sobrevive al test de truncado del
histórico.

Descartadas dos alternativas que sí resolvían aquí:

- Quedarse con el rango más largo introduce lookahead: para saber
  cuál es el más largo hay que esperar a que ambos terminen, y para
  entonces ya se habría operado el primero.
- Quedarse con el más reciente favorece justo los rectángulos de peor
  calidad estructural, los declarados tarde, que incluyen en su
  ventana la ruptura del rango anterior.

El agrupamiento es transitivo (enlace simple): si A solapa con B y B
con C, los tres comparten grupo aunque A y C no se toquen. Es
deliberado, para que el Filtro 3 reciba el conjunto completo de
candidatos encadenados y no fragmentos de él.

Precio de referencia
---------------------
Se usa el cierre (``close``) para la regresión, para ``precio_medio``,
para los pivotes y para la contención. ``high``/``low`` solo
intervienen en el ATR (unidad de tolerancia) y en
``precio_max``/``precio_min`` del tramo, que SPEC.md §4 necesita para
delimitar el rango de precio del perfil FRVP.

Nota sobre lookahead bias
--------------------------
Verificado punto por punto:

- Regresión, R², precio medio y ATR se calculan con ``rolling`` hacia
  atrás: la ventana que termina en ``i`` solo usa velas ``i-N+1..i``,
  todas cerradas. No se usa ``center=True``.
- La detección de pivotes SÍ mira R velas hacia adelante (es
  intrínseco: un swing high no se distingue de un máximo local
  cualquiera hasta que pasan R velas). La fuga se evita en el USO, no
  en el cálculo: al evaluar la ventana que termina en ``t`` solo se
  admiten pivotes en velas ``i`` con ``i + R <= t``. Las últimas R
  velas de la ventana nunca aportan pivote. Un pivote no existe hasta
  que está confirmado.
- La contención usa los N cierres de la ventana, todos ya cerrados.
- El fin del rango se busca hacia adelante desde el rectángulo ya
  congelado, y el resultado se etiqueta con su instante de
  conocimiento (ver ``declarado_en`` y ``confirmado_en``). La
  existencia del rango y su rectángulo se conocen en
  ``declarado_en``, nunca en ``inicio``: ``inicio`` está N-1 velas
  antes. Su ``fin`` no se conoce hasta ``confirmado_en``, que llega
  ``cierres_fuera_fin_rango`` velas después de ``fin``. El consumidor
  de este DataFrame (FRVP, backtest) debe respetar esas dos marcas
  temporales: usar un rango antes de ``declarado_en``, o su ``fin``
  antes de ``confirmado_en``, sería lookahead.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COLUMNAS_REQUERIDAS = ["high", "low", "close"]
COLUMNAS_RESULTADO = [
    "ventana",
    "tipo",
    "inicio",
    "fin",
    "techo",
    "suelo",
    "precio_max",
    "precio_min",
    "calidad",
    "contencion",
    "altura_atr",
    "r2",
    "cruces",
    "toques_techo",
    "toques_suelo",
    "declarado_en",
    "confirmado_en",
    "en_curso",
    "grupo_solape",
]

CLAVES_CFG_GLOBALES = (
    "ventanas",
    "fraccion_duracion_minima",
    "fraccion_separacion_toques",
    "barras_confirmacion_pivote",
    "altura_maxima_atr_base",
    "altura_calibrada_sobre_velas",
    "altura_maxima_pct",
    "r2_maximo",
    "fraccion_pendiente_altura",
    "contencion_minima_cierres",
    "cruces_minimos_medio",
    "banda_bordes",
    "ocupacion_bordes_minima",
    "recorridos_minimos_base",
    "fraccion_cola_contencion",
    "altura_sin_penalizar_pct",
    "pct_por_recorrido_extra",
    "cierres_fuera_fin_rango",
    "toques_minimos_nivel",
    "anchura_banda_atr",
    "atr_periodo",
    "calidad_minima_seleccion",
    "solape_maximo_seleccion",
)
CLAVES_CFG_VENTANA = ("velas", "tipo")
TIPOS_VALIDOS = ("secundario", "principal")

# Velas mínimas para evaluar el R² de un borde del rango. Por debajo
# de esta muestra el R² no es significativo: cualquier movimiento
# suave parece una tendencia.
VELAS_BORDE_MINIMAS = 20


def _validar_config(cfg: dict) -> None:
    """Comprueba que la configuración del Filtro 1 tiene todas las claves.

    Falla ruidosamente ante un ``config.yaml`` desactualizado en vez de
    dejar que el módulo opere con parámetros a medias.

    Parameters
    ----------
    cfg : dict
        Contenido de ``config["filtro_1_rango_lateral"]``.

    Raises
    ------
    KeyError
        Si falta alguna clave global o alguna clave de una ventana.
    ValueError
        Si la lista de ventanas está vacía o si el ``tipo`` de alguna
        no es ``"secundario"`` ni ``"principal"``.
    """
    faltantes = [clave for clave in CLAVES_CFG_GLOBALES if clave not in cfg]
    if faltantes:
        logger.error(
            "Faltan claves en config.yaml (filtro_1_rango_lateral): %s", faltantes
        )
        raise KeyError(f"Faltan claves en filtro_1_rango_lateral: {faltantes}")

    if not cfg["ventanas"]:
        logger.error("La lista `ventanas` de config.yaml está vacía")
        raise ValueError("La lista `ventanas` está vacía")

    for parametros in cfg["ventanas"]:
        faltantes_v = [c for c in CLAVES_CFG_VENTANA if c not in parametros]
        if faltantes_v:
            logger.error("Faltan claves de una ventana en config.yaml: %s", faltantes_v)
            raise KeyError(f"Faltan claves de la ventana {parametros}: {faltantes_v}")

        if parametros["tipo"] not in TIPOS_VALIDOS:
            logger.error(
                "Tipo de rango no válido en la ventana de %s velas: %r "
                "(esperado uno de %s)",
                parametros["velas"],
                parametros["tipo"],
                TIPOS_VALIDOS,
            )
            raise ValueError(
                f"Tipo de rango no válido en la ventana de "
                f"{parametros['velas']} velas: {parametros['tipo']!r}"
            )


def _regresion_rodante(
    precio: pd.Series, ventana: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calcula pendiente, R² y precio medio de una regresión lineal
    rodante del precio frente al tiempo.

    Vectorizado: usa ``rolling().corr()`` y ``rolling().std()`` de
    pandas en vez de ajustar una regresión vela a vela. La
    correlación (y por tanto R²) es invariante a desplazamientos del
    eje temporal, así que correlacionar el precio con un índice
    entero global (en vez de uno reiniciado a 0 en cada ventana) da
    exactamente el mismo resultado que usar un índice local 0..N-1
    dentro de cada ventana.

    Parameters
    ----------
    precio : pd.Series
        Serie de precio (cierre) con índice temporal, ordenada
        cronológicamente.
    ventana : int
        Tamaño de la ventana móvil, en velas del timeframe de
        ``precio``.

    Returns
    -------
    tuple[pd.Series, pd.Series, pd.Series]
        ``(pendiente, r2, precio_medio)``, alineadas con el índice de
        ``precio``. Las primeras ``ventana - 1`` posiciones son NaN
        (no hay histórico suficiente para completar la ventana).
    """
    tiempo = pd.Series(np.arange(len(precio), dtype="float64"), index=precio.index)

    correlacion = precio.rolling(ventana).corr(tiempo)
    desviacion_precio = precio.rolling(ventana).std(ddof=1)
    desviacion_tiempo = float(np.std(np.arange(ventana, dtype="float64"), ddof=1))

    pendiente = correlacion * desviacion_precio / desviacion_tiempo
    r2 = correlacion.pow(2)
    precio_medio = precio.rolling(ventana).mean()

    return pendiente, r2, precio_medio


def _atr(df: pd.DataFrame, periodo: int) -> pd.Series:
    """Calcula el ATR (Average True Range) como media móvil simple del
    rango verdadero.

    Se usa únicamente como unidad de tolerancia para agrupar pivotes
    (ver docstring del módulo), no como nivel de precio.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con columnas ``high``, ``low`` y ``close``, ordenadas
        cronológicamente.
    periodo : int
        Número de velas de la media móvil.

    Returns
    -------
    pd.Series
        ATR alineado con el índice de ``df``. Las primeras
        ``periodo`` posiciones son NaN.

    Notes
    -----
    Sin lookahead: el rango verdadero de la vela ``i`` usa la vela
    ``i`` y el cierre de ``i-1``, y la media es ``rolling`` hacia
    atrás.
    """
    cierre_previo = df["close"].shift(1)
    rango_verdadero = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - cierre_previo).abs(),
            (df["low"] - cierre_previo).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return rango_verdadero.rolling(periodo).mean()


def _posiciones_pivotes(
    cierre: pd.Series, barras_confirmacion: int
) -> tuple[np.ndarray, np.ndarray]:
    """Localiza los swing highs y swing lows sobre los cierres.

    Un swing high en la vela ``i`` exige que ``close[i]`` sea
    estrictamente mayor que los ``barras_confirmacion`` cierres
    anteriores y que los ``barras_confirmacion`` posteriores; simétrico
    para el swing low. La comparación estricta por ambos lados descarta
    las mesetas (varias velas al mismo precio) sin necesidad de una
    regla de desempate.

    Vectorizado con dos ``rolling().max()`` desplazadas, sin recorrer
    velas.

    Parameters
    ----------
    cierre : pd.Series
        Serie de cierres, ordenada cronológicamente.
    barras_confirmacion : int
        Número de velas exigidas a cada lado del pivote (``R``).

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        ``(posiciones_altos, posiciones_bajos)``, posiciones enteras
        ascendentes dentro de ``cierre``.

    Notes
    -----
    Este cálculo mira ``barras_confirmacion`` velas hacia adelante, lo
    cual es intrínseco a la definición de pivote. La fuga temporal se
    evita en el uso: quien consuma estas posiciones debe admitir solo
    los pivotes con ``posicion + barras_confirmacion <= t`` al evaluar
    la vela ``t`` (ver :func:`_rectangulo_ventana`). Las posiciones sin
    contexto completo a izquierda o derecha quedan excluidas
    automáticamente, porque el ``rolling`` devuelve NaN y la
    comparación resulta ``False``.
    """
    maximo_izquierda = cierre.shift(1).rolling(barras_confirmacion).max()
    maximo_derecha = (
        cierre.shift(-barras_confirmacion).rolling(barras_confirmacion).max()
    )
    minimo_izquierda = cierre.shift(1).rolling(barras_confirmacion).min()
    minimo_derecha = (
        cierre.shift(-barras_confirmacion).rolling(barras_confirmacion).min()
    )

    es_alto = (cierre > maximo_izquierda) & (cierre > maximo_derecha)
    es_bajo = (cierre < minimo_izquierda) & (cierre < minimo_derecha)

    return np.flatnonzero(es_alto.to_numpy()), np.flatnonzero(es_bajo.to_numpy())


def _toques_separados(posiciones: np.ndarray, separacion_minima: int) -> int:
    """Cuenta toques que son ocasiones DISTINTAS, no velas contiguas.

    Dos pivotes separados por una vela pertenecen al mismo movimiento:
    son un solo test del nivel, no dos. Se recorren en orden temporal y
    solo se cuenta uno cada vez que han pasado al menos
    ``separacion_minima`` velas desde el anterior contado.

    Parameters
    ----------
    posiciones : np.ndarray
        Posiciones temporales de los pivotes, no necesariamente
        ordenadas.
    separacion_minima : int
        Velas mínimas entre dos toques para contarlos por separado.

    Returns
    -------
    int
        Número de toques independientes.
    """
    if posiciones.size == 0:
        return 0

    ordenadas = np.sort(posiciones)
    contados = 1
    ultima = ordenadas[0]
    for posicion in ordenadas[1:]:
        if posicion - ultima >= separacion_minima:
            contados += 1
            ultima = posicion
    return contados


def _nivel_por_extremo(
    precios: np.ndarray,
    posiciones: np.ndarray,
    anchura: float,
    toques_minimos: int,
    separacion_minima: int,
    hacia_arriba: bool,
) -> tuple[float, int] | None:
    """Devuelve el nivel más extremo que el precio haya testeado varias
    veces en ocasiones distintas.

    El nivel se busca desde el pivote MÁS EXTREMO hacia dentro (el más
    alto para un techo, el más bajo para un suelo), no en la moda de la
    distribución, y se acepta el primero que reúna ``toques_minimos``
    pivotes dentro de ``anchura`` **separados en el tiempo**.

    La separación es lo que impide que un pico aislado defina el borde:
    dos velas contiguas del mismo impulso cuentan como un solo toque,
    así que el nivel acaba en el precio donde el mercado volvió de
    verdad más de una vez. Sin ella, la cola de la tendencia anterior
    —dos swing highs consecutivos de la misma caída— fijaba el techo
    muy por encima del rango real.

    Descender hacia dentro en vez de rechazar la ventana es
    deliberado: si el extremo no está confirmado, el rango no
    desaparece, simplemente se traza en el nivel confirmado que sí
    haya, que es lo que hace un analista a mano.

    Parameters
    ----------
    precios : np.ndarray
        Precios de los pivotes candidatos (swing highs o swing lows).
    posiciones : np.ndarray
        Posiciones temporales de esos pivotes, alineadas con
        ``precios``.
    anchura : float
        Tolerancia para contar toques, en unidades de precio.
    toques_minimos : int
        Toques independientes mínimos para aceptar un nivel.
    separacion_minima : int
        Velas mínimas entre dos toques para contarlos por separado.
    hacia_arriba : bool
        ``True`` para un techo (se busca desde el máximo hacia abajo),
        ``False`` para un suelo (desde el mínimo hacia arriba).

    Returns
    -------
    tuple[float, int] | None
        ``(nivel, n_toques)``, o ``None`` si ningún nivel reúne
        suficientes toques independientes.
    """
    if precios.size < toques_minimos:
        return None

    # Candidatos ordenados de más extremo a menos: el primero que
    # reúna toques independientes suficientes gana.
    orden = np.argsort(-precios if hacia_arriba else precios)

    for indice in orden:
        nivel = float(precios[indice])
        if hacia_arriba:
            cerca = (precios <= nivel) & (precios >= nivel - anchura)
        else:
            cerca = (precios >= nivel) & (precios <= nivel + anchura)

        toques = _toques_separados(posiciones[cerca], separacion_minima)
        if toques >= toques_minimos:
            return nivel, toques

    return None


def _rectangulo_ventana(
    cierres: np.ndarray,
    posiciones_altos: np.ndarray,
    posiciones_bajos: np.ndarray,
    inicio: int,
    fin: int,
    barras_confirmacion: int,
    anchura: float,
    toques_minimos: int,
    separacion_minima: int,
) -> tuple[float, float, int, int] | None:
    """Construye el rectángulo (techo y suelo) de una ventana concreta.

    Solo se admiten pivotes ya confirmados en la vela ``fin``: un
    pivote en la vela ``i`` no existe hasta la vela
    ``i + barras_confirmacion``, así que las últimas
    ``barras_confirmacion`` velas de la ventana nunca aportan pivote.

    Parameters
    ----------
    cierres : np.ndarray
        Cierres de toda la serie.
    posiciones_altos, posiciones_bajos : np.ndarray
        Posiciones de swing highs y swing lows en toda la serie,
        ascendentes (ver :func:`_posiciones_pivotes`).
    inicio, fin : int
        Primera y última posición de la ventana, ambas incluidas.
    barras_confirmacion : int
        Velas de confirmación del pivote (``R``).
    anchura : float
        Anchura de la banda de agrupación, en unidades de precio.
    toques_minimos : int
        Toques mínimos por lado para aceptar un nivel.
    separacion_minima : int
        Velas mínimas entre dos toques para contarlos por separado.

    Returns
    -------
    tuple[float, float, int, int] | None
        ``(techo, suelo, toques_techo, toques_suelo)``, o ``None`` si
        algún lado no reúne suficientes toques o si el rectángulo sale
        degenerado (techo por debajo o igual que el suelo, posible
        cuando las agrupaciones de máximos y mínimos se solapan).
    """
    ultimo_confirmable = fin - barras_confirmacion
    if ultimo_confirmable < inicio:
        return None

    altos = posiciones_altos[
        (posiciones_altos >= inicio) & (posiciones_altos <= ultimo_confirmable)
    ]
    bajos = posiciones_bajos[
        (posiciones_bajos >= inicio) & (posiciones_bajos <= ultimo_confirmable)
    ]

    resultado_techo = _nivel_por_extremo(
        cierres[altos], altos, anchura, toques_minimos,
        separacion_minima, hacia_arriba=True,
    )
    if resultado_techo is None:
        return None

    resultado_suelo = _nivel_por_extremo(
        cierres[bajos], bajos, anchura, toques_minimos,
        separacion_minima, hacia_arriba=False,
    )
    if resultado_suelo is None:
        return None

    techo, toques_techo = resultado_techo
    suelo, toques_suelo = resultado_suelo
    if techo <= suelo:
        return None

    return techo, suelo, toques_techo, toques_suelo


def _cruces_punto_medio(cierres: np.ndarray, medio: float) -> int:
    """Cuenta cuántas veces la serie de cierres cruza un nivel.

    Mide la oscilación dentro del rectángulo: cada cruce del punto
    medio es medio recorrido de un extremo al otro. Los cierres
    exactamente en el nivel se ignoran, para que una serie que se
    apoya en él no genere cruces falsos.

    Parameters
    ----------
    cierres : np.ndarray
        Cierres del tramo a medir.
    medio : float
        Nivel a cruzar, normalmente ``(techo + suelo) / 2``.

    Returns
    -------
    int
        Número de cambios de lado.
    """
    signo = np.sign(cierres - medio)
    signo = signo[signo != 0]
    if signo.size < 2:
        return 0
    return int(np.count_nonzero(np.diff(signo)))


def _recortar_por_contencion(
    cierres: np.ndarray,
    inicio: int,
    fin: int,
    techo: float,
    suelo: float,
    contencion_minima: float,
) -> int | None:
    """Adelanta el inicio del rango hasta que el tramo entero está
    contenido en el rectángulo.

    Un rango puede empezar en una vela que sí cierra dentro y venir
    precedido de un tramo de entrada mayormente fuera: basta con que
    nunca acumule una racha completa de cierres fuera. Eso produce
    rangos que arrancan en mitad del movimiento que los formó. Se exige
    al tramo completo el mismo umbral de contención que se le exigió a
    la ventana de declaración.

    Vectorizado con sumas acumuladas: la contención de todos los
    inicios candidatos se evalúa de una vez, sin recorrer velas.

    Parameters
    ----------
    cierres : np.ndarray
        Cierres de toda la serie.
    inicio, fin : int
        Posiciones del tramo, ambas incluidas.
    techo, suelo : float
        Rectángulo congelado.
    contencion_minima : float
        Fracción mínima de cierres dentro del rectángulo.

    Returns
    -------
    int | None
        El primer inicio (>= ``inicio``) cuyo tramo hasta ``fin``
        cumple la contención, o ``None`` si ninguno la cumple.
    """
    tramo = cierres[inicio : fin + 1]
    dentro = ((tramo >= suelo) & (tramo <= techo)).astype(np.int64)

    # dentro_desde[i] = cuántos cierres de tramo[i:] están dentro.
    dentro_desde = np.concatenate((dentro[::-1].cumsum()[::-1], [0]))
    longitudes = np.arange(len(tramo), 0, -1)
    contenciones = dentro_desde[:-1] / longitudes

    # El inicio elegido debe además cerrar DENTRO: de nada sirve un
    # tramo bien contenido que arranca en una vela fuera del rectángulo.
    validos = np.flatnonzero((contenciones >= contencion_minima) & (dentro == 1))
    if validos.size == 0:
        return None
    return inicio + int(validos[0])


def _recorridos_extremos(
    cierres: np.ndarray, techo: float, suelo: float, banda: float
) -> int:
    """Cuenta las veces que el precio va de una banda extrema a la otra.

    Un recorrido es un viaje completo del tercio alto al bajo del
    rectángulo o al revés. Mide algo distinto de los cruces del punto
    medio: estos cuentan cualquier vaivén, aunque sea pequeño y
    centrado, mientras que un recorrido exige llegar hasta los bordes.
    Es lo que distingue un rango que el precio trabaja de arriba abajo
    de uno que solo atraviesa.

    Los cierres de la franja central se ignoran: no informan de a qué
    extremo se dirige el precio.

    Parameters
    ----------
    cierres : np.ndarray
        Cierres del tramo a medir.
    techo, suelo : float
        Rectángulo.
    banda : float
        Grosor de cada banda extrema, como fracción de la altura.

    Returns
    -------
    int
        Número de recorridos completos.
    """
    altura = techo - suelo
    if altura <= 0:
        return 0

    zona = np.zeros(cierres.size, dtype=np.int8)
    zona[cierres >= techo - banda * altura] = 1
    zona[cierres <= suelo + banda * altura] = -1

    visitas = zona[zona != 0]
    if visitas.size < 2:
        return 0
    return int(np.count_nonzero(np.diff(visitas)))


def _recortar_cabeza(
    cierres: np.ndarray,
    inicio: int,
    fin: int,
    techo: float,
    suelo: float,
    contencion_minima: float,
    velas_cabeza: int,
    r2_maximo: float,
) -> int:
    """Adelanta el inicio del rango mientras su cabeza no sea lateral.

    Simétrico de :func:`_recortar_cola`, con una exigencia añadida. A
    la cabeza se le piden dos cosas:

    - **Contención**: la misma que se exigió a la ventana de
      declaración. La contención sobre el tramo completo no basta para
      depurar el arranque, porque en un rango largo unas pocas velas
      fuera al principio no bajan del umbral global.
    - **No ser tendencial**: R² por debajo del mismo umbral que aplica
      el criterio general. Un rango puede arrancar con el precio ya
      DENTRO del rectángulo pero todavía subiendo con fuerza, que es
      la cola del movimiento que lo formó, no lateralidad. La
      contención no lo detecta porque el precio está dentro; el R² sí.

    Se avanza hasta la primera cabeza que cumpla ambas.

    Vectorizado con sumas acumuladas: la contención de todas las
    cabezas candidatas se evalúa de una vez, sin recorrer velas.

    Parameters
    ----------
    cierres : np.ndarray
        Cierres de toda la serie.
    inicio, fin : int
        Posiciones del tramo, ambas incluidas.
    techo, suelo : float
        Rectángulo congelado.
    contencion_minima : float
        Fracción mínima de cierres dentro del rectángulo.
    velas_cabeza : int
        Longitud de la cabeza que se examina.
    r2_maximo : float
        R² máximo admitido en la cabeza.

    Returns
    -------
    int
        El primer inicio (>= ``inicio``) cuya cabeza cumple ambas
        condiciones. Si ninguno las cumple, devuelve ``fin``.
    """
    tramo = cierres[inicio : fin + 1]
    dentro = ((tramo >= suelo) & (tramo <= techo)).astype(np.int64)
    acumulado = np.concatenate(([0], dentro.cumsum()))

    # Para cada posible inicio i, contención de las `velas_cabeza`
    # velas siguientes (o de las que queden si el tramo se acaba antes).
    posiciones = np.arange(len(tramo))
    hasta = np.minimum(len(tramo), posiciones + velas_cabeza)
    longitudes = hasta - posiciones
    contenciones = (acumulado[hasta] - acumulado[posiciones]) / longitudes

    # R² de cada cabeza. `rolling` da ventanas que TERMINAN en cada
    # posición, así que la cabeza que empieza en i es la ventana que
    # termina en i + velas_cabeza - 1.
    serie = pd.Series(tramo)
    tiempo = pd.Series(np.arange(len(tramo), dtype="float64"))
    r2_ventana = serie.rolling(velas_cabeza).corr(tiempo).pow(2).to_numpy()
    r2_cabeza = np.full(len(tramo), np.nan)
    desplazamiento = velas_cabeza - 1
    if desplazamiento < len(tramo):
        r2_cabeza[: len(tramo) - desplazamiento] = r2_ventana[desplazamiento:]
    # Las últimas posiciones no tienen cabeza completa: el tramo se
    # acaba antes, así que no se les exige el criterio de tendencia.
    plano = np.isnan(r2_cabeza) | (r2_cabeza < r2_maximo)

    validos = np.flatnonzero(
        (contenciones >= contencion_minima) & (dentro == 1) & plano
    )
    if validos.size == 0:
        return fin
    return inicio + int(validos[0])


def _recortar_cola(
    cierres: np.ndarray,
    inicio: int,
    fin: int,
    techo: float,
    suelo: float,
    contencion_minima: float,
    velas_cola: int,
) -> int:
    """Retrasa el fin del rango mientras su cola esté mal contenida.

    La regla de los 5 cierres consecutivos exige una racha limpia para
    dar el rango por roto. Cuando el precio rompe entrando y saliendo
    —sale tres velas, vuelve una, vuelve a salir— nunca junta la racha,
    y el rango sobrevive varios días dentro de su propia rotura.

    Se corrige exigiendo a las últimas ``velas_cola`` velas la misma
    contención que se exigió a la ventana de declaración: si el tramo
    final ya no respeta el rectángulo, el rango terminó antes.

    Vectorizado con sumas acumuladas: la contención de todas las colas
    candidatas se evalúa de una vez, sin recorrer velas.

    Parameters
    ----------
    cierres : np.ndarray
        Cierres de toda la serie.
    inicio, fin : int
        Posiciones del tramo, ambas incluidas.
    techo, suelo : float
        Rectángulo congelado.
    contencion_minima : float
        Fracción mínima de cierres dentro del rectángulo.
    velas_cola : int
        Longitud de la cola que se examina.

    Returns
    -------
    int
        El último fin (<= ``fin``) cuya cola cumple la contención. Si
        ninguno la cumple, devuelve ``inicio``.
    """
    tramo = cierres[inicio : fin + 1]
    dentro = ((tramo >= suelo) & (tramo <= techo)).astype(np.int64)
    acumulado = np.concatenate(([0], dentro.cumsum()))

    # Para cada posible fin e, contención de las ultimas `velas_cola`
    # velas (o de todas las disponibles si aún no hay tantas).
    posiciones = np.arange(len(tramo))
    desde = np.maximum(0, posiciones - velas_cola + 1)
    longitudes = posiciones - desde + 1
    contenciones = (acumulado[posiciones + 1] - acumulado[desde]) / longitudes

    validos = np.flatnonzero((contenciones >= contencion_minima) & (dentro == 1))
    if validos.size == 0:
        return inicio
    return inicio + int(validos[-1])


def _primer_inicio_racha(mascara: np.ndarray, longitud: int) -> int | None:
    """Localiza el comienzo de la primera racha de ``longitud`` valores
    ``True`` consecutivos.

    Vectorizado con suma acumulada: la suma de cualquier ventana de
    ``longitud`` posiciones se obtiene por diferencia de acumulados,
    sin recorrer la máscara.

    Parameters
    ----------
    mascara : np.ndarray
        Array booleano.
    longitud : int
        Número de ``True`` consecutivos exigidos.

    Returns
    -------
    int | None
        Posición inicial de la primera racha, o ``None`` si no hay
        ninguna.
    """
    if mascara.size < longitud:
        return None

    acumulado = np.concatenate(([0], np.cumsum(mascara.astype(np.int64))))
    sumas_ventana = acumulado[longitud:] - acumulado[:-longitud]
    posiciones = np.flatnonzero(sumas_ventana == longitud)

    return int(posiciones[0]) if posiciones.size else None


def _detectar_rangos_una_serie(
    df: pd.DataFrame,
    ventana: int,
    duracion_minima: int,
    barras_confirmacion: int,
    altura_maxima_atr: float,
    altura_maxima_pct: float,
    r2_maximo: float,
    fraccion_pendiente_altura: float,
    contencion_minima: float,
    cruces_minimos: int,
    banda_bordes: float,
    ocupacion_minima: float,
    recorridos_base: float,
    altura_sin_penalizar: float,
    fraccion_cola: float,
    pct_por_recorrido: float,
    cierres_fuera_fin: int,
    toques_minimos: int,
    separacion_minima: int,
    anchura_banda_atr: float,
    atr_periodo: int,
) -> pd.DataFrame:
    """Detecta rangos laterales en un DataFrame OHLC con una única
    ventana móvil, sin distinguir a qué timeframe pertenece.

    Parameters
    ----------
    df : pd.DataFrame
        Velas con columnas ``high``, ``low``, ``close`` y
        ``DatetimeIndex`` ordenado cronológicamente.
    ventana : int
        Tamaño de la ventana móvil, en velas de ``df``.
    duracion_minima : int
        Duración mínima de un rango detectado, en velas de ``df``.
    barras_confirmacion : int
        Velas exigidas a cada lado de un pivote (``R``).
    altura_maxima_atr : float
        Altura máxima del rectángulo, en múltiplos del ATR medido en
        la vela de declaración.
    r2_maximo : float
        R² máximo de la regresión lineal para considerar rango.
    fraccion_pendiente_altura : float
        Fracción de la altura del rectángulo que puede consumir la
        deriva de la recta de regresión (ver docstring del módulo).
    contencion_minima : float
        Fracción mínima de cierres de la ventana dentro del
        rectángulo.
    cruces_minimos : int
        Veces que el precio debe cruzar el punto medio del rectángulo
        dentro de la ventana, para exigir oscilación real.
    cierres_fuera_fin : int
        Cierres consecutivos fuera del rectángulo que dan por
        terminado el rango.
    toques_minimos : int
        Toques mínimos por lado para aceptar techo o suelo.
    separacion_minima : int
        Velas mínimas entre dos toques para contarlos por separado.
    anchura_banda_atr : float
        Anchura de la banda de agrupación, en múltiplos del ATR.
    atr_periodo : int
        Periodo del ATR.

    Returns
    -------
    pd.DataFrame
        Un rango por fila, con todas las columnas de
        ``COLUMNAS_RESULTADO`` menos ``timeframe``. Vacío si no se
        detecta ningún rango.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas OHLC requeridas.
    """
    faltantes = set(COLUMNAS_REQUERIDAS) - set(df.columns)
    if faltantes:
        logger.error("Faltan columnas OHLC en los datos: %s", faltantes)
        raise KeyError(f"Faltan columnas OHLC: {faltantes}")

    columnas_vacio = [
        c for c in COLUMNAS_RESULTADO if c not in ("tipo", "calidad")
    ]
    n_velas = len(df)
    if n_velas < ventana:
        logger.warning(
            "Solo hay %d velas para una ventana de %d: no se detecta nada",
            n_velas,
            ventana,
        )
        return pd.DataFrame(columns=columnas_vacio)

    cierres = df["close"].to_numpy(dtype="float64")
    maximos = df["high"].to_numpy(dtype="float64")
    minimos = df["low"].to_numpy(dtype="float64")

    pendiente, r2, precio_medio = _regresion_rodante(df["close"], ventana)
    atr = _atr(df, atr_periodo)
    posiciones_altos, posiciones_bajos = _posiciones_pivotes(
        df["close"], barras_confirmacion
    )

    valores_atr = atr.to_numpy(dtype="float64")
    valores_r2 = r2.to_numpy(dtype="float64")
    derivas = (pendiente.abs() * ventana).to_numpy(dtype="float64")
    anchuras_banda = valores_atr * anchura_banda_atr

    # Criterios baratos y vectorizables: filtran la mayoría de las
    # velas antes de construir ningún rectángulo. El criterio de
    # pendiente ya no entra aquí: desde que se ancla a la altura del
    # rectángulo, no se puede evaluar hasta tenerlo construido.
    criterio_previo = ((r2 < r2_maximo) & atr.notna() & (atr > 0)).to_numpy()

    rangos: list[dict] = []
    fin_anterior: int | None = None
    grupo_solape = -1

    # Recorrido secuencial sobre las velas CANDIDATAS (las que ya pasan
    # los criterios vectorizados de regresión), no sobre todas las filas
    # del DataFrame. No es el bucle vela a vela que prohíbe CLAUDE.md:
    # la semántica de congelar el rectángulo al declarar el rango y
    # extenderlo hasta la racha de ruptura es intrínsecamente secuencial
    # con estado (el fin de un rango depende de un rectángulo fijado en
    # una vela anterior, y determina desde dónde se reanuda la
    # búsqueda), así que no admite forma de reducción rodante. Todo lo
    # que sí es vectorizable —regresión, R², ATR, pivotes, contención y
    # búsqueda de la racha— se calcula con numpy/pandas.
    candidatos = np.flatnonzero(criterio_previo)
    candidatos = candidatos[candidatos >= ventana - 1]
    siguiente_permitido = 0

    for posicion_actual in candidatos:
        t = int(posicion_actual)
        if t < siguiente_permitido:
            continue

        inicio = t - ventana + 1
        rectangulo = _rectangulo_ventana(
            cierres,
            posiciones_altos,
            posiciones_bajos,
            inicio,
            t,
            barras_confirmacion,
            float(anchuras_banda[t]),
            toques_minimos,
            separacion_minima,
        )
        if rectangulo is None:
            continue

        techo, suelo, toques_techo, toques_suelo = rectangulo
        altura = techo - suelo
        alt_atr = altura / valores_atr[t]

        # Tope de altura: un rectángulo demasiado alto no delimita un
        # rango utilizable para su función (ver docstring del módulo).
        if alt_atr > altura_maxima_atr:
            continue

        # Salvaguarda contra "contenedores": rectángulos tan altos que
        # la contención se cumple sola. El tope en ATR no los detecta
        # —medido, el contenedor de ONDO son 12.4 ATR y un rango bueno
        # de BTC 11.5— porque el ATR ya viene inflado por el propio
        # movimiento que formó la caja. En precio sí se separan: si el
        # techo más que dobla el suelo, el precio no consolidó, cerró
        # un ciclo entero.
        if altura / suelo > altura_maxima_pct:
            continue

        # Criterio de pendiente, anclado a la altura del rectángulo.
        if derivas[t] >= fraccion_pendiente_altura * altura:
            continue

        cierres_ventana = cierres[inicio : t + 1]
        dentro_ventana = (cierres_ventana >= suelo) & (cierres_ventana <= techo)
        contencion = float(dentro_ventana.mean())
        if contencion < contencion_minima:
            continue

        # Estabilidad: la ventana no puede contener ya una racha de
        # ruptura completa (ver docstring del módulo).
        if _primer_inicio_racha(~dentro_ventana, cierres_fuera_fin) is not None:
            continue

        # Oscilación: descarta las formaciones en V, que pasan todos los
        # criterios anteriores (pendiente neta casi nula y contenidas)
        # sin ser lateralidad.
        n_cruces = _cruces_punto_medio(cierres_ventana, (techo + suelo) / 2)
        if n_cruces < cruces_minimos:
            continue

        # Recorridos exigidos según la altura. Una caja alta recorrida
        # dos veces es un contenedor: el precio subió y volvió, no
        # consolidó. Una caja baja recorrida dos veces es un rango
        # normal. Ni la altura ni los recorridos separan por separado
        # —medido, el contenedor de ONDO y un rango bueno de BTC tienen
        # ambos 2 recorridos, y sus alturas en ATR se solapan—, pero su
        # combinación sí: se exige un recorrido más por cada
        # `pct_por_recorrido_extra` de altura por encima de
        # `altura_sin_penalizar_pct`.
        altura_relativa = altura / suelo
        exceso = max(0.0, altura_relativa - altura_sin_penalizar)
        recorridos_exigidos = recorridos_base + exceso / pct_por_recorrido
        if _recorridos_extremos(cierres_ventana, techo, suelo, banda_bordes) < (
            recorridos_exigidos
        ):
            continue

        # Ocupación de los bordes: el precio tiene que USAR el
        # rectángulo, no limitarse a caber dentro. Una caja lo bastante
        # alta contiene cualquier cosa —la contención se cumple por
        # construcción y deja de filtrar—, así que se exige además que
        # los cierres visiten las dos bandas extremas. Un rango real
        # testea techo y suelo; un contenedor los tiene de adorno.
        margen = banda_bordes * altura
        ocupacion_alta = float((cierres_ventana >= techo - margen).mean())
        ocupacion_baja = float((cierres_ventana <= suelo + margen).mean())
        if min(ocupacion_alta, ocupacion_baja) < ocupacion_minima:
            continue

        # Rango declarado: el rectángulo queda congelado aquí.
        #
        # Extensión hacia ATRÁS. `inicio` sale de la mecánica de la
        # ventana (t - N + 1), así que un lateral más largo que N
        # empieza por fuerza más tarde de lo que le toca. Se retrocede
        # con la misma regla que cierra el rango hacia adelante: se
        # sigue hacia atrás mientras el precio no acumule
        # `cierres_fuera_fin` cierres consecutivos fuera del
        # rectángulo ya congelado.
        #
        # Causalmente seguro: son velas pasadas, todas cerradas y
        # conocidas en el momento de la declaración. La marca temporal
        # `declarado_en` sigue diciendo cuándo se supo del rango.
        if inicio > 0:
            hacia_atras = cierres[:inicio][::-1]
            fuera_atras = (hacia_atras > techo) | (hacia_atras < suelo)
            corte = _primer_inicio_racha(fuera_atras, cierres_fuera_fin)
            inicio = 0 if corte is None else inicio - corte

        fuera_desde_inicio = (cierres[inicio:] > techo) | (cierres[inicio:] < suelo)
        inicio_racha = _primer_inicio_racha(fuera_desde_inicio, cierres_fuera_fin)

        if inicio_racha is None:
            fin = n_velas - 1
            confirmado_en = pd.NaT
            en_curso = True
            siguiente_permitido = n_velas
        else:
            posicion_racha = inicio + inicio_racha
            fin = posicion_racha - 1
            # La ruptura no se confirma hasta el último cierre de la racha.
            posicion_confirmacion = posicion_racha + cierres_fuera_fin - 1
            confirmado_en = df.index[posicion_confirmacion]
            en_curso = False
            # La racha de ruptura no pertenece a ningún rango: la
            # detección se reanuda después de ella.
            siguiente_permitido = posicion_confirmacion + 1

        # Recorte de bordes: el inicio es mecánico (t - N + 1), no la
        # vela en la que el precio entra de verdad en el rectángulo, y
        # el final puede arrastrar cierres que ya se habían salido sin
        # llegar a completar la racha de ruptura. Se ajustan ambos
        # extremos a la primera y última vela que cierran DENTRO.
        # Es causalmente seguro: todas esas velas ya están cerradas y
        # conocidas cuando se fija cada extremo.
        dentro_tramo = (cierres[inicio : fin + 1] >= suelo) & (
            cierres[inicio : fin + 1] <= techo
        )
        if not dentro_tramo.any():
            siguiente_permitido = t + 1
            continue
        inicio += int(dentro_tramo.argmax())
        fin -= int(dentro_tramo[::-1].argmax())

        # Contención sobre el TRAMO COMPLETO, no solo sobre la ventana
        # de declaración. La extensión hacia atrás tolera hasta
        # `cierres_fuera_fin - 1` cierres fuera seguidos, así que puede
        # arrastrar un tramo de entrada que está mayormente fuera del
        # rectángulo sin llegar a acumular una racha completa. Se
        # recorta el inicio hacia adelante hasta que el rango entero
        # cumple el mismo umbral que exigió su ventana.
        inicio = _recortar_por_contencion(
            cierres, inicio, fin, techo, suelo, contencion_minima
        )
        if inicio is None:
            siguiente_permitido = t + 1
            continue

        # Y los dos recortes locales, simétricos. La contención global
        # no basta para depurar los extremos: en un rango largo, unas
        # pocas velas fuera en un borde no bajan del umbral, así que el
        # rango arranca dentro del movimiento que lo formó o sobrevive
        # dentro de su propia rotura.
        # Mínimo de 20 velas: por debajo de esa muestra el R² deja de
        # ser significativo y cualquier movimiento suave parece una
        # tendencia. Con N=40 la fracción daría 4 velas.
        velas_borde = max(VELAS_BORDE_MINIMAS, round(ventana * fraccion_cola))
        inicio = _recortar_cabeza(
            cierres, inicio, fin, techo, suelo, contencion_minima,
            velas_borde, r2_maximo,
        )
        fin = _recortar_cola(
            cierres, inicio, fin, techo, suelo, contencion_minima, velas_borde
        )

        if fin - inicio + 1 < duracion_minima:
            # Descartado por duración: se reanuda en la vela siguiente a
            # la de declaración, no tras la ruptura, para no saltarse
            # velas que aún podrían declarar un rango válido.
            siguiente_permitido = t + 1
            continue

        # Marcado de solapes. Es causalmente seguro hacerlo aquí: la
        # detección se reanuda tras confirmar la ruptura del rango
        # anterior, así que al declarar este el anterior ya está
        # cerrado y su `fin` es conocido. El grupo de un rango se fija
        # en su declaración y no se revisa después, aunque más tarde
        # se le unan otros.
        if fin_anterior is None or inicio > fin_anterior:
            grupo_solape += 1
        fin_anterior = max(fin, fin_anterior if fin_anterior is not None else fin)

        rangos.append(
            {
                "ventana": ventana,
                "inicio": df.index[inicio],
                "fin": df.index[fin],
                "techo": techo,
                "suelo": suelo,
                "precio_max": float(maximos[inicio : fin + 1].max()),
                "precio_min": float(minimos[inicio : fin + 1].min()),
                "contencion": contencion,
                "altura_atr": alt_atr,
                "r2": float(valores_r2[t]),
                "cruces": n_cruces,
                "toques_techo": toques_techo,
                "toques_suelo": toques_suelo,
                "declarado_en": df.index[t],
                "confirmado_en": confirmado_en,
                "en_curso": en_curso,
                "grupo_solape": grupo_solape,
            }
        )

    if not rangos:
        return pd.DataFrame(columns=columnas_vacio)

    return pd.DataFrame(rangos, columns=columnas_vacio)


def seleccionar_rangos(rangos: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Se queda con los rangos operables, descartando los redundantes.

    El detector devuelve la misma consolidación vista por varias
    ventanas a la vez, más los rangos flojos que cumplen las reglas por
    los pelos. Sobre cada rango se traza un FRVP con tres niveles
    (VAH, POC, VAL) que se proyectan hacia la derecha, así que 116
    rangos serían más de 300 líneas y el gráfico dejaría de ser
    operable.

    Selección por supresión de no-máximos: se toma el mejor rango, se
    descarta todo el que se solape con él por encima de
    ``solape_maximo_seleccion``, y se repite.

    Dos decisiones que la hacen funcionar:

    - **Se ordena por ``calidad * duración``, no por calidad sola.** La
      calidad premia los rectángulos estrechos, así que ordenar solo
      por ella dejaba que un rango pequeño y limpio eliminase al
      grande que delimita la estructura. Medido contra los rangos que
      el autor traza a mano, ordenar por calidad sola daba un ajuste
      medio de 0.72 y ordenar por el producto lo sube a 0.86.
    - **La supresión se hace por separado dentro de cada ``tipo``.** Un
      rango operativo dentro de uno de contexto no es redundante: es
      justo la estructura anidada que se quiere operar. Compitiendo
      todos contra todos, la caja grande borraba las pequeñas de su
      interior.

    No sustituye al Filtro 3: este elige el mejor rectángulo de una
    misma zona y escala, mientras que el Filtro 3 tendrá que decidir
    cuál de los ya seleccionados es el relevante para operar en cada
    momento.

    Parameters
    ----------
    rangos : pd.DataFrame
        Salida de :func:`detectar_rangos_laterales`.
    config : dict
        Configuración cargada de ``config.yaml``. Se usan
        ``calidad_minima_seleccion`` (un umbral por tipo) y
        ``solape_maximo_seleccion``.

    Returns
    -------
    pd.DataFrame
        Subconjunto de ``rangos``, ordenado cronológicamente.
    """
    cfg = config["filtro_1_rango_lateral"]
    calidad_minima: dict[str, float] = cfg["calidad_minima_seleccion"]
    solape_maximo: float = cfg["solape_maximo_seleccion"]

    duracion = (rangos["fin"] - rangos["inicio"]).dt.total_seconds()
    candidatos = rangos.assign(_relevancia=rangos["calidad"] * duracion)

    supervivientes = []
    for tipo, grupo in candidatos.groupby("tipo", sort=False):
        piso = calidad_minima[tipo]
        grupo = grupo[grupo["calidad"] >= piso]
        if not grupo.empty:
            supervivientes.append(_suprimir_solapados(grupo, solape_maximo))

    if not supervivientes:
        return rangos.iloc[:0]

    resultado = pd.concat(supervivientes, ignore_index=True)
    return (
        resultado.drop(columns="_relevancia")
        .sort_values(["inicio", "ventana"])
        .reset_index(drop=True)
    )


def _suprimir_solapados(
    candidatos: pd.DataFrame, solape_maximo: float
) -> pd.DataFrame:
    """Supresión de no-máximos temporal sobre ``_relevancia``.

    Parameters
    ----------
    candidatos : pd.DataFrame
        Rangos de un mismo tipo, con la columna ``_relevancia``.
    solape_maximo : float
        Solape temporal (intersección sobre unión) por encima del cual
        dos rangos se consideran la misma zona.

    Returns
    -------
    pd.DataFrame
        Los rangos que sobreviven.
    """
    ordenados = candidatos.sort_values("_relevancia", ascending=False)
    inicios = ordenados["inicio"].to_numpy()
    fines = ordenados["fin"].to_numpy()

    elegidos: list[int] = []
    # Bucle sobre candidatos, no sobre velas: son unas pocas decenas y
    # la supresión de no-máximos es secuencial por definición (cada
    # elección condiciona qué queda disponible después).
    for i in range(len(ordenados)):
        solapado = False
        for j in elegidos:
            union = max(fines[i], fines[j]) - min(inicios[i], inicios[j])
            solape = (min(fines[i], fines[j]) - max(inicios[i], inicios[j])) / union
            if solape > solape_maximo:
                solapado = True
                break
        if not solapado:
            elegidos.append(i)

    return ordenados.iloc[elegidos]


def _calidad(
    rangos: pd.DataFrame,
    altura_maxima_atr: float,
    r2_maximo: float,
    contencion_minima: float,
) -> pd.Series:
    """Puntúa de 0 a 1 lo "lateral y limpio" que es cada rango.

    Todos los rangos devueltos ya han superado los criterios de
    declaración: la calidad no decide si algo es rango, sino cuál de
    ellos es mejor para operar. Combina tres medidas, cada una
    normalizada al margen que el criterio correspondiente permitía:

    - ``estrechez``: cuánto por debajo del tope de altura se queda el
      rectángulo. Un rango de 3 ATR de alto es mucho más tranquilo que
      uno de 15: el precio se mueve menos dentro de él.
    - ``planitud``: cuánto por debajo del R² máximo se queda. Cuanto
      más bajo, menos se parece a una recta inclinada.
    - ``limpieza``: cuánto por encima del mínimo de contención se
      queda. Mide si el precio respeta los bordes o se sale a menudo.

    Las tres pesan igual. Es una nota de partida razonada, no
    calibrada empíricamente contra resultados de operativa.

    Parameters
    ----------
    rangos : pd.DataFrame
        Rangos de una misma ventana, con ``altura_atr``, ``r2`` y
        ``contencion``.
    altura_maxima_atr : float
        Tope de altura aplicado a esa ventana.
    r2_maximo : float
        R² máximo admitido.
    contencion_minima : float
        Contención mínima admitida.

    Returns
    -------
    pd.Series
        Nota entre 0 y 1, alineada con ``rangos``.
    """
    estrechez = 1.0 - rangos["altura_atr"] / altura_maxima_atr
    planitud = 1.0 - rangos["r2"] / r2_maximo
    margen_contencion = 1.0 - contencion_minima
    limpieza = (rangos["contencion"] - contencion_minima) / margen_contencion

    nota = (estrechez + planitud + limpieza) / 3.0
    return nota.clip(0.0, 1.0).round(3)


def detectar_rangos_laterales(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Detecta rangos laterales con varias ventanas sobre 4h (Filtro 1,
    SPEC.md §5).

    Corre la detección de forma independiente con cada ventana de
    ``config["filtro_1_rango_lateral"]["ventanas"]`` sobre la misma
    serie de 4h, y devuelve todos los rangos detectados sin fusionar
    entre ventanas: son rangos de escalas distintas, intencionadamente
    independientes, y su resolución se delega al Filtro 3 a través de
    ``grupo_solape``.

    Parameters
    ----------
    df : pd.DataFrame
        Velas de 4h con columnas ``high``, ``low``, ``close`` y
        ``DatetimeIndex`` ordenado cronológicamente.
    config : dict
        Configuración cargada de ``config.yaml`` (ver
        :func:`data.loader.cargar_config`). Se usa la sección
        ``filtro_1_rango_lateral``.

    Returns
    -------
    pd.DataFrame
        Una fila por rango lateral detectado, con columnas:
        ``ventana`` (N en velas de 4h), ``tipo`` (``"secundario"`` o
        ``"principal"``, según el tamaño de la ventana que lo detectó),
        ``calidad`` (nota de 0 a 1: cuanto más plano, estrecho y
        respetado, mejor; ver :func:`_calidad`),
        ``inicio`` y ``fin`` (primera y última vela del rango),
        ``techo`` y ``suelo`` (rectángulo congelado),
        ``precio_max``/``precio_min`` (extremos reales del tramo, para
        el FRVP), ``contencion`` (fracción de cierres dentro del
        rectángulo en la ventana de declaración), ``cruces`` (veces
        que el precio cruza el punto medio en esa ventana),
        ``toques_techo`` y
        ``toques_suelo`` (pivotes agrupados en cada nivel),
        ``declarado_en`` (vela en la que el rango pasa a ser
        conocido), ``confirmado_en`` (vela en la que se confirma la
        ruptura que lo termina; ``NaT`` si sigue abierto),
        ``en_curso`` y ``grupo_solape`` (identificador compartido por
        los rangos del mismo timeframe que se solapan en el tiempo;
        ver docstring del módulo). Vacío si no se detecta ningún
        rango.

    Raises
    ------
    KeyError
        Si a ``df`` le faltan columnas OHLC requeridas, o si a
        ``config.yaml`` le falta algún parámetro del Filtro 1.
    ValueError
        Si la lista de ventanas está vacía o si el ``tipo`` de alguna
        no es ``"secundario"`` ni ``"principal"``.
    """
    cfg = config["filtro_1_rango_lateral"]
    _validar_config(cfg)

    r2_maximo: float = cfg["r2_maximo"]
    fraccion_pendiente_altura: float = cfg["fraccion_pendiente_altura"]
    contencion_minima: float = cfg["contencion_minima_cierres"]
    cruces_minimos: int = cfg["cruces_minimos_medio"]
    banda_bordes: float = cfg["banda_bordes"]
    ocupacion_minima: float = cfg["ocupacion_bordes_minima"]
    recorridos_base: float = cfg["recorridos_minimos_base"]
    fraccion_cola: float = cfg["fraccion_cola_contencion"]
    altura_sin_penalizar: float = cfg["altura_sin_penalizar_pct"]
    pct_por_recorrido: float = cfg["pct_por_recorrido_extra"]
    cierres_fuera_fin: int = cfg["cierres_fuera_fin_rango"]
    toques_minimos: int = cfg["toques_minimos_nivel"]
    anchura_banda_atr: float = cfg["anchura_banda_atr"]
    atr_periodo: int = cfg["atr_periodo"]
    fraccion_duracion: float = cfg["fraccion_duracion_minima"]
    fraccion_separacion: float = cfg["fraccion_separacion_toques"]
    barras_confirmacion: int = cfg["barras_confirmacion_pivote"]
    altura_base: float = cfg["altura_maxima_atr_base"]
    altura_calibrada: int = cfg["altura_calibrada_sobre_velas"]
    altura_maxima_pct: float = cfg["altura_maxima_pct"]

    resultados_por_ventana: list[pd.DataFrame] = []
    desplazamiento_grupo = 0

    for parametros in cfg["ventanas"]:
        ventana: int = parametros["velas"]

        # El tope de altura escala con la RAÍZ del tamaño de ventana,
        # que es como crece el recorrido de un paseo aleatorio: una
        # ventana cuatro veces más larga admite el doble de altura, no
        # cuatro veces más. Un tope fijo partía los rangos largos en
        # fragmentos (ver docstring del módulo).
        altura_maxima_atr = altura_base * math.sqrt(ventana / altura_calibrada)

        rangos = _detectar_rangos_una_serie(
            df,
            ventana=ventana,
            duracion_minima=int(ventana * fraccion_duracion),
            barras_confirmacion=barras_confirmacion,
            altura_maxima_atr=altura_maxima_atr,
            altura_maxima_pct=altura_maxima_pct,
            r2_maximo=r2_maximo,
            fraccion_pendiente_altura=fraccion_pendiente_altura,
            contencion_minima=contencion_minima,
            cruces_minimos=cruces_minimos,
            banda_bordes=banda_bordes,
            ocupacion_minima=ocupacion_minima,
            recorridos_base=recorridos_base,
            altura_sin_penalizar=altura_sin_penalizar,
            fraccion_cola=fraccion_cola,
            pct_por_recorrido=pct_por_recorrido,
            cierres_fuera_fin=cierres_fuera_fin,
            toques_minimos=toques_minimos,
            separacion_minima=max(1, round(ventana * fraccion_separacion)),
            anchura_banda_atr=anchura_banda_atr,
            atr_periodo=atr_periodo,
        )
        if rangos.empty:
            continue

        # Cada ventana numera sus grupos desde 0: se desplazan para que
        # sean únicos en el resultado combinado. Dos rangos de ventanas
        # distintas nunca comparten grupo aunque se solapen en el
        # tiempo: son escalas intencionadamente independientes, y su
        # resolución es cosa del Filtro 3.
        rangos["grupo_solape"] += desplazamiento_grupo
        desplazamiento_grupo = int(rangos["grupo_solape"].max()) + 1

        rangos.insert(1, "tipo", parametros["tipo"])
        rangos.insert(
            2,
            "calidad",
            _calidad(rangos, altura_maxima_atr, r2_maximo, contencion_minima),
        )
        resultados_por_ventana.append(rangos)

    if not resultados_por_ventana:
        return pd.DataFrame(columns=COLUMNAS_RESULTADO)

    resultado = pd.concat(resultados_por_ventana, ignore_index=True)
    return resultado.sort_values(["inicio", "ventana"]).reset_index(drop=True)
