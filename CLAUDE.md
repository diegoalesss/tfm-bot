# Proyecto: Bot de trading algorítmico (TFM)

## Contexto
TFM del Máster en IA aplicada a mercados financieros.
Entrega: 2 de septiembre de 2026. Defensa ante tribunal
en septiembre.
Exchange objetivo: Kraken (futuros) vía CCXT.
Timeframe de decisión: 4h. Backtest sin apalancamiento (1x).

## Estado de la estrategia: EN CONSTRUCCIÓN
La especificación funcional está en SPEC.md y está INCOMPLETA
a propósito. Se está desarrollando por fases:

CERRADO a 02/09/2026 (no tocar sin motivo y sin medir):
- Ingesta y validación (data/loader.py, data/validator.py)
- Filtro 1, rangos laterales (core/range_detector.py)
- Filtro 2, FRVP (core/frvp.py)
- Visualización (notebooks/exploracion.ipynb)

CONSTRUIDO DESPUÉS, para MEDIR (no es estrategia cerrada; el
detalle y las mediciones están en SPEC.md §8 a §15):
- Rejilla de niveles operables (core/levels.py), con selección
  causal para el backtest
- Motor de backtest y métricas (execution/backtest.py, metrics.py)
- Imbalances semanales (core/imbalances.py), como objetivo
- Estructura de mercado (core/structure.py)
- Momento y divergencias (core/momentum.py)
- Funding (data/funding.py, core/flujo.py) — medido y DESCARTADO
- Osciladores (core/osciladores.py) — medido, fuera del score
- Convergencia de señales (core/convergencia.py) — ACTIVA, como
  multiplicador de tamaño, no como filtro

SIGUIENTE FASE: sin decidir. Los dos candidatos son el Filtro 3
(FRVP sobre el rango previo en tendencia; falta el criterio para
elegir ese rango, SPEC.md §7) y un filtro que descarte las malas
entradas por una razón y no por su resultado pasado, que es lo que
SPEC.md §8 señala como el trabajo pendiente real.

PENDIENTE: filtros 4 a 6, líneas de tendencia, lógica de
confluencia, capa de ejecución (paper y live).

Implicación: no asumas que SPEC.md describe la estrategia final.
No añadas reglas, filtros ni indicadores que no estén escritos ahí.
Si algo no está definido, pregúntame antes de implementarlo.
Diseña los módulos para poder ampliarlos sin reescribirlos.

Los apartados 1 y 2 están cerrados y validados. Si un cambio los
toca, hay que medirlo antes de darlo por bueno (ver más abajo).
SPEC.md §6 lleva el estado y §7 lo que queda abierto, con las
mediciones de lo que ya se probó y no funcionó: consúltalo antes
de proponer una idea que quizá ya se descartó.

## FRVP (core/frvp.py): cómo se usa
    perfil = calcular_frvp(velas_del_timeframe, inicio, fin, config)
    perfil["poc"], perfil["vah"], perfil["val"]
La granularidad de las velas la elige `timeframe_construccion(n, config)`
según la duración del rango (SPEC.md §1): 15m si es corto, 1h si es
medio, 4h si es largo.

El perfil de un rango NO existe antes de su `confirmado_en`, porque
su `fin` no se conoce hasta entonces. Operar sus niveles antes de esa
vela es lookahead.

## Filtro 1 (ya implementado): cómo se usa
Dos pasos, en este orden:
    rangos = detectar_rangos_laterales(df_4h, config)
    operables = seleccionar_rangos(rangos, config)
El primero devuelve TODOS los rangos que cumplen los criterios
(~110 sobre 2 años, la misma zona vista por varias ventanas). El
segundo se queda con ~20, que son los que se llevan al gráfico y
al FRVP.

Solo se trabaja sobre velas de 4h. La detección usa varias
ventanas (40, 60, 150, 250, 400 velas) para captar rangos
anidados de distinta escala.

Cada rango lleva un `tipo` que indica su ESCALA, no su calidad, y
LOS DOS SON OPERABLES:
- `principal` (150-400 velas): laterales de estructura mayor, de
  1 a 3 meses. Son los MÁS operables: más tiempo construyendo el
  rango significa más volumen en el perfil y niveles más fiables
- `secundario` (40-60 velas): rangos anidados dentro de los
  principales, también operables pero de menor peso

## Cómo se valida el trabajo del Filtro 1
El criterio de verdad son los rangos que yo trazo a mano en
TradingView, y lo que importa de ellos son las FECHAS de inicio y
fin, no los niveles exactos de techo y suelo (esos los pongo a
ojo). El motivo: sobre esas fechas se ancla el FRVP.

Referencia actual, BTC en 4h:

    1  2024-11-21 → 2025-02-24        5  2026-02-05 → 2026-04-13
    2  2025-02-25 → 2025-04-22        6  2026-04-14 → 2026-05-27
    3  2025-05-09 → 2025-07-09        7  2026-06-04 → 2026-08-19
    4  2025-11-17 → 2026-01-28

**Ajuste actual: 0.900 de IoU temporal medio, los 7 detectados.**
Ese número es la línea de base: un cambio que lo baje es un
retroceso, por muy bien que arregle un caso concreto.

La medición está en `tests/test_ajuste_manual.py`: imprime el
desglose rango a rango y falla si la media baja de la línea de
base. Se mide sobre los rangos SELECCIONADOS (los crudos dan 0.928,
pero con 116 candidatos casi siempre hay uno que encaja y no son
los que van al gráfico ni al FRVP).

    .venv\Scripts\python.exe tests\test_ajuste_manual.py

Reglas al tocar el detector:
- Mide SIEMPRE contra los 7 antes y después del cambio
- No optimices contra un caso suelto: arreglar uno ha roto otros
  ya varias veces. Pasó con el criterio de ocupación de bordes
  (0.875 → 0.747) y al dar más peso a la duración en la selección
- Si un cambio no mejora la media, revierte y documenta la
  medición en SPEC.md como alternativa descartada. Ese registro
  vale tanto como el código: evita repetir el mismo intento

Los datos están en caché (`data/raw/`), así que medir es barato:
no hace falta descargar nada.

## Reglas de código (obligatorias)
- Python 3.14, PEP 8 estricto
- Type hints en todas las funciones
- Vectorizado con pandas/numpy. Prohibidos los bucles sobre filas
  de DataFrame salvo justificación explícita en comentario
- Manejo de excepciones en toda I/O y llamada a API
- Docstrings estilo NumPy en funciones públicas
- Sin números mágicos: los parámetros van en config.yaml

## Regla crítica: prohibido el lookahead bias
Ningún cálculo puede usar información de velas futuras.
Todo indicador, régimen de mercado y nivel de FRVP se calcula
únicamente con velas ya cerradas. Antes de escribir cualquier
función de señal, verifica explícitamente que no hay fuga temporal
y déjalo indicado en el docstring.

## Regla de datos
- El precio (OHLC) procede SIEMPRE de Kraken: es donde se
  ejecuta y donde saltan los stops
- El volumen es parametrizable (volume_source en config.yaml):
  "kraken" en fase 1, "aggregated" en fase 2
- Nunca mezclar precio de un exchange con ejecución en otro
- El FRVP se construye con velas de 15m, aunque la decisión
  se tome en 4h
- Única excepción, y va declarada en SPEC.md §13: el funding se
  descarga de Binance porque Kraken solo lo publica desde agosto
  de 2026. Se sostiene porque el desequilibrio de apalancamiento
  es global, pero el número exacto no es el que cobra Kraken. Da
  igual en la práctica: el funding se midió y NO aporta, así que
  no entra en la estrategia

## Cómo verificar que no hay lookahead
`tests/test_range_detector.py` tiene el test que lo garantiza
(`test_sin_lookahead_sobre_datos_reales`): corta el histórico al
50/70/90% y exige que todo rango ya confirmado antes del corte
salga IDÉNTICO. Compara 200 rangos.

Si tocas el detector, ese test es la red de seguridad. **No lo
relajes para que pase: si falla, es que el cambio mira al futuro.**

Matiz importante, porque ya llevó a confusión: que un test falle
tras un cambio no significa siempre que el código esté mal. Puede
ser que el test se apoyara en una suposición que el cambio
invalida. Ocurrió con `test_grupo_solape_sobre_datos_reales`, que
comparaba con el fin del rango inmediatamente anterior cuando el
código compara con el alcance máximo del grupo; al recortar colas,
`fin` dejó de ser monótono y la simplificación se rompió. Ahí lo
correcto era corregir el test, no el código. Pero la carga de la
prueba es tuya: hay que demostrar con datos cuál de los dos está
equivocado antes de tocar ninguno.

Cada rango lleva dos marcas temporales que hay que respetar aguas
abajo (FRVP, backtest):
- `declarado_en`: vela en la que el rango pasa a conocerse.
  NUNCA coincide con `inicio`, que está N-1 velas antes
- `confirmado_en`: vela en la que se confirma la ruptura que lo
  cierra, 5 velas después de `fin`
Usar un rango antes de `declarado_en`, o su `fin` antes de
`confirmado_en`, es lookahead.

## Ejecutar las pruebas y ver los gráficos
Ocho ficheros, 119 pruebas, todas deben salir en verde:

    .venv\Scripts\python.exe tests\test_range_detector.py    26/26
    .venv\Scripts\python.exe tests\test_backtest.py          32/32
    .venv\Scripts\python.exe tests\test_flujo.py             19/19
    .venv\Scripts\python.exe tests\test_imbalances.py        10/10
    .venv\Scripts\python.exe tests\test_momentum.py          10/10
    .venv\Scripts\python.exe tests\test_ajuste_manual.py      8/8
    .venv\Scripts\python.exe tests\test_osciladores.py        8/8
    .venv\Scripts\python.exe tests\test_structure.py          6/6

Todas de golpe:

    foreach ($t in (Get-ChildItem tests\test_*.py | Where-Object Name `
      -notmatch 'range_detector_manual|ingesta_manual')) { `
      "$($t.Name): $(& .venv\Scripts\python.exe $t.FullName | `
      Select-Object -Last 1)" }

Regenerar los gráficos:

    .venv\Scripts\python.exe -m jupyter nbconvert --to notebook `
      --execute --inplace notebooks\exploracion.ipynb

No hay pytest instalado: las pruebas se escriben como funciones
`test_*` con `assert` y el fichero se ejecuta directamente con el
intérprete del `.venv`.

`test_range_detector_manual.py` y `test_ingesta_manual.py` no son
pruebas pese al nombre: son scripts de inspección que imprimen y no
afirman nada. `test_ajuste_manual.py` sí lo es, y además imprime.

En `notebooks/` hay tres ficheros y solo uno es un notebook:

    exploracion.ipynb    el CÓDIGO
    rangos_btc.html      un GRÁFICO
    rangos_ondo.html     el otro GRÁFICO

Los `.html` son autocontenidos (Plotly incrustado, funcionan sin
conexión) y llevan un menú desplegable para cambiar de vista sin
volver a ejecutar nada. En este Windows los `.html` están asociados
a Internet Explorer, que no renderiza Plotly y muestra código en
bruto: **hay que abrirlos con Chrome**.

Aviso: el notebook ya se sobrescribió una vez desde un editor
externo con una versión antigua, perdiendo celdas enteras. Si está
abierto en Jupyter o VS Code, cerrarlo antes de ejecutarlo. La
señal de alarma no es que cambie el tamaño del fichero —eso pasa
en cada ejecución— sino que desaparezcan celdas.

## Manejo de errores
En un bot de trading, fallar ruidosamente es preferible a
operar con datos corruptos.
- Prohibido capturar excepciones y continuar en silencio
- Todo except debe registrar el error y, si compromete la
  integridad de los datos, detener la ejecución
- Los datos descargados se validan antes de usarse: huecos,
  duplicados y valores imposibles

## Arquitectura
core/       lógica de estrategia (idéntica en backtest y live)
              range_detector.py  Filtro 1
              frvp.py            Filtro 2
execution/  capa intercambiable: backtest.py, paper.py, live.py
data/       ingesta y validación
              raw/               caché en parquet, no se versiona
notebooks/  exploración y gráficos
tests/      pruebas unitarias
config.yaml parámetros de la estrategia

La capa de ejecución debe ser intercambiable sin tocar core/.

Los parámetros van TODOS en config.yaml, con un comentario que
explique de dónde sale cada valor y qué se midió para elegirlo. Es
lo que permite defender las decisiones ante el tribunal: un número
sin justificación es un número que no se puede defender.

## Entorno
- Windows, PowerShell
- Entorno virtual en .venv (ya creado y activo)
- Los comandos que me propongas deben ser de Windows,
  no de Mac/Linux

## Idioma
- Conversación conmigo: español
- Nombres de variables, funciones y clases: inglés
- Comentarios y docstrings: español

## Forma de trabajar
Desarrollo modular e incremental: un módulo cada vez, con su
script de prueba. No avances al siguiente módulo sin mi aprobación.
