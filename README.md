# Sistema algorítmico de reversión sobre perfil de volumen

Trabajo Fin de Máster — Inteligencia artificial aplicada a mercados financieros.

Bot de trading que identifica los precios donde el mercado concentró su
actividad, espera a que el precio vuelva a visitarlos y opera contra el
movimiento que los alcanza. Desarrollado íntegramente en Python, validado
sobre dos años de datos históricos y sometido a control frente al azar.

**[Memoria completa con resultados interactivos →](https://claude.ai/code/artifact/a0890ff8-5877-43b0-b642-e735ac4419e1)**

## Resultados

| | Bitcoin | ONDO |
|---|---|---|
| Operaciones | 68 | 89 |
| Retorno | **+28,5 %** | **+23,7 %** |
| Profit factor | 1,78 | 1,54 |
| Tasa de acierto | 30,9 % | 33,7 % |
| Ganancia media | +2,91 R | +2,25 R |
| Pérdida media | −0,73 R | −0,74 R |
| Caída máxima | −7,0 % | −6,6 % |

Los dos activos están escogidos porque representan regímenes de mercado
opuestos: capitalización muy alta con la liquidez más profunda del sector,
y capitalización media con un libro de órdenes mucho más fino. Que la misma
lógica funcione en ambos extremos es lo que sostiene que no depende de las
particularidades de un activo concreto.

**Control frente al azar**: se generaron 20 rejillas de niveles falsos, con
la misma cantidad y distribución pero situados en precios distintos.
Ninguna supera al sistema en Bitcoin.

## Cómo funciona

1. **Detección de rangos** — identifica las fases en que el precio lleva
   semanas oscilando dentro de una banda. Contrastado con rangos trazados
   manualmente: coincide en un 90 %.
2. **Perfil de volumen** — dentro de cada rango calcula cómo se repartió el
   volumen negociado por precio, y de ahí salen tres niveles operables.
3. **Filtro de contexto** — tres condiciones antes de operar: volatilidad no
   comprimida, ausencia de tendencia fuerte y llegada con empuje al nivel.
4. **Gestión de salida** — pérdida acotada en 1,5 × ATR y tres objetivos
   escalonados, cerrando un tercio en cada uno. Tras el segundo, la posición
   pasa a riesgo cero.

## Componente de inteligencia artificial

Se evaluaron los dos usos posibles de un modelo supervisado con el mismo
protocolo de validación:

- **Como filtro** (decidir *si* operar): pierde frente a las tres reglas en
  las cuatro validaciones. Con unas decenas de operaciones de entrenamiento
  y 19 variables, el modelo memoriza en lugar de generalizar. **Descartado.**
- **Como dimensionador** (decidir *cuánto* arriesgar): mejora el resultado
  en los dos activos. Si el modelo se equivoca la operación se dimensiona
  mal, pero no se cancela una buena ni se abre una mala. **Integrado**, en
  `core/calificador_ia.py`.

El modelo confirma además, por su cuenta, que la variable más informativa de
las 19 es el impulso de aproximación al nivel: exactamente la misma que el
análisis manual había identificado como el único filtro productivo.

## Arquitectura

```
core/         lógica de estrategia, idéntica en backtest y en operativa real
  range_detector.py   detección y selección de rangos laterales
  frvp.py             perfil de volumen de rango fijo
  levels.py           rejilla de niveles operables
  osciladores.py      estocástico, ADX, Bollinger, Keltner, squeeze, TTM
  momentum.py         RSI, MACD y divergencias
  structure.py        estructura de mercado
  imbalances.py       huecos de precio semanales sin rellenar
  convergencia.py     puntuación de señales y multiplicador de tamaño
  calificador_ia.py   modelo aprendido y su multiplicador de tamaño
execution/    capa intercambiable: backtest, paper y live
data/         ingesta, validación y caché local en Parquet
experiments/  scripts de medición reproducibles
notebooks/    exploración y gráficos interactivos
tests/        130 pruebas automáticas
config.yaml   todos los parámetros, cada uno con su justificación
```

La capa de ejecución es intercambiable sin tocar una sola línea de `core/`,
que es la condición para que lo validado en el backtest sea exactamente lo
que se ejecute en el mercado.

## Prevención del sesgo de anticipación

Ningún cálculo puede usar información de velas futuras. Una prueba
automática corta el histórico al 50 %, al 70 % y al 90 % y exige que todo lo
detectado antes del corte resulte idéntico, comparando 200 rangos.
Cualquier discrepancia delata el uso de información que aún no existía.

El lado de cada operación lo fija el cierre de la vela anterior, nunca lo
que ocurre dentro de la vela en curso. Es una limitación real que se asume
a propósito: es la única forma de que el resultado histórico sea
reproducible en tiempo real.

## Reproducir los resultados

Requiere Python 3.14 y un entorno virtual en `.venv`.

```powershell
# Instalar dependencias
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Backtest completo y métricas de los dos activos
.venv\Scripts\python.exe experiments\exp_toques_frvp.py

# Componente de IA: filtro aprendido frente al filtro por reglas
.venv\Scripts\python.exe experiments\exp_ia_filtro.py

# Componente de IA: el modelo dimensionando la posición
.venv\Scripts\python.exe experiments\exp_ia_sizing.py

# Calidad de la entrada, aislada de la gestión de salida
.venv\Scripts\python.exe experiments\exp_calidad_entrada.py

# Ajuste del detector de rangos frente a los trazados manualmente
.venv\Scripts\python.exe tests\test_ajuste_manual.py

# Gráficos interactivos: genera notebooks/rangos_btc.html y rangos_ondo.html
.venv\Scripts\python.exe -m jupyter nbconvert --to notebook `
  --execute --inplace notebooks\exploracion.ipynb
```

### Pruebas

130 pruebas en 8 ficheros, todas deben salir en verde:

```powershell
foreach ($t in (Get-ChildItem tests\test_*.py | Where-Object Name `
  -notmatch 'range_detector_manual|ingesta_manual')) { `
  "$($t.Name): $(& .venv\Scripts\python.exe $t.FullName | `
  Select-Object -Last 1)" }
```

No se usa pytest: cada fichero de pruebas se ejecuta directamente con el
intérprete del entorno virtual.

## Documentación del proyecto

| Documento | Contenido |
|---|---|
| `memoria.md` | Memoria del trabajo |
| `SPEC.md` | Especificación funcional y registro completo de mediciones |
| `config.yaml` | Parámetros, cada uno con la justificación de su valor |

`SPEC.md` incluye el registro de las catorce variables que se midieron y se
descartaron, cada una con su medición. Ese catálogo evita repetir intentos
ya realizados y es lo que permite justificar por qué el sistema tiene los
parámetros que tiene y no otros.

## Nota sobre los datos

La caché de datos históricos (`data/raw/`) no se versiona por tamaño. Se
regenera automáticamente en la primera ejecución descargando de Kraken a
través de CCXT.
