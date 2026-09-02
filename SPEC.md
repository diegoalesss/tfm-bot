# Especificación funcional — Bot FRVP + Líneas de tendencia

Estado: EN CONSTRUCCIÓN
Última actualización: 02/09/2026

## 1. Infraestructura

- Exchange de ejecución: Kraken Futures (perpetuos), vía CCXT
- Backtest sin apalancamiento (1x). El escalado es decisión
  posterior de gestión de capital
- Timeframe de decisión: 4h
- Granularidad de construcción del FRVP según duración del rango:
    - Rango < 60 velas de 4h    → construir con 15m
    - Rango entre 60 y 200      → construir con 1h
    - Rango > 200 velas de 4h   → construir con 4h
  (reduce el error de asignación intra-vela)
- Histórico: 2 años

## 2. Fuente de datos

- Precio (OHLC): siempre Kraken. Es donde se ejecuta y donde
  saltan los stops
- Volumen: parametrizable en config.yaml
    volume_source: "kraken" | "aggregated"
- Fase 1: solo Kraken. Fase 2: agregado (Binance, Bybit, OKX,
  Kraken), sumando volumen por vela
- Motivo del diseño: permite comparar ambas versiones con todo
  lo demás idéntico → test de robustez para la memoria
- Al agregar: normalizar unidades (base vs quote), asumir
  paridad USDT≈USD y declararlo como supuesto, registrar
  cobertura cuando falten velas

## 3. Universo de activos

- Watchlist fija de 5-8 pares líquidos, definida ex-ante por
  criterio objetivo (volumen medio + antigüedad de histórico)
- No hay escáner sobre todos los activos: evita data snooping
- El escáner queda como línea futura de trabajo

## 4. Motor FRVP

### Parámetros
- Bins: 1000. Fijado, no se somete a análisis de sensibilidad:
  replica la configuración que el autor usa en TradingView,
  garantizando coherencia entre análisis manual y automatizado
- Value Area: 70% (parametrizable)
- Método Value Area: expansión CME desde el POC
- Desempate del POC: bin más cercano al centro del rango
- Rango de precio del perfil: máximo y mínimo del tramo
  seleccionado. No es parámetro, se deriva del tramo

### Anclaje del perfil

IMPLEMENTADO: el perfil se ancla al tramo completo del rango que
devuelve el Filtro 1, de `inicio` a `fin`, ambos incluidos.

Ya no hace falta buscar una "vela de indecisión" ni excluir a mano
la vela de ruptura, porque el propio Filtro 1 entrega esos límites
depurados:
  - `inicio` está recortado a la primera vela que cierra DENTRO
    del rectángulo y con el tramo entero cumpliendo la contención
  - `fin` excluye las 5 velas de la racha de ruptura y además se
    retrasa mientras la cola no respete el rectángulo
Es decir, el criterio de "última vela antes de la ruptura" ya está
aplicado aguas arriba, y de forma más estricta que la original.

DESCARTADO: el anclaje por vela de indecisión (mecha superior e
inferior > 0.2 del rango de la vela, relajando 0.2 → 0.15 → 0.1).
Se diseñó cuando el inicio del rango era una fecha aproximada y
hacía falta afinarla. Con los recortes del Filtro 1 ya no aporta:
buscaría una vela dentro de un tramo cuyo primer cierre ya está,
por construcción, dentro del rectángulo.

### Test de robustez del anclaje  [NO IMPLEMENTADO]

Pensado para comparar dos perfiles sobre la misma zona (uno desde
la vela de indecisión y otro desde el inicio de la zona) y aceptar
el nivel solo si POC/VAH/VAL coinciden dentro de tolerancia.

Queda sin implementar porque su premisa desapareció: al anclar
siempre al tramo del Filtro 1 solo hay un anclaje posible, así que
no hay dos perfiles que comparar.

Si más adelante se quiere una medida de robustez del nivel, habría
que plantearla de otra forma: por ejemplo, comparar el perfil del
tramo completo con el de su segunda mitad, y exigir que el POC no
se desplace más de una tolerancia dada (orden de magnitud:
0.5 * ATR).

## 5. Filtros de la estrategia

### Filtro 1 — Detección de rango lateral  [CERRADO E IMPLEMENTADO]

Método: caja de Darvas con techo y suelo en el pivote confirmado
más extremo de cada lado, validada estadísticamente sobre
ventanas móviles de varios tamaños.

El rectángulo se deriva de la ESTRUCTURA del precio, con
independencia del test de contención. Es deliberado: si el techo
y el suelo se derivaran de los mismos cierres que luego se
cuentan (percentiles, media ± k·σ, área de valor de cierres), el
criterio de contención se cumpliría por construcción y no
filtraría nada. Un criterio que no puede fallar no filtra.

**Construcción del rectángulo**

Pivotes de swing sobre CIERRES, no sobre mechas (metodología
Darvas: reduce las señales falsas por picos intradía). Un swing
high en la vela i exige que close[i] sea estrictamente mayor que
los R cierres anteriores y los R posteriores; simétrico para el
swing low.

Un pivote no existe hasta que está confirmado: solo se admiten
pivotes con i + R <= t al evaluar la vela t. Es obligatorio para
evitar lookahead.

El techo es el swing high confirmado MÁS EXTREMO de la ventana;
el suelo, el swing low más bajo. No la moda de la distribución
de pivotes.

Motivo: el rectángulo y el FRVP tienen trabajos distintos. El
rectángulo DELIMITA la estructura; el FRVP encuentra los niveles
relevantes DENTRO de ella. Si el rectángulo buscara además dónde
se agrupa el precio, duplicaría el trabajo del FRVP, y peor,
porque contaría toques en vez de medir volumen.

Y para la regla de ruptura el extremo es lo correcto: superar el
máximo confirmado previo significa algo; superar la moda no,
porque el precio ya estuvo por encima varias veces sin que el
rango muriera.

El riesgo de que un valor atípico fije el nivel está acotado por
construcción: los pivotes se calculan sobre CIERRES y exigen
confirmación a ambos lados, así que una mecha intradía no puede
definir un extremo. El tope de altura en ATR queda como
salvaguarda adicional.

El nivel se busca desde el pivote más extremo HACIA DENTRO y se
acepta el primero que reúna 2 toques (parametrizable) dentro de
la tolerancia. Si el extremo no está confirmado, el rango no
desaparece: se traza en el nivel confirmado que sí haya, que es
lo que hace un analista a mano.

**Los toques deben estar SEPARADOS EN EL TIEMPO.** Dos pivotes de
velas contiguas pertenecen al mismo impulso: son un solo test del
nivel, no dos. Se exige una separación mínima del 10% de la
ventana (25 velas con N=250, ~4 días).

Sin esa regla, el techo del lateral de BTC de feb-abr 2026 salía
en 78798 porque lo confirmaban dos swing highs del 2 y el 3 de
febrero, o sea la cola de la caída anterior, no el rango. El
rectángulo medía 16000 de alto frente a los 7500 reales, y el
88.8% de los cierres vivían en el 80% central: el precio casi no
visitaba los bordes, señal inequívoca de caja inflada. Con la
separación, el techo baja a 74884 y el ajuste al rango trazado a
mano sube de 0.72 a 0.93.

Descartado: nivel por moda (banda deslizante de anchura fija con
más toques, nivel = mediana de la banda). Medido sobre el lateral
de feb-may 2026 de ONDO en 1d, daba techo 0.2699 con 8 toques
frente a un extremo de 0.2943 con 2: un rectángulo cortado muy
por debajo de la estructura real. El interior de un rango se
visita más que su borde, por definición, así que la moda tiende
sistemáticamente hacia dentro.

El ATR se calcula con mechas, pero aquí no define ningún nivel
de precio: es solo la unidad de tolerancia con la que se decide
si dos pivotes están en el mismo sitio.

Desviación respecto a Darvas clásico: la caja original fija el
techo en UN máximo confirmado y el suelo en el mínimo de las
barras siguientes. Aquí el nivel se busca desde el pivote más
extremo hacia dentro y se acepta el primero confirmado por
varios toques separados en el tiempo. Es una variante
robustecida, no Darvas literal.

**Criterios de declaración**

Es rango si, sobre una ventana de N velas:
    R² < 0.3                               (el precio rebota,
                                            no sigue una recta)
    Y existe rectángulo (>=2 toques por lado, techo > suelo)
    Y (techo - suelo) <= tope_ATR de la ventana        (altura)
    Y (techo - suelo) / suelo <= 1.20        (salvaguarda altura)
    Y |pendiente| * N < 0.5 * (techo - suelo)          (deriva)
    Y >=85% de los cierres dentro del rectángulo (parametrizable)
    Y sin racha de 5 cierres consecutivos fuera dentro de la
      ventana
    Y >=7 cruces del punto medio del rectángulo dentro de la
      ventana                                       (oscilación)
    Y recorridos >= 1 + max(0, altura_rel - 0.30) / 0.10
                                                   (contenedores)

Y, una vez congelado el rectángulo y fijados los extremos:
    contención >= 85% sobre el TRAMO COMPLETO, no solo sobre la
    ventana que lo declaró

**Criterio de pendiente**

Anclado a la altura del rectángulo, no a un porcentaje fijo del
precio: la escala la pone la propia estructura detectada. Si la
deriva igualara la altura completa, el precio habría recorrido el
rectángulo de suelo a techo a lo largo de la ventana, que es un
canal inclinado y no un rango. Limitándola a la mitad, la
componente tendencial se queda como mucho con medio rectángulo y
el resto tiene que ser oscilación.

Sustituye al umbral del 7.5% del precio medio, que era el viejo
0.5 × 15% heredado del criterio de contención ya eliminado.

**Tipo de rango y tope de altura**

Cada ventana declara un tipo, que describe la ESCALA del
rectángulo, no su calidad, y viaja en la columna `tipo`. LOS DOS
SON OPERABLES: sobre ambos se traza un FRVP y de ambos salen
niveles de entrada.

  principal (N=150,250,400)  Laterales de estructura mayor, de 1 a
                             3 meses. Son LOS MÁS OPERABLES:
                             cuanto más tiempo pasa el precio
                             construyendo el rango, más volumen
                             acumula el perfil y más fiables son
                             su VAH, POC y VAL.
  secundario (N=40, 60)      Rangos anidados dentro de los
                             principales, de 1 a 2 semanas.
                             También operables, pero de menor
                             peso.

Estos tipos se llamaron antes `contexto` y `operativo`, nombres
heredados de la fase multi-timeframe en la que se suponía que
solo los rectángulos pequeños generaban entradas. Era engañoso:
los grandes no delimitan estructura para que otros operen dentro,
son ellos mismos el mejor sitio donde operar.

El tope se expresa en múltiplos del ATR, medido en la vela de
declaración, y no en porcentaje del precio: el porcentaje no es
invariante de escala, que fue justo el problema del criterio de
contención sustituido.

El tope no es único: escala con la raíz del tamaño de ventana
(ver "Ventanas múltiples sobre 4h"). Un rango de 400 velas
recorre naturalmente más ATR que uno de 40, y aplicarle el mismo
tope lo partía en fragmentos.

**Oscilación: distinguir un lateral de una formación en V**

Una V —el precio entra por un extremo del rectángulo, lo recorre
hasta el opuesto y vuelve— pasa todos los demás criterios: su
pendiente neta es casi nula, su R² es bajo y queda contenida. Pero
no es lateralidad, es un movimiento direccional de ida y vuelta.

Lo que las separa es la oscilación: en un lateral el precio
recorre el rectángulo de arriba abajo varias veces; en una V lo
hace una sola. Se exige un mínimo de 7 cruces del punto medio
dentro de la ventana de declaración.

7 es el umbral más bajo que elimina las siete formas en V
confirmadas visualmente sobre ONDO y BTC en 4h. Con 6 sobrevive
ONDO 2026-05-12, que fue el caso que motivó el criterio.

Se mide sobre la ventana de declaración, no sobre el tramo
completo: el tramo completo no se conoce hasta que el rango
termina, así que filtrar con él sería lookahead.

El umbral NO se escala con el tamaño de la ventana. Se probó
escalarlo (7/60 ≈ 0.117 cruces por vela) y es inviable: con
N=250 exigiría 29 cruces y con N=400, 47, umbrales inalcanzables
que anulaban por completo la detección en las ventanas largas.

El razonamiento del escalado era erróneo: un lateral no cruza el
punto medio más veces por ser más largo, sino por ser más
oscilante. El número de cruces mide una propiedad de forma, no
de duración, así que el umbral es absoluto.

Descartado: usar el número de toques del nivel como medida de
oscilación. Tenía sentido con el nivel por moda (los toques
contaban el racimo denso), pero con el nivel por extremo cuentan
solo los pivotes pegados al extremo, que en un lateral son dos o
tres por mucho que el precio oscile. Medido: subir el mínimo de
toques de 2 a 3 reduce la detección en 4h de 48 a 6 rangos, sigue
dejando pasar una V y elimina el lateral de feb-may de ONDO.

Descartado: contar recorridos completos entre bandas del 25%
superior e inferior. Satura —laterales buenos se quedan en 2
recorridos— y aun así deja pasar una V.

**Recorridos según la altura: rangos frente a contenedores**

Un rectángulo lo bastante alto contiene cualquier cosa, así que la
contención se cumple sola y deja de filtrar. El resultado son
"contenedores": cajas que abarcan un ciclo entero de subida y
vuelta, no una consolidación.

Ni la altura ni el número de recorridos los delatan por separado.
Medido: el contenedor de ONDO de nov-24 a mar-25 y el lateral bueno
de BTC de feb-abr 2026 tienen AMBOS 2 recorridos, y sus alturas en
ATR se solapan (12.4 y 11.5 ATR). El ATR no sirve aquí porque viene
inflado por el propio movimiento que formó la caja.

Lo que los separa es la COMBINACIÓN. Una caja del 118% recorrida dos
veces es un contenedor; una del 19% recorrida dos veces es un
lateral normal. Cuanto más alta la caja, más recorridos hacen falta
para creerse que el precio la trabajó:

    exigidos = 1 + max(0, altura_relativa - 0.30) / 0.10

Un recorrido es un viaje completo de la banda alta a la baja del
rectángulo, o al revés, medido sobre la ventana de declaración. Es
distinto de los cruces del punto medio: estos cuentan cualquier
vaivén, aunque sea pequeño y centrado, mientras que un recorrido
exige llegar hasta los bordes.

Efecto medido: elimina el contenedor de ONDO (necesitaria 9.8
recorridos y tiene 2), recupera el lateral de jul-oct 2025 que
faltaba, y corrige el arranque del rango en curso de abril a mayo de
2026. En BTC no cambia nada: el ajuste sigue en 0.875 con los 7
rangos de referencia.

**Congelación y fin del rango**

Al declararse el rango, el rectángulo se CONGELA. A partir de
ahí el rango solo puede terminar por 5 cierres consecutivos
fuera del rectángulo congelado (parametrizable). Motivos: un
rectángulo recalculado en cada vela se ensancharía para absorber
los cierres que se salen, y la regla de los 5 cierres no se
dispararía nunca; además, un rectángulo móvil hace el backtest
irreproducible.

Una vela que sale y vuelve es un barrido de stops, no una
ruptura. Las mechas no cuentan como salida, solo los cierres.

El rango termina en la vela anterior a la primera de la racha.
La racha de ruptura no pertenece a ningún rango: la detección se
reanuda después de ella.

**Extensión hacia atrás**

`inicio` sale de la mecánica de la ventana (t - N + 1), así que un
lateral más largo que N empieza por fuerza más tarde de lo que le
toca. Tras congelar el rectángulo se retrocede con la MISMA regla
que lo cierra hacia adelante: se sigue hacia atrás mientras el
precio no acumule 5 cierres consecutivos fuera.

Es causalmente seguro: son velas pasadas, ya cerradas y conocidas
en el momento de la declaración, y `declarado_en` sigue diciendo
cuándo se supo del rango. El test de truncado lo confirma.

Medido contra las fechas que el autor traza a mano en BTC, el
ajuste temporal medio sube de 0.858 a 0.875, y el rango de
nov-25 a ene-26 pasa de empezar 9 días tarde a 3 días pronto.

**Recorte de la cabeza**

Simétrico del recorte de la cola en la contención, con una
exigencia añadida: la cabeza tampoco puede ser TENDENCIAL (R² por
debajo del mismo umbral general).

Es lo que distingue este recorte del que hace la contención. El
lateral de BTC de noviembre de 2024 arrancaba el día 11, diez días
antes del trazado manual, y la contención de su cabeza era 0.95:
por contención no había nada que recortar, porque el precio ya
estaba DENTRO del rectángulo. Lo que ocurría es que ese tramo era
la cola del rally de 76000 a 99000: el precio estaba dentro de la
caja pero todavía subiendo con fuerza, que no es lateralidad. La
contención no puede verlo; el R² sí.

El R² local no es monótono (0.31 al inicio, 0.90 diez velas
después, 0.05 en el índice 50), así que la regla es avanzar hasta
la primera cabeza plana, no hasta que el R² empiece a bajar.

Efecto medido: el ajuste temporal medio sube de 0.875 a 0.900, y
el caso peor del conjunto de referencia pasa de 0.54 a 0.71,
corrigiendo su inicio de +10 días a +0.8.

La longitud del borde examinado es el 10% de la ventana con un
MÍNIMO de 20 velas. El mínimo no es cosmético: con N=40 la
fracción daría 4 velas, y el R² sobre 4 puntos es alto casi
siempre, de modo que el criterio rechazaba cualquier cosa. Por
debajo de esa muestra el R² deja de ser significativo.

**Recorte de la cola**

La regla de los 5 cierres consecutivos exige una racha limpia para
dar el rango por roto. Cuando el precio rompe entrando y saliendo
—sale tres velas, vuelve una, vuelve a salir— nunca junta la racha,
y el rango sobrevive varios días dentro de su propia rotura.

Se corrige exigiendo a las últimas velas del rango (el 10% de su
ventana) la misma contención que se exigió a la ventana de
declaración. Si el tramo final ya no respeta el rectángulo, el rango
terminó antes.

Medido en ONDO: el lateral de diciembre de 2024 llegaba al día 29
cuando el precio ya rompía desde el 27, entrando y saliendo del suelo
sin llegar nunca a cinco cierres seguidos fuera. Con el recorte
termina el 26.

Efecto colateral a tener en cuenta: `fin` deja de crecer de forma
monótona entre rangos consecutivos de una misma ventana. El
agrupamiento de solapes ya lo contemplaba —compara contra el punto
más lejano alcanzado por el grupo, no contra el fin del rango
anterior—, pero la prueba que lo verificaba usaba la simplificación
del rango inmediato y hubo que corregirla.

**Recorte de bordes**

`inicio` sale de la mecánica de la ventana (t - N + 1), no de la
vela en la que el precio entra de verdad en el rectángulo, y el
final puede arrastrar cierres que ya se habían salido sin llegar
a completar la racha. Ambos extremos se recortan a la primera y
la última vela que cierran DENTRO del rectángulo.

Es causalmente seguro: esas velas ya están cerradas y son
conocidas cuando se fija cada extremo. Medido sobre los rangos de
principales de BTC, elimina el sobrante de 4 velas que arrastraban 6
de los 9 rangos por el borde izquierdo. Si al agotarse el histórico el rango no
ha roto, se marca como abierto (en_curso).

El criterio de "sin racha dentro de la ventana" es consecuencia
necesaria de lo anterior, no una regla añadida: sin él, una
ventana podría superar el 85% teniendo ya dentro una racha
completa, es decir, un rango que habría terminado antes de
declararse.

**Precisión frente a robustez**

El rectángulo NO necesita ser preciso. Su función es acotar el
tramo sobre el que se traza el FRVP, no servir como nivel de
entrada: los niveles de entrada salen del FRVP (VAH/POC/VAL), no
de los bordes del rectángulo. Se prioriza un rectángulo
aproximado y estable sobre uno exacto y sensible a un solo
pivote. Por eso el nivel es la mediana del grupo y no su
extremo: deja el rectángulo ligeramente estrecho, y el 15% de
cierres que el criterio tolera fuera es exactamente el margen
que absorbe esa diferencia.

**Estado del criterio de contención**

El umbral del 85% dejó de ser prácticamente activo al pasar el
nivel de moda a extremo: con el rectángulo envolviendo la
estructura en vez de cortarla, casi ningún cierre queda fuera
(contención mediana 0.98-1.00, frente a 0.85-0.92 antes).

Medido sobre 10.136 ventanas de ONDO y BTC en los tres
timeframes, la contención es el criterio bloqueante en **3
ventanas (0.03%)**. No es cero, así que se conserva; pero ha
dejado de ser el filtro que era y no debe contarse como tal al
razonar sobre el poder de rechazo del Filtro 1. Reparto de los
bloqueos:

    R2                6079   59.97%
    sin rectángulo    3319   32.74%
    (pasa)             278    2.74%
    altura             204    2.01%
    oscilación         165    1.63%
    pendiente           55    0.54%
    racha interna       33    0.33%
    contención           3    0.03%

**Marcas temporales (anti-lookahead)**

Cada rango detectado lleva dos instantes de conocimiento:
  declarado_en  — vela en la que el rango pasa a ser conocido.
                  Nunca coincide con inicio: inicio está N-1
                  velas antes.
  confirmado_en — vela en la que se confirma la ruptura que lo
                  termina, 5 velas después de fin.
El consumidor (FRVP, backtest) debe respetarlas: usar un rango
antes de declarado_en, o su fin antes de confirmado_en, sería
lookahead.

**Ventanas múltiples sobre 4h**

La detección se corre SOLO sobre 4h, con varias ventanas de
distinto tamaño, para captar rangos anidados de distinta escala
sin elegir una a priori:

    N=40   secundario  tope 6.5 ATR
    N=60   secundario  tope 8.0 ATR
    N=150  principal   tope 12.6 ATR
    N=250  principal   tope 16.3 ATR
    N=400  principal   tope 20.7 ATR

R=3 para todas: es un criterio de forma sobre velas de 4h y no
depende del tamaño de la ventana. Duración mínima 0.66·N, que no
llega a activarse nunca (la duración es siempre >= N-4 por
construcción) y se conserva como salvaguarda.

El tope de altura escala con la RAÍZ del tamaño de ventana, que
es como crece el recorrido de un paseo aleatorio:

    tope = 8.0 * sqrt(N / 60)

Calibración: las ventanas se eligieron contra 9 rangos que el
autor traza a mano en TradingView sobre ONDO y BTC en 4h. Esta
combinación reproduce 8 de las 9 con IoU > 0.3 y 6 con IoU > 0.5
(IoU 2D = solape temporal × solape de precio), IoU medio 0.51,
sin admitir ninguna de las 7 formaciones en V confirmadas.

Descartado: multi-timeframe (1w/1d/4h con N=26/60/60). Fue la
fase intermedia del diseño y no lograba trazar los laterales
grandes. El tamaño del rectángulo lo fija la ventana, y una de
60 velas (~10 días) no puede ver un lateral de 3 meses por
muchos parámetros que se ajusten: los tres laterales de BTC que
el autor traza a mano salían solo como fragmentos sueltos, con
un 10-16% de solape temporal. Volver a multi-N sobre 4h es lo
que pedía la versión original de este documento ("la detección
se corre con N = 60, 150 y 400").

Descartado: subir N sin tocar nada más. Medido: con N=100 o más
la detección cae a 0-1 rangos, porque los demás criterios están
calibrados para N=60. Hay que escalar el tope de altura con
sqrt(N) y, a la vez, NO escalar el umbral de cruces.

Descartado: ensanchar la tolerancia de los toques como remedio.
Sube el número de rangos (BTC 16 → 30 con 2.0 ATR) pero no su
tamaño: produce más rectángulos, no más grandes, porque el
tamaño lo sigue fijando la ventana.

**Selección de rangos operables**

El detector devuelve la misma consolidación vista por varias
ventanas a la vez: 116 rangos en BTC sobre 2 años. Sobre cada uno
se traza un FRVP con tres niveles (VAH, POC, VAL) proyectados
hacia la derecha, así que serían más de 300 líneas y el gráfico
dejaría de poder operarse.

`seleccionar_rangos` aplica supresión de no-máximos: toma el
rango más relevante, descarta el que se solape con él por encima
del 5%, y repite. Dos decisiones la hacen funcionar:

  Se ordena por CALIDAD x DURACIÓN, no por calidad sola. La
  calidad premia los rectángulos estrechos, así que ordenar solo
  por ella dejaba que un rango pequeño y limpio eliminase al
  grande que delimita la estructura. Medido contra los rangos
  trazados a mano, el ajuste medio pasa de 0.72 a 0.86.

  La supresión se hace DENTRO DE CADA TIPO. un rango secundario
  dentro de uno principales no es redundante: es la estructura
  anidada que se quiere operar. Compitiendo todos contra todos,
  la caja grande borraba las pequeñas de su interior.

El listón de calidad es distinto por tipo (0.40 principal, 0.70 secundario). La nota incluye la estrechez del rectángulo, y un
rango de 3 meses es alto por naturaleza: exigirle la misma nota
que a uno de 10 días lo penalizaría por ser justo lo que se le
pide. El listón alto en secundario es lo que mantiene el gráfico
legible, porque son los numerosos.

Resultado: BTC pasa de 116 a 20 rangos, de los cuales 9 son
principales —el autor traza 7 a mano sobre el mismo periodo— y 11
secundarios anidados.

Medido sobre las FECHAS de esos 7 rangos (lo que importa, porque
ahí se ancla el FRVP), el ajuste temporal medio es 0.900 y los 7
se detectan.

Descartado: dar más peso a la duración en la relevancia. Arregla
el rango de feb-abr 2025 (0.54 -> 0.69) pero rompe los de feb-abr
y abr-may de 2026 (0.93 -> 0.59 y 0.89 -> 0.45). No compensa.

No sustituye al Filtro 3: esto elige el mejor rectángulo de una
misma zona y escala; el Filtro 3 decidirá cuál de los ya
seleccionados es el relevante para operar en cada momento.

**Nota de calidad**

Cada rango lleva una nota de 0 a 1 que mide lo lateral y limpio
que es, para poder priorizar. No decide si algo es rango: todos
los devueltos ya han pasado los criterios. Combina, a partes
iguales:

  estrechez  Cuánto por debajo del tope de altura se queda. Un
             rango de 3 ATR es más tranquilo que uno de 15.
  planitud   Cuánto por debajo del R² máximo se queda.
  limpieza   Cuánto por encima del mínimo de contención.

Es una nota razonada, no calibrada contra resultados de
operativa.

**Rangos solapados**

Dos rangos consecutivos del mismo timeframe pueden solaparse:
`inicio` se sitúa N-1 velas antes de la declaración, así que un
rango declarado tras una ruptura arrastra su ventana sobre el
tramo del rango anterior.

El Filtro 1 NO los resuelve. Los devuelve todos y los marca con
`grupo_solape`, un identificador compartido por los rangos
encadenados. La resolución se delega al Filtro 3, que ya tiene
pendiente elegir el rango previo "más importante" entre varios
candidatos: resolverlo dos veces con criterios distintos sería
incoherente.

El marcado es causalmente seguro. La detección se reanuda tras
confirmar la ruptura del rango anterior, así que al declararse un
rango el anterior ya está cerrado y su `fin` es conocido. El
grupo se fija en la declaración y no se revisa después, aunque
más tarde se le unan otros rangos.

Descartadas dos alternativas que sí resolvían en el Filtro 1:

  Quedarse con el más largo — es lookahead. Para saber cuál es el
  más largo hay que esperar a que ambos terminen, y para entonces
  ya se habría operado el primero.

  Quedarse con el más reciente — favorece justo los rectángulos
  de peor calidad estructural, los declarados tarde, que incluyen
  en su ventana la ruptura del rango anterior.

El agrupamiento es transitivo (enlace simple): si A solapa con B
y B con C, los tres comparten grupo aunque A y C no se toquen.
Deliberado, para que el Filtro 3 reciba el conjunto completo de
candidatos encadenados y no fragmentos.

Un grupo nunca cruza timeframes: son escalas intencionadamente
independientes.

Duración mínima: valores heredados de SPEC.md, a validar
empíricamente. Con los N actuales no llegan a activarse nunca
(la duración de un rango es siempre >= N-4 por construcción); se
conservan como salvaguarda ante cambios de N.

Descartado: validación por vela de absorción (volumen >200% +
mecha larga). Motivo: falsos positivos (mecha climática sin
rango posterior) y falsos negativos (rangos válidos sin
absorción visible). No aporta poder predictivo.

Descartado: arquitectura sin ventana rodante (detectar pivotes,
confirmar niveles por conteo de toques, abrir la caja y
extenderla hacia la derecha hasta la ruptura). Se evaluó como
solución al techo cortado antes de descartarla, por dos
mediciones:

  1. La extensión temporal YA funciona. El lateral feb-may de
     ONDO dura 73 velas con N=60: la regla de los 5 cierres lo
     extiende más allá de la ventana sin problema. El límite que
     se le atribuía a N no existe.
  2. La ventana no solo acota la vida de la caja: acota qué
     pivotes pueden formar el nivel, y eso es deseable. Sin ella
     los pivotes no caducan, y simulando el enfoque los máximos
     de enero de 2026 (0.3459) fijaban el techo del lateral de
     marzo. Habría que reponer ese límite con otra regla, es
     decir, reintroducir un lookback por la puerta de atrás.

Además implicaba ~60% de reescritura y degradaba la separación
multi-timeframe, que hoy procede sobre todo de N: sin ella, 1d y
4h convergerían a cajas redundantes y `tipo` dejaría de poder
atarse al timeframe. El techo cortado se resolvió cambiando el
nivel de moda a extremo, que es ortogonal a la arquitectura.

Descartado: contención por rango absoluto,
(max - min) / precio_medio < 15%. Motivo: era el cuello de
botella del filtro y no es invariante de escala temporal.
Sustituido por la contención por cierres descrita arriba.

### Filtro 2 — FRVP en rango lateral  [CERRADO E IMPLEMENTADO]

Sobre cada rango que entrega el Filtro 1 se traza su FRVP con los
parámetros y el anclaje de la sección 4, y de él salen los tres
niveles operativos: VAH, POC y VAL.

Implementado en `core/frvp.py`:

    perfil = calcular_frvp(velas, inicio, fin, config)
    perfil["poc"], perfil["vah"], perfil["val"]

La granularidad de las velas la elige `timeframe_construccion()`
según la duración del rango, siguiendo la regla de §1: 15m si dura
menos de 60 velas de 4h, 1h hasta 200, y 4h por encima.

El volumen de cada vela se reparte UNIFORMEMENTE entre su mínimo y
su máximo, en proporción al solape con cada bin. Es la aproximación
estándar cuando no se dispone del volumen por precio real, que
exigiría datos de tick: dentro de una vela no se sabe a qué precios
se negoció, y repartir uniformemente no sesga hacia ningún extremo.
La granularidad elegida arriba es lo que acota el error.

Verificado sobre ONDO y BTC: el POC cae dentro del rectángulo del
rango en los 46 casos, y el cálculo de los 46 perfiles tarda 0.2 s.

Los tres niveles se proyectan hacia la derecha en los rangos
principales, que es donde se vigila la reentrada del precio. En los
secundarios se dibujan solo dentro de su caja: con decenas de
rangos, extender todas las líneas hace el gráfico ilegible.

**Marca temporal**: el perfil de un rango NO existe antes de su
`confirmado_en`, porque su `fin` no se conoce hasta entonces.
Operar sus niveles antes de esa vela sería lookahead. Esto encaja
con la operativa prevista —entrar cuando el precio vuelve a testear
zonas de rangos PREVIOS, ya rotos— pero el backtest debe imponerlo
de forma explícita.

### Filtro 3 — FRVP en tendencia  [PARCIAL]

Si hay tendencia, se identifica el rango lateral previo más
importante y se traza el FRVP únicamente sobre él, sin
extender el perfil hacia la derecha.

Se colocan líneas horizontales en VAH, POC y VAL. Estas líneas
sí se extienden hacia la derecha (color rojo en la
visualización).

PENDIENTE: criterio objetivo para elegir el rango previo "más
importante" cuando la detección multi-ventana devuelve varios
candidatos.

### Filtros 4, 5 y 6  [PENDIENTES]

## 6. Estado de implementación

Cerrado a 02/09/2026. Fases 1 y 2 completas.

| Módulo | Estado | Fichero |
|---|---|---|
| Ingesta y validación de datos | implementado | `data/loader.py`, `data/validator.py` |
| Filtro 1 — rangos laterales | implementado | `core/range_detector.py` |
| Filtro 2 — FRVP | implementado | `core/frvp.py` |
| Visualización | implementado | `notebooks/exploracion.ipynb` |
| Filtro 3 — FRVP en tendencia | pendiente | — |
| Filtros 4, 5 y 6 | sin definir | — |
| Rejilla de niveles operables | implementado | `core/levels.py` |
| Imbalances semanales (§9) | implementado | `core/imbalances.py` |
| Estructura de mercado (§10) | implementado | `core/structure.py` |
| Momento y divergencias (§12) | implementado | `core/momentum.py` |
| Funding rate (§13) | implementado, descartado | `data/funding.py`, `core/flujo.py` |
| Convergencia de señales (§13) | implementado, activo | `core/convergencia.py` |
| Motor de backtest (experimental) | implementado | `execution/backtest.py`, `execution/metrics.py` |
| Búsqueda de configuración | implementado | `experiments/optimizar.py` |
| Ajuste al criterio manual (IoU) | implementado | `tests/test_ajuste_manual.py` |
| Capa de ejecución (paper / live) | pendiente | — |

El motor de backtest existe para MEDIR (ver §8), no porque la
estrategia esté definida. No conoce ninguna regla: recibe la rejilla de
niveles ya construida y la ejecuta.

Pruebas: 119, todas en verde.

    .venv\Scripts\python.exe tests\test_range_detector.py    26/26
    .venv\Scripts\python.exe tests\test_backtest.py          32/32
    .venv\Scripts\python.exe tests\test_imbalances.py        10/10
    .venv\Scripts\python.exe tests\test_structure.py          6/6
    .venv\Scripts\python.exe tests\test_momentum.py          10/10
    .venv\Scripts\python.exe tests\test_flujo.py             19/19
    .venv\Scripts\python.exe tests\test_osciladores.py       16/16
    .venv\Scripts\python.exe tests\test_ajuste_manual.py      8/8

La garantía crítica es `test_sin_lookahead_sobre_datos_reales`:
corta el histórico al 50/70/90% y exige que todo rango ya
confirmado antes del corte salga IDÉNTICO. Compara 200 rangos. Si
se toca el detector, ese test es la red de seguridad, y no debe
relajarse para que pase: si falla, es que el cambio mira al futuro.

### Cómo se valida el Filtro 1

El criterio de verdad son los rangos que el autor traza a mano en
TradingView, y lo que importa de ellos son las FECHAS de inicio y
fin, no los niveles exactos de techo y suelo: sobre esas fechas se
ancla el FRVP.

Referencia actual: 7 rangos de BTC en 4h desde noviembre de 2024.
**Ajuste temporal medio 0.900 (IoU), los 7 detectados**, seis de ellos
con el INICIO a menos de 3 días de la fecha trazada a mano. Ninguna de
las 7 formaciones en V confirmadas se cuela.

La medición está en `tests/test_ajuste_manual.py`, que imprime el
desglose rango a rango y **fija la línea de base con un `assert`**: si
un cambio en el detector baja la media, la prueba falla. Se mide sobre
los rangos SELECCIONADOS, no sobre los crudos. Los crudos dan 0.928,
pero es un número engañoso —con 116 candidatos casi siempre hay uno que
encaja— y además no son los que se llevan al gráfico ni al FRVP: lo que
hay que validar es lo que el sistema ELIGE.

| # | trazado a mano | mejor candidato | IoU | desfase inicio / fin (d) |
|---|---|---|---|---|
| 1 | 2024-11-21 → 2025-02-24 | 2024-11-12 → 2025-02-26 | 0.898 | -8.3 / +2.5 |
| 2 | 2025-02-25 → 2025-04-22 | 2025-02-25 → 2025-04-06 | **0.711** | +0.8 / **-15.3** |
| 3 | 2025-05-09 → 2025-07-09 | 2025-05-09 → 2025-07-10 | 0.971 | +0.3 / +1.5 |
| 4 | 2025-11-17 → 2026-01-28 | 2025-11-18 → 2026-01-31 | 0.934 | +1.7 / +3.3 |
| 5 | 2026-02-05 → 2026-04-13 | 2026-02-03 → 2026-04-17 | 0.926 | -1.3 / +4.0 |
| 6 | 2026-04-14 → 2026-05-27 | 2026-04-15 → 2026-06-01 | 0.865 | +1.5 / +5.0 |
| 7 | 2026-06-04 → 2026-08-19 | 2026-06-03 → 2026-08-19 | 0.993 | -0.2 / +0.3 |

El desglose señala dónde está el margen: los inicios están bien (seis de
siete a menos de 2 días) y **lo que desvía son los finales**, siempre
salvo uno hacia el futuro. El caso 2 es el que arrastra la media, y es
el mismo de §7: el candidato correcto existe pero pierde en la selección.

Esos 7 rangos se dieron DESPUÉS de fijar los parámetros, que se
habían calibrado contra otras 9 cajas distintas: funcionan como
validación fuera de muestra, no como ajuste a medida.

## 7. Decisiones pendientes

### Del Filtro 1

- **Causa raíz recurrente, sin resolver**: cuando dos cajas compiten
  por la misma zona, la selección elige la de mejor nota y el autor
  elige la que respeta la estructura de precio, aunque sea más alta
  y más corta. La nota de calidad penaliza las cajas altas, y a
  veces la caja alta es la correcta. Se manifiesta en tres casos
  medidos:
    - BTC feb-abr 2025: sale 0.54 de ajuste. El candidato correcto
      existe (0.685 de ajuste) pero pierde por 3 décimas en
      `calidad x duración`
    - ONDO may-ago 2026: el rango grande empieza el 26 de mayo en
      vez del 5-7. El candidato bueno existe y lo suprime uno de
      calidad 0.853
    - ONDO 19 may - 20 jun 2026: falta como secundario, y es el
      mismo candidato del caso anterior
  Ya se probó dar más peso a la duración: arregla el primero y
  rompe otros dos. Hace falta otra idea.
- ONDO agosto 2026 sale con IoU 0.20 porque la ventana de 150 velas
  funde dos cajas que el autor separa. Falta una ventana intermedia
  o un criterio de separación
- Calibración empírica de N, R, toques mínimos y anchura de banda:
  los valores actuales son de partida, razonados pero no medidos
- Reactivar el criterio de ocupación de bordes con una medida que
  tolere la asimetría (ver `ocupacion_bordes_minima` en
  `config.yaml`)

### Del resto del sistema

- **Stop estructural con tamaño variable** [idea del autor, 02/09/2026,
  APARCADA a propósito]. En vez de stop a distancia fija en ATR y
  tamaño de posición constante, colocar el stop **detrás del siguiente
  nivel** (más un margen por si el precio va a mecharlo), siempre que
  ese nivel no quede demasiado lejos, y ajustar el TAMAÑO de la
  posición para que la pérdida en euros sea aproximadamente la misma
  en todas las operaciones. Es decir: el stop lo pone la estructura y
  el riesgo lo pone el sizing, no al revés.

  Más adelante, el tamaño podría subir cuando haya convergencia de
  varios indicadores. Eso NO se toca ahora.

  Aparcado por decisión del autor con un motivo correcto: no tiene
  sentido optimizar el stop mientras los TP estén sin definir. Medido
  en §8: con solo 27 niveles, el primer objetivo llega a quedar a un
  -30% de la entrada, así que hoy el experimento mide la resistencia
  del stop y no la calidad de la entrada. Primero los objetivos.
- **Operar el cierre de un imbalance semanal** [idea del autor,
  02/09/2026, APARCADA a propósito]. Abrir operación cada vez que el
  precio termina de rellenar un imbalance semanal importante, no solo
  usarlos como objetivo. Aparcado por decisión del autor con el mismo
  criterio que la anterior: primero cerrar los SL y TP de las entradas
  del FRVP, y solo después añadir una fuente de entradas nueva.
- Criterio de selección del rango previo (Filtro 3). Es el mismo
  problema que resolver los grupos de `grupo_solape` del Filtro 1,
  y debe resolverse una sola vez
- Medida de robustez del nivel del FRVP, si se quiere (§4)
- Definición de los filtros 4, 5 y 6
- Composición final de la watchlist

### `seleccionar_rangos` no es causal  [detectado 02/09/2026]

La supresión de no-máximos se aplica sobre el histórico completo. Solo
compite entre rangos que se SOLAPAN EN EL TIEMPO, así que no es que un
rango lejano borre a otro de años antes: dos zonas separadas en el
tiempo nunca se enfrentan, y que el precio vuelva años después a la
misma zona de precio no resta valor al rango antiguo, lo refuerza.

El problema es más local: la supresión es voraz y recorre los
candidatos por relevancia, de modo que al añadir rangos posteriores
cambia el orden de esa cola y puede cambiar cuál sobrevive entre
VECINOS solapados. Un rango que en su momento era el elegido de su
zona puede dejar de estarlo cuando aparece otro que se le solapa. Al
operar el primero, esa decisión todavía no podía tomarse.

Para el GRÁFICO da igual (se dibuja a toro pasado), pero cualquier
backtest que consuma esa salida hereda un sesgo de selección con
información futura.

Resuelto en `core/levels.seleccionar_causalmente`, que rehace la
selección en cada instante de confirmación con los rangos conocidos
hasta entonces. Un rango entra en la rejilla si sobrevive a la
selección de su propio momento, y que más tarde lo suprima otro no lo
retira. Verificado con `test_seleccion_causal_no_usa_el_futuro`:
truncar el histórico al 50/70/90% no cambia lo ya seleccionado.

Efecto medido: BTC pasa de 20 rangos (selección global) a 36
(selección causal); ONDO, de 27 a 37. Son más porque un rango que en su
día fue el elegido no desaparece aunque después llegue otro mejor.

`seleccionar_rangos` NO se ha tocado: sigue siendo la función correcta
para el gráfico. Lo que cambia es quién la llama y con qué datos.

## 8. Experimentos de operativa

Mediciones exploratorias sobre lo ya cerrado. No definen estrategia:
sirven para decidir con datos qué construir después. Código en
`experiments/`, motor en `execution/backtest.py`.

### Toques de VAH/POC/VAL a pelo  [medido 02/09/2026]

**Regla probada.** El precio llega a un nivel desde arriba → long;
desde abajo → short. Orden limit permanente en el nivel, con el lado
fijado por el cierre de la última vela de 4h. Salida escalonada en
tercios sobre los tres siguientes niveles de la rejilla, stop a
break-even al alcanzar el segundo, stop inicial a 1·ATR(14) por detrás
de la entrada. Sin niveles por delante: TP 5% / SL 2%. Costes: maker
0.02%, taker 0.05%, deslizamiento 0.05% en el stop. Ejecución resuelta
sobre velas de 15m; ante la duda dentro de una vela, gana el stop.

**Universo: los FRVP de los rangos principales de la selección global,
ni uno más ni uno menos.** Son exactamente los que dibuja el gráfico:
9 rangos en BTC y 10 en ONDO, 27 niveles cada uno. Decisión del autor:
se opera lo que se ve y se valida a mano.

**Resultado: la regla pierde en los dos símbolos.**

| | BTC | ONDO |
|---|---|---|
| operaciones | 103 | 122 |
| acierto | 22.3% | 16.4% |
| R medio | **-0.099** | **-0.474** |
| profit factor | 1.03 | 0.41 |
| baselines que la baten (de 20) | 5 | 18 |

**Lo que sí queda establecido:**

1. *BTC y ONDO se comportan al revés.* En BTC la regla queda a un
   suspiro del equilibrio (profit factor 1.03, -1.5% de retorno) y solo
   5 de 20 pasadas con los niveles descolocados la igualan: hay señal.
   En ONDO se hunde (PF 0.41) y 18 de 20 la baten: ahí los niveles no
   aportan nada. Un mismo perfil de volumen no vale igual en un activo
   de 60.000 $ que en uno de 0,30 $.
2. *El VAL de BTC es el único nivel rentable de todo el experimento*:
   29 operaciones, 31.0% de acierto y **+0.635 R de media** (+18.4 R
   acumulados). El VAH del mismo activo pierde (-0.407 R en 48
   operaciones). No es simétrico, y eso es una pista: comprar el suelo
   del área de valor no es lo mismo que vender su techo.
3. *Escalonar la salida aporta.* La variante que cierra el 100% en TP1
   es peor en ambos: BTC -0.243 R frente a -0.099; ONDO -0.304 frente
   a -0.474 en R medio, pero peor en R total porque casi duplica el
   número de operaciones.
4. *El problema está en el stop, no en la entrada.* En BTC, 82 de 103
   operaciones mueren en stop. El stop de 1·ATR mide un 1.4% de
   mediana y el primer objetivo está bastante más lejos: el precio
   tiene que acertar el giro casi de inmediato o no llega ni al primer
   tercio. Las que sobreviven al primer empujón rinden mucho (las 7 que
   llegan a TP3 promedian **+6.17 R**), así que la entrada encuentra
   giros reales; lo que falla es que el stop no los deja respirar.

**Sesgo de selección, medido en vez de escondido.** La rejilla sale de
`seleccionar_rangos` sobre el histórico completo, que no es causal (ver
§7). Corriendo la misma regla sobre la rejilla causal —que no usa
información futura pero triplica el número de niveles— el resultado no
mejora, así que el sesgo no está sosteniendo el resultado:

| | BTC global | BTC causal | ONDO global | ONDO causal |
|---|---|---|---|---|
| niveles | 27 | 75 | 27 | 57 |
| operaciones | 103 | 181 | 122 | 264 |
| R medio | -0.099 | -0.118 | -0.474 | -0.458 |
| R total | -10.2 | -21.3 | -57.8 | -120.9 |

Se cambia con `seleccion_rangos` en `config.yaml`. El motor sí es
causal, y eso se verifica aparte con `test_sin_lookahead_backtest`.

**Descartado por ahora**: esta regla como estrategia. No se descarta el
FRVP como fuente de niveles —los puntos 1 y 2 dicen lo contrario en
BTC—, sino operar sus toques sin más contexto y con un stop atado al
ATR de 4h.

**Siguiente medición**: barrer `mult_atr_stop` (1.0 → 1.5, 2.0, 3.0).
Es la palanca que apunta el punto 4, y es barata: los datos están en
caché.

**Aviso de interpretación**: el retorno total (-1.5% en BTC, -86.4% en
ONDO) usa el 100% del capital en cada operación, que no es una gestión
de riesgo defendible. La métrica limpia es R. Cuando se defina la
gestión de capital habrá que rehacer la curva con riesgo fijo por
operación.

**Reproducir**: `.venv\Scripts\python.exe experiments\exp_toques_frvp.py`
Parámetros en `config.yaml`, sección `experimento_toques_frvp`.

### Imbalances como objetivo y «TP1 nunca más cerca que el stop»
[medido 02/09/2026]

Dos reglas nuevas, medidas por separado sobre la misma rejilla de 27
niveles para saber qué aporta cada una:

1. **Imbalances como zona de objetivo** — de cada imbalance semanal
   vivo salen dos: su borde de entrada y el 50% de lo que le queda sin
   rellenar. Entran en la misma lista que los niveles del FRVP,
   ordenados por cercanía. Solo para SALIR: las entradas siguen
   produciéndose únicamente en niveles del FRVP.
2. **TP1 nunca más cerca que el stop** — un primer objetivo por debajo
   de 1R hace que el primer tercio arriesgue más de lo que puede ganar.

Resultado en R medio por operación:

| | BTC | ONDO |
|---|---|---|
| base (ninguna de las dos) | **-0.099** | **-0.474** |
| solo TP1 ≥ stop | -0.215 | -0.533 |
| solo imbalances | -0.176 | **-0.464** |
| las dos | -0.270 | -0.503 |

**«TP1 ≥ stop» empeora en los dos activos y en las dos
configuraciones.** El motivo es mecánico: al alejar el primer
objetivo, las operaciones que antes cobraban un parcial cercano ahora
no cobran nada y mueren enteras en el stop. Además duran más, y con
una sola posición simultánea eso reduce el número de operaciones (BTC
103 → 88). La idea era razonable —no arriesgar 2 para ganar 1— pero
medida no se sostiene: en esta estrategia el parcial cercano es lo que
paga los stops.

**Los imbalances tienen efecto mixto y pequeño**: en ONDO mejoran algo
(-0.474 → -0.464) y en BTC empeoran el R medio (-0.099 → -0.176)
aunque suben el profit factor al mejor de los cuatro (1.03 → 1.05).
Bajan de 13 a 10 las operaciones en modo fallback, y ahí está la
clave: **el fallback fijo de 5%/2% rendía +0.846 R de media** frente a
-0.236 de las que usan objetivos de la rejilla. Cada operación que un
imbalance rescata del fallback es una que pierde su mejor objetivo.

Eso apunta a la conclusión de fondo: el problema no es que falten
zonas, es que **un objetivo a distancia fija (5%) funciona mejor que
uno anclado a estructura**, al menos con este stop. Antes de añadir más
zonas conviene medir el objetivo fijo como regla principal.

Se controlan con `usar_imbalances_como_objetivo` y
`tp1_al_menos_como_el_stop` en `config.yaml`.

### Dos posiciones simultáneas y tope de distancia entre objetivos
[medido 02/09/2026]

**Posiciones simultáneas: de 1 a 2.** El caso que lo destapó: ONDO el
11-03-2025, el precio cae sobre el rango de oct-nov 2024, toca el VAH
a las 00:15 y el POC a las 00:45. Con el límite en 1 el POC no pudo
operarse hasta 26 días después, cuando se cerró la del VAH. Pero ahí
había DOS operaciones, no una: cuando el precio barre varios niveles
en el mismo movimiento, cada uno es una entrada.

Medido en BTC, R medio por operación:

| posiciones | ops | R medio | R total | PF |
|---|---|---|---|---|
| 1 | 95 | -0.270 | -25.6 | 1.02 |
| **2** | **129** | **+0.150** | **+19.3** | **1.45** |
| 3 | 156 | +0.042 | +6.5 | 1.26 |
| sin límite | 163 | -0.007 | -1.2 | 1.19 |

**AVISO de sobreajuste**: son cuatro valores probados sobre un solo
activo y se elige el mejor. ONDO pierde con todos (-0.458 con 2). Hace
falta validarlo en más símbolos antes de darlo por bueno.

**Tope de distancia entre objetivos (5%).** Idea del autor: si tras
TP1 el siguiente nivel está lejísimos, no esperar hasta él y cerrar el
tramo a un 5% de TP1. Se aplica en cascada. Resultado, con 2
posiciones:

| | BTC R medio | BTC PF | ONDO R medio | ONDO PF |
|---|---|---|---|---|
| sin tope | **+0.150** | 1.45 | -0.458 | 0.50 |
| tope entre objetivos | +0.062 | 1.27 | -0.353 | 0.58 |
| tope también desde la entrada | -0.039 | 1.12 | **-0.184** | **0.82** |

**Efecto contrario en cada activo**: en BTC el tope resta y en ONDO
suma, y cuanto más agresivo, más marcada es la divergencia. Encaja con
lo ya visto: los objetivos a distancia fija ayudan donde la estructura
está lejos (ONDO) y estorban donde los niveles son buenos (BTC). No
hay un valor que gane en los dos.

Queda activado el tope entre objetivos, que es lo que pidió el autor,
con `distancia_maxima_objetivo_pct` y `tope_tambien_desde_la_entrada`
en `config.yaml`.

### Estado actual del experimento  [02/09/2026]

Configuración vigente: 2 posiciones simultáneas, imbalances como
objetivo, TP1 ≥ stop, tope del 5% entre objetivos.

| | BTC | ONDO |
|---|---|---|
| operaciones | 135 | 189 |
| acierto | 23.0% | 18.0% |
| R medio | **+0.062** | -0.353 |
| R total | +8.3 | -66.7 |
| profit factor | **1.27** | 0.58 |
| retorno total | **+20.0%** | — |
| max drawdown | -15.4% | — |
| baselines que la baten (de 20) | **1** | — |

**BTC es rentable por primera vez**, y solo 1 de 20 pasadas con los
niveles descolocados lo iguala: el resultado no es la deriva del
mercado. ONDO sigue perdiendo con cualquier ajuste probado.

Desglose de BTC que marca el camino:

| nivel | ops | acierto | R medio | R total |
|---|---|---|---|---|
| VAL | 38 | 31.6% | **+0.675** | +25.7 |
| POC | 42 | 23.8% | +0.212 | +8.9 |
| VAH | 55 | 16.4% | **-0.478** | -26.3 |

El VAH es el único nivel que pierde, y pierde lo suficiente para
comerse casi todo lo que ganan los otros dos. Sin él, el sistema
rendiría bastante más. Antes de tocarlo hay que entender POR QUÉ: la
hipótesis de trabajo es que en un mercado con sesgo alcista, vender el
techo del área de valor va contra la corriente dominante.

### Búsqueda sistemática de configuración  [02/09/2026]

Herramienta: `experiments/optimizar.py`. Está construida para que sea
DIFÍCIL encontrar un ganador falso:

- Cada configuración se evalúa en **cuatro subconjuntos** —BTC y ONDO,
  primera y segunda mitad del histórico— y se puntúa por el **PEOR**,
  no por la media. Una regla que gana mucho en un tramo y se hunde en
  otro no puntúa.
- Se exige un mínimo de operaciones por subconjunto, porque un R medio
  espectacular sobre cinco trades no significa nada.

Es deliberadamente conservador: elegir por la media es la forma más
rápida de fabricar un resultado que no se repite fuera de muestra.

**Hallazgo principal: el stop de 1.0·ATR era el peor valor posible.**
Peor caso de los cuatro subconjuntos:

| mult_atr_stop | peor caso |
|---|---|
| 1.0 | -0.336 |
| **1.5** | **-0.059** |
| 2.0 | -0.135 |
| 2.5 | -0.118 |

Contradice el diagnóstico anterior («las que mueren en stop apenas se
mueven a favor, así que ensancharlo no las salvaría»). Ese diagnóstico
se hizo CON el stop en 1.0, y era circular: con un stop que salta por
ruido, ninguna operación llega a moverse a favor. Corregido a 1.5.

**Los objetivos a múltiplos fijos del riesgo rescatan a ONDO y hunden
a BTC.** Con `objetivos_en_r: [1.0, 1.5, 2.5]`, ONDO pasa a positivo en
las dos mitades (+0.115 / +0.019) por primera vez, mientras BTC cae de
+0.098 a +0.044 y de -0.059 a -0.105. Cada activo pide lo contrario.

**Descartado: la nota de calidad por operación.** Se implementó como
pidió el autor, combinando a partes iguales confluencia, recorrido
disponible en R, calidad del rango de origen y alineación con la
estructura —los cuatro elegidos por razonamiento, antes de mirar
resultados. Medida sobre 202 operaciones, **no separa, y de hecho
correlaciona al revés** (-0.111 con el resultado en R):

| cuartil de nota | ops | acierto | R medio |
|---|---|---|---|
| más baja | 52 | 32.7% | +0.034 |
| | 49 | 32.7% | +0.142 |
| | 53 | 30.2% | +0.237 |
| **más alta** | 48 | **10.4%** | **-0.536** |

Los factores individuales tampoco funcionan: con 3 zonas en
confluencia el R medio es -0.914, y a más recorrido disponible, peor
resultado. La nota se conserva anotada en cada trade (columnas
`calidad`, `confluencia`, `r_potencial`, `regimen`) porque es
información útil para el análisis, pero `nota_minima_operacion` queda
en 0.0: filtrar por ella empeoraría el sistema.

**Descartado: el filtro de estructura como regla general.** Medido tras
corregir el fallo de §10:

| | BTC R medio | BTC PF | ONDO R medio | ONDO PF |
|---|---|---|---|---|
| sin filtro | +0.072 | 1.40 | **-0.081** | **0.99** |
| 4h, no en contra | +0.095 | 1.20 | -0.116 | 0.88 |
| 1d, no en contra | -0.059 | 1.04 | -0.074 | 0.92 |
| **1w, a favor** | **+0.328** | **1.41** | -0.396 | 0.55 |
| 1w, no en contra | +0.219 | 1.35 | -0.312 | 0.73 |

La estructura semanal es un filtro excelente para BTC (+0.328 R, más
que cuadruplica el resultado) y **veneno para ONDO**. No hay ajuste que
mejore los dos, así que queda en `"ninguno"`: activarlo sería elegir el
activo que mejor queda, que es justo lo que este apartado intenta
evitar. Es, además, un resultado interesante en sí mismo — sugiere que
el filtro tendría que ser por activo, o que ONDO no es operable con
esta estrategia.

### Sin límite de posiciones simultáneas  [medido 02/09/2026]

Probado a petición del autor: `max_posiciones_simultaneas: null`, que
abre todas las órdenes que se llenen. El nominal se reparte entre el
número de niveles, así que cada operación mueve 1/27 del capital en
vez de 1/2.

| | BTC PF | BTC R medio | ONDO PF | ONDO R medio |
|---|---|---|---|---|
| límite 2 | **1.34** | +0.033 | 0.96 | -0.073 |
| **sin límite** | 1.11 | -0.058 | **1.11** | **+0.005** |

**Es la única configuración en la que los dos activos superan un
profit factor de 1.** El límite de 2 daba mejor BTC a costa de que
ONDO no levantara cabeza; sin límite los dos quedan en 1.11, que es
poco margen pero es margen en ambos. Se adopta por eso.

**Cuidado al comparar drawdowns entre esas dos filas**: el 40% que
baja al 3% no es una mejora de la estrategia, es el sizing. Con 27
posiciones posibles cada una arriesga 27 veces menos. Lo comparable
entre configuraciones es R y PF, no el drawdown ni el retorno.

**Fallback con tres objetivos escalonados.** También a petición del
autor: sin zonas por delante, TP1 a un 5% de la entrada, TP2 a un 5%
de TP1 y TP3 a un 5% de TP2, en vez de cerrar el 100% en el primero.
Medido, **resta**:

| | BTC PF | ONDO PF |
|---|---|---|
| objetivo único | **1.11** | 1.11 |
| tres escalonados | 1.06 | 1.11 |

Solo 6-8 operaciones caen en fallback, así que el efecto es pequeño,
pero va en contra. Queda activado porque es lo pedido; se apaga con
`fallback_escalonado: false`.

El stop del fallback pasa a ser el mismo de ATR que el resto
(`sl_fallback_usa_atr: true`), para no tener dos criterios de riesgo
distintos conviviendo.

### Configuración vigente y resultado  [02/09/2026]

Stop 1.5·ATR, **sin límite de posiciones**, objetivos en niveles del
FRVP e imbalances, tope del 5% entre objetivos, TP1 ≥ stop, fallback
escalonado, sin filtro de estructura, sin filtro de nota.

| | BTC | ONDO |
|---|---|---|
| operaciones | 122 | 144 |
| acierto | 22.1% | 29.2% |
| R medio | -0.097 | **+0.005** |
| profit factor | **1.06** | **1.11** |
| retorno total | +0.4% | +1.8% |
| max drawdown | -2.8% | -3.1% |
| baselines que la baten (de 20) | **4** | 12 |

Los dos activos quedan por encima de PF 1 y con retorno positivo por
primera vez a la vez. Pero el contraste contra el azar se ha debilitado
respecto a configuraciones anteriores: en ONDO, 12 de 20 pasadas con
los niveles descolocados igualan el resultado, así que **allí no hay
evidencia de que los niveles del FRVP aporten nada**. En BTC, 4 de 20:
queda algo de señal, menos que antes.

Lectura honesta: repartir el riesgo entre muchas posiciones aplana la
curva y saca a los dos activos del terreno negativo, pero no mejora la
CALIDAD de las señales — solo diluye el daño de las malas. El margen es
mínimo en ambos.

Sigue sin haber una configuración positiva en los CUATRO subconjuntos.
Con FRVP e imbalances semanales solos, el sistema está en el filo. Es
el punto de partida honesto sobre el que añadir las capas siguientes.

### Gestión de riesgo: riesgo fijo por operación  [02/09/2026]

Hasta aquí el tamaño de posición era `capital / n_posiciones`, una
simplificación con dos defectos: el riesgo real por operación variaba
según dónde cayera el stop (de 0.05 $ a 0.41 $ sobre 100 $ en ONDO), y
el divisor era arbitrario —27 porque hay 27 niveles, no por ninguna
razón operativa—.

Sustituido por **riesgo fijo**: se decide cuánto se está dispuesto a
perder y el nominal sale de la distancia al stop.

    nominal = capital * riesgo_por_operacion_pct / distancia_al_stop

En los dos modos se respeta el 1x: no se compromete más capital del que
queda libre contando las posiciones abiertas. `max_posiciones_simultaneas`
deja de gobernar el tamaño y pasa a ser solo un límite de concurrencia.

**Por qué 0.5% y no más.** El criterio de Kelly sobre las operaciones
medidas:

| | BTC | ONDO |
|---|---|---|
| acierto | 22.1% | 29.2% |
| ganancia media | +3.16 R | +2.41 R |
| pérdida media | -1.02 R | -0.99 R |
| esperanza | **-0.097 R** | **+0.005 R** |
| **Kelly** | **-3.1%** | **+0.2%** |

**En BTC el Kelly es negativo: la apuesta óptima es no operar.** En
ONDO es +0.2%, o sea cero a efectos prácticos. Simulado sobre las
operaciones reales, capital final según el riesgo por operación:

| riesgo/op | BTC | ONDO |
|---|---|---|
| 0.5% | 0.94x | 1.00x |
| 2% | 0.72x | 0.94x |
| 5% | 0.32x | 0.64x |
| 25% y 50% | **ruina** | **ruina** |

Con el 50% —el reparto que implicaba el límite de 2 posiciones— el
sistema quiebra en ambos activos con las MISMAS señales. Es aritmética
de la composición, no calidad de las entradas: perder el 50% obliga a
ganar el 100% para volver al punto de partida.

**Ninguna gestión de riesgo arregla una estrategia sin esperanza
positiva.** El sizing no crea ventaja, solo administra la que haya. Por
eso queda en 0.5%, por debajo de medio Kelly, y no se sube hasta que la
esperanza sea positiva y estable.

### Las entradas son buenas; lo que falta es descartar las malas

Medido sobre las 266 operaciones de ambos activos:

| | |
|---|---|
| ganancia media | **+2.70 R** |
| pérdida media | -1.00 R |
| ratio | **2.70 a 1** |
| acierto actual | 25.9% |
| acierto para empatar | 27.1% |
| **faltan** | **1.1 puntos** |

Cuando la entrada funciona, gana 2.7 veces lo que pierde cuando falla.
Eso es una buena entrada. El sistema está a **1.1 puntos porcentuales
de acierto** de ser rentable, y quitando solo la peor combinación de
nivel × dirección ya lo sería (+0.048 R medio, 28.0% de acierto).

**Pero elegir qué quitar mirando el resultado pasado es sobreajuste**,
y además no hay patrón común: `vah short` pierde -0.987 R en BTC y gana
+0.103 en ONDO. Lo único positivo en los dos activos es `poc long`
(+0.227 y +0.423).

La conclusión operativa es que el trabajo pendiente NO está en la
entrada ni en la gestión, sino en un **filtro que descarte las malas
entradas por una razón, no por su resultado histórico**. Es exactamente
el hueco que vienen a llenar las capas que quedan por añadir: mapa de
liquidaciones, líneas de tendencia, imbalances diarios y compresiones.

### Acortar el TP1  [medido 02/09/2026]

Idea del autor a partir de un short de BTC del 1 de mayo de 2025 que
recorrió un 3.4% a favor y acabó en stop sin cobrar nada: poner un tope
al primer objetivo para cobrar ese recorrido.

**El diagnóstico le da la razón, pero solo en ONDO.** De las
operaciones que acaban en stop, cuántas llegaron a moverse a favor:

| | mediana | llegan al 3% |
|---|---|---|
| BTC | 1.19% | 19% |
| ONDO | 2.42% | **42%** |

**Fallo de implementación detectado por el autor y corregido.** La
primera versión aplicaba el tope de TP1 ANTES del recorte en cascada
del 5%, así que TP2 se medía desde el TP1 ya acortado y arrastraba
también a TP3:

    niveles del FRVP en 108, 112 y 120
      sin tope de TP1:   [108,  112,    117.6]
      con tope (mal):    [103,  108.15, 113.56]   <- TP2 y TP3 movidos
      con tope (bien):   [103,  112,    117.6]    <- solo TP1

Le cortaba las alas justo a las operaciones largas, que son las que
sostienen el sistema. Corregido aplicando el tope DESPUÉS del recorte
en cascada: solo cambia TP1, y TP2 y TP3 siguen siendo estructura. Las
mediciones hechas antes de la corrección quedaron invalidadas.

**Resultado tras la corrección** (el ATR es la unidad correcta y no el
porcentaje: el stop de 1.5 ATR mide 2.3% en BTC y 4.9% en ONDO, así que
un "3%" no significa lo mismo en cada activo):

| tope | BTC R / PF | ONDO R / PF | peor de 4 subconjuntos |
|---|---|---|---|
| sin tope | -0.097 / 1.06 | +0.005 / 1.11 | -0.097 |
| **1.0 ATR** | **-0.057 / 1.09** | **+0.037 / 1.14** | **-0.067** |
| 1.5 ATR | -0.105 / 1.02 | +0.066 / 1.22 | -0.112 |
| 3.0 ATR | -0.127 / 0.99 | +0.077 / 1.26 | -0.136 |

**1.0 ATR mejora LOS DOS ACTIVOS a la vez**, que es lo que no había
conseguido ninguna otra palanca: BTC sube de 1.06 a 1.09 de profit
factor con el drawdown bajando del 17.3% al 13.2%, y ONDO de 1.11 a
1.14. Adoptado.

Con 1.5 ATR ONDO va mejor todavía (PF 1.22, y sus dos mitades en
positivo por primera vez) pero BTC empeora, así que se queda en 1.0 por
el criterio del peor caso.

### Break-even tras TP1 en vez de TP2  [descartado, medido 02/09/2026]

| | acierto | R medio | PF |
|---|---|---|---|
| BE en TP1 | BTC 48.8% · ONDO **69.7%** | -0.134 / +0.002 | 0.92 / 1.12 |
| **BE en TP2** | BTC 25.6% · ONDO 35.7% | -0.145 / **+0.028** | 0.93 / **1.15** |

Adelantar el break-even dispara el acierto —ONDO llega al 70%— y aun
así el sistema rinde igual o peor, porque las ganadoras grandes se
cierran a cero antes de desarrollarse. **Es el recordatorio de que el
acierto no es la métrica**: lo es acierto x ganancia media. Se conserva
`mover_be_en_tp: 2`.

### El patrón que se repite: BTC y ONDO piden lo contrario

No es casualidad de un parámetro suelto. Cada palanca medida separa a
los dos activos en direcciones opuestas:

| palanca | BTC prefiere | ONDO prefiere |
|---|---|---|
| objetivos | niveles reales | múltiplos fijos de R |
| filtro de estructura | sí (1w, +0.328 R) | no (-0.396 R) |
| tope de TP1 | ninguno | 1.5 ATR |
| tope de distancia (5%) | ninguno | sí |

La lectura es que un único juego de parámetros globales no va a servir
para los dos. Las salidas posibles son **configuración por activo**
—con su propia validación fuera de muestra, o el sobreajuste está
garantizado— o un filtro que capture la diferencia de fondo entre
ambos, que es lo que podrían aportar las capas pendientes.

## 9. Imbalances semanales  [IMPLEMENTADO — sin usar todavía]

Zonas de precio que el mercado atravesó sin negociar. Se van a usar
como **zonas de objetivo**, además de los niveles del FRVP.

Motivo de incorporarlos ahora, medido en §8: con solo 27 niveles del
FRVP, el primer objetivo llega a quedar a un **-30% de la entrada**
(los shorts de marzo de 2025 en BTC tenían el TP1 en 64.175 entrando en
91.137, porque no hay ningún nivel en medio). Prácticamente ninguna
operación lo alcanza, así que hoy el experimento mide la resistencia
del stop y no la calidad de la entrada. Hacen falta zonas de salida
intermedias.

**Definición: FVG de tres velas.** Si el mínimo de la tercera vela
queda por encima del máximo de la primera, esa franja no se negoció
(imbalance alcista); simétrico para el bajista. En cripto no hay huecos
de apertura como en acciones —el mercado no cierra nunca—, así que este
es el único hueco real y por eso es el que se busca.

**Timeframe: semanal.** Son zonas de estructura mayor; en 4h saldrían
decenas sin peso.

**Relleno progresivo.** Un imbalance no es un interruptor. Conforme el
precio vuelve a entrar en la franja, la parte visitada deja de ser
hueco y se recorta; lo que queda sin visitar sigue vivo aunque sea un
10% del original. Cuando el precio lo recorre entero, muere y deja de
dibujarse. El borde solo avanza en una dirección, así que basta un
mínimo (o máximo) acumulado, que solo mira hacia atrás.

**El relleno se mide con mechas**: si el precio pasó por ahí, esa
franja ya se negoció, aunque la vela cerrara fuera. Coherente con
definir el hueco por máximos y mínimos, y es lo que pidió el autor.

Implementado en `core/imbalances.py`:

    imbalances = detectar_imbalances(velas_semanales)
    vivos = imbalances_vivos(imbalances, velas_4h, instante)

**Marca temporal**: un imbalance no existe hasta que CIERRA la tercera
vela de su patrón (antes no se conoce su extremo). Esa marca viaja en
`confirmado_en` y el consumidor debe respetarla, igual que con los
rangos.

Medido sobre 2 años: 18 imbalances en BTC (4 sin rellenar hoy) y 18 en
ONDO (6 sin rellenar). Se dibujan en morado, en la vista «Imbalances
1w» y en las tres de operaciones, con forma de escalera: cada peldaño
es una vez que el precio se comió un trozo.

Pruebas en `tests/test_imbalances.py` (10, en verde), incluida
`test_imbalances_vivos_no_usa_el_futuro`, que corta el histórico al
50/70/90% y exige que lo que estaba vivo antes del corte salga igual.

Se usan como zonas de objetivo desde el 02/09/2026 (§8).

## 11. Filtro de impulso de aproximación  [IMPLEMENTADO Y ACTIVO]

La pregunta que lo origina la formuló el autor: hay toques del nivel en
los que el precio **reacciona** y rebota, y toques en los que el precio
**atraviesa** el nivel sin inmutarse. Los segundos son los que hay que
descartar. ¿Cómo se distinguen antes de entrar?

En términos de perfil de volumen es la diferencia entre **rechazo** y
**aceptación**: hay rechazo cuando existe liquidez pasiva suficiente
para absorber el flujo agresivo, y aceptación cuando no la hay.

**La medida: cuánto ATR ha recorrido el precio en las 6 velas
anteriores al toque.** Distingue una llegada disparada de una que se
arrastra, y se calcula con velas cerradas.

**El resultado sale AL REVÉS de lo que se suponía.** Medido por
cuartiles de impulso sobre 272 operaciones:

| cuartil | R medio | acierto |
|---|---|---|
| Q1 — llegada más lenta | **-0.417** | 19% |
| Q2 | -0.069 | 24% |
| Q3 | -0.147 | 21% |
| Q4 — llegada más vertical | **+0.610** | **43%** |

Las operaciones que peor rinden no son las verticales: son **las
lentas**. La correlación del impulso con el resultado es positiva en
los dos activos (+0.087 en BTC, +0.244 en ONDO).

Interpretación: cuando el precio llega disparado a un nivel llega
sobreextendido, y el rebote técnico es más probable y más amplio.
Cuando llega arrastrándose, el nivel ya está digerido y lo atraviesa
sin drama. La intuición contraria —«lo vertical rompe»— describe lo que
pasa DESPUÉS de romper, no lo que ocurre al llegar.

Se descartaron por menos poder predictivo: expansión de volatilidad
(ATR corto / ATR largo), convicción (cuerpo sobre rango), volumen
relativo e impulso a 3 velas.

**Efecto del filtro** (peor de los cuatro subconjuntos):

| mínimo | BTC R / PF | ONDO R / PF | peor de 4 |
|---|---|---|---|
| sin filtro | -0.057 / 1.09 | +0.037 / 1.14 | -0.067 |
| 0.5 ATR | +0.046 / 1.20 | +0.189 / 1.35 | +0.031 |
| **1.0 ATR** | **+0.110 / 1.36** | **+0.164 / 1.30** | **+0.069** |
| 2.0 ATR | +0.139 / 1.42 | +0.289 / 1.73 | -0.005 |
| 2.5 ATR | +0.012 / 1.28 | +0.359 / 1.90 | +0.336 |

**Es la primera configuración con los CUATRO subconjuntos en
positivo.** Y lo importante no es el valor elegido, sino que TODOS los
umbrales mejoran respecto a no filtrar: la señal está en la variable,
no en el ajuste.

Se adopta 1.0 y no 2.5 —que tiene mejor peor-caso— porque 2.5 deja solo
45-54 operaciones por activo, muestra demasiado pequeña.

PENDIENTE: validar en más activos antes de darlo por bueno.

## 17. La lectura manual del indicador, contrastada  [02/09/2026]

El autor facilitó su configuración exacta del SQZ+ADX+TTM y cómo lo
lee. Dos cosas salieron de ahí, y las dos son informativas.

### El «Key Level» 23 no bate al 35

Su indicador usa Key Level 23. El proyecto había elegido 35 midiendo,
sin conocer ese valor, así que había que comprobarlo: si funcionaba
igual, ganaba el suyo por tener respaldo fuera de nuestra muestra.

| adx_maximo | BTC R / PF | ONDO R / PF | peor de 4 |
|---|---|---|---|
| **23** | **+0.590 / 2.48** | +0.126 / 1.33 | -0.029 |
| 25 | +0.640 / 2.54 | +0.100 / 1.23 | +0.005 |
| 30 | +0.505 / 2.32 | +0.132 / 1.27 | -0.018 |
| **35** | +0.394 / 2.03 | **+0.264 / 1.51** | **+0.205** |
| 40 | +0.341 / 1.87 | +0.217 / 1.43 | +0.204 |

Con 23, BTC mejora mucho (PF 2.48) pero **ONDO se hunde** y su primera
mitad se va a negativo. El 35 gana por el criterio del peor
subconjunto, y el 40 queda casi igual, lo que confirma que la meseta
está arriba. Se mantiene 35.

### La fase del histograma: al revés de la lectura manual

El autor compra «en valle rojo» y vende «en valle verde desarrollado»,
es decir, con el histograma **agotándose**. Eso no es el nivel del
histograma —que ya se había medido y descartado— sino su FASE: signo
cruzado con derivada, la lectura clásica de cuatro colores.

Medido sobre las 157 operaciones actuales:

| | R medio | ops |
|---|---|---|
| fase agotándose (la lectura manual) | **-0.086** | 26 |
| fase acelerando | **+0.401** | 131 |

Consistente en los dos activos (BTC -0.017 vs +0.457, ONDO -0.122 vs
+0.355). **Lo que funciona es lo contrario de la lectura habitual.**

Encaja con el hallazgo del impulso (§11): lo que este sistema quiere es
que el precio llegue al nivel con fuerza, no agotado. Pero **no es
redundante con él**, que era el riesgo evidente: la correlación entre
ambos es de solo +0.215 y el efecto se mantiene dentro de cada tramo de
impulso.

| | fase agotándose | fase acelerando |
|---|---|---|
| impulso bajo | -0.077 | **+0.212** |
| impulso alto | +0.327 | **+0.647** |

Se adopta como sexta señal del score (`fase_ttm`), con los escalones de
tamaño corridos de 4 a 5. Efecto en el ratio retorno/drawdown: ONDO
3.24 → 3.78, BTC 5.47 → 5.27. Mejora el peor de los dos y la
correlación del score con el resultado sube de +0.106 a +0.133.

**Matiz honesto sobre la discrepancia**: el autor opera en 1h con
divergencias, no en 4h sobre niveles del FRVP. Que su lectura no
funcione EN ESTE SISTEMA no dice nada sobre si le funciona a él en el
suyo; son contextos distintos.

### Panel en los gráficos

`notebooks/exploracion.ipynb` dibuja ahora una tercera fila con el
histograma del TTM en sus cuatro colores, la línea del ADX con el Key
Level 23 y el umbral 35 del filtro, y marcas de squeeze. Siempre
visible, para poder contrastar lo que ve el bot con lo que se ve en
TradingView.

## 16. Régimen de volatilidad: ADX y squeeze  [ACTIVO, 02/09/2026]

El mayor salto del proyecto, y el primero que sale de una hipótesis
formulada por adelantado en vez de de una búsqueda.

### La hipótesis

La estrategia es de REVERSIÓN: entra contra el movimiento cuando el
precio toca un nivel. De ahí dos predicciones falsables:

- un nivel tocado **en tendencia fuerte se rompe** → el ADX, que mide
  fuerza de tendencia sin decir hacia dónde, debería separar con signo
  NEGATIVO;
- un nivel tocado **con la volatilidad comprimida** no da recorrido
  para llegar a ningún objetivo, y el stop salta por ruido → el
  *squeeze* debería ser malo.

Las dos se confirman.

### Cómo se encontró, que es lo interesante

En la muestra completa el ADX **no separa**: +0.024 en BTC y +0.043 en
ONDO. Solo aparece al mirar **dentro del subconjunto que ya pasa el
filtro de impulso**: -0.096 y -0.059, consistente y con el signo
predicho.

Es la primera variable que **aporta información donde el impulso no
llega**, en lugar de ser un espejo suyo. Justifica por sí sola el paso
de método que introdujeron los bloques 1-3 (§15): medir siempre también
sobre el subconjunto ya filtrado.

### Medición

Sobre las 222 operaciones que se operan de verdad:

| | BTC R [1ª/2ª] | ONDO R [1ª/2ª] | ops |
|---|---|---|---|
| todas | +0.110 [+0.43/+0.07] | +0.164 [+0.18/+0.16] | 222 |
| sin squeeze | +0.206 [+0.57/+0.16] | +0.204 [+0.21/+0.20] | 204 |
| ADX < 35 | +0.245 [+0.57/+0.19] | +0.251 [+0.23/+0.27] | 147 |
| **sin squeeze y ADX < 35** | **+0.405 [+0.70/+0.35]** | **+0.316 [+0.28/+0.35]** | 134 |

El squeeze por sí solo es demoledor: con la volatilidad comprimida el R
medio es **-0.504 en BTC y -0.805 en ONDO**, frente a +0.206 y +0.204
sin ella. Son pocas operaciones (18 de 222) pero el signo es el mismo en
los dos activos y la magnitud, enorme.

**Sobre el umbral del ADX**: se probaron 20, 25, 30 y 35. El 30 también
funciona (+0.364 / +0.271 combinado), así que 35 no es un pico aislado
sino una meseta —que es lo que distingue un efecto real de una
casualidad—. Se elige 35 por conservar más operaciones.

### Efecto en el sistema completo

| | antes | **con filtro de régimen** |
|---|---|---|
| BTC | +0.110 · PF 1.36 · +12.1% · DD -10.9% | **+0.394 · PF 2.03 · +22.2% · DD -4.9%** |
| ONDO | +0.164 · PF 1.30 · +12.3% · DD -8.4% | **+0.264 · PF 1.51 · +13.9% · DD -5.7%** |
| peor de 4 subconjuntos | +0.069 | **+0.205** |
| baselines que baten a BTC | 4 de 20 | **0 de 20** |

Mejoran a la vez R medio, profit factor, retorno **y** drawdown, en los
dos activos. Y ninguna de las 20 pasadas con los niveles descolocados
alcanza a BTC.

### Se cierra el agujero del VAH

El VAH venía siendo el único nivel que perdía (-0.256 R en BTC frente a
+0.414 del VAL), sin explicación. Con el filtro de régimen:

| nivel | ops | acierto | R medio |
|---|---|---|---|
| VAL | 21 | 38.1% | +0.539 |
| **VAH** | 29 | 24.1% | **+0.272** |
| POC | 18 | 33.3% | +0.424 |

**Los tres niveles en positivo por primera vez.** La explicación encaja:
el VAH fallaba porque se vendía el techo del área de valor en tendencia
alcista fuerte, y esas son exactamente las operaciones que el ADX alto
descarta. No era el nivel: era el régimen en el que se operaba.

Implementado en `core/osciladores.py` (`adx`, `bollinger`, `keltner`,
`squeeze`, `momento_ttm`) y controlado por `adx_maximo` y
`evitar_squeeze` en `config.yaml`.

**Descartados del mismo bloque**: el momento del TTM (+0.140 BTC /
-0.047 ONDO, signos opuestos), la anchura de Bollinger en percentil
(+0.078 / -0.098) y el %B (-0.063 / +0.015). Los tres inconsistentes
dentro del subconjunto filtrado.

### Divergencias, revisadas con el indicador que usa el autor

El momento del TTM se había descartado por su NIVEL, pero lo que se usa
en la práctica son sus DIVERGENCIAS —«los valles rojos haciendo un
mínimo más alto mientras el precio hace uno más bajo»—. Medido sobre las
157 operaciones actuales, comparando las tres fuentes:

| divergencia a favor vs resto | BTC | ONDO |
|---|---|---|
| MACD | +0.776 vs +0.344 | +0.210 vs +0.283 |
| RSI | +0.722 vs +0.385 | +0.617 vs +0.219 |
| TTM | +0.704 vs +0.335 | +0.228 vs +0.272 |

Ninguna es concluyente: las muestras a favor son de 2 a 23 operaciones,
y en ONDO el MACD y el TTM van al revés. Además **las tres son casi el
mismo indicador**: MACD y RSI coinciden en el 82% de las operaciones,
MACD y TTM en el 72%. No aportan tres lecturas, aportan una.

### Revalidación del score tras el cambio de régimen

El score se calibró sobre 272 operaciones y ahora se opera sobre 157
distintas, así que hubo que revalidarlo. Sigue siendo monótono (score 2:
+0.179, score 4: +0.518, score 5: +0.780) pero **dos de sus cinco
señales han dejado de sumar**:

| señal | BTC con/sin | ONDO con/sin | |
|---|---|---|---|
| rotación | +0.683 / -0.101 | +0.271 / +0.249 | sigue sumando |
| contra estructura | +0.547 / +0.029 | +0.342 / +0.095 | sigue sumando |
| divergencia | +0.776 / +0.344 | +0.210 / +0.283 | inconsistente |
| poca confluencia | +0.327 / **+0.903** | +0.267 / +0.180 | se invirtió |

**No se tocan todavía**: con muestras de 11 a 31 operaciones, quitar
señales sería ajustar al ruido. Queda anotado para revisarlo cuando
haya más activos, que es lo que dará muestra suficiente.

### Gestión de riesgo: el Kelly ya no es negativo

| | antes | ahora | ratio gana/pierde |
|---|---|---|---|
| BTC | **-3.1%** (no operar) | **+13.6%** | 3.99 : 1 |
| ONDO | +0.2% | **+11.8%** | 3.02 : 1 |

Por primera vez hay margen real para arriesgar. El multiplicador de
tamaño por convergencia sube de 1.5 a 2.0, que mejora el ratio
retorno/drawdown en los dos activos (BTC 4.44 → 5.47, ONDO 3.10 →
3.24).

**Pero el riesgo base se queda en 0.5%**, con lo que el máximo por
operación es un 1%: **seis veces menos que el medio Kelly**. Es
deliberado —el Kelly supone que la distribución medida es la real, y con
68-89 operaciones por activo la incertidumbre es enorme— y además subir
la base al 1% mejora BTC (ratio 5.97) pero empeora ONDO (2.96).

## 15. Calificadores de operación: tres bloques medidos  [02/09/2026]

Objetivo del autor: **abrir el máximo de operaciones con buena
calificación y el mínimo con mala**, decidiendo antes de entrar. Se
atacan de uno en uno, cerrando cada bloque antes del siguiente.

### Regla que sale del primer bloque

**Una variable predictiva no sirve si explotarla cuesta más de lo que
aporta.** Hay que medir el sistema completo, nunca solo la correlación.

### Bloque 1 — Rechazo en la vela del toque  [descartado]

| medida | BTC | ONDO |
|---|---|---|
| **cierre lejos del nivel** | **+0.207** | **+0.235** |
| **penetración del nivel** | **-0.236** | **-0.193** |
| mecha de rechazo | -0.013 | +0.085 |
| volumen relativo | -0.043 | -0.154 |
| absorción | -0.021 | +0.130 |

Las dos primeras son **las señales más predictivas de todo el
proyecto** —por cuartiles, el cierre más pegado al nivel da -0.580 R con
un 12% de acierto frente a +0.18 en el resto— y aun así **no son
explotables**. Usarlas exige esperar al cierre de la vela y entrar peor:

| confirmar_rechazo | BTC R / PF | ONDO R / PF | peor de 4 |
|---|---|---|---|
| **null (limit directa)** | **+0.110 / 1.36** | **+0.164 / 1.30** | **+0.069** |
| 0.00 | -0.076 / 0.82 | -0.019 / 0.93 | -0.130 |
| 0.15 | -0.290 / 0.56 | -0.033 / 0.86 | -0.339 |

Esperar cuesta el precio de entrada (la ganancia media cae de +2.80 a
+2.33 R) **y además baja el acierto** (25% → 23%), porque al entrar más
lejos del nivel el stop queda peor colocado respecto a la estructura.
Implementado en `confirmar_rechazo`, desactivado.

### Bloque 2 — Aceleración del movimiento  [descartado]

¿Importa CÓMO se reparte el impulso? Cinco medidas: reparto entre las 3
últimas velas y las 6, ratio de velocidades, delta del impulso,
extensión y rectitud del recorrido.

Dos salen consistentes por separado (reparto -0.073/-0.061, rectitud
+0.056/+0.256), pero **la prueba decisiva las tumba**: dentro de las
operaciones que ya pasan el filtro de impulso, ninguna mantiene el
signo en los dos activos (rectitud: -0.061 BTC / +0.119 ONDO). La
información ya estaba capturada por el impulso.

### Bloque 3 — Osciladores  [medido, fuera del score]

El ejemplo del autor: si el precio cae hacia un nivel y el momento no
acompaña, hay agotamiento. Lo que califica no es el NIVEL del oscilador
sino si confirma o contradice al precio.

| señal | R con | R sin | BTC | ONDO |
|---|---|---|---|---|
| **estocástico extremo** | **+0.157** | -0.082 | +0.237 vs -0.184 | +0.097 vs +0.007 |
| RSI extremo | -0.060 | +0.006 | -0.136 | +0.010 |
| divergencia del RSI | +0.127 | -0.020 | **-0.109** | +0.381 |

**El RSI extremo no separa y el estocástico sí**, sobre las mismas
operaciones. La diferencia está en lo que mide cada uno: el RSI compara
la magnitud de subidas y bajadas, mientras que el estocástico dice
literalmente **en qué parte de su rango reciente está el precio**, que
es lo relevante al tocar un nivel. La divergencia del RSI es
inconsistente (negativa en BTC), al revés que la del MACD.

**Pero el estocástico NO entra en el score, por redundancia.** El 94% de
las operaciones que lo activan activan también el impulso, frente al 44%
de las que no: es casi un subconjunto —si el precio llega disparado al
nivel, acaba en el extremo de su rango—. Sumarlo diluye el score en vez
de enriquecerlo (la correlación cae de +0.231 a +0.076).

Como filtro directo tampoco se adopta: mejora el R medio (BTC +0.182,
ONDO +0.198) pero recorta las operaciones un 62% (222 → 84) sin mejorar
el retorno de forma clara.

Implementado en `core/osciladores.py` y registrado en la columna
`senales` de cada operación. Se reactiva sin tocar código con
`senales_opcionales_en_score: ["estocastico"]`.

### Lección de los tres bloques

Los tres han encontrado señales **predictivas** y ninguna ha resultado
**explotable**, por dos motivos distintos que conviene distinguir:

- el bloque 1 falla por **coste de ejecución**: la información existe
  pero llega tarde y cobrarla sale caro;
- los bloques 2 y 3 fallan por **redundancia**: la información ya
  estaba dentro del filtro de impulso.

Antes de añadir una señal nueva hay que preguntarse si es independiente
de las que ya hay, y comprobarlo sobre el subconjunto ya filtrado, no
sobre la muestra completa.

## 13. Contexto de flujo y convergencia de señales  [02/09/2026]

Motivo: el bot de referencia que sigue el autor (NQS ÆON) tiene una
esperanza prácticamente idéntica a la nuestra (≈ +0.13 R/op frente a
+0.141) y sus TP a 1R/2R/3R rinden PEOR que nuestros niveles en BTC. Su
ventaja, si la tiene, no está en las salidas: está en el contexto de
flujo —posiciones de fondos, funding, liquidaciones— que a nosotros nos
falta por completo.

### Qué se puede conseguir (verificado antes de implementar)

| dato | histórico | veredicto |
|---|---|---|
| Funding (Binance) | BTC desde 2024-09-03 (8h), ONDO desde 2024-09-02 (4h) | **backtesteable** |
| Funding (Kraken) | solo desde 2025-08-27 | no cubre el periodo |
| **Open interest** | el endpoint rechaza `startTime`: solo 30 días | **NO backtesteable** |
| Liquidaciones | no expuesto por CCXT | fuera de alcance |
| Posiciones de fondos | Hyperliquid, API pública | otra integración, pendiente |

El open interest queda fuera del backtest: sin histórico no hay forma de
medir si aporta. Es candidato para paper/live, donde sí puede
registrarse en vivo.

**Supuesto declarado**: el precio y la ejecución son de Kraken y el
funding de Binance. Misma clase de mezcla que `volume_source:
"aggregated"` (§2). Se sostiene porque el desequilibrio de
apalancamiento en perpetuos es una variable global —los arbitrajistas
mantienen los funding alineados entre plataformas— pero el número exacto
de Binance no es el que cobra Kraken.

Implementado en `data/funding.py` (ingesta con caché y validación) y
`core/flujo.py` (z-score, acumulado y orientación a favor del trade).
2.190 pagos descargados en BTC y 4.379 en ONDO.

### El funding NO aporta  [descartado]

| medida | BTC | ONDO |
|---|---|---|
| funding crudo | -0.104 | +0.125 |
| funding orientado a favor | +0.013 | +0.019 |
| z-score orientado | +0.018 | +0.083 |
| acumulado orientado | +0.045 | +0.010 |

Ninguna correlación pasa el listón, ningún cuartil es monótono, y lo
definitivo: **por signo va al revés en cada activo**. En BTC tener el
funding a favor da -0.179 R y en contra +0.041; en ONDO, +0.113 y
-0.038. Se conserva el módulo —la ingesta es correcta y sirve para
paper/live— pero no entra en la estrategia.

### Convergencia de señales  [ACTIVO — como sizing, no como filtro]

Ninguna señal decide sola, pero **sumadas informan**. Aporte de cada una
por separado (R medio con la señal frente a sin ella):

| señal | con | sin | BTC | ONDO |
|---|---|---|---|---|
| **impulso** | **+0.252** | -0.390 | +0.176 | +0.312 |
| **rotación** | +0.095 | -0.243 | +0.140 | +0.059 |
| **divergencia** | +0.131 | -0.039 | +0.195 | +0.089 |
| estructura a favor | **-0.171** | +0.070 | -0.159 | -0.178 |
| confluencia alta | **-0.236** | +0.016 | -0.221 | -0.271 |
| funding | -0.011 | -0.000 | -0.179 | +0.113 |

Tres suman y **dos restan de forma consistente en los dos activos**, así
que estas se cuentan INVERTIDAS: puntúa ir CONTRA la estructura semanal
y tener POCA confluencia. Lo primero es contraintuitivo hasta que se
recuerda que esto es una estrategia de reversión —se entra contra el
movimiento, así que la estructura dominante es lo que hay que
contradecir—. Lo segundo encaja con que un nivel muy disputado ya está
gastado.

Con las cinco en el sentido correcto, el score **es monótono**:

| score | ops | acierto | R medio |
|---|---|---|---|
| 2 | 56 | 12.5% | -0.533 |
| 3 | 113 | 21.2% | -0.237 |
| **4** | **82** | **42.7%** | **+0.704** |

Correlación con el resultado: **+0.247 en BTC y +0.219 en ONDO**.

**Como FILTRO no funciona.** Con `score >= 4` BTC se dispara (+0.414 R,
PF 1.85) pero ONDO se queda plano (+0.015, PF 1.05) y el peor de los
cuatro subconjuntos empeora de +0.069 a -0.007: sacrifica demasiadas
operaciones en ONDO.

**Como MULTIPLICADOR DE TAMAÑO sí.** No cambia qué operaciones se toman
—el R medio y el PF quedan igual— pero reparte mejor el capital:

| escalado | BTC ret / DD (ratio) | ONDO ret / DD (ratio) |
|---|---|---|
| ninguno | +5.9% / -8.7% (0.67) | +10.5% / -5.3% (**1.98**) |
| **0.5 / 1.0 / 1.5** | **+12.1% / -10.9% (1.11)** | **+12.3% / -8.4% (1.47)** |
| 0.25 / 1.0 / 2.0 | +18.8% / -12.3% (**1.54**) | +14.9% / -11.6% (1.29) |

Se mira el retorno AJUSTADO POR RIESGO y no el bruto: escalar tamaño
siempre sube el retorno si la esperanza es positiva, y eso solo no
prueba nada.

**Por ratio la elección no es unánime**, y conviene decirlo: BTC pasa de
0.67 a 1.11, pero ONDO CAE de 1.98 a 1.47. Por ratio puro BTC querría el
escalado fuerte (1.54) y ONDO no querría ninguno (1.98) — el mismo
patrón de §8, cada activo pide lo contrario. Se adopta el suave porque
el fuerte dobla el riesgo base y con el Kelly del sistema aún rozando
cero no hay margen para tanto, y porque de las tres filas es la que más
levanta al activo peor dejando al otro todavía por encima de él.

*(Tabla remedida el 02/09/2026 sobre la configuración vigente. La
versión anterior daba +19.9% / -12.0% y +16.6% / -9.3% en la fila del
escalado fuerte y afirmaba que el ratio de ONDO «apenas se mueve». Se
midió sobre una configuración anterior; cuál de los cambios posteriores
la desplazó no se ha aislado.)*

Implementado en `core/convergencia.py`. Cada operación registra su
`score` y qué señales la apoyaban (columna `senales`), para poder
auditarla después.

## 12. Qué más se probó para detectar el rechazo  [02/09/2026]

Investigación abierta sobre cómo distinguir un rechazo real de un
nivel. Todo medido sobre las 272 operaciones de ambos activos, sin el
filtro de impulso para no sesgar la muestra. Se anota también lo que NO
funciona, que es la mitad del valor.

**Criterio para dar algo por bueno**: correlación del mismo signo en
LOS DOS activos y efecto visible por cuartiles. Una variable que separa
en BTC y no en ONDO es una casualidad hasta que se demuestre lo
contrario.

### Momento: RSI y MACD  (`core/momentum.py`)

Implementados con sus pruebas, incluida la de truncado. Medidos:

| medida | BTC | ONDO |
|---|---|---|
| RSI | -0.055 | -0.041 |
| RSI orientado a favor del trade | +0.016 | +0.019 |
| histograma del MACD | -0.090 | -0.022 |

**Ninguno aporta.** El nivel del RSI en el momento del toque no predice
si el nivel aguantará.

### Divergencias precio-momento

La idea del autor: divergencia entre el precio y el histograma del
MACD justo en el toque de un nivel. Implementado en
`core.momentum.divergencias`, comparando pivotes CONFIRMADOS y
declarando la divergencia solo cuando el segundo pivote se confirma.

| | ops | R medio |
|---|---|---|
| BTC, divergencia a favor | 21 | **+0.195** |
| BTC, divergencia en contra | 17 | **-0.324** |
| BTC, ninguna | 85 | -0.066 |
| ONDO, a favor | 32 | +0.089 |
| ONDO, en contra | 25 | +0.008 |
| ONDO, ninguna | 92 | +0.027 |

**En BTC separa de verdad** —medio R de diferencia entre tenerla a
favor o en contra— y en ONDO apenas. Probada como veto (descartar las
entradas con divergencia en contra), el efecto sobre el conjunto es
ambiguo y con muestras de 17-25 operaciones. Queda implementada y
medida, sin activar.

### Ideas de la literatura de Market Profile

| idea | resultado |
|---|---|
| **Desgaste del nivel** (naked/virgin POC, Dalton): un nivel intacto atrae, cada visita lo consume | **No se sostiene.** Correlación -0.071 en BTC y +0.109 en ONDO, signos opuestos, y por grupos el patrón es errático con muestras de 6-11 operaciones |
| **Antigüedad del nivel** | Consistente pero marginal (+0.058 / +0.079) |
| **Sobreextensión** (z-score frente a la media de 50) | No monótono por cuartiles; no aporta |
| **Sesión horaria** | La franja asiática (0-5 UTC) es la peor con diferencia (-0.121 R, 17.4% de acierto) frente a +0.139 en la europea. Efecto real pero es un filtro de calendario: alto riesgo de data mining, no se adopta sin más evidencia |
| **Rotación del área de valor** (Steidlmayer) | **Funciona** — ver abajo |

### Rotación del área de valor  [implementado, sin activar]

Dentro del área de valor el precio ROTA entre sus extremos: si viene
del POC hacia el VAH, lo normal es que lo alcance, no que rebote antes
de llegar. Operar contra esa rotación es apostar contra el
comportamiento típico del perfil.

| zona de llegada | BTC | ONDO |
|---|---|---|
| POC (dentro del AV) | +0.157 | +0.091 |
| llega desde FUERA del AV | +0.129 | +0.019 |
| **llega desde DENTRO del AV** | **-0.515** | **-0.014** |

Esto da fundamento teórico a algo que antes parecía data mining: el
`vah short` que venía saliendo como la peor combinación es exactamente
«llegar al VAH desde dentro del área de valor», y el `val long` es su
simétrico. No son dos casualidades, son el mismo fenómeno.

**Pero no se activa, y el motivo importa**: por separado el filtro
dispara BTC (R medio -0.057 → +0.173, PF 1.37) y hunde la segunda mitad
de ONDO. Combinado con el filtro de impulso es todavía peor, porque
**los dos capturan parte de lo mismo**: las entradas que llegan «desde
dentro» suelen ser también las que llegan arrastrándose.

| filtros | peor de los cuatro subconjuntos |
|---|---|
| ninguno | -0.067 |
| solo rotación | -0.078 |
| **solo impulso** | **+0.069** |
| impulso + rotación | -0.096 |

Lección general: dos filtros buenos por separado pueden estorbarse. Hay
que medir la combinación, nunca sumarlos por fe.

## 10. Estructura de mercado  [IMPLEMENTADO]

Alcista, bajista o indefinida, según la sucesión de swings
(`core/structure.py`):

- **alcista** — máximo mayor que el anterior Y mínimo mayor
- **bajista** — máximo menor que el anterior Y mínimo menor
- **indefinida** — el resto: el precio se ensancha o consolida

Exigir que coincidan máximo y mínimo es lo que separa una tendencia de
un tramo simplemente ancho. Los swings se calculan sobre CIERRES con
confirmación a ambos lados, reutilizando `_posiciones_pivotes` del
Filtro 1: tener dos nociones de «máximo relevante» en el mismo sistema
sería pedir que se contradigan.

Se marca además la **ruptura de estructura**: el cierre que deja atrás
el último swing confirmado en contra. Llega antes que el cambio de
régimen, que necesita dos pivotes nuevos.

**Marca temporal**: un pivote de la vela `i` no existe hasta `i + R`.
En la vela `t` solo se usan pivotes con `i + R <= t`. Al juzgar la
estructura en un timeframe mayor (`estructura_alineada`), el régimen de
una vela diaria o semanal no llega a las velas de 4h hasta que esa vela
CIERRA.

Pruebas en `tests/test_structure.py` (6, en verde), incluidas
`test_estructura_no_usa_el_futuro` y
`test_la_estructura_de_un_timeframe_mayor_no_se_adelanta`.

**Aviso de un fallo detectado y corregido (02/09/2026)**: la primera
versión pasaba el DataFrame completo a `_posiciones_pivotes`, que
espera la SERIE de cierres. Devolvía índices de una matriz aplanada y
el régimen salía siempre «indefinida». Las mediciones del filtro
hechas antes de la corrección quedaron invalidadas y se rehicieron. Lo
detectaron los tests sintéticos; los de datos reales no, porque
comparaban «indefinida» con «indefinida» y pasaban igual.

## 18. Calidad de la entrada, aislada de la salida  [02/09/2026]

Todo lo medido hasta el bloque 5 usaba `pnl_r`, que es entrada Y
gestión de salida juntas. Eso deja una pregunta sin responder: **¿el
sistema gana por acertar el momento de entrar, o por cómo cierra?**

La respuesta importa porque cambia dónde merece la pena seguir
trabajando, y porque un descarte pasado puede haber sido culpa de la
gestión y no de la señal.

### Método

Para cada operación, partiendo del precio de entrada y **sin stop ni
objetivos**, se sigue el precio N velas de 4h y se anota

    MFE          máxima excursión a favor
    MAE          máxima excursión en contra
    eficiencia   MFE / (MFE + MAE),  0.5 = moneda al aire

Todo en unidades de ATR de la vela de entrada, para que BTC y ONDO sean
comparables. Horizonte principal: 24 velas de 4h (4 días).

El valor absoluto del MFE no dice nada —crece solo con el horizonte—,
así que se mide contra dos controles:

- **aleatorio**: 400 remuestreos de fechas al azar en la misma época,
  con el mismo reparto long/short
- **rechazado**: los toques de los mismos niveles que los filtros
  (squeeze, ADX, impulso) tumbaron. Es el control bueno, porque aísla
  lo que aportan los filtros de lo que aporta el nivel

### Resultado

| | BTC MFE / MAE / ef | ONDO MFE / MAE / ef |
|---|---|---|
| aceptado por los filtros | 3.21 / 3.16 / **0.519** (n=191) | 3.27 / 2.94 / **0.529** (n=289) |
| rechazado por los filtros | 2.63 / 2.78 / 0.513 (n=447) | 2.64 / 2.78 / 0.517 (n=483) |
| aleatorio | 2.98 / 2.99 / 0.500 | 2.93 / 2.91 / 0.502 |

La eficiencia real supera al **80.5%** de los remuestreos aleatorios en
BTC y al **94.0%** en ONDO.

**La ventaja de entrada existe, pero es pequeña**: dos o tres puntos de
eficiencia sobre la moneda al aire. Los filtros suben el MFE de 2.63 a
3.21 (BTC) y de 2.64 a 3.27 (ONDO) —un 22% más de recorrido a favor—
pero apenas mueven la eficiencia, porque el MAE sube casi lo mismo. Lo
que los filtros seleccionan no son toques que sufran menos, sino toques
que se MUEVEN más. Es coherente con que el filtro que funciona sea el
de impulso.

### Sobre las 157 operaciones realmente ejecutadas

| | n | MFE | MAE | eficiencia | R real |
|---|---|---|---|---|---|
| BTC | 68 | 3.38 | 2.13 | 0.59 | +0.394 |
| ONDO | 89 | 3.05 | 2.56 | 0.55 | +0.264 |

La eficiencia sube de 0.519 a 0.59 en BTC entre «aceptado» y
«ejecutado». La diferencia la ponen el cooldown y el límite de capital,
que descartan toques adicionales sobre un nivel ya operado. Con n=68 no
es concluyente, pero apunta a que **desduplicar los toques de un mismo
nivel aporta**, no solo evita ruido.

### Cuánto se deja sobre la mesa

R máximo que la operación llegó a ofrecer, tomando el stop de 1.5 ATR
como unidad, frente al R que se captura:

| | h=12 | h=24 | h=48 |
|---|---|---|---|
| BTC ofrecido / capturado | 1.59 / 0.39 = **25%** | 2.26 / 0.39 = 17% | 3.05 / 0.39 = 13% |
| ONDO ofrecido / capturado | 1.41 / 0.26 = **19%** | 2.03 / 0.26 = 13% | 2.50 / 0.26 = 11% |

Distribución del recorrido ofrecido a 24 velas:

| llegó a ofrecer | BTC | ONDO |
|---|---|---|
| ≥ 0.5 R | 92.6% | 86.5% |
| ≥ 1.0 R | 75.0% | 68.5% |
| ≥ 1.5 R | 60.3% | 57.3% |
| ≥ 2.0 R | 45.6% | 41.6% |
| ≥ 3.0 R | 25.0% | 19.1% |

El MFE es un techo que incorpora información del futuro: nadie captura
el 100%. Pero **tres de cada cuatro entradas de BTC llegan a ofrecer
1 R y solo se captura 0.39**, así que el margen está en la salida, no
en la entrada.

### Sin ventaja direccional consistente

| | BTC | ONDO |
|---|---|---|
| long | ef 0.638, R +0.71 | ef 0.559, R +0.15 |
| short | ef 0.552, R +0.13 | ef 0.543, R +0.43 |

El signo se invierte entre activos: no hay una dirección que sea mejor,
hay una época alcista en BTC. Confirma que no procede filtrar por lado.

### El score predice la eficiencia en BTC, no en ONDO

| score | BTC ef / R | ONDO ef / R |
|---|---|---|
| 2 | 0.390 / -0.62 | 0.516 / +0.35 |
| 3 | 0.576 / +0.25 | 0.562 / +0.33 |
| 4 | 0.646 / +0.76 | 0.538 / +0.21 |
| 5 | 0.733 / +0.47 | 0.590 / +0.20 |

En BTC la eficiencia es monótona con el score, lo que confirma que el
calificador mide algo real de la ENTRADA y no solo del resultado. En
ONDO la eficiencia sube pero el R no. Es la misma disonancia que ya
obligó a usar el score como multiplicador de tamaño y no como filtro.

### Conclusiones para la memoria

1. **El sistema no gana por adivinar el giro.** La ventaja de entrada
   es de dos o tres puntos de eficiencia sobre el azar. Gana por la
   asimetría de la gestión: stop fijo contra objetivos escalonados.
2. **Los filtros seleccionan movimiento, no acierto.** Suben el MFE un
   22% y el MAE casi lo mismo. Su valor está en dar recorrido que
   cobrar, no en evitar el sufrimiento.
3. **El margen de mejora está en la salida**, donde se captura entre el
   13% y el 25% de lo que la operación llega a ofrecer.
4. **Advertencia metodológica**: los descartes de los bloques 1 a 5 se
   midieron sobre `pnl_r`, o sea CON esta gestión de salida. Una señal
   descartada puede haber fallado por la gestión y no por la señal. El
   caso claro es el «cierre lejos del nivel» del bloque 1, la variable
   más predictiva encontrada (+0.207 / +0.235) y descartada por el
   coste de esperar al cierre.

Script de la medición: `experiments/exp_calidad_entrada.py`.

## 19. Por qué no se abre una operación: la traza  [02/09/2026]

Pregunta recurrente al mirar los gráficos: «aquí el precio tocó el
nivel, ¿por qué no entró?». Hay **cinco** causas posibles, y conviene
tenerlas listadas porque tres de ellas no se ven en el gráfico.

1. **El lado no es el que parece.** El lado lo fija el CIERRE de la
   vela anterior, no lo que hace el precio dentro de la vela. Si el
   precio cruza el nivel dentro de una vela y cierra al otro lado, la
   orden que queda puesta para la vela siguiente es la contraria. Es
   consecuencia directa de la regla anti-lookahead: el lado solo se
   puede conocer con velas cerradas.
2. **Squeeze activo** (Bollinger dentro de Keltner).
3. **ADX ≥ 35**.
4. **Impulso < 1.0 ATR** en 6 velas.
5. **Cooldown de 6 velas sobre el nivel**, o **capital agotado** por
   posiciones abiertas, que sin apalancamiento es un tope real.

Ejemplos verificados sobre BTC:

| fecha | lo que se esperaba | causa real |
|---|---|---|
| 11/04/2025 | un long | el único nivel tocado fue el POC 83.687 y el precio llegó desde abajo: la orden era SHORT. El long sí existió, el día 10 en el VAL (+2.56 R) |
| 01/05/2025 | un short | orden short en el POC puesta y tumbada por el **squeeze**. Al liberarse ocho horas después el precio ya cerraba por encima del POC, y la orden cambió de lado |
| 07/05/2025 | un short | el short en el POC se colocaba el día 6 a las 16:00 y lo tumbó el **impulso 0.65**. El día 7 ya pasaba los filtros, pero el nivel estaba en **cooldown** por el long que dio +2.14 R |

**Cabo suelto detectado**: el cooldown es por NIVEL, no por nivel y
dirección. El 7 de mayo impidió un short después de un long ganador en
sentido contrario, que es una operación distinta y no una repetición.
Separar el cooldown por dirección es un cambio pequeño y medible que
queda pendiente.

## 20. Filtro aprendido: la IA no bate a las reglas  [02/09/2026]

Requisito del máster: incorporar una técnica de IA **justificada y
comparada contra la versión inicial**. Se planteó como clasificación
binaria supervisada —dado el estado del mercado al tocar un nivel,
¿ganará la operación?— con 19 variables y dos modelos pequeños:
regresión logística (L2, C=0.1) y `HistGradientBoostingClassifier`
(profundidad 3, 120 iteraciones, `min_samples_leaf=25`).

Diseño contra el sobreajuste: walk-forward (60% antiguo → 40%
reciente), cruzado entre activos, umbral fijado en el ENTRENAMIENTO al
percentil que iguala la selectividad de las reglas, y dos referencias
siempre presentes (sin filtrar, y reglas).

### Resultado: pierde en las cuatro validaciones

| validación | sin filtrar | **reglas** | logística | boosting |
|---|---|---|---|---|
| walk-forward BTC | +0.001 | **+0.785** | -0.385 | -0.018 |
| walk-forward ONDO | -0.120 | **+0.196** | -0.169 | -0.016 |
| BTC → ONDO | +0.037 | **+0.400** | -0.020 | +0.072 |
| ONDO → BTC | -0.057 | **+0.508** | -0.101 | +0.156 |

Sin una sola excepción. Con 73 y 89 operaciones de entrenamiento y 19
variables, la limitación no es de modelado sino de MUESTRA. Y el modelo
lineal va peor que el no lineal, y ambos peor que tres reglas: cuando la
referencia simple gana a las dos alternativas complejas, lo que falta es
señal por operación, no capacidad.

### Lo que sí aporta: confirma el hallazgo principal

Coeficientes de la logística sobre los dos activos juntos, por
magnitud: **impulso +0.305** (el mayor de los 19), confluencia -0.206,
di_orientado +0.176, atr_relativo +0.167.

El modelo **redescubre por su cuenta que el impulso de aproximación es
la variable más informativa**, que es exactamente lo que había
encontrado la medición univariante. Dos métodos independientes
señalando la misma variable es una validación cruzada del resultado
central del proyecto. El signo negativo de `confluencia` también
reproduce un hallazgo previo.

### Condiciones para reabrir la vía

1. Más datos: 8-10 pares y 4-5 años → varios miles de operaciones.
2. Menos variables: 5-6 elegidas por criterio económico, no estadístico.
3. Etiqueta de regresión (R esperado) en vez de binaria.
4. Uso como calificador de TAMAÑO, no como filtro: un error del modelo
   dimensiona mal la operación en vez de cancelarla.

Reproducible: `experiments/exp_ia_filtro.py`.





