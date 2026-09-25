# Reporte de calidad e indicadores - Examen 03

Banda analizada: **840 - 860 MHz** | Mediciones procesadas: **61** | Recorrido: **26.23 km**

Generado automaticamente por `etl.py`. Todos los numeros de este documento salen de la misma corrida que produce `indicadores.csv`.

## 1. Fuente de datos y alcance

| Concepto | Valor |
|---|---|
| Archivos de la serie | `001.txt` .. `061.txt` (61) |
| Columnas por archivo | 1029 (1024 bins de espectro + 5 metadatos) |
| Resolucion espectral | 19.53 kHz por bin |
| Canales | 4 bloques de 5 MHz = 256 bins cada uno |
| Umbral de ocupacion | -60 dBm |

Excluidos a proposito de la serie:

- `medidaprueba.txt` y `medidapureba2.txt`: ensayos del operador; el primero tiene longitud, latitud y altura en 0.0 y el segundo una temperatura (38.1 C) fuera del rango de la campana. No pertenecen al recorrido.
- `ANTENNA1.csv`: barrido de perdida de retorno (S11) de la antena entre 700 y 950 MHz, tomado con un Agilent N9914A. Es caracterizacion del instrumento, no una medicion de espectro; sirve para respaldar la validez de la medida en la banda, no para el calculo de ocupacion.

## 2. Reporte de calidad

| Metrica | Valor |
|---|---|
| Mediciones leidas | 61 |
| Mediciones con estructura correcta | 61 (100%) |
| Hallazgos criticos | 3 |
| Advertencias | 1 |
| Mediciones buenas | 59 |
| Mediciones degradadas | 1 |
| Mediciones imputadas | 1 |
| Bins de espectro revisados | 62464 |
| Bins NaN, infinitos o fuera de [-140, 0] dBm | 0 |

### Hallazgos detallados

| Archivo | Campo | Gravedad | Detalle |
|---|---|---|---|
| `008.txt` | gps | critico | latitud/longitud en 0.0 (sin fix) |
| `008.txt` | hdop | advertencia | error de distancia 4.4 (degradado) |
| `008.txt` | altura | critico | altura 0.0 m fuera de rango |
| `017.txt` | hdop | critico | error de distancia 17.3 (inutilizable) |

El espectro esta integro: de los 62464 valores de potencia revisados, ninguno resulto NaN, infinito ni fuera del rango fisico del receptor. Los defectos se concentran exclusivamente en la georreferenciacion.

## 3. Tecnicas de imputacion

| Archivo | Campo | Tecnica | Efecto |
|---|---|---|---|
| `008.txt` | gps | interpolacion lineal entre 007.txt y 009.txt | (0.00000, 0.00000, 0.0) -> (6.22636, -75.60079, 1545.0) |

**Total de datos modificados: 1.** Se tocaron unicamente los campos de posicion; ni un solo valor de espectro fue alterado.

### Por que interpolacion lineal en el GPS

La estacion es movil y mide a lo largo de un recorrido continuo, asi que la posicion de la medicion k esta acotada por la k-1 y la k+1. La interpolacion lineal es la hipotesis mas debil disponible -en el sentido estadistico: la que menos supone sobre el fenomeno- porque solo asume que el vehiculo transito entre ambos vecinos, sin postular ruta, velocidad real ni paradas. Si el hueco cayera en un extremo de la serie no habria con que interpolar y la medicion se descartaria.

Alternativas consideradas y por que se descartaron:

| Alternativa | Que asume | Por que no |
|---|---|---|
| Descartar la medicion | nada | Sacrifica un espectro integro; el defecto estaba en el GPS, no en la radio |
| Vecino mas cercano | que el vehiculo no se movio | Introduce un error del tamano del espaciado completo entre vecinos |
| **Interpolacion lineal** | que transito entre ambos vecinos | **Seleccionada**: error acotado y minimo de supuestos |
| Spline o curva suave | velocidad y aceleracion continuas | Anade supuestos que ningun dato respalda |
| Reconstruccion por velocidad real | marcas de tiempo | Inviable: los archivos no registran timestamp |

### Incertidumbre de la posicion imputada

El punto real solo puede estar sobre el tramo que une a los dos vecinos validos, de modo que el error maximo posible de la interpolacion es la mitad de esa separacion:

| Archivo | Separacion entre vecinos | Error maximo |
|---|---|---|
| `008.txt` | 1.260 km | +-0.630 km |

Como referencia, la separacion tipica entre mediciones consecutivas de la campana es de 0.387 km (mediana) y 1.012 km (maxima). La posicion imputada queda por tanto dentro del mismo orden de magnitud que la resolucion espacial del muestreo, y no degrada la georreferenciacion del conjunto.

### Por que `017.txt` no se imputa

Su error de distancia es 17.3, muy por encima del resto de la campana (entre 0.7 y 2.0), pero **tiene fix**: entrega una coordenada real, solo que imprecisa. Sobrescribirla por interpolacion destruiria informacion valida. Se marca como degradada para que el dashboard pueda filtrarla, y su espectro se conserva integro porque la calidad de la medida de RF no depende del HDOP.

## 4. Ruta de la estacion movil

| Concepto | Valor |
|---|---|
| Recorrido total | 26.23 km |
| Latitud | 6.15801 a 6.24326 |
| Longitud | -75.60950 a -75.56914 |
| Altura | 1499.0 a 1589.6 m |
| Puntos de medicion | 61 |

El orden alfabetico de los archivos (`001` -> `061`) es tambien el orden cronologico del recorrido, lo que permite reconstruir la trayectoria como una polilinea sin necesidad de marca de tiempo.

![Recorrido de la estacion movil](graficas/04_ruta.png)

*Figura 4. Izquierda: trayectoria con la calidad del dato de cada punto. Derecha: el mismo recorrido coloreado por la potencia del canal mas contaminado.*

## 5. Incidencia de la temperatura del sensor

Rango observado: **42.81 a 50.41 C**.

| Correlacion de Pearson | r | Lectura |
|---|---|---|
| Temperatura vs orden de medicion | +0.859 | fuerte |
| Temperatura vs piso de ruido (p10) | +0.211 | debil |
| Temperatura vs error de distancia | -0.102 | despreciable |

**Conclusion.** La temperatura no es una variable ambiental sino el calentamiento progresivo del propio equipo: su correlacion dominante es con el orden de la medicion (r = +0.859), es decir, sube monotonamente a medida que avanza la campana. Su incidencia sobre la calidad del dato es **debil**: contra el piso de ruido da r = +0.211, lo que sugiere una leve elevacion del ruido termico del receptor conforme se calienta, pero no alcanza a comprometer las mediciones. Sobre la precision del GPS no tiene efecto alguno (r = -0.102). No se descarta ninguna medicion por temperatura.

![Incidencia de la temperatura](graficas/03_temperatura.png)

*Figura 3. Izquierda: la temperatura sube de forma monotona con el avance de la jornada. Derecha: su relacion con el piso de ruido es debil; el color indica el orden de la medicion.*

## 6. Ocupacion por canal (Parseval discreto)

### Metodo

Parseval establece que la energia de la senal es la suma de |X[k]|^2 sobre los bins de la FFT. Como el espectro viene en dBm por bin:

1. **dBm -> mW** (`10^(dBm/10)`). Los dB son logaritmos: promediarlos directamente da la media geometrica y subestima cualquier canal con picos.
2. **Sumar los 256 bins** del bloque de 5 MHz -> potencia total del canal.
3. **Dividir entre 256** -> potencia media de ocupacion.
4. **Volver a dBm** para comparar contra el umbral de -60 dBm.

### Por que no se promedian los dBm directamente

Los dB son logaritmos, y el promedio de logaritmos es la media **geometrica**, no la aritmetica. Aplicado a potencias eso subestima sistematicamente cualquier canal que tenga picos: un bin con mucha senal queda compensado por los bins en silencio, cuando fisicamente la potencia que llega a la antena es la **suma** de ambas y esta dominada por la fuerte.

El impacto sobre este dataset es medible: de las 244 combinaciones medicion-canal, **20 (8.2%) cambian de clasificacion** segun el metodo empleado. Todas en el mismo sentido: el promedio ingenuo las declara libres y Parseval las declara ocupadas.

Caso mas marcado, `009.txt` en el canal D (sus bins van de -76.0 a -27.4 dBm):

| Metodo | Resultado | Veredicto |
|---|---|---|
| Promedio directo de los 256 valores en dBm (incorrecto) | -64.84 dBm | libre |
| Parseval: a mW, sumar, promediar, volver a dBm (correcto) | -44.92 dBm | **ocupado** |
| **Diferencia** | **19.93 dB** | |

Esos 19.93 dB equivalen a un factor de 98 en potencia real. El canal contiene un pico de -27.4 dBm que el promedio logaritmico diluye entre los bins en silencio hasta hacerlo desaparecer bajo el umbral. Con el metodo incorrecto se le reportaria a la Agencia que esa banda esta disponible cuando no lo esta.

### Por que la potencia media y no la total

Sumar 256 bins agrega 24.08 dB (`10*log10(256)`) de offset puramente aritmetico: aparece por el hecho de sumar, no por energia presente en el aire. Ese offset desplaza a **todos** los canales por igual por encima del umbral, con lo que el criterio dejaria de discriminar.

Verificacion sobre `001.txt`:

| Canal | Potencia media | >-60 dBm | Potencia total | >-60 dBm |
|---|---|---|---|---|
| A | -68.07 dBm | **no** | -43.99 dBm | si |
| B | -66.65 dBm | **no** | -42.56 dBm | si |
| C | -38.50 dBm | si | -14.42 dBm | si |
| D | -67.27 dBm | **no** | -43.19 dBm | si |

Con la potencia media 1 de los 4 canales resulta ocupado; con la total, 4 de 4. La segunda lectura no informa nada utilizable para un plan de frecuencias. Por eso el umbral se evalua sobre la potencia media, aunque **ambas quedan registradas** en `indicadores.csv` (columnas `p_media_A..D` y `p_total_A..D`) para permitir verificar este mismo razonamiento.

### Resultados

| Canal | Banda | Potencia media global | Maximo | Mediciones ocupadas | % |
|---|---|---|---|---|---|
| **A** | 840 - 845 MHz | -46.38 dBm | -28.83 dBm | 15 / 61 | 24.6% |
| **B** | 845 - 850 MHz | -36.89 dBm | -26.05 dBm | 20 / 61 | 32.8% |
| **C** | 850 - 855 MHz | -28.15 dBm | -11.33 dBm | 53 / 61 | 86.9% |
| **D** | 855 - 860 MHz | -44.46 dBm | -28.50 dBm | 18 / 61 | 29.5% |

**Canal mas contaminado: C.** Con -28.15 dBm de potencia media esta 18.2 dB por encima del canal mas limpio y supera el umbral de ocupacion en el 86.9% del recorrido.

**Canal menos contaminado: A.** Potencia media de -46.38 dBm y solo 24.6% de mediciones por encima del umbral.

Orden de contaminacion, de mayor a menor: **C > B > D > A**.

![Comparacion de los cuatro canales](graficas/02_potencia_por_canal.png)

*Figura 2. Izquierda: potencia media por Parseval de cada canal frente al umbral. Derecha: dispersion de las 61 mediciones y porcentaje de puntos ocupados.*

## 7. Frecuencias extremas del sistema

| | Bin | Frecuencia | Potencia promedio |
|---|---|---|---|
| Mas contaminada | 600 | **851.7188 MHz** | -21.38 dBm |
| Menos contaminada | 214 | **844.1797 MHz** | -57.45 dBm |

Diferencia entre ambas: **36.07 dB**.

![Frecuencias extremas del sistema](graficas/01_frecuencias_extremas.png)

*Figura 1. Perfil promedio de la banda con ambas frecuencias senaladas y detalle ampliado de cada una. Generada por `graficas.py`.*

El maximo no es un bin aislado: alrededor de 851.7188 MHz hay un lobulo continuo de unos **645 kHz** dentro de los 3 dB del pico, compatible con una portadora real y no con un artefacto de la FFT (tramo contiguo 851.3867 - 852.0117 MHz).

### Descarte de artefactos del receptor

El USRP introduce un offset de DC en su frecuencia central, que en esta campana es 850 MHz (bin 512) y cae justo en la frontera entre los canales B y C. Se verifico ese bin: marca -36.97 dBm, alineado con sus vecinos inmediatos, **sin pico de DC**. La ocupacion elevada del canal C es por tanto energia real del aire y no un artefacto instrumental.

## 8. Recomendacion tecnica para la ANE

Con base en la potencia integrada por Parseval sobre 61 puntos de medicion en el occidente de Medellin:

- **No asignar el canal C (850 - 855 MHz).** Es el bloque mas contaminado de la banda: -28.15 dBm de potencia media y ocupacion en el 86.9% del recorrido. Cualquier asignacion nueva aqui enfrentaria interferencia co-canal en practicamente toda el area cubierta.
- **Priorizar el canal A (840 - 845 MHz).** Es el mas limpio: -46.38 dBm de potencia media y solo 24.6% de puntos por encima del umbral. Es la mejor opcion para un despliegue nuevo.
- **Evitar la vecindad de 851.7188 MHz** en cualquier plan de frecuencias: es la portadora dominante de toda la banda, 36.07 dB por encima del punto mas limpio del espectro.
- **Reservar 844.1797 MHz como referencia de piso de ruido** para futuras campanas de monitoreo en el sector.

### Limitaciones del estudio

- La campana cubre 26.23 km del sector occidental; los resultados no son extrapolables al resto del Valle de Aburra sin mediciones adicionales.
- Las mediciones son instantaneas a lo largo de un recorrido, no un monitoreo continuo: no capturan variacion horaria de la ocupacion.
- `017.txt` aporta espectro valido pero su posicion tiene un error de distancia de 17.3 y no debe usarse para inferencias geograficas finas.

## 9. Bonificacion: estimacion del origen de la contaminacion

Se busca ubicar geograficamente la fuente que contamina cada banda. Se aplicaron dos metodos y se reportan ambos, porque el primero -el rigurosamente correcto si existiera un unico emisor- resulta no concluyente, y ese resultado negativo es en si mismo un hallazgo tecnico.

### Metodo 1: inversion log-distancia (trilateracion)

La potencia recibida de un emisor fijo decae segun el modelo log-distancia:

```
P(d) = P0 - 10 * n * log10(d)   =>   P = P0 + n * x,  x = -10*log10(d)
```

donde `n` es el exponente de perdida de trayecto (2 en espacio libre, 2.7 a 4 en entorno urbano). Conocida la posicion del emisor, la nube de puntos (x, P) deberia formar una recta. Como no se conoce, se invierte el problema: se barre una malla de 25600 candidatos sobre el area y para cada uno se ajusta esa recta por minimos cuadrados. El candidato con mayor R^2 y pendiente fisicamente plausible seria la posicion del emisor.

| Canal | R^2 del mejor ajuste | n ajustado | n sin restringir | Resultado |
|---|---|---|---|---|
| A | 0.133 | 1.55 | +1.15 | **no concluyente** |
| B | 0.154 | 1.73 | +1.73 | **no concluyente** |
| C | 0.095 | 1.54 | -2.06 | **no concluyente** |
| D | 0.139 | 1.63 | +1.42 | **no concluyente** |

**El metodo falla en los cuatro canales.** El criterio principal es el ajuste: el mejor R^2 obtenido es 0.154, muy por debajo del 0.60 exigido para aceptar una localizacion. Con ajustes tan pobres, la coordenada que devuelve el optimizador no tiene significado fisico.

La cuarta columna refuerza el diagnostico. Al liberar la restriccion sobre la pendiente:

- En canal C el optimo global tiene pendiente **negativa** (-2.06), lo que implicaria que la potencia *aumenta* con la distancia al punto hallado. Es imposible para una fuente: el optimizador esta localizando un minimo de campo, no un emisor.
- En canal A, canal D la pendiente cae por debajo de 1.5 (+1.15, +1.42), es decir una atenuacion mas lenta que en espacio libre. Tampoco describe propagacion real desde un emisor.
- En canal B la pendiente si es fisicamente admisible (1.73), pero el ajuste sigue siendo demasiado debil (R^2 = 0.154) para sostener una localizacion.

Causas identificadas:

1. **No hay un emisor, hay decenas.** Una banda celular la sirven multiples estaciones base repartidas por la ciudad; el campo agregado no decae desde un punto unico.
2. **La geometria del muestreo es degenerada.** El recorrido es practicamente un corredor lineal a lo largo del valle, y para trilaterar se requiere observar la fuente desde angulos diversos.
3. **El sombreado urbano domina la senal de distancia.** La potencia de un mismo canal varia mas de 61 dB entre los puntos del recorrido, un rango atribuible a edificaciones y topografia que enmascara por completo la atenuacion por distancia.

### Metodo 2: centroide ponderado del decil superior

Ante la falla del metodo 1 se adopta un estimador mas modesto pero sostenible: se toman las 6 mediciones del decil de mayor potencia de cada canal y se calcula su centroide ponderado por potencia en escala **lineal** (mW), no en dBm, por la misma razon expuesta en la seccion 6.

Esto **no localiza un transmisor**: delimita la zona de maxima incidencia, es decir hacia donde se concentra la energia dominante vista desde el corredor recorrido.

| Canal | Centro estimado | Radio medio | Radio maximo | Separacion al decil debil | Dinamica |
|---|---|---|---|---|---|
| **A** | 6.20005, -75.58513 | 3.02 km | 4.56 km | 2.03 km | 61.4 dB |
| **B** | 6.18115, -75.59215 | 2.07 km | 3.57 km | 3.69 km | 64.3 dB |
| **C** | 6.19512, -75.58824 | 3.00 km | 3.97 km | 3.19 km | 61.3 dB |
| **D** | 6.19471, -75.58622 | 2.56 km | 4.03 km | 2.23 km | 61.9 dB |

**Validacion.** Para descartar que el centroide sea un artefacto del promedio se calculo tambien el centroide del decil *inferior* de cada canal. Ambos quedan separados entre 2.03 y 3.69 km, lo que confirma la existencia de un gradiente espacial real: las mediciones fuertes y las debiles no estan mezcladas, ocupan zonas distintas del recorrido.

Las cuatro zonas convergen en un area comun del sur del corredor (latitud 6.181 a 6.200, longitud -75.592 a -75.585), lo que sugiere un foco de emision compartido para toda la banda antes que emisores independientes por canal.

### Alcance de la estimacion

Los radios obtenidos (2.07 a 3.02 km) no son un margen de error instrumental sino la dispersion real de las mediciones que sustentan cada centro. Se reportan explicitamente para que la Agencia no interprete estas coordenadas como una localizacion puntual. Para localizar emisores con precision util se requeriria una campana con geometria de muestreo bidimensional y, preferiblemente, antena directiva con medicion de azimut.

Los resultados completos, incluidos los diagnosticos del metodo 1, quedan en `salida/fuentes_estimadas.csv`.

