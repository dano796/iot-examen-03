# Ocupacion de espectro 840 - 860 MHz

Analisis de contaminacion del espectro radioelectrico en la banda celular de
840 a 860 MHz, sector occidental de Medellin, a partir de 61 mediciones
tomadas con una estacion movil de monitoreo (USRP + GNU Radio + GPS).

Examen 3 de Internet de las Cosas — Universidad Pontificia Bolivariana.

## Que hace

Un proceso ETL que limpia las mediciones, audita su calidad, imputa lo que
falta e integra la potencia de cada canal con la sumatoria de Parseval, mas
un dashboard web que visualiza los resultados sobre el mapa de la ciudad.

| Canal | Banda | Potencia media | Ocupacion |
|---|---|---|---|
| A | 840 - 845 MHz | -46.38 dBm | 24.6 % |
| B | 845 - 850 MHz | -36.89 dBm | 32.8 % |
| **C** | **850 - 855 MHz** | **-28.15 dBm** | **86.9 %** |
| D | 855 - 860 MHz | -44.46 dBm | 29.5 % |

Canal mas contaminado: **C**. Canal mas limpio: **A**. Frecuencia mas
contaminada del sistema: **853.145 MHz** (mediana -41.7 dBm, sobre el umbral
en el 91.8 % del recorrido); la mas limpia: **843.613 MHz** (-72.6 dBm).

## Estructura

```
etl.py          limpieza, auditoria, imputacion, Parseval y reporte
fuentes.py      estimacion del origen de la contaminacion (bonificacion)
graficas.py     figuras estaticas del informe
dashboard.py    servidor web interactivo (Dash + Plotly)
capturas.py     capturas automaticas de las vistas del dashboard
informe.py      ensamblado del entregable final en PDF

medidas_2026_20/  dataset original: 001.txt .. 061.txt
salida/           todo lo generado por el pipeline
```

## Instalacion

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

`etl.py` y `fuentes.py` funcionan solo con numpy; el resto necesita el
entorno completo.

Si la descarga de Chromium falla, `capturas.py` e `informe.py` detectan y
reutilizan el Chrome del sistema automaticamente.

## Ejecucion

```bash
# 1. Pipeline de datos: indicadores, estimacion de fuentes y reporte
python3 etl.py

# 2. Figuras del informe
.venv/bin/python graficas.py

# 3. Dashboard (deja corriendo en http://127.0.0.1:8050)
.venv/bin/python dashboard.py

# 4. Capturas del dashboard (con el servidor arriba, en otra terminal)
.venv/bin/python capturas.py

# 5. Entregable final
.venv/bin/python informe.py
```

## Salidas

| Archivo | Contenido |
|---|---|
| `salida/Informe_Examen03.pdf` | Entregable final, 20 paginas |
| `salida/reporte_calidad.md` | Informe en Markdown, 10 secciones |
| `salida/indicadores.csv` | 61 mediciones x 21 columnas |
| `salida/fuentes_estimadas.csv` | Estimacion de origen por canal |
| `salida/graficas/` | Figuras 1 a 4 del informe |
| `salida/capturas_dashboard/` | Vistas del tablero |

El reporte en Markdown lo **genera** `etl.py` a partir de los mismos arreglos
que producen el CSV, de modo que no puede desincronizarse de los datos:
editarlo a mano se pierde en la siguiente corrida. Para cambiar su redaccion
hay que tocar `escribir_reporte()` en `etl.py`.

## Notas metodologicas

**Parseval sobre potencia lineal.** La potencia de cada canal se integra
pasando los dBm a mW, sumando los 256 bins del bloque de 5 MHz y
promediando, no promediando los dBm directamente. Sobre este dataset la
diferencia cambia la clasificacion de 20 de las 244 combinaciones
medicion-canal.

**Imputacion acotada.** Solo `008.txt` fue modificado (GPS sin fix), por
interpolacion lineal entre sus vecinos, con un error maximo de +-0.630 km.
`017.txt` tiene posicion imprecisa (HDOP 17.3) pero valida, asi que se marca
como degradada y no se toca. Ningun valor de espectro fue alterado.

**Frecuencias extremas por mediana.** Parseval se aplica dentro de cada
espectro; para agregar entre las 61 ubicaciones se usa la mediana por bin.
La media lineal la domina una sola medicion: `016.txt` tiene el piso de
ruido 35.6 dB sobre el tipico (posible saturacion del receptor) y aporta el
97 % de la energia del bin que esa media senalaba (851.719 MHz). La mediana
senala 853.145 MHz en 61 de 61 pruebas quitando una medicion cada vez.
`016.txt` no se descarta: se marca en la columna `anomalia_espectral`.

**Estimacion de fuentes.** La trilateracion log-distancia resulta no
concluyente en los cuatro canales (mejor R^2 = 0.154, con pendiente negativa
en el canal C). Se reporta ese resultado negativo con su evidencia y se
entrega en su lugar el centroide ponderado del decil de mayor potencia, que
delimita una zona de incidencia y no un transmisor.

**Verificacion con estaciones base reales.** Junto a las dos zonas de mayor
potencia hay estaciones base celulares, ubicadas en Google Street View:
Guayabal (6.201370, -75.584742), a 35 m de `016.txt`, y Sur (6.168150,
-75.608361), a 288 m de `024.txt`. Con la ganancia fija de 40 dB del receptor,
pasar al pie de la de Guayabal satura el front-end: eso explica el piso de
ruido anomalo de `016.txt`. El centro estimado del canal A queda a 153 m de
esa antena; el del canal B cae entre las dos, que es la limitacion del
centroide cuando hay mas de un emisor.
