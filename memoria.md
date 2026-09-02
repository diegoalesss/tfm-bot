# Bot de trading algorítmico basado en perfil de volumen de rango fijo

**Trabajo Fin de Máster — Máster en IA aplicada a mercados financieros**

Autor: Diego Alessandro Muñoz Agreda
Entrega: septiembre de 2026

---

## Índice

1. [Planteamiento y objetivos](#1-planteamiento-y-objetivos)
2. [Arquitectura y calidad del código](#2-arquitectura-y-calidad-del-código)
3. [Datos: ingesta y validación](#3-datos-ingesta-y-validación)
4. [Filtro 1 — Detección de rangos laterales](#4-filtro-1--detección-de-rangos-laterales)
5. [Filtro 2 — Perfil de volumen de rango fijo](#5-filtro-2--perfil-de-volumen-de-rango-fijo)
6. [Prevención del sesgo de anticipación](#6-prevención-del-sesgo-de-anticipación)
7. [Pruebas y validación](#7-pruebas-y-validación)
8. [Alternativas evaluadas y descartadas](#8-alternativas-evaluadas-y-descartadas)
9. [Viabilidad operativa](#9-viabilidad-operativa)
10. [Componente de inteligencia artificial](#10-componente-de-inteligencia-artificial)
11. [Generación de señales y gestión del riesgo](#11-generación-de-señales-y-gestión-del-riesgo)
12. [Backtest y resultados](#12-backtest-y-resultados)
13. [Conclusiones](#13-conclusiones)

---

## 1. Planteamiento y objetivos

### 1.1 El problema

El perfil de volumen de rango fijo (*Fixed Range Volume Profile*, FRVP)
es una herramienta habitual en el análisis técnico discrecional: se
traza sobre un tramo del gráfico y devuelve los precios donde se
concentró la negociación. De él salen tres niveles operativos —POC,
VAH y VAL— que actúan como zonas de reacción cuando el precio vuelve
a visitarlos.

Su punto débil es que **el resultado depende por completo de dónde se
ancle el perfil**, y ese anclaje lo decide el operador a ojo. Dos
analistas trazando sobre el mismo gráfico obtienen niveles distintos,
lo que impide contrastar la estrategia con rigor: no se puede
retroceder sobre el histórico ni medir si funciona.

Automatizar el FRVP no es difícil. Lo difícil es automatizar **la
decisión de dónde anclarlo**, que es donde reside el criterio del
analista.

### 1.2 Objetivo

Construir un sistema que decida ese anclaje mediante reglas
explícitas, reproducibles y auditables, de modo que el trazado deje de
depender del juicio del operador y la estrategia pueda validarse.

Objetivos concretos de esta fase:

1. Detectar automáticamente los rangos laterales sobre los que se
   ancla el perfil, con criterios que un tercero pueda replicar
2. Garantizar que ningún cálculo usa información futura, requisito
   ineludible para que un backtest posterior tenga validez
3. Calcular el FRVP y sus niveles sobre los rangos detectados
4. Validar la detección contra el criterio manual del autor

### 1.3 Metodología de validación

Un detector de rangos no tiene una respuesta objetivamente correcta:
qué es "un lateral" es una interpretación. Se adopta como criterio de
verdad **el trazado manual del autor sobre TradingView**, que es la
operativa que se pretende sistematizar.

De ese trazado importan **las fechas de inicio y fin**, no los niveles
exactos de techo y suelo. El motivo es funcional: sobre esas fechas se
ancla el perfil, y son ellas las que determinan los niveles
operativos. Los bordes de precio del rectángulo no se usan para
operar.

La métrica es el **IoU temporal** (intersección sobre unión de los
intervalos de fechas), que penaliza tanto quedarse corto como
excederse.

### 1.4 Alcance

Fase 1 (ingesta y validación de datos), fase 2 (Filtro 1 y Filtro 2) y
visualización, completadas. Los capítulos 10 a 13 corresponden a fases
posteriores y se dejan sin contenido.

---

## 2. Arquitectura y calidad del código

### 2.1 Estructura

```
core/       lógica de estrategia, idéntica en backtest y en vivo
              range_detector.py   Filtro 1: rangos laterales
              frvp.py             Filtro 2: perfil de volumen
data/       ingesta y validación
              loader.py           descarga vía CCXT, caché en parquet
              validator.py        auditoría de calidad de las velas
              raw/                caché, no versionada
execution/  capa intercambiable: backtest, paper, live (pendiente)
notebooks/  exploración y gráficos
tests/      pruebas
config.yaml parámetros de la estrategia
```

La separación entre `core/` y `execution/` es deliberada: la lógica de
estrategia debe ser idéntica en backtest y en producción. Si difieren,
los resultados del backtest no dicen nada sobre el comportamiento real.

| Módulo | Líneas | Sin comentarios | Funciones |
|---|---|---|---|
| `core/range_detector.py` | 1738 | 1373 | 18 |
| `core/frvp.py` | 264 | 208 | 4 |
| `data/loader.py` | 398 | 315 | 7 |
| `data/validator.py` | 236 | 197 | 5 |
| `tests/test_range_detector.py` | 767 | 556 | 31 |

La proporción de documentación es alta a propósito: cada decisión de
diseño lleva anotado el porqué y, cuando se probó una alternativa, la
medición que la descartó. Ese registro tiene el mismo valor que el
código, porque evita repetir intentos que ya fracasaron.

### 2.2 Convenciones

- Python 3.14, PEP 8, anotaciones de tipo en todas las funciones
- Docstrings estilo NumPy en las funciones públicas
- Cálculo vectorizado con pandas y NumPy. Los bucles sobre filas están
  prohibidos salvo justificación explícita en comentario. Hay dos
  excepciones documentadas, ambas por semántica secuencial genuina: el
  barrido del detector (el rectángulo se congela en una vela y
  condiciona desde dónde se reanuda la búsqueda) y la supresión de
  no-máximos de la selección (cada elección altera el conjunto
  disponible)
- Sin números mágicos: todos los parámetros viven en `config.yaml`,
  cada uno con un comentario que explica de dónde sale su valor

### 2.3 Manejo de errores

En un bot de trading, fallar ruidosamente es preferible a operar con
datos corruptos. El criterio aplicado:

- Prohibido capturar excepciones y continuar en silencio
- Toda excepción se registra y, si compromete la integridad de los
  datos, detiene la ejecución
- La configuración se valida al entrar (`_validar_config`): un
  `config.yaml` desactualizado aborta con `KeyError` o `ValueError`
  indicando qué falta, en lugar de operar con parámetros a medias
- Los errores de red se reintentan con espera creciente; los errores
  del exchange (símbolo inválido, parámetros incorrectos) no son
  transitorios y se propagan de inmediato

---

## 3. Datos: ingesta y validación

### 3.1 Origen

El precio procede **siempre de Kraken Futures**, vía CCXT. Es donde se
ejecutarán las órdenes y donde saltarán los stops: mezclar el precio
de un exchange con la ejecución en otro introduciría una discrepancia
imposible de cuantificar.

El volumen es parametrizable (`volume_source`): Kraken en esta fase,
agregado multi-exchange en una fase posterior. El diseño permite
comparar ambas versiones con todo lo demás idéntico, lo que convierte
la elección en un test de robustez en lugar de en un supuesto.

Histórico: 2 años. Timeframe de decisión: 4h. Para el perfil se
descargan además 15m y 1h.

### 3.2 Auditoría de calidad

`data/validator.py` comprueba tres familias de defectos antes de que
los datos se usen: huecos (timestamps ausentes en la serie esperada),
duplicados y valores imposibles (`high < low`, volumen negativo, OHLC
nulo).

Resultado sobre el conjunto completo:

| Símbolo | TF | Velas | Esperadas | Cobertura | Huecos | Duplicados | Imposibles |
|---|---|---|---|---|---|---|---|
| ONDO/USD:USD | 15m | 70 079 | 70 079 | 100,00 % | 0 | 0 | 0 |
| ONDO/USD:USD | 1h | 17 519 | 17 519 | 100,00 % | 0 | 0 | 0 |
| ONDO/USD:USD | 4h | 4 379 | 4 379 | 100,00 % | 0 | 0 | 0 |
| BTC/USD:USD | 15m | 70 079 | 70 079 | 100,00 % | 0 | 0 | 0 |
| BTC/USD:USD | 1h | 17 519 | 17 519 | 100,00 % | 0 | 0 | 0 |
| BTC/USD:USD | 4h | 4 379 | 4 379 | 100,00 % | 0 | 0 | 0 |

Cobertura íntegra: 179 954 velas sin un solo hueco, duplicado ni valor
imposible. Rango temporal: 31/08/2024 a 31/08/2026.

El resultado limpio no invalida la auditoría. Los mercados de cripto
operan de forma continua, sin sesiones ni festivos, lo que elimina la
principal fuente de huecos en datos de renta variable; y la caché se
construyó de una sola vez sobre un exchange estable. La comprobación
sigue siendo necesaria porque el coste de operar sobre datos corruptos
es asimétricamente alto frente al de verificarlos.

### 3.3 Exclusión de la vela en curso

El descargador descarta la última vela si su cierre es posterior al
instante actual. Una vela abierta cambia de valor después de leerse:
guardarla contaminaría la caché con datos que no existían en el
momento que dicen representar. Es la primera de las salvaguardas
contra el sesgo de anticipación.

---

## 4. Filtro 1 — Detección de rangos laterales

### 4.1 Metodología

Caja de Darvas con techo y suelo en el **pivote confirmado más extremo
de cada lado**, validada estadísticamente sobre ventanas móviles de
varios tamaños.

El principio de diseño que ordena todo el filtro: **el rectángulo se
deriva de la estructura del precio, con independencia del test que
después lo valida**. Si el techo y el suelo se calcularan a partir de
los mismos cierres que luego se cuentan —percentiles, media ± k·σ,
área de valor de cierres— el criterio de contención se cumpliría por
construcción. Un criterio que no puede fallar no filtra nada.

### 4.2 Construcción del rectángulo

Los pivotes se calculan **sobre cierres, no sobre mechas**, siguiendo
la metodología de Darvas: reduce las señales falsas por picos
intradía. Un *swing high* en la vela *i* exige que `close[i]` supere
estrictamente los R cierres anteriores y los R posteriores (R = 3).

Un pivote **no existe hasta que está confirmado**: al evaluar la vela
*t* solo se admiten pivotes con `i + R <= t`. Las últimas R velas de
la ventana nunca aportan pivote. Esta restricción es obligatoria para
evitar el sesgo de anticipación, y es la razón de que el cálculo mire
hacia adelante pero el uso no.

El nivel se busca **desde el pivote más extremo hacia dentro** y se
acepta el primero que reúna dos toques dentro de la tolerancia
(0,5·ATR). Si el extremo no está confirmado, el rango no desaparece:
se traza en el nivel confirmado que sí exista, que es lo que hace un
analista a mano.

**Los toques deben estar separados en el tiempo** (mínimo, el 10 % de
la ventana). Dos pivotes de velas contiguas pertenecen al mismo
impulso: son un solo test del nivel, no dos.

*Evidencia*: sin esta regla, el techo del lateral de BTC de febrero a
abril de 2026 salía en 78 798, confirmado por dos *swing highs* del 2
y el 3 de febrero —la cola de la caída anterior, no el rango—. El
rectángulo medía 16 000 de alto frente a los 7 500 reales, y el 88,8 %
de los cierres vivían en el 80 % central: el precio apenas visitaba
los bordes, señal inequívoca de caja inflada. Con la separación el
techo baja a 74 884 y el ajuste al trazado manual sube de 0,72 a 0,93.

### 4.3 Ventanas múltiples

La detección corre **solo sobre velas de 4h**, con cinco ventanas de
distinto tamaño, para captar rangos anidados de distinta escala sin
elegir una a priori:

| N (velas de 4h) | Duración | Tipo | Tope de altura |
|---|---|---|---|
| 40 | ~1 semana | secundario | 6,5 ATR |
| 60 | ~10 días | secundario | 8,0 ATR |
| 150 | ~1 mes | principal | 12,6 ATR |
| 250 | ~6 semanas | principal | 16,3 ATR |
| 400 | ~2,5 meses | principal | 20,7 ATR |

El tipo describe la **escala**, no la calidad, y **ambos son
operables**. Los principales son los de mayor peso: cuanto más tiempo
pasa el precio construyendo el rango, más volumen acumula el perfil y
más fiables son sus niveles.

El tope de altura escala con la **raíz** del tamaño de ventana, que es
como crece el recorrido de un paseo aleatorio:

```
tope = 8,0 · √(N / 60)
```

### 4.4 Criterios de declaración

Sobre la ventana de N velas que termina en *t*, se declara rango si se
cumplen a la vez:

| # | Criterio | Umbral |
|---|---|---|
| 1 | R² de la regresión de cierres | < 0,3 |
| 2 | Existe rectángulo | ≥ 2 toques separados por lado |
| 3 | Altura en ATR | ≤ tope de la ventana |
| 4 | Altura en precio (salvaguarda) | ≤ 120 % del suelo |
| 5 | Deriva de la regresión | < 0,5 · altura del rectángulo |
| 6 | Contención de cierres | ≥ 85 % |
| 7 | Sin racha de ruptura interna | < 5 cierres seguidos fuera |
| 8 | Oscilación | ≥ 7 cruces del punto medio |
| 9 | Recorridos según altura | ≥ 1 + máx(0, altura_rel − 0,30)/0,10 |

Y, tras congelar el rectángulo y fijar los extremos, la contención del
**tramo completo** debe alcanzar también el 85 %.

Dos criterios merecen explicación.

**El criterio de pendiente (5)** se ancla a la altura del rectángulo,
no a un porcentaje fijo del precio: la escala la pone la estructura
detectada. Si la deriva igualara la altura completa, el precio habría
recorrido el rectángulo de suelo a techo a lo largo de la ventana, que
es un canal inclinado y no un rango. Limitándola a la mitad, la
componente tendencial se queda como mucho con medio rectángulo.

**El criterio de recorridos (9)** distingue un rango de un
*contenedor*: un rectángulo tan alto que la contención se cumple sola.
Ni la altura ni el número de recorridos los separan por sí solos
—medido, un contenedor de ONDO y un lateral bueno de BTC tienen ambos
2 recorridos, y sus alturas en ATR se solapan (12,4 y 11,5)—. Lo que
los separa es la combinación: una caja del 118 % recorrida dos veces
es un contenedor; una del 19 % recorrida dos veces es un lateral
normal.

### 4.5 Congelación, extensión y recortes

Al declararse el rango, **el rectángulo se congela**. A partir de ahí
solo puede terminar por acumular 5 cierres consecutivos fuera. Los
motivos son dos: un rectángulo recalculado en cada vela se ensancharía
para absorber los cierres que se salen, y la regla de ruptura no
llegaría a dispararse nunca; y un rectángulo móvil hace el backtest
irreproducible.

Una vela que sale y vuelve es un barrido de stops, no una ruptura: por
eso se exigen cierres **consecutivos**. La racha de ruptura no
pertenece a ningún rango.

Sobre esa base se aplican cuatro ajustes de los extremos, todos
causalmente seguros porque operan sobre velas ya cerradas:

- **Extensión hacia atrás**. `inicio` sale de la mecánica de la
  ventana (`t − N + 1`), así que un lateral más largo que N empieza
  por fuerza más tarde de lo que le toca. Se retrocede con la misma
  regla que cierra el rango. Efecto medido: el ajuste medio sube de
  0,858 a 0,875.
- **Recorte de bordes**. Ambos extremos se ajustan a la primera y la
  última vela que cierran dentro del rectángulo. Antes, 6 de 9 rangos
  arrastraban 4 velas sobrantes por la izquierda; ahora, ninguna.
- **Recorte de la cola**. La regla de los 5 cierres exige una racha
  limpia; si el precio rompe entrando y saliendo nunca la junta, y el
  rango sobrevive dentro de su propia rotura. Se exige a las últimas
  velas la misma contención que a la ventana de declaración. Medido:
  el lateral de ONDO de diciembre de 2024 llegaba al día 29 cuando el
  precio rompía desde el 27; con el recorte termina el 26.
- **Recorte de la cabeza**. Simétrico del anterior en la contención,
  con una exigencia añadida: la cabeza tampoco puede ser tendencial
  (R² por debajo del mismo umbral general).

Este último merece detalle porque ilustra un límite de la contención
como criterio. El lateral de BTC de noviembre de 2024 arrancaba el
día 11, diez días antes del trazado manual, y la contención de su
cabeza era 0,95: por contención no había nada que recortar, porque el
precio ya estaba **dentro** del rectángulo. Lo que ocurría es que ese
tramo era la cola del rally de 76 000 a 99 000: el precio estaba
dentro de la caja pero todavía subiendo con fuerza, que no es
lateralidad. La contención no puede verlo —el precio está dentro— y
el R² sí.

El R² local resulta además no ser monótono (0,31 al inicio, 0,90 diez
velas después, 0,05 en el índice 50), de modo que la regla debe ser
avanzar hasta la primera cabeza plana, no hasta que el R² empiece a
bajar.

Efecto medido: el ajuste medio sube de 0,875 a **0,900**, y el caso
peor del conjunto de referencia pasa de 0,54 a 0,71.

La longitud del borde examinado es el 10 % de la ventana **con un
mínimo de 20 velas**. El mínimo no es cosmético: con N=40 la fracción
daría 4 velas, y el R² sobre 4 puntos es alto casi siempre, de modo
que el criterio rechazaba cualquier cosa. Por debajo de esa muestra
el R² deja de ser estadísticamente significativo.

### 4.6 Selección de rangos operables

El detector devuelve la misma consolidación vista por varias ventanas
a la vez: 116 rangos en BTC sobre dos años. Como sobre cada uno se
traza un perfil con tres niveles proyectados hacia la derecha, serían
más de 300 líneas y el gráfico dejaría de poder operarse.

`seleccionar_rangos` aplica supresión de no-máximos. Dos decisiones la
hacen funcionar:

- **Se ordena por calidad × duración, no por calidad sola.** La nota
  de calidad premia los rectángulos estrechos, de modo que ordenar
  solo por ella permitía que un rango pequeño y limpio eliminara al
  grande que delimita la estructura. Medido: el ajuste medio pasa de
  0,72 a 0,86.
- **La supresión se hace dentro de cada tipo.** Un rango secundario
  contenido en uno principal no es redundante: es la estructura
  anidada que se quiere operar.

Resultado:

| Símbolo | Detectados | Seleccionados | Principales | Secundarios |
|---|---|---|---|---|
| BTC/USD:USD | 116 | 20 | 9 | 11 |
| ONDO/USD:USD | 101 | 27 | 10 | 17 |

### 4.7 Nota de calidad

Cada rango lleva una nota de 0 a 1 que mide lo lateral y limpio que
es, para poder priorizar. No decide si algo es un rango —todos los
devueltos ya pasaron los criterios— sino cuál es mejor para operar.
Combina a partes iguales: **estrechez** (cuánto por debajo del tope de
altura se queda), **planitud** (cuánto por debajo del R² máximo) y
**limpieza** (cuánto por encima del mínimo de contención).

Es una nota razonada, no calibrada contra resultados de operativa.

### 4.8 Poder de rechazo de cada criterio

Sobre 42 000 evaluaciones de ventana (ambos símbolos, las cinco
ventanas):

| Criterio bloqueante | Ventanas | % |
|---|---|---|
| R² | 24 949 | 59,40 % |
| **(pasa)** | **5 388** | **12,83 %** |
| Racha interna | 3 724 | 8,87 % |
| Pendiente | 2 943 | 7,01 % |
| Oscilación | 2 344 | 5,58 % |
| Contención | 1 031 | 2,45 % |
| Altura en ATR | 670 | 1,60 % |
| Sin rectángulo | 502 | 1,20 % |
| Recorridos | 440 | 1,05 % |
| Altura en % (salvaguarda) | 9 | 0,02 % |

El R² concentra el 59 % de los rechazos: descarta las ventanas
claramente tendenciales antes de construir ningún rectángulo, que es
el cálculo caro. Los criterios estructurales actúan sobre el 40 %
restante. La salvaguarda de altura en porcentaje bloquea solo 9
ventanas, consistente con su papel: no es un filtro activo sino una
red contra rectángulos degenerados que el tope en ATR no detecta.

---

## 5. Filtro 2 — Perfil de volumen de rango fijo

### 5.1 Cálculo

Implementado en `core/frvp.py`. Parámetros: 1000 bins y Value Area del
70 %, replicando la configuración que el autor usa en TradingView, lo
que garantiza coherencia entre el análisis manual y el automatizado.

El volumen de cada vela se reparte **uniformemente entre su mínimo y
su máximo**, en proporción al solape con cada bin. Es la aproximación
estándar cuando no se dispone del volumen por precio real, que
exigiría datos de tick: dentro de una vela no se sabe a qué precios se
negoció, y repartir uniformemente no sesga hacia ningún extremo.

Para acotar el error de esa aproximación, la granularidad de las velas
se adapta a la duración del rango: 15m si dura menos de 60 velas de
4h, 1h hasta 200, y 4h por encima. Cuanto más corto el rango, más fina
la vela.

La Value Area se expande desde el POC por el **método CME**: se añade
repetidamente el par de bins con más volumen hasta cubrir el 70 % del
total. Es el método de TradingView, de modo que los niveles coinciden
con los del análisis manual. El POC se desempata por cercanía al
centro del rango.

### 5.2 Anclaje

El perfil se ancla al **tramo completo del rango** que entrega el
Filtro 1. No hace falta buscar una vela de anclaje ni excluir a mano
la vela de ruptura, porque el Filtro 1 ya entrega los límites
depurados: `inicio` recortado a la primera vela que cierra dentro del
rectángulo, y `fin` con las 5 velas de ruptura excluidas y la cola
recortada.

Esto resuelve el problema planteado en el capítulo 1: **el anclaje
deja de ser una decisión discrecional y pasa a ser consecuencia de una
cadena de reglas verificables**.

### 5.3 Verificación

47 perfiles calculados sobre los rangos seleccionados de ambos
símbolos. El POC cae dentro del rectángulo del rango en **47 de 47**.
Tiempo de cálculo: 0,2 s para los 47.

### 5.4 Marca temporal

El perfil de un rango **no existe antes de su `confirmado_en`**,
porque su `fin` no se conoce hasta entonces. Operar sus niveles antes
de esa vela sería sesgo de anticipación.

Esto encaja con la operativa prevista —entrar cuando el precio vuelve
a testear zonas de rangos previos, ya rotos— pero el backtest deberá
imponerlo de forma explícita.

---

## 6. Prevención del sesgo de anticipación

El sesgo de anticipación (*lookahead bias*) es el error que invalida
un backtest sin dejar rastro: el sistema parece rentable porque usó
información que no existía en el momento de decidir. En un trabajo
cuyo objetivo es hacer contrastable una estrategia discrecional, es la
amenaza principal.

### 6.1 Puntos de fuga y cómo se cierran

| Punto | Riesgo | Medida |
|---|---|---|
| Descarga | La última vela puede estar abierta | Se descarta si su cierre es futuro |
| Regresión, R², ATR | Ventanas centradas | `rolling` hacia atrás; nunca `center=True` |
| Pivotes | Su definición mira R velas adelante | Se admiten solo con `i + R <= t` |
| Contención | — | Solo cierres de la ventana, todos cerrados |
| Fin del rango | Se busca hacia adelante | Se etiqueta con su instante de conocimiento |

La detección de pivotes es el caso más delicado y merece detalle: el
cálculo **sí** mira hacia adelante, porque un *swing high* no se
distingue de un máximo local cualquiera hasta que pasan R velas. La
fuga no se evita en el cálculo sino **en el uso**: al evaluar la
ventana que termina en *t* solo se admiten pivotes ya confirmados. Un
pivote no existe hasta que está confirmado.

### 6.2 Marcas temporales

Cada rango lleva dos instantes de conocimiento que el consumidor debe
respetar:

- **`declarado_en`** — vela en la que el rango pasa a ser conocido.
  Nunca coincide con `inicio`, que está N−1 velas antes: el rango
  existió antes de que se pudiera saber que existía.
- **`confirmado_en`** — vela en la que se confirma la ruptura que lo
  cierra, 5 velas después de `fin`.

Usar un rango antes de `declarado_en`, o su `fin` antes de
`confirmado_en`, es sesgo de anticipación.

### 6.3 Test de truncado

La garantía no descansa en la revisión del código sino en una prueba
automática. `test_sin_lookahead_sobre_datos_reales` **corta el
histórico al 50 %, 70 % y 90 %** y exige que todo rango ya confirmado
antes del corte salga **idéntico** al que sale con la serie completa:
mismas fechas de inicio y fin, mismos techo y suelo, misma
confirmación.

Si algún cálculo usara velas futuras, un rango cerrado antes del corte
saldría distinto al recortar la serie por detrás.

**Resultado: 200 rangos comparados entre los tres cortes, sin una sola
diferencia.**

El emparejamiento se hace por `(ventana, declarado_en)` y no por
posición, porque con varias ventanas el orden del resultado combinado
no tiene por qué coincidir entre la serie completa y la truncada. La
vela de declaración identifica un rango de forma única dentro de su
ventana.

Esta prueba es la red de seguridad del proyecto: cualquier cambio
futuro en el detector debe superarla, y **no debe relajarse para que
pase**. Si falla, el cambio mira al futuro.

---

## 7. Pruebas y validación

### 7.1 Suite de pruebas

26 pruebas, todas en verde. Se escriben como funciones `test_*` con
`assert`, recogibles por `pytest` pero ejecutables directamente con el
intérprete del entorno virtual, en línea con el resto de scripts del
proyecto.

Cubren cuatro niveles:

1. **Unitarias sobre primitivas**: detección de pivotes (zigzag
   conocido, mesetas, falta de contexto), elección del nivel por
   extremo, toques del mismo impulso, búsqueda de rachas
2. **De criterio, con series sintéticas**: cada criterio se aísla
   variando una sola magnitud. Por ejemplo, el criterio de pendiente
   se verifica con dos series de **idéntica deriva y distinta
   amplitud**: la ancha pasa y la estrecha no, cosa que sería
   imposible si el umbral se anclara al precio en lugar de a la altura
   del rectángulo
3. **De invariantes sobre datos reales**: techo > suelo, contención
   por encima del mínimo, `declarado_en` posterior a `inicio`,
   `confirmado_en` posterior a `fin`, coherencia del agrupamiento de
   solapes
4. **Anti-lookahead**: el test de truncado del apartado 6.3

### 7.2 Ajuste al criterio manual

Contraste contra los 7 rangos que el autor traza a mano sobre BTC en
4h desde noviembre de 2024:

| Ref | Trazado manual | Detectado | N | IoU | Δ inicio | Δ fin |
|---|---|---|---|---|---|---|
| 1 | 21/11/24 – 24/02/25 | 12/11/24 – 26/02/25 | 400 | 0,90 | −8,3 d | +2,5 d |
| 2 | 25/02/25 – 22/04/25 | 25/02/25 – 06/04/25 | 250 | 0,71 | **+0,8 d** | −15,3 d |
| 3 | 09/05/25 – 09/07/25 | 09/05/25 – 10/07/25 | 150 | **0,97** | +0,3 d | +1,5 d |
| 4 | 17/11/25 – 28/01/26 | 18/11/25 – 31/01/26 | 400 | 0,93 | +1,7 d | +3,3 d |
| 5 | 05/02/26 – 13/04/26 | 03/02/26 – 17/04/26 | 400 | 0,93 | −1,3 d | +4,0 d |
| 6 | 14/04/26 – 27/05/26 | 15/04/26 – 01/06/26 | 250 | 0,86 | +1,5 d | +5,0 d |
| 7 | 04/06/26 – 19/08/26 | 03/06/26 – 19/08/26 | 250 | **0,99** | −0,2 d | +0,3 d |

**IoU temporal medio: 0,900. Los 7 detectados.** Seis de los siete
inicios caen a menos de 2 días de la fecha trazada a mano.

La asimetría del error es informativa: los inicios están bien
ajustados (mediana de 1,0 días de desviación) mientras que los
finales tienden a llegar tarde (+2,5, +3,3, +4,0, +5,0 días). Es
consistente con el mecanismo: la regla de los 5 cierres consecutivos
necesita, por construcción, que la ruptura se consume antes de darla
por buena. Un retraso de dos a cinco días en el cierre de un rango
que dura meses no compromete el anclaje del perfil.

**Nota metodológica relevante**: estos 7 rangos se facilitaron
*después* de fijar los parámetros, que se habían calibrado contra un
conjunto distinto de 9 cajas. Funcionan por tanto como validación
fuera de muestra y no como ajuste a medida. Es una distinción
importante: calibrar los parámetros contra las mismas cajas con las
que después se mide el acierto produciría una cifra sin valor.

### 7.3 Limitación conocida

El caso 2 obtiene 0,71, el más bajo del conjunto. Su inicio es ahora
exacto (+0,8 días), de modo que **el error residual está por completo
en el cierre**: el sistema lo termina el 6 de abril y el trazado
manual el 22, quince días después.

Ese caso resistió varias iteraciones en 0,54 y solo cedió al recorte
de cabeza (apartado 4.5), que corrigió su inicio de +10 días a +0,8.
El cierre sigue abierto.

Detrás hay una causa que se manifiesta también en dos casos de ONDO:
**la nota de calidad penaliza las cajas altas, y a veces la caja alta
es la correcta**. Cuando dos rectángulos compiten por la misma zona,
la selección prefiere el estrecho y el criterio manual prefiere el que
respeta la estructura de precio.

Se probó dar más peso a la duración en la relevancia: arregla este
caso (0,54 → 0,69 en su momento) pero degrada los casos 5 y 6 (de 0,93
a 0,59 y de 0,89 a 0,45). El intercambio sale a pérdida y se registró
como alternativa descartada.

---

## 8. Alternativas evaluadas y descartadas

El apartado recoge las decisiones de diseño que se probaron y se
abandonaron, con la evidencia que las tumbó. Documentarlas tiene dos
funciones: justificar por qué el diseño final es el que es, y evitar
que un trabajo futuro repita intentos ya fallidos.

### 8.1 Contención por rango absoluto

**Planteamiento**: es rango si `(máx − mín) / precio_medio < 15 %`.

**Descartado** por dos motivos. Era el cuello de botella del filtro, y
no es invariante de escala temporal: el mismo umbral aplicado a
ventanas de duración distinta mide cosas distintas. Medido sobre ONDO,
11 de 31 rangos superaban el 15 % siendo laterales legítimos.

Sustituido por contención por conteo de cierres, que sí es
adimensional.

### 8.2 Niveles derivados de los cierres

**Planteamiento**: derivar techo y suelo de percentiles de los
cierres, de la media ± k·σ, o de un área de valor de cierres.

**Descartado por razonamiento, antes de implementarlo**: cualquiera de
esas opciones deriva el rectángulo de los mismos cierres que después
se cuentan para validarlo, de modo que el criterio de contención se
cumpliría por construcción y no filtraría nada. Un criterio que no
puede fallar no es un criterio.

Este razonamiento determinó el principio de diseño de todo el filtro:
el rectángulo se deriva de la estructura, la validación de los
cierres, y ambas cosas deben ser independientes.

### 8.3 Nivel por moda frente a nivel por extremo

**Planteamiento**: situar el techo donde se agrupan los *swing highs*
(banda deslizante con más toques, nivel en la mediana), en lugar de en
el pivote más extremo.

**Descartado con medición**: sobre el lateral de ONDO de febrero a
mayo de 2026, el nivel modal daba techo 0,2699 con 8 toques frente a
un extremo de 0,2943 con 2. El rectángulo quedaba cortado muy por
debajo de la estructura real, con una contención del 0,85 frente al
1,00 que da el extremo.

El motivo es estructural: **el interior de un rango se visita más que
su borde, por definición**, así que la moda tiende sistemáticamente
hacia dentro. Y para la regla de ruptura el extremo es lo correcto:
superar el máximo confirmado previo significa algo, superar la moda no.

### 8.4 Arquitectura sin ventana rodante

**Planteamiento**: prescindir de la ventana móvil y usar el enfoque
secuencial habitual —detectar pivotes, confirmar niveles por conteo de
toques, abrir la caja y extenderla hacia la derecha hasta la ruptura—.

**Descartado con dos mediciones**:

1. **La extensión temporal ya funciona.** El lateral de ONDO de
   febrero a mayo dura 73 velas con N=60: la regla de los 5 cierres lo
   extiende más allá de la ventana sin problema. El límite que se le
   atribuía a N no existía.
2. **La ventana hace un trabajo adicional que no se había
   identificado**: acota qué pivotes pueden formar el nivel. Sin ella
   los pivotes no caducan, y al simular el enfoque los máximos de
   enero de 2026 (0,3459) fijaban el techo del lateral de marzo.
   Habría que reponer ese límite con otra regla, es decir,
   reintroducir un lookback por la puerta de atrás.

Implicaba además reescribir el 60 % del módulo y degradaba la
separación entre escalas. El problema que motivó la evaluación —el
techo cortado— se resolvió cambiando el nivel de moda a extremo, que
es ortogonal a la arquitectura.

### 8.5 Vela de absorción

**Planteamiento**: validar el rango exigiendo una vela de absorción
(volumen > 200 % de la media y mecha larga) en su formación.

**Descartado**: produce falsos positivos (mechas climáticas sin rango
posterior) y falsos negativos (rangos válidos sin absorción visible).
No aporta poder predictivo.

### 8.6 Compresión de volatilidad

**Planteamiento**: detectar el rango por contracción del ATR o del
ancho de las bandas, criterio habitual en la literatura de
*volatility squeeze*.

**Descartado**: identifica consolidaciones estrechas y previas a una
expansión, que no es lo que busca esta estrategia. El objetivo son
rangos amplios y duraderos donde se acumule volumen suficiente para
que el perfil tenga significado estadístico; un rango comprimido tiene
poco volumen repartido en poco precio y su POC no discrimina.

### 8.7 Toques del nivel como medida de oscilación

**Planteamiento**: en lugar de contar cruces del punto medio, usar el
número de toques que confirman el nivel, que ya se calcula.

**Descartado con medición**: subir el mínimo de toques de 2 a 3 reduce
la detección en 4h de 48 a 6 rangos —un 87 % menos—, sigue dejando
pasar una formación en V y elimina el lateral de febrero a mayo de
ONDO.

La idea tenía sentido cuando el nivel era la moda, porque entonces los
toques contaban el racimo denso. Con el nivel por extremo cuentan solo
los pivotes pegados al borde, que en un lateral son dos o tres por
mucho que el precio oscile. **Los toques miden cuántas veces se tocó
el techo, no cuánto osciló el precio: son cosas distintas.**

### 8.8 El tope de ATR como causa de la fragmentación

**Planteamiento**: los laterales grandes salían partidos en
fragmentos; la hipótesis natural era que el tope de altura los
rechazaba.

**Descartado con medición**: el bloqueante dominante era "sin
rectángulo" (~80 % de las ventanas); el tope de altura bloqueaba entre
el 0,7 % y el 6 %. Subir el tope de 5 a 8 ATR aportaba 3 rangos y
saturaba en 7.

Se probó también ensanchar la tolerancia de los toques: duplica el
número de rangos (BTC de 16 a 30) pero **no su tamaño**. Produce más
rectángulos, no más grandes.

**La causa real era de escala**: el tamaño del rectángulo lo fija la
ventana, y con N=60 velas de 4h (~10 días) es imposible ver un lateral
de 3 meses, se ajuste lo que se ajuste. La solución fue el multi-N del
apartado 4.3. Este episodio ilustra el valor de medir antes de
corregir: las dos hipótesis intuitivas eran falsas.

### 8.9 Multi-timeframe (1w / 1d / 4h)

**Planteamiento**: correr la detección sobre timeframes reales
distintos en lugar de sobre varias ventanas del mismo.

**Descartado con medición**: no lograba trazar los laterales grandes,
porque 1d y 1w tampoco cubrían esa escala con N=60 y N=26. Los tres
laterales de BTC trazados a mano salían solo como fragmentos, con un
10-16 % de solape temporal.

Un hallazgo contraintuitivo de esa fase merece registro: al normalizar
por el ATR del propio timeframe, **los rectángulos semanales salían
más estrechos que los de 4h**. Uno de 1w que medía un 63,7 % de altura
en precio eran solo 2,5 ATR semanales. El ATR absorbe la escala
temporal casi por completo.

### 8.10 Escalado del umbral de oscilación con N

**Planteamiento**: escalar los 7 cruces con el tamaño de ventana para
conservar la tasa por vela.

**Descartado con medición**: con N=250 exigiría 29 cruces y con N=400,
47, umbrales inalcanzables que anulaban toda la detección en las
ventanas largas. El razonamiento era erróneo: **un lateral no cruza el
punto medio más veces por ser más largo, sino por ser más oscilante**.
El número de cruces mide una propiedad de forma, no de duración.

### 8.11 Ocupación de los bordes

**Planteamiento**: exigir que un porcentaje mínimo de cierres caiga en
las bandas extremas del rectángulo, para impedir que una caja
demasiado alta pase el filtro.

**Descartado con medición**: el ajuste a los rangos trazados a mano
cae de 0,875 a 0,747 —medido sobre la línea base de entonces—,
porque parte en dos los laterales de BTC de
febrero-abril y abril-mayo de 2026 (de 0,93 a 0,41 y de 0,89 a 0,45).

El motivo es de fondo, no de calibración: **dentro de un lateral bueno
el precio no reparte sus visitas de forma uniforme, pasa temporadas
pegado a una mitad del rectángulo**. Medir la ocupación sobre la
ventana entera castiga esa asimetría, que es normal.

El mecanismo se conserva desactivado en `config.yaml`, con la
medición anotada y una indicación de cómo habría que replantearlo.

### 8.12 Resolución de solapes por el rango más largo

**Planteamiento**: cuando dos rangos se solapan, quedarse con el más
largo.

**Descartado por razonamiento causal**: para saber cuál es el más
largo hay que esperar a que ambos terminen, y para entonces ya se
habría operado el primero. Es sesgo de anticipación, y habría hecho
fallar el test de truncado.

Se descartó igualmente quedarse con el más reciente, que favorece los
rectángulos declarados tarde —los que incluyen en su ventana la
ruptura del rango anterior— y por tanto los de peor calidad
estructural.

La solución adoptada es marcar los solapes con `grupo_solape` y
delegar la resolución al Filtro 3, que ya tiene pendiente el mismo
problema: elegir el rango previo relevante entre varios candidatos.
Resolverlo dos veces con criterios distintos sería incoherente.

---

## 9. Viabilidad operativa

### 9.1 Compatibilidad con la operativa real

La estrategia consiste en abrir posición cuando el precio vuelve a
testear los niveles (VAH, POC, VAL) de rangos **previos**, ya rotos:
largo si llega desde abajo, corto si llega desde arriba.

Esa operativa es compatible con las marcas temporales del sistema. El
perfil de un rango se conoce en su `confirmado_en`, y las entradas se
evalúan necesariamente después, cuando el precio regresa. No hay
conflicto entre lo que el sistema sabe y cuándo puede actuar.

### 9.2 Restricciones consideradas

- **Ejecución donde se mide**: el precio procede del mismo exchange
  donde se ejecutará, evitando discrepancias entre la señal y el
  fill
- **Sin apalancamiento en el backtest** (1x). El escalado es una
  decisión posterior de gestión de capital y mezclarlo con la
  validación de la señal confundiría dos preguntas distintas
- **Watchlist fija** de pares líquidos definida ex ante por criterio
  objetivo, en lugar de un escáner sobre todos los activos. Evita el
  *data snooping*: probar la estrategia sobre cientos de activos y
  quedarse con los que funcionan produce un resultado que no se
  reproduce fuera de muestra

### 9.3 Coste computacional

La detección sobre 4 379 velas con las cinco ventanas y el cálculo de
los 47 perfiles se ejecutan en segundos sobre un portátil. No hay
obstáculo de rendimiento para el backtest ni para la operativa en
vivo, donde el timeframe de decisión de 4h deja un margen amplio.

### 9.4 Limitaciones

- La detección se ha validado sobre **dos activos**. Ampliar la
  watchlist es requisito antes de operar
- Los parámetros son valores de partida razonados, **no calibrados
  empíricamente** contra resultados de operativa
- El criterio de verdad es el trazado manual de un solo analista, con
  la subjetividad que eso implica
- El sistema detecta rangos y calcula niveles, pero **no genera
  señales ni gestiona riesgo**: sin esos componentes no puede
  afirmarse nada sobre su rentabilidad

---

## 10. Componente de inteligencia artificial

### 10.1 Por qué un clasificador supervisado

El filtro de operaciones del sistema son tres reglas encadenadas —sin
squeeze, ADX < 35, impulso ≥ 1.0 ATR— halladas midiendo **una variable
cada vez**. Ese método encuentra efectos individuales, pero es incapaz
de explorar las **combinaciones**: con 19 variables candidatas, probarlas
a mano es inviable.

Ese es exactamente un problema de **clasificación binaria supervisada**:
dado el estado del mercado en el instante en que el precio toca un nivel
del perfil de volumen, ¿acabará la operación en ganancia?

Se comparan dos modelos, deliberadamente pequeños:

| modelo | configuración | papel |
|---|---|---|
| Regresión logística | L2, `C=0.1`, variables estandarizadas | referencia lineal |
| Gradient boosting | `HistGradientBoostingClassifier`, profundidad 3, 120 iteraciones, `min_samples_leaf=25`, `l2=1.0` | modelo no lineal |

Si el bosque no bate a la regresión logística, la complejidad adicional
no está justificada.

### 10.2 Las 19 variables

Todas se calculan sobre la vela de 4h **ya cerrada** en el momento de
colocar la orden, la misma que usa el motor para decidir, de modo que el
modelo no ve nada que la estrategia no vea.

| grupo | variables |
|---|---|
| momento | `impulso`, `momento_ttm_orientado`, `estocastico_orientado`, `divergencia_favor`, `fase_favorable` |
| tendencia | `adx`, `di_orientado` |
| volatilidad | `squeeze`, `velas_squeeze`, `bb_ancho_pct`, `atr_relativo` |
| nivel | `calidad`, `confluencia`, `r_potencial`, `es_poc`, `es_vah`, `es_val` |
| operación | `riesgo_pct`, `es_long` |

Las variables que dependen del lado van **orientadas** a la dirección de
la operación: un DI+ alto favorece a un largo y perjudica a un corto, así
que el signo tiene que formar parte de la variable o la información se
cancela.

### 10.3 Diseño contra el sobreajuste

La muestra es pequeña —123 operaciones en BTC y 149 en ONDO sin
filtrar—, así que todo el diseño está orientado a no engañarse:

1. **Walk-forward**: se entrena con el 60% más antiguo y se evalúa con
   el 40% más reciente. Nunca validación cruzada aleatoria, que en
   series temporales filtra información del futuro.
2. **Cruzado entre activos**: se entrena en BTC y se evalúa en ONDO, y
   al revés. Es la prueba dura: si el modelo aprendió algo general
   sobrevive; si memorizó un activo, se hunde.
3. **Igualdad de selectividad**: el umbral de probabilidad se fija en el
   conjunto de ENTRENAMIENTO, en el percentil que deja pasar la misma
   proporción de operaciones que las reglas. Sin esto, el modelo podría
   ganar simplemente operando menos.
4. **Dos referencias siempre presentes** en el mismo conjunto de prueba:
   operar todo sin filtrar, y el filtro por reglas.

### 10.4 Resultados

**Walk-forward**, evaluado sobre operaciones posteriores a las de
entrenamiento:

| | criterio | ops | R medio | acierto | PF |
|---|---|---|---|---|---|
| **BTC** | sin filtrar | 50 | +0.001 | 24.0% | 1.00 |
| | **reglas (actual)** | 10 | **+0.785** | 30.0% | **2.50** |
| | regresión logística | 17 | −0.385 | 17.6% | 0.32 |
| | gradient boosting | 13 | −0.018 | 23.1% | 0.97 |
| **ONDO** | sin filtrar | 60 | −0.120 | 26.7% | 0.80 |
| | **reglas (actual)** | 18 | **+0.196** | 38.9% | **1.35** |
| | regresión logística | 32 | −0.169 | 25.0% | 0.71 |
| | gradient boosting | 33 | −0.016 | 30.3% | 0.97 |

**Cruzado entre activos**, entrenando en uno y evaluando en el otro
completo:

| entreno → prueba | criterio | ops | R medio | acierto | PF |
|---|---|---|---|---|---|
| BTC → ONDO | sin filtrar | 149 | +0.037 | 30.2% | 1.07 |
| | **reglas** | 59 | **+0.400** | 39.0% | **1.81** |
| | regresión logística | 121 | −0.020 | 28.9% | 0.97 |
| | gradient boosting | 94 | +0.072 | 31.9% | 1.13 |
| ONDO → BTC | sin filtrar | 123 | −0.057 | 22.0% | 0.91 |
| | **reglas** | 36 | **+0.508** | 27.8% | **1.95** |
| | regresión logística | 52 | −0.101 | 21.2% | 0.84 |
| | gradient boosting | 8 | +0.156 | 25.0% | 1.27 |

**El filtro aprendido pierde contra el filtro por reglas en las cuatro
validaciones, sin una sola excepción.** No es un resultado marginal: las
reglas dan entre +0.196 y +0.785 de R medio, y el mejor modelo va de
−0.385 a +0.156.

### 10.5 Interpretación

Este es el resultado que hay, y la conclusión honesta es que **la IA no
se incorpora al sistema**. Los motivos son concretos:

1. **La muestra no da para 19 variables.** Con 73 y 89 operaciones de
   entrenamiento, cualquier modelo con esa dimensionalidad ajusta ruido.
   Es el problema clásico de la maldición de la dimensionalidad, y no se
   arregla con más regularización sino con más datos.
2. **El modelo lineal se comporta peor que el no lineal**, y ambos peor
   que tres reglas. Cuando la referencia simple gana a las dos
   alternativas complejas, el mensaje no es que falte capacidad de
   modelado: es que **falta señal por operación**.
3. **Las reglas ya incorporan conocimiento externo a la muestra.** El
   squeeze, el ADX y el impulso no salieron de optimizar sobre estos
   datos: son indicadores con décadas de literatura, y solo se calibró su
   umbral. Ese respaldo fuera de muestra es precisamente lo que el modelo
   no tiene.

### 10.6 La confirmación que sí aporta el modelo

Los coeficientes de la regresión logística, entrenada sobre los dos
activos juntos, ordenados por magnitud:

| variable | coeficiente | lectura |
|---|---|---|
| **impulso** | **+0.305** | la más influyente de las 19 |
| confluencia | −0.206 | adversa |
| di_orientado | +0.176 | favorable |
| atr_relativo | +0.167 | favorable |
| riesgo_pct | +0.136 | favorable |
| momento_ttm_orientado | +0.131 | favorable |
| velas_squeeze | +0.130 | favorable |
| adx | +0.117 | favorable |

**El modelo redescubre por su cuenta que la variable más informativa es
el impulso de aproximación**, que es exactamente la que el análisis
manual había identificado como el único filtro que funcionaba. Que dos
métodos independientes —medición univariante por un lado, ajuste
multivariante por otro— señalen la misma variable es una **validación
cruzada del hallazgo principal del trabajo**.

El coeficiente negativo de `confluencia` también reproduce un resultado
ya medido a mano: los niveles donde se apilan varios perfiles rinden
peor, no mejor, seguramente porque son zonas muy transitadas donde el
precio ya no reacciona.

### 10.7 Qué haría falta para que la IA aportase

La conclusión no es «la IA no sirve aquí», sino «**la IA no sirve con
272 operaciones**». Las condiciones para reabrir la vía, en orden:

1. **Más datos, que es lo determinante**: 8-10 pares y 4-5 años de
   histórico llevarían la muestra a varios miles de operaciones, que es
   el orden de magnitud donde estos modelos empiezan a funcionar.
2. **Menos variables**: seleccionar 5-6 de las 19 por criterio
   económico, no estadístico, antes de entrenar.
3. **Etiqueta más rica**: predecir el R esperado (regresión) en vez de
   ganar/perder (clasificación) aprovecha la magnitud del resultado, que
   la etiqueta binaria tira a la basura.
4. **Uso como calificador, no como filtro**: en vez de aceptar o
   rechazar, modular el tamaño de la posición con la probabilidad
   estimada. Es más robusto a un modelo mediocre, porque un error no
   cancela la operación sino que solo la dimensiona mal.

Reproducible con `experiments/exp_ia_filtro.py`.

---

## 11. Generación de señales y gestión del riesgo

### 11.1 La señal de entrada

La estrategia es de **reversión a la media sobre niveles de volumen**.
Cada rango lateral confirmado produce un perfil de volumen con tres
niveles operables —VAL, POC y VAH—, y esos niveles siguen vigentes
mucho después de que el rango termine.

La regla es una sola frase: **cuando el precio toca un nivel vigente, se
opera contra el movimiento de aproximación**. Si llega desde arriba, se
compra; si llega desde abajo, se vende.

El lado lo fija el **cierre de la vela de 4h anterior**, no lo que hace
el precio dentro de la vela. Es consecuencia directa de la prohibición de
anticipación: el lado solo puede conocerse con velas cerradas. Tiene un
efecto observable —si el precio cruza el nivel dentro de una vela y
cierra al otro lado, la orden que queda puesta es la contraria— y es un
coste asumido a cambio de que el backtest sea honesto.

### 11.2 Los filtros de contexto

De todos los toques posibles solo se opera una fracción. Tres reglas,
en este orden:

| filtro | umbral | fundamento |
|---|---|---|
| **Squeeze** | descartar si está activo | Bandas de Bollinger dentro de los canales de Keltner: volatilidad comprimida, sin recorrido que cobrar. R medio con squeeze: **−0.504 en BTC y −0.805 en ONDO** |
| **ADX** | descartar si ≥ 35 | Un nivel tocado en tendencia fuerte se rompe; en régimen lateral se respeta. Es la hipótesis central de una estrategia de reversión |
| **Impulso** | exigir ≥ 1.0 ATR en 6 velas | No operar niveles a los que el precio llega arrastrándose. Es el filtro más productivo del sistema |

El umbral del ADX se probó en 20, 25, 30 y 35, y forma **meseta** —30
también funciona—, que es lo que distingue un efecto real de una
casualidad de ajuste. Se eligió 35 por conservar más operaciones.

### 11.3 La gestión de la salida

Es lo que convierte una ventaja de entrada pequeña en un sistema
rentable, y por tanto el verdadero motor del resultado.

- **Stop**: 1.5 ATR por detrás de la entrada. Definición de la unidad
  de riesgo R.
- **Tres objetivos escalonados**, en los siguientes niveles vigentes en
  la dirección del trade, cerrando un tercio en cada uno.
- **TP1 limitado a 1.0 ATR**, aplicado *después* de la cascada de
  objetivos para que no arrastre a TP2 y TP3.
- **Break-even tras TP2**: el resto de la posición pasa a riesgo cero.
- **Respaldo si no hay niveles**: objetivos escalonados al 5%.
- Los **imbalances semanales sin rellenar** también sirven como zona de
  objetivo.

Toda la ejecución se resuelve sobre velas de **15 minutos**, no de 4
horas, para que el orden entre el stop y el objetivo dentro de una misma
vela no quede al azar.

### 11.4 Dimensionamiento por convergencia

El tamaño **no** es fijo. Se arriesga un porcentaje constante del
capital —0.5%— y el nominal sale de la distancia al stop, de modo que
todas las operaciones pesan lo mismo en R independientemente de la
volatilidad del momento.

Sobre eso se aplica un **multiplicador por convergencia de señales**.
Cinco señales binarias —impulso, divergencia del MACD, rotación del área
de valor, estructura y confluencia de niveles— dan una puntuación de 0 a
5, y esa puntuación escala el riesgo:

| puntuación | multiplicador |
|---|---|
| 0-2 | ×0.5 |
| 3-4 | ×1.0 |
| 5 | ×2.0 |

Se usa como **multiplicador de tamaño y no como filtro** porque el score
es monótono en BTC pero no en ONDO: descartar por él hunde el segundo
activo. Modular el tamaño aprovecha la información sin arriesgar nada
cuando el score se equivoca.

Sin apalancamiento (1x), el capital comprometido nunca supera el
disponible, lo que actúa además como límite natural de posiciones
simultáneas.

---

## 12. Backtest y resultados

### 12.1 Condiciones

- **Activos**: BTC/USD y ONDO/USD, futuros perpetuos de Kraken.
- **Periodo**: 2 años de histórico, decisión en 4h, ejecución en 15m.
- **Apalancamiento**: 1x.
- **Costes**: comisión y deslizamiento aplicados en cada entrada y cada
  salida parcial.
- **Capital inicial**: 10.000 USD, riesgo del 0.5% por operación.

### 12.2 Resultados

| | BTC | ONDO |
|---|---|---|
| operaciones | 68 | 89 |
| **R medio** | **+0.394** | **+0.264** |
| **profit factor** | **1.78** | **1.54** |
| tasa de acierto | 30.9% | 33.7% |
| ganancia media | +2.91 R | +2.25 R |
| pérdida media | −0.73 R | −0.74 R |
| **retorno** | **+28.5%** | **+23.7%** |
| máxima caída | −7.0% | −6.6% |

**El sistema acierta menos de una de cada tres veces y aun así gana.**
Esa es su naturaleza: la ganancia media es cuatro veces la pérdida media,
gracias a que el stop está acotado en 1 R y los objetivos escalonados
dejan correr la parte final de la posición. De las 68 operaciones de
BTC, 48 terminan en stop y solo 15 alcanzan el tercer objetivo — pero
esas 15 pagan todo lo demás.

### 12.3 Validación contra el azar

Se generaron **20 rejillas de niveles desplazados** por activo,
conservando el número de niveles y su distribución pero moviendo los
precios. Es la prueba de si el resultado viene del perfil de volumen o
de la gestión de salida aplicada a cualquier precio.

**Ninguna de las 20 rejillas aleatorias bate a BTC.** En ONDO lo hace
una de las 20. Los niveles del FRVP aportan información real.

### 12.4 Robustez

Todo hallazgo tuvo que superar el mismo protocolo antes de entrar:

1. **Mismo signo en los dos activos.** Lo que solo funciona en uno se
   descarta.
2. **Monotonía por cuartiles.** Un efecto real crece de forma ordenada;
   un artefacto salta.
3. **Validación por el PEOR de cuatro subconjuntos** (BTC y ONDO, cada
   uno partido en dos mitades temporales). Se juzga por el peor caso, no
   por la media.

El sistema actual da **+0.205 de R medio en su peor subconjunto**, es
decir, sigue siendo rentable en el trozo de datos donde peor se comporta.

### 12.5 Calidad de la entrada, medida aparte de la salida

Para separar «acertar el momento de entrar» de «gestionar bien la
salida» se midió, sin stop ni objetivos, la excursión máxima favorable
(MFE) y adversa (MAE) durante los 4 días siguientes a cada entrada, con
la **eficiencia = MFE/(MFE+MAE)**, donde 0.5 es una moneda al aire.

| | BTC | ONDO |
|---|---|---|
| aceptado por los filtros | ef **0.519** | ef **0.529** |
| rechazado por los filtros | ef 0.513 | ef 0.517 |
| **aleatorio** (400 remuestreos) | ef 0.500 | ef 0.501 |

La eficiencia real supera al 80.5% de los remuestreos aleatorios en BTC
y al 94.0% en ONDO: la ventaja de entrada **existe pero es pequeña**.

El hallazgo relevante es *cómo* actúan los filtros: suben el MFE un 22%
(2.63 → 3.21 ATR en BTC) pero suben el MAE casi lo mismo. **Lo que
seleccionan no son toques que sufran menos, sino toques que se mueven
más.** Coherente con que el filtro productivo sea el de impulso.

Y el margen que queda: el 75% de las entradas de BTC llegan a ofrecer 1
R o más, y se captura 0.39. **Entre el 13% y el 25% de lo que la
operación llega a ofrecer.** El recorrido de mejora está en la salida.

---

## 13. Conclusiones

**1. El sistema es rentable en los dos activos y supera al azar.**
+28.5% en BTC y +23.7% en ONDO con caídas máximas por debajo del 7%, y
ninguna de las 20 rejillas de niveles aleatorios bate a BTC.

**2. No gana por adivinar el giro, gana por la asimetría de la
gestión.** La ventaja de entrada es de dos o tres puntos de eficiencia
sobre una moneda al aire. Con un 31% de acierto, lo que produce el
resultado es que la ganancia media cuadruplique a la pérdida media.

**3. El filtro de impulso es el hallazgo principal, y está doblemente
validado.** Lo identificó la medición univariante y lo confirmó por su
cuenta el modelo multivariante, que le asigna el mayor coeficiente de
las 19 variables.

**4. La IA no mejora al sistema con esta cantidad de datos, y eso
también es un resultado.** Un gradient boosting y una regresión
logística pierden contra tres reglas en las cuatro validaciones. Con 73
y 89 operaciones de entrenamiento, la limitación no es de modelado sino
de muestra. La vía se reabre con 8-10 pares y 4-5 años de histórico.

**5. Documentar lo que no funciona vale tanto como documentar lo que
funciona.** Catorce variables se midieron y descartaron —funding rate,
naked POC, nivel del RSI, divergencia del RSI, histograma del MACD,
aceleración, estocástico, Key Level 23, y otras—, cada una con su
medición registrada. Ese registro evita repetir intentos y es lo que
permite justificar cada parámetro del `config.yaml`.

**6. Las dos lecciones de método que cambiaron el trabajo**:

- *Una variable predictiva no sirve si explotarla cuesta más de lo que
  aporta.* La señal más predictiva encontrada (+0.207/+0.235) resultó
  inexplotable: esperar al cierre para confirmarla destruía la colocación
  del stop.
- *Toda señal nueva hay que probarla sobre el subconjunto YA FILTRADO.*
  Es lo único que distingue información nueva de un espejo de un filtro
  que ya está puesto. Dos bloques enteros de variables cayeron por ahí.

**7. Lo que queda.** Reconstruir el mapa de liquidaciones, incorporar el
VWAP, medir la calidad del perfil de origen, y sobre todo **validar en
5-8 activos más**: llegado este punto, más datos valen más que más
indicadores.

---

## Anexo A — Reproducibilidad

```powershell
# Pruebas: 130 en 8 ficheros, todas deben salir en verde
foreach ($t in (Get-ChildItem tests\test_*.py | Where-Object Name `
  -notmatch 'range_detector_manual|ingesta_manual')) { `
  "$($t.Name): $(& .venv\Scripts\python.exe $t.FullName | `
  Select-Object -Last 1)" }

# Backtest completo y métricas de los dos activos
.venv\Scripts\python.exe experiments\exp_toques_frvp.py

# Calidad de la entrada aislada de la salida (§12.5)
.venv\Scripts\python.exe experiments\exp_calidad_entrada.py

# Componente de IA: filtro aprendido contra filtro por reglas (§10)
.venv\Scripts\python.exe experiments\exp_ia_filtro.py

# Ajuste del Filtro 1 contra los rangos trazados a mano (IoU 0.900)
.venv\Scripts\python.exe tests\test_ajuste_manual.py

# Gráficos: genera notebooks/rangos_btc.html y rangos_ondo.html
.venv\Scripts\python.exe -m jupyter nbconvert --to notebook `
  --execute --inplace notebooks\exploracion.ipynb
```

Los datos están cacheados en `data/raw/` en formato parquet, de modo
que ninguna de las cifras de esta memoria depende de volver a
descargar del exchange.

## Anexo B — Documentos del proyecto

| Documento | Contenido |
|---|---|
| `SPEC.md` | Especificación funcional. §6 estado de implementación, §7 decisiones pendientes |
| `CLAUDE.md` | Convenciones y reglas de trabajo sobre el repositorio |
| `config.yaml` | Parámetros, cada uno con la justificación de su valor |
