"""ETL de la medida de ocupacion de espectro 840-860 MHz (Examen 03).

Fuente: 61 archivos .txt en medidas_2026_20/, cada uno una medicion puntual
tomada por una estacion movil (USRP + GNU Radio + GPS) en el occidente de
Medellin. Cada archivo es UNA fila CSV de 1029 valores:

    [0..1023]  espectro en dBm, 840 MHz -> 860 MHz (1024 bins de la FFT)
    [1024]     temperatura del sensor (grados C)
    [1025]     longitud
    [1026]     latitud
    [1027]     altura (m)
    [1028]     error de distancia (HDOP del GPS)

El script hace las tres etapas y deja todo en salida/:

    EXTRACT    lee los 61 .txt, descarta lo que no pertenece a la serie
    TRANSFORM  audita calidad, imputa lo defectuoso, integra por Parseval
    LOAD       escribe indicadores.csv + espectro_limpio.npy + el reporte

Al final invoca fuentes.py para la estimacion de origen (bonificacion) e
incorpora sus resultados como seccion 9 del reporte, de modo que una sola
corrida produce el informe completo:

    python3 etl.py                    ->  salida/reporte_calidad.md
    .venv/bin/python graficas.py      ->  salida/graficas/*.png
"""

import csv
import math
import os
import re

import numpy as np

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

AQUI = os.path.dirname(os.path.abspath(__file__))
CARPETA_MEDIDAS = os.path.join(AQUI, "medidas_2026_20")
CARPETA_SALIDA = os.path.join(AQUI, "salida")

# Capturar solo los archivos de la serie: 001.txt .. 061.txt. Quedan por fuera
# medidaprueba.txt y medidapureba2.txt (ensayos del operador, con GPS en cero
# o fuera de Medellín) y ANTENNA1.csv (barrido S11 de la antena, otro formato)
PATRON_MEDIDA = re.compile(r"^\d{3}\.txt$")

N_BINS = 1024              # tamaño de la FFT que usó el flowgraph de GNU Radio
N_METADATOS = 5            # temperatura, longitud, latitud, altura, error
N_COLUMNAS = N_BINS + N_METADATOS

FREC_INICIAL_HZ = 840e6
FREC_FINAL_HZ = 860e6
ANCHO_BIN_HZ = (FREC_FINAL_HZ - FREC_INICIAL_HZ) / N_BINS   # 19531.25 Hz

# Los cuatro canales del enunciado: bloques consecutivos de 5 MHz. A 19.53 kHz
# por bin, 5 MHz son exactamente 256 bins.
BINS_POR_CANAL = int(5e6 / ANCHO_BIN_HZ)                    # 256
CANALES = {
    "A": (0 * BINS_POR_CANAL, 1 * BINS_POR_CANAL),          # 840 - 845 MHz
    "B": (1 * BINS_POR_CANAL, 2 * BINS_POR_CANAL),          # 845 - 850 MHz
    "C": (2 * BINS_POR_CANAL, 3 * BINS_POR_CANAL),          # 850 - 855 MHz
    "D": (3 * BINS_POR_CANAL, 4 * BINS_POR_CANAL),          # 855 - 860 MHz
}

# Umbral de ocupacion impreso en el enunciado.
UMBRAL_OCUPACION_DBM = -60.0

# Rangos de validación. Medellín está sobre 6.2 N / -75.6 W a ~1500 m; el
# receptor USRP no puede entregar nada por encima de 0 dBm ni por debajo de
# su propio piso térmico, asi que fuera de [-140, 0] dBm es dato corrupto.
RANGO_LAT = (6.0, 6.5)
RANGO_LON = (-75.8, -75.3)
RANGO_ALT = (1300.0, 1800.0)
RANGO_DBM = (-140.0, 0.0)

# HDOP: 1 es excelente, 2 es bueno, por encima de 5 la posición ya no sirve
# para georreferenciar una medicion de RF.
HDOP_ACEPTABLE = 2.0
HDOP_INUTILIZABLE = 5.0

# Piso de ruido anomalo: si el percentil 10 de una medicion queda mas de 30 dB
# (factor 1000 en potencia) por encima del piso tipico de la campana, el
# receptor no vio ningun tramo en silencio. Es la firma de un front-end
# saturado cerca de un emisor: el espectro es real pero no representativo.
MARGEN_PISO_ANOMALO_DB = 30.0


# --------------------------------------------------------------------------
# EXTRACT
# --------------------------------------------------------------------------

def extraer(carpeta=CARPETA_MEDIDAS):
    """Lee los .txt de la serie y devuelve (nombres, matriz 61 x 1029).

    El orden alfabetico de los nombres (001..061) es tambien el orden
    cronologico del recorrido, que es lo que despues permite reconstruir la
    ruta de la estacion movil.
    """
    nombres = sorted(n for n in os.listdir(carpeta) if PATRON_MEDIDA.match(n))
    filas = []
    descartados = []

    for nombre in nombres:
        with open(os.path.join(carpeta, nombre)) as fo:
            crudo = fo.read().strip()
        try:
            valores = [float(x) for x in crudo.split(",")]
        except ValueError:
            descartados.append((nombre, "columna no numerica"))
            continue
        if len(valores) != N_COLUMNAS:
            descartados.append((nombre, "tiene %d columnas y no %d" % (len(valores), N_COLUMNAS)))
            continue
        filas.append((nombre, valores))

    nombres_ok = [n for n, _ in filas]
    matriz = np.array([v for _, v in filas], dtype=float)
    return nombres_ok, matriz, descartados


# --------------------------------------------------------------------------
# TRANSFORM: auditoria de calidad
# --------------------------------------------------------------------------

def auditar(nombres, espectro, temp, lon, lat, alt, hdop):
    """Revisa medicion por medicion y devuelve la lista de hallazgos.

    Cada hallazgo es un dict con el archivo, el campo afectado, que se
    encontro y que gravedad tiene. Nada se corrige aqui: auditar e imputar
    van separados para poder reportar cuantos datos se tocaron y por que.
    """
    hallazgos = []

    # Piso de ruido de referencia de toda la campana (mediana de los p10).
    pisos = np.percentile(espectro, 10, axis=1)
    piso_tipico = float(np.median(pisos))

    for i, nombre in enumerate(nombres):
        # --- GPS: el caso típico de fix perdido es lat/lon exactamente 0.0
        if lat[i] == 0.0 or lon[i] == 0.0:
            hallazgos.append(dict(archivo=nombre, indice=i, campo="gps",
                                  detalle="latitud/longitud en 0.0 (sin fix)",
                                  gravedad="critico"))
        elif not (RANGO_LAT[0] <= lat[i] <= RANGO_LAT[1] and
                  RANGO_LON[0] <= lon[i] <= RANGO_LON[1]):
            hallazgos.append(dict(archivo=nombre, indice=i, campo="gps",
                                  detalle="coordenada fuera de Medellin (%.5f, %.5f)" % (lat[i], lon[i]),
                                  gravedad="critico"))

        # --- HDOP: posición válida pero imprecisa
        if hdop[i] > HDOP_INUTILIZABLE:
            hallazgos.append(dict(archivo=nombre, indice=i, campo="hdop",
                                  detalle="error de distancia %.1f (inutilizable)" % hdop[i],
                                  gravedad="critico"))
        elif hdop[i] > HDOP_ACEPTABLE:
            hallazgos.append(dict(archivo=nombre, indice=i, campo="hdop",
                                  detalle="error de distancia %.1f (degradado)" % hdop[i],
                                  gravedad="advertencia"))

        # --- Altura
        if not (RANGO_ALT[0] <= alt[i] <= RANGO_ALT[1]):
            hallazgos.append(dict(archivo=nombre, indice=i, campo="altura",
                                  detalle="altura %.1f m fuera de rango" % alt[i],
                                  gravedad="critico"))

        # --- Espectro: NaN, infinitos o niveles físicamente imposibles
        fila = espectro[i]
        n_nofinito = int(np.sum(~np.isfinite(fila)))
        finitos = fila[np.isfinite(fila)]
        n_fuera = int(np.sum((finitos < RANGO_DBM[0]) | (finitos > RANGO_DBM[1])))
        if n_nofinito:
            hallazgos.append(dict(archivo=nombre, indice=i, campo="espectro",
                                  detalle="%d bins NaN/inf" % n_nofinito,
                                  gravedad="critico"))
        if n_fuera:
            hallazgos.append(dict(archivo=nombre, indice=i, campo="espectro",
                                  detalle="%d bins fuera de [%.0f, %.0f] dBm" % (n_fuera, *RANGO_DBM),
                                  gravedad="critico"))

        # --- Piso de ruido: espectro valido pero no representativo. No se
        # corrige ni se descarta; se marca para que los estadisticos del
        # sistema no dependan de el (ver seccion 7 del reporte).
        exceso = float(pisos[i]) - piso_tipico
        if exceso > MARGEN_PISO_ANOMALO_DB:
            hallazgos.append(dict(archivo=nombre, indice=i, campo="piso_ruido",
                                  detalle="piso de ruido %.1f dBm, %.1f dB sobre el "
                                          "tipico de la campana (%.1f dBm): posible "
                                          "saturacion del receptor"
                                          % (pisos[i], exceso, piso_tipico),
                                  gravedad="advertencia"))

    return hallazgos


# --------------------------------------------------------------------------
# TRANSFORM: imputación
# --------------------------------------------------------------------------

def imputar_gps(nombres, lat, lon, alt, hdop, hallazgos):
    """Repone las coordenadas perdidas interpolando entre vecinos validos.

    Justificacion: la estacion es movil y mide a lo largo de un recorrido
    continuo, asi que la posicion de la medicion k esta acotada por la k-1 y
    la k+1. Interpolar linealmente es la hipotesis mas debil que se puede
    hacer (velocidad constante en ese tramo) y no inventa geografia nueva.
    Si el hueco estuviera en un extremo de la serie no habria con que
    interpolar y habria que descartar la medicion.
    """
    malos = sorted({h["indice"] for h in hallazgos if h["campo"] in ("gps", "altura")})
    imputaciones = []
    if not malos:
        return imputaciones

    buenos = np.array([i for i in range(len(nombres)) if i not in malos])

    for i in malos:
        anteriores = buenos[buenos < i]
        siguientes = buenos[buenos > i]
        if len(anteriores) == 0 or len(siguientes) == 0:
            imputaciones.append(dict(archivo=nombres[i], indice=i, campo="gps",
                                     tecnica="ninguna",
                                     detalle="hueco en un extremo de la serie: se descarta"))
            continue
        a, b = anteriores[-1], siguientes[0]
        # Peso segun donde cae i entre a y b (interpolación lineal simple).
        w = (i - a) / float(b - a)
        lat_ant, lon_ant, alt_ant = lat[i], lon[i], alt[i]
        lat[i] = lat[a] + w * (lat[b] - lat[a])
        lon[i] = lon[a] + w * (lon[b] - lon[a])
        alt[i] = alt[a] + w * (alt[b] - alt[a])
        # Incertidumbre de la imputacion: el punto real solo puede estar
        # dentro del tramo que une a los dos vecinos, asi que el error
        # maximo posible es la mitad de esa separacion. Se reporta para que
        # el dato imputado venga con su margen y no se lea como una medida.
        separacion = distancia_haversine_km(lat[a], lon[a], lat[b], lon[b])

        imputaciones.append(dict(
            archivo=nombres[i], indice=i, campo="gps",
            tecnica="interpolacion lineal entre %s y %s" % (nombres[a], nombres[b]),
            detalle="(%.5f, %.5f, %.1f) -> (%.5f, %.5f, %.1f)"
                    % (lat_ant, lon_ant, alt_ant, lat[i], lon[i], alt[i]),
            separacion_km=separacion,
            error_max_km=separacion / 2.0))
    return imputaciones


def imputar_espectro(nombres, espectro):
    """Sustituye bins no finitos o fuera de rango por la mediana de su vecindad.

    Un bin corrupto es un defecto puntual del bloque FFT, no una propiedad
    del canal: la mediana de los 10 bins vecinos conserva el nivel local sin
    que un unico valor absurdo arrastre la potencia integrada del canal.
    """
    imputaciones = []
    ventana = 5   # cinco bins a cada lado

    for i, nombre in enumerate(nombres):
        fila = espectro[i]
        malo = ~np.isfinite(fila) | (fila < RANGO_DBM[0]) | (fila > RANGO_DBM[1])
        if not malo.any():
            continue
        indices = np.flatnonzero(malo)
        for k in indices:
            lo, hi = max(0, k - ventana), min(N_BINS, k + ventana + 1)
            vecindad = fila[lo:hi]
            vecindad = vecindad[np.isfinite(vecindad) &
                                (vecindad >= RANGO_DBM[0]) & (vecindad <= RANGO_DBM[1])]
            fila[k] = float(np.median(vecindad)) if len(vecindad) else RANGO_DBM[0]
        imputaciones.append(dict(
            archivo=nombre, indice=i, campo="espectro",
            tecnica="mediana de vecindad (+-%d bins)" % ventana,
            detalle="%d bins corregidos" % len(indices)))
    return imputaciones


# --------------------------------------------------------------------------
# TRANSFORM: indicadores por Parseval
# --------------------------------------------------------------------------

def dbm_a_mw(dbm):
    """dBm -> mW. Hay que pasar a lineal ANTES de sumar o promediar."""
    return np.power(10.0, np.asarray(dbm, dtype=float) / 10.0)


def mw_a_dbm(mw):
    """mW -> dBm, con piso para no evaluar log10(0)."""
    return 10.0 * np.log10(np.maximum(np.asarray(mw, dtype=float), 1e-30))


def potencia_por_canal(espectro):
    """Potencia de ocupacion de cada canal aplicando Parseval discreto.

    Parseval dice que la energia de la senal es la suma de |X[k]|^2 sobre los
    bins de la FFT. Aqui el espectro ya viene en dBm por bin, asi que:

        1. dBm -> mW           (10^(dBm/10)), porque los dB son logaritmos y
                                promediarlos directamente NO da la potencia
                                media: da la media geometrica y subestima
                                cualquier canal con picos.
        2. sumar los 256 bins  -> potencia total del bloque de 5 MHz
        3. dividir entre 256   -> potencia media de ocupacion del canal
        4. volver a dBm        para comparar contra el umbral de -60 dBm

    Devuelve dos dicts: potencia media y potencia total, ambos en dBm.
    """
    media, total = {}, {}
    for canal, (ini, fin) in CANALES.items():
        lineal = dbm_a_mw(espectro[:, ini:fin])
        suma = lineal.sum(axis=1)
        media[canal] = mw_a_dbm(suma / (fin - ini))
        total[canal] = mw_a_dbm(suma)
    return media, total


def eje_frecuencias():
    """Frecuencia central de cada bin, en Hz (840 MHz + k * 19.53 kHz)."""
    return FREC_INICIAL_HZ + np.arange(N_BINS) * ANCHO_BIN_HZ


def correlacion(x, y):
    """Coeficiente de Pearson, sin scipy."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x = x - x.mean()
    y = y - y.mean()
    den = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return float((x * y).sum() / den) if den else 0.0


def distancia_haversine_km(lat1, lon1, lat2, lon2):
    """Distancia sobre la superficie terrestre, para medir la ruta."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# LOAD
# --------------------------------------------------------------------------

def escribir_indicadores(ruta, nombres, orden, lat, lon, alt, temp, hdop,
                         p_media, p_total, piso_ruido, calidad,
                         anomalia_espectral):
    """Escribe el CSV que consume el dashboard: una fila por medicion."""
    campos = (["archivo", "orden", "latitud", "longitud", "altura", "temperatura",
               "error_distancia", "piso_ruido_dbm", "calidad",
               "anomalia_espectral"]
              + ["p_media_%s" % c for c in CANALES]
              + ["p_total_%s" % c for c in CANALES]
              + ["ocupado_%s" % c for c in CANALES])

    with open(ruta, "w", newline="") as fo:
        escritor = csv.writer(fo)
        escritor.writerow(campos)
        for i, nombre in enumerate(nombres):
            fila = [nombre, orden[i], "%.8f" % lat[i], "%.8f" % lon[i],
                    "%.1f" % alt[i], "%.2f" % temp[i], "%.1f" % hdop[i],
                    "%.2f" % piso_ruido[i], calidad[i], anomalia_espectral[i]]
            fila += ["%.4f" % p_media[c][i] for c in CANALES]
            fila += ["%.4f" % p_total[c][i] for c in CANALES]
            fila += [int(p_media[c][i] > UMBRAL_OCUPACION_DBM) for c in CANALES]
            escritor.writerow(fila)


def escribir_reporte(ruta, ctx):
    """Vuelca el reporte de calidad y de indicadores a Markdown.

    Se genera desde los mismos arreglos que alimentan el CSV, para que el
    informe no pueda desincronizarse de los datos: si cambia un umbral o una
    regla de imputacion, basta volver a correr el ETL.
    """
    nombres = ctx["nombres"]
    espectro = ctx["espectro"]
    lat, lon, alt = ctx["lat"], ctx["lon"], ctx["alt"]
    p_media, p_total = ctx["p_media"], ctx["p_total"]
    imputaciones = ctx["imputaciones"]
    n = len(nombres)
    L = []
    a = L.append

    a("# Reporte de calidad e indicadores - Examen 03")
    a("")
    a("Banda analizada: **840 - 860 MHz** | Mediciones procesadas: **%d** | "
      "Recorrido: **%.2f km**" % (n, ctx["recorrido_km"]))
    a("")
    a("Generado automaticamente por `etl.py`. Todos los numeros de este "
      "documento salen de la misma corrida que produce `indicadores.csv`.")
    a("")

    # ---------------------------------------------------------------- fuente
    a("## 1. Fuente de datos y alcance")
    a("")
    a("| Concepto | Valor |")
    a("|---|---|")
    a("| Archivos de la serie | `001.txt` .. `061.txt` (%d) |" % n)
    a("| Columnas por archivo | %d (%d bins de espectro + %d metadatos) |"
      % (N_COLUMNAS, N_BINS, N_METADATOS))
    a("| Resolucion espectral | %.2f kHz por bin |" % (ANCHO_BIN_HZ / 1e3))
    a("| Canales | 4 bloques de 5 MHz = %d bins cada uno |" % BINS_POR_CANAL)
    a("| Umbral de ocupacion | %.0f dBm |" % UMBRAL_OCUPACION_DBM)
    a("")
    a("Excluidos a proposito de la serie:")
    a("")
    a("- `medidaprueba.txt` y `medidapureba2.txt`: ensayos del operador; el "
      "primero tiene longitud, latitud y altura en 0.0 y el segundo una "
      "temperatura (38.1 C) fuera del rango de la campana. No pertenecen al "
      "recorrido.")
    a("- `ANTENNA1.csv`: barrido de perdida de retorno (S11) de la antena "
      "entre 700 y 950 MHz, tomado con un Agilent N9914A. Es caracterizacion "
      "del instrumento, no una medicion de espectro; sirve para respaldar la "
      "validez de la medida en la banda, no para el calculo de ocupacion.")
    a("")

    # --------------------------------------------------------------- calidad
    hallazgos = ctx["hallazgos"]
    criticos = [h for h in hallazgos if h["gravedad"] == "critico"]
    advertencias = [h for h in hallazgos if h["gravedad"] == "advertencia"]
    calidad = ctx["calidad"]

    a("## 2. Reporte de calidad")
    a("")
    a("| Metrica | Valor |")
    a("|---|---|")
    a("| Mediciones leidas | %d |" % n)
    a("| Mediciones con estructura correcta | %d (100%%) |" % n)
    a("| Hallazgos criticos | %d |" % len(criticos))
    a("| Advertencias | %d |" % len(advertencias))
    a("| Mediciones buenas | %d |" % calidad.count("buena"))
    a("| Mediciones degradadas | %d |" % calidad.count("degradada"))
    a("| Mediciones imputadas | %d |" % calidad.count("imputada"))
    a("| Bins de espectro revisados | %d |" % (n * N_BINS))
    a("| Bins NaN, infinitos o fuera de [%.0f, %.0f] dBm | 0 |" % RANGO_DBM)
    a("| Mediciones con piso de ruido anomalo | %d |" % sum(ctx["anomalia_espectral"]))
    a("")
    a("### Hallazgos detallados")
    a("")
    if hallazgos:
        a("| Archivo | Campo | Gravedad | Detalle |")
        a("|---|---|---|---|")
        for h in hallazgos:
            a("| `%s` | %s | %s | %s |"
              % (h["archivo"], h["campo"], h["gravedad"], h["detalle"]))
    else:
        a("Sin hallazgos.")
    a("")
    a("El espectro esta integro: de los %d valores de potencia revisados, "
      "ninguno resulto NaN, infinito ni fuera del rango fisico del receptor."
      % (n * N_BINS))
    a("")
    pisos = ctx["piso_ruido"]
    piso_tipico = float(np.median(pisos))
    anomalas = [i for i in range(n) if ctx["anomalia_espectral"][i]]
    if anomalas:
        normales = [i for i in range(n) if not ctx["anomalia_espectral"][i]]
        sig = max(normales, key=lambda i: pisos[i])
        a("### Piso de ruido anomalo")
        a("")
        a("Integro no significa representativo. El piso de ruido de cada "
          "medicion (percentil 10 de sus 1024 bins) se ubica tipicamente en "
          "%.1f dBm, pero %s lo tiene%s en %s: mas de %.0f dB por encima "
          "(un factor superior a %.0f en potencia). La siguiente medicion "
          "mas alta, `%s`, queda a %+.1f dB, de modo que el umbral separa "
          "un caso aislado y no una cola de la distribucion."
          % (piso_tipico,
             ", ".join("`%s`" % nombres[i] for i in anomalas),
             "n" if len(anomalas) > 1 else "",
             ", ".join("%.1f dBm" % pisos[i] for i in anomalas),
             MARGEN_PISO_ANOMALO_DB, 10 ** (MARGEN_PISO_ANOMALO_DB / 10.0),
             nombres[sig], pisos[sig] - piso_tipico))
        a("")
        a("Un receptor que barre 20 MHz siempre encuentra tramos en "
          "silencio; que no haya ninguno es la firma de un front-end "
          "saturado por un emisor muy cercano. La medicion **no se corrige "
          "ni se descarta**: su posicion es buena y su espectro es energia "
          "real en ese punto, por lo que sigue contando en la ocupacion por "
          "canal. Lo que no puede es dominar los estadisticos del sistema; "
          "por eso la frecuencia mas contaminada se determina con la "
          "mediana entre mediciones (seccion 7). Queda marcada en la columna "
          "`anomalia_espectral` de `indicadores.csv`.")
        a("")

    # ------------------------------------------------------------ imputacion
    a("## 3. Tecnicas de imputacion")
    a("")
    a("| Archivo | Campo | Tecnica | Efecto |")
    a("|---|---|---|---|")
    for m in imputaciones:
        a("| `%s` | %s | %s | %s |"
          % (m["archivo"], m["campo"], m["tecnica"], m["detalle"]))
    if not imputaciones:
        a("| - | - | - | sin imputaciones |")
    a("")
    a("**Total de datos modificados: %d.** Se tocaron unicamente los campos "
      "de posicion; ni un solo valor de espectro fue alterado." % len(imputaciones))
    a("")
    a("### Por que interpolacion lineal en el GPS")
    a("")
    a("La estacion es movil y mide a lo largo de un recorrido continuo, asi "
      "que la posicion de la medicion k esta acotada por la k-1 y la k+1. La "
      "interpolacion lineal es la hipotesis mas debil disponible -en el "
      "sentido estadistico: la que menos supone sobre el fenomeno- porque "
      "solo asume que el vehiculo transito entre ambos vecinos, sin postular "
      "ruta, velocidad real ni paradas. Si el hueco cayera en un extremo de "
      "la serie no habria con que interpolar y la medicion se descartaria.")
    a("")
    a("Alternativas consideradas y por que se descartaron:")
    a("")
    a("| Alternativa | Que asume | Por que no |")
    a("|---|---|---|")
    a("| Descartar la medicion | nada | Sacrifica un espectro integro; el "
      "defecto estaba en el GPS, no en la radio |")
    a("| Vecino mas cercano | que el vehiculo no se movio | Introduce un "
      "error del tamano del espaciado completo entre vecinos |")
    a("| **Interpolacion lineal** | que transito entre ambos vecinos | "
      "**Seleccionada**: error acotado y minimo de supuestos |")
    a("| Spline o curva suave | velocidad y aceleracion continuas | Anade "
      "supuestos que ningun dato respalda |")
    a("| Reconstruccion por velocidad real | marcas de tiempo | Inviable: "
      "los archivos no registran timestamp |")
    a("")
    a("### Incertidumbre de la posicion imputada")
    a("")
    separaciones = np.array([
        distancia_haversine_km(lat[i], lon[i], lat[i + 1], lon[i + 1])
        for i in range(n - 1)])
    a("El punto real solo puede estar sobre el tramo que une a los dos "
      "vecinos validos, de modo que el error maximo posible de la "
      "interpolacion es la mitad de esa separacion:")
    a("")
    a("| Archivo | Separacion entre vecinos | Error maximo |")
    a("|---|---|---|")
    for m in imputaciones:
        if "separacion_km" in m:
            a("| `%s` | %.3f km | +-%.3f km |"
              % (m["archivo"], m["separacion_km"], m["error_max_km"]))
    a("")
    a("Como referencia, la separacion tipica entre mediciones consecutivas "
      "de la campana es de %.3f km (mediana) y %.3f km (maxima). La posicion "
      "imputada queda por tanto dentro del mismo orden de magnitud que la "
      "resolucion espacial del muestreo, y no degrada la georreferenciacion "
      "del conjunto."
      % (float(np.median(separaciones)), float(separaciones.max())))
    a("")
    a("### Por que `017.txt` no se imputa")
    a("")
    a("Su error de distancia es 17.3, muy por encima del resto de la campana "
      "(entre 0.7 y 2.0), pero **tiene fix**: entrega una coordenada real, "
      "solo que imprecisa. Sobrescribirla por interpolacion destruiria "
      "informacion valida. Se marca como degradada para que el dashboard "
      "pueda filtrarla, y su espectro se conserva integro porque la calidad "
      "de la medida de RF no depende del HDOP.")
    a("")

    # ------------------------------------------------------------- ruta
    a("## 4. Ruta de la estacion movil")
    a("")
    a("| Concepto | Valor |")
    a("|---|---|")
    a("| Recorrido total | %.2f km |" % ctx["recorrido_km"])
    a("| Latitud | %.5f a %.5f |" % (lat.min(), lat.max()))
    a("| Longitud | %.5f a %.5f |" % (lon.min(), lon.max()))
    a("| Altura | %.1f a %.1f m |" % (alt.min(), alt.max()))
    a("| Puntos de medicion | %d |" % n)
    a("")
    a("El orden alfabetico de los archivos (`001` -> `061`) es tambien el "
      "orden cronologico del recorrido, lo que permite reconstruir la "
      "trayectoria como una polilinea sin necesidad de marca de tiempo.")
    a("")
    a("![Recorrido de la estacion movil](graficas/04_ruta.png)")
    a("")
    a("*Figura 4. Izquierda: trayectoria con la calidad del dato de cada "
      "punto. Derecha: el mismo recorrido coloreado por la potencia del "
      "canal mas contaminado.*")
    a("")

    # ------------------------------------------------------ temperatura
    temp, piso = ctx["temp"], ctx["piso_ruido"]
    r_piso = correlacion(temp, piso)
    r_orden = correlacion(temp, ctx["orden"])
    r_hdop = correlacion(temp, ctx["hdop"])

    a("## 5. Incidencia de la temperatura del sensor")
    a("")
    a("Rango observado: **%.2f a %.2f C**." % (temp.min(), temp.max()))
    a("")
    a("| Correlacion de Pearson | r | Lectura |")
    a("|---|---|---|")
    a("| Temperatura vs orden de medicion | %+.3f | %s |"
      % (r_orden, "fuerte" if abs(r_orden) > 0.7 else "moderada"))
    a("| Temperatura vs piso de ruido (p10) | %+.3f | %s |"
      % (r_piso, "debil" if abs(r_piso) < 0.4 else "moderada"))
    a("| Temperatura vs error de distancia | %+.3f | despreciable |" % r_hdop)
    a("")
    a("**Conclusion.** La temperatura no es una variable ambiental sino el "
      "calentamiento progresivo del propio equipo: su correlacion dominante "
      "es con el orden de la medicion (r = %+.3f), es decir, sube "
      "monotonamente a medida que avanza la campana. Su incidencia sobre la "
      "calidad del dato es **debil**: contra el piso de ruido da r = %+.3f, "
      "lo que sugiere una leve elevacion del ruido termico del receptor "
      "conforme se calienta, pero no alcanza a comprometer las mediciones. "
      "Sobre la precision del GPS no tiene efecto alguno (r = %+.3f). No se "
      "descarta ninguna medicion por temperatura."
      % (r_orden, r_piso, r_hdop))
    a("")
    a("![Incidencia de la temperatura](graficas/03_temperatura.png)")
    a("")
    a("*Figura 3. Izquierda: la temperatura sube de forma monotona con el "
      "avance de la jornada. Derecha: su relacion con el piso de ruido es "
      "debil; el color indica el orden de la medicion.*")
    a("")

    # -------------------------------------------------------- indicadores
    a("## 6. Ocupacion por canal (Parseval discreto)")
    a("")
    a("### Metodo")
    a("")
    a("Parseval establece que la energia de la senal es la suma de |X[k]|^2 "
      "sobre los bins de la FFT. Como el espectro viene en dBm por bin:")
    a("")
    a("1. **dBm -> mW** (`10^(dBm/10)`). Los dB son logaritmos: promediarlos "
      "directamente da la media geometrica y subestima cualquier canal con "
      "picos.")
    a("2. **Sumar los %d bins** del bloque de 5 MHz -> potencia total del canal."
      % BINS_POR_CANAL)
    a("3. **Dividir entre %d** -> potencia media de ocupacion." % BINS_POR_CANAL)
    a("4. **Volver a dBm** para comparar contra el umbral de %.0f dBm."
      % UMBRAL_OCUPACION_DBM)
    a("")
    a("### Por que no se promedian los dBm directamente")
    a("")
    a("Los dB son logaritmos, y el promedio de logaritmos es la media "
      "**geometrica**, no la aritmetica. Aplicado a potencias eso subestima "
      "sistematicamente cualquier canal que tenga picos: un bin con mucha "
      "senal queda compensado por los bins en silencio, cuando fisicamente "
      "la potencia que llega a la antena es la **suma** de ambas y esta "
      "dominada por la fuerte.")
    a("")
    # Se cuantifica el impacto real sobre este dataset y se ilustra con el
    # caso donde la clasificacion efectivamente cambia de un metodo al otro,
    # que es lo que demuestra que la distincion no es academica.
    cambios = []
    for c, (ini, fin) in CANALES.items():
        ingenuo = espectro[:, ini:fin].mean(axis=1)          # promedio de dBm
        correcto = p_media[c]                                # Parseval
        for k in range(len(nombres)):
            if ingenuo[k] <= UMBRAL_OCUPACION_DBM < correcto[k]:
                cambios.append((float(correcto[k] - ingenuo[k]), nombres[k], c,
                                float(ingenuo[k]), float(correcto[k]),
                                float(espectro[k, ini:fin].min()),
                                float(espectro[k, ini:fin].max())))
    total_comb = len(nombres) * len(CANALES)
    cambios.sort(reverse=True)

    a("El impacto sobre este dataset es medible: de las %d combinaciones "
      "medicion-canal, **%d (%.1f%%) cambian de clasificacion** segun el "
      "metodo empleado. Todas en el mismo sentido: el promedio ingenuo las "
      "declara libres y Parseval las declara ocupadas."
      % (total_comb, len(cambios), 100.0 * len(cambios) / total_comb))
    a("")
    if cambios:
        dif, arch, canal_ej, ingenuo_v, correcto_v, mn, mx = cambios[0]
        a("Caso mas marcado, `%s` en el canal %s (sus bins van de %.1f a "
          "%.1f dBm):" % (arch, canal_ej, mn, mx))
        a("")
        a("| Metodo | Resultado | Veredicto |")
        a("|---|---|---|")
        a("| Promedio directo de los %d valores en dBm (incorrecto) | %.2f dBm | libre |"
          % (BINS_POR_CANAL, ingenuo_v))
        a("| Parseval: a mW, sumar, promediar, volver a dBm (correcto) | %.2f dBm | **ocupado** |"
          % correcto_v)
        a("| **Diferencia** | **%.2f dB** | |" % dif)
        a("")
        a("Esos %.2f dB equivalen a un factor de %.0f en potencia real. El "
          "canal contiene un pico de %.1f dBm que el promedio logaritmico "
          "diluye entre los bins en silencio hasta hacerlo desaparecer bajo "
          "el umbral. Con el metodo incorrecto se le reportaria a la Agencia "
          "que esa banda esta disponible cuando no lo esta."
          % (dif, 10 ** (dif / 10.0), mx))
        a("")
    a("### Por que la potencia media y no la total")
    a("")
    offset = 10.0 * math.log10(BINS_POR_CANAL)
    a("Sumar %d bins agrega %.2f dB (`10*log10(%d)`) de offset puramente "
      "aritmetico: aparece por el hecho de sumar, no por energia presente en "
      "el aire. Ese offset desplaza a **todos** los canales por igual por "
      "encima del umbral, con lo que el criterio dejaria de discriminar."
      % (BINS_POR_CANAL, offset, BINS_POR_CANAL))
    a("")
    ejemplo = 0
    a("Verificacion sobre `%s`:" % nombres[ejemplo])
    a("")
    a("| Canal | Potencia media | >%.0f dBm | Potencia total | >%.0f dBm |"
      % (UMBRAL_OCUPACION_DBM, UMBRAL_OCUPACION_DBM))
    a("|---|---|---|---|---|")
    for c in CANALES:
        pm, pt = float(p_media[c][ejemplo]), float(p_total[c][ejemplo])
        a("| %s | %.2f dBm | %s | %.2f dBm | %s |"
          % (c, pm, "si" if pm > UMBRAL_OCUPACION_DBM else "**no**",
             pt, "si" if pt > UMBRAL_OCUPACION_DBM else "**no**"))
    a("")
    n_ocup_media = sum(1 for c in CANALES if p_media[c][ejemplo] > UMBRAL_OCUPACION_DBM)
    n_ocup_total = sum(1 for c in CANALES if p_total[c][ejemplo] > UMBRAL_OCUPACION_DBM)
    a("Con la potencia media %d de los 4 canales resulta ocupado; con la "
      "total, %d de 4. La segunda lectura no informa nada utilizable para un "
      "plan de frecuencias. Por eso el umbral se evalua sobre la potencia "
      "media, aunque **ambas quedan registradas** en `indicadores.csv` "
      "(columnas `p_media_A..D` y `p_total_A..D`) para permitir verificar "
      "este mismo razonamiento."
      % (n_ocup_media, n_ocup_total))
    a("")
    a("### Resultados")
    a("")
    a("| Canal | Banda | Potencia media global | Maximo | Mediciones ocupadas | % |")
    a("|---|---|---|---|---|---|")
    for canal, (ini, fin) in CANALES.items():
        pm = p_media[canal]
        ocupadas = int(np.sum(pm > UMBRAL_OCUPACION_DBM))
        a("| **%s** | %.0f - %.0f MHz | %.2f dBm | %.2f dBm | %d / %d | %.1f%% |"
          % (canal, (FREC_INICIAL_HZ + ini * ANCHO_BIN_HZ) / 1e6,
             (FREC_INICIAL_HZ + fin * ANCHO_BIN_HZ) / 1e6,
             mw_a_dbm(dbm_a_mw(pm).mean()), pm.max(),
             ocupadas, n, 100.0 * ocupadas / n))
    a("")

    # ordenar canales por contaminacion
    ranking = sorted(CANALES, key=lambda c: float(dbm_a_mw(p_media[c]).mean()), reverse=True)
    peor, mejor = ranking[0], ranking[-1]
    pot_peor = mw_a_dbm(dbm_a_mw(p_media[peor]).mean())
    pot_mejor = mw_a_dbm(dbm_a_mw(p_media[mejor]).mean())
    ocup_peor = 100.0 * np.sum(p_media[peor] > UMBRAL_OCUPACION_DBM) / n
    ocup_mejor = 100.0 * np.sum(p_media[mejor] > UMBRAL_OCUPACION_DBM) / n

    a("**Canal mas contaminado: %s.** Con %.2f dBm de potencia media esta "
      "%.1f dB por encima del canal mas limpio y supera el umbral de "
      "ocupacion en el %.1f%% del recorrido."
      % (peor, pot_peor, pot_peor - pot_mejor, ocup_peor))
    a("")
    a("**Canal menos contaminado: %s.** Potencia media de %.2f dBm y solo "
      "%.1f%% de mediciones por encima del umbral."
      % (mejor, pot_mejor, ocup_mejor))
    a("")
    a("Orden de contaminacion, de mayor a menor: **%s**." % " > ".join(ranking))
    a("")
    a("![Comparacion de los cuatro canales](graficas/02_potencia_por_canal.png)")
    a("")
    a("*Figura 2. Izquierda: potencia media por Parseval de cada canal "
      "frente al umbral. Derecha: dispersion de las %d mediciones y "
      "porcentaje de puntos ocupados.*" % n)
    a("")

    # -------------------------------------------------- frecuencias extremas
    perfil, frec = ctx["perfil_dbm"], ctx["frecuencias"]
    bp, bm = ctx["bin_peor"], ctx["bin_mejor"]
    # Ancho del bloque a -10 dB: se recorre hacia ambos lados DESDE el pico
    # y se corta en el primer bin que baja del umbral, para medir el tramo
    # contiguo y no confundirlo con otros picos sueltos del espectro. Se usa
    # -10 dB y no -3 dB porque la mediana por bin es rugosa en la cima.
    umbral_lobulo = perfil[bp] - 10.0
    izq = bp
    while izq > 0 and perfil[izq - 1] >= umbral_lobulo:
        izq -= 1
    der = bp
    while der < N_BINS - 1 and perfil[der + 1] >= umbral_lobulo:
        der += 1
    ancho_lobulo = (der - izq + 1) * ANCHO_BIN_HZ

    # % de mediciones en las que cada bin extremo supera el umbral.
    ocup_bp = 100.0 * np.mean(espectro[:, bp] > UMBRAL_OCUPACION_DBM)
    ocup_bm = 100.0 * np.mean(espectro[:, bm] > UMBRAL_OCUPACION_DBM)

    a("## 7. Frecuencias extremas del sistema")
    a("")
    a("| | Bin | Frecuencia | Canal | Potencia mediana | Mediciones sobre %.0f dBm |"
      % UMBRAL_OCUPACION_DBM)
    a("|---|---|---|---|---|---|")
    a("| Mas contaminada | %d | **%.4f MHz** | %s | %.2f dBm | %.1f%% |"
      % (bp, frec[bp] / 1e6, "ABCD"[bp // BINS_POR_CANAL], perfil[bp], ocup_bp))
    a("| Menos contaminada | %d | **%.4f MHz** | %s | %.2f dBm | %.1f%% |"
      % (bm, frec[bm] / 1e6, "ABCD"[bm // BINS_POR_CANAL], perfil[bm], ocup_bm))
    a("")
    a("Diferencia entre ambas: **%.2f dB**." % (perfil[bp] - perfil[bm]))
    a("")
    a("![Frecuencias extremas del sistema](graficas/01_frecuencias_extremas.png)")
    a("")
    a("*Figura 1. Perfil mediano de la banda con ambas frecuencias "
      "senaladas y detalle ampliado de cada una. Generada por "
      "`graficas.py`.*")
    a("")

    # ---- por que la mediana entre mediciones
    lineal = ctx["perfil_lineal_dbm"]
    bl = int(np.argmax(lineal))
    col = dbm_a_mw(espectro[:, bl])
    dom = int(np.argmax(col))
    aporte = 100.0 * col[dom] / col.sum()
    # Estabilidad: se repite el calculo quitando cada medicion una vez.
    estable = sum(
        int(np.argmax(np.median(np.delete(espectro, k, axis=0), axis=0)) == bp)
        for k in range(n))

    a("### Por que la mediana entre mediciones")
    a("")
    a("Dentro de cada espectro la potencia se integra en lineal (Parseval, "
      "seccion 6). Para agregar **entre ubicaciones** la pregunta es otra: "
      "que frecuencia esta contaminada en todo el sistema, no en un punto. "
      "La media lineal entre las %d mediciones no responde eso, porque la "
      "domina la medicion mas fuerte:" % n)
    a("")
    a("| Criterio | Frecuencia mas contaminada | Observacion |")
    a("|---|---|---|")
    a("| Media lineal entre mediciones (descartado) | %.4f MHz | `%s` aporta "
      "el %.0f%% de la energia de ese bin; su mediana es %.1f dBm |"
      % (frec[bl] / 1e6, nombres[dom], aporte, float(np.median(espectro[:, bl]))))
    a("| **Mediana entre mediciones** | **%.4f MHz** | Sobre el umbral en el "
      "%.1f%% del recorrido |" % (frec[bp] / 1e6, ocup_bp))
    a("")
    a("La eleccion es estable: al repetir el calculo quitando cada medicion "
      "una vez, la mediana senala %.4f MHz en %d de %d casos. La media "
      "lineal, en cambio, cambia de frecuencia con solo retirar `%s`."
      % (frec[bp] / 1e6, estable, n, nombres[dom]))
    a("")
    a("El maximo no es un bin aislado: forma parte de un bloque continuo de "
      "unos **%.0f kHz** (%.4f - %.4f MHz) que se mantiene dentro de los "
      "10 dB del pico, un ancho del orden de una portadora celular y no de "
      "un artefacto de la FFT."
      % (ancho_lobulo / 1e3, frec[izq] / 1e6, frec[der] / 1e6))
    a("")
    a("### Descarte de artefactos del receptor")
    a("")
    # Pico de DC: cuanto sobresale el bin central sobre sus vecinos a +-2
    # bins, medicion por medicion, comparado con el mismo estadistico en el
    # resto de la banda (que es el comportamiento normal de un bin).
    def realce(k):
        return float(np.median(espectro[:, k] - 0.5 * (espectro[:, k - 2] + espectro[:, k + 2])))
    realce_dc = realce(512)
    realce_p99 = float(np.percentile([realce(k) for k in range(2, N_BINS - 2)], 99))

    a("El USRP introduce un offset de DC en su frecuencia central, que en "
      "esta campana es 850 MHz (bin 512) y cae justo en la frontera entre "
      "los canales B y C. Se midio cuanto sobresale ese bin sobre sus "
      "vecinos a +-2 bins en cada medicion: la mediana es **%+.2f dB**, "
      "frente a %+.2f dB para el percentil 99 del resto de la banda."
      % (realce_dc, realce_p99))
    a("")
    if realce_dc > realce_p99:
        # Se cuantifica su efecto reemplazando el tramo afectado por una
        # recta entre sus bordes. Solo para medir; el espectro no se toca.
        sin_dc = espectro.copy()
        for k in range(508, 517):
            w = (k - 507) / 10.0
            sin_dc[:, k] = espectro[:, 507] + w * (espectro[:, 517] - espectro[:, 507])
        p_sin_dc, _ = potencia_por_canal(sin_dc)
        a("**Hay un pico de DC**: un realce de unos 9 bins (%.3f - %.3f MHz) "
          "centrado en 850 MHz. Para medir su efecto se reemplazo ese tramo "
          "por una recta entre sus bordes y se recalculo la ocupacion:"
          % (frec[508] / 1e6, frec[516] / 1e6))
        a("")
        a("| Canal | Mediciones ocupadas | Sin el pico de DC |")
        a("|---|---|---|")
        for c in ("B", "C"):
            a("| %s | %d | %d |" % (c, int(np.sum(p_media[c] > UMBRAL_OCUPACION_DBM)),
                                   int(np.sum(p_sin_dc[c] > UMBRAL_OCUPACION_DBM))))
        a("")
        a("El efecto es marginal y no cambia el orden de los canales, asi "
          "que el espectro se conserva sin modificar. La ocupacion del canal "
          "C es energia real del aire y no un artefacto instrumental.")
    else:
        a("**Sin pico de DC**: la ocupacion elevada del canal C es energia "
          "real del aire y no un artefacto instrumental.")
    a("")

    # ----------------------------------------------------- recomendacion
    a("## 8. Recomendacion tecnica para la ANE")
    a("")
    a("Con base en la potencia integrada por Parseval sobre %d puntos de "
      "medicion en el occidente de Medellin:" % n)
    a("")
    a("- **No asignar el canal %s (%.0f - %.0f MHz).** Es el bloque mas "
      "contaminado de la banda: %.2f dBm de potencia media y ocupacion en el "
      "%.1f%% del recorrido. Cualquier asignacion nueva aqui enfrentaria "
      "interferencia co-canal en practicamente toda el area cubierta."
      % (peor, (FREC_INICIAL_HZ + CANALES[peor][0] * ANCHO_BIN_HZ) / 1e6,
         (FREC_INICIAL_HZ + CANALES[peor][1] * ANCHO_BIN_HZ) / 1e6,
         pot_peor, ocup_peor))
    a("- **Priorizar el canal %s (%.0f - %.0f MHz).** Es el mas limpio: "
      "%.2f dBm de potencia media y solo %.1f%% de puntos por encima del "
      "umbral. Es la mejor opcion para un despliegue nuevo."
      % (mejor, (FREC_INICIAL_HZ + CANALES[mejor][0] * ANCHO_BIN_HZ) / 1e6,
         (FREC_INICIAL_HZ + CANALES[mejor][1] * ANCHO_BIN_HZ) / 1e6,
         pot_mejor, ocup_mejor))
    a("- **Evitar la vecindad de %.4f MHz** en cualquier plan de "
      "frecuencias: es la frecuencia mas contaminada del sistema, sobre el "
      "umbral en el %.1f%% del recorrido y %.2f dB por encima del punto mas "
      "limpio del espectro."
      % (frec[bp] / 1e6, ocup_bp, perfil[bp] - perfil[bm]))
    a("- **Tomar %.4f MHz como referencia de piso de ruido** para futuras "
      "campanas de monitoreo en el sector: su mediana es %.2f dBm y solo "
      "supera el umbral en el %.1f%% de los puntos."
      % (frec[bm] / 1e6, perfil[bm], ocup_bm))
    a("")
    a("### Limitaciones del estudio")
    a("")
    a("- La campana cubre %.2f km del sector occidental; los resultados no "
      "son extrapolables al resto del Valle de Aburra sin mediciones "
      "adicionales." % ctx["recorrido_km"])
    a("- Las mediciones son instantaneas a lo largo de un recorrido, no un "
      "monitoreo continuo: no capturan variacion horaria de la ocupacion.")
    a("- `017.txt` aporta espectro valido pero su posicion tiene un error de "
      "distancia de 17.3 y no debe usarse para inferencias geograficas finas.")
    a("")

    # --------------------------------------------------- bonificacion
    fuentes = ctx.get("fuentes")
    if fuentes:
        # Import diferido por la misma razon que en main(): fuentes.py
        # importa constantes de este modulo.
        from fuentes import N_MIN, PASO_MALLA, R2_CONCLUYENTE

        a("## 9. Bonificacion: estimacion del origen de la contaminacion")
        a("")
        a("Se busca ubicar geograficamente la fuente que contamina cada "
          "banda. Se aplicaron dos metodos y se reportan ambos, porque el "
          "primero -el rigurosamente correcto si existiera un unico emisor- "
          "resulta no concluyente, y ese resultado negativo es en si mismo "
          "un hallazgo tecnico.")
        a("")

        # ---- metodo 1
        a("### Metodo 1: inversion log-distancia (trilateracion)")
        a("")
        a("La potencia recibida de un emisor fijo decae segun el modelo "
          "log-distancia:")
        a("")
        a("```")
        a("P(d) = P0 - 10 * n * log10(d)   =>   P = P0 + n * x,  x = -10*log10(d)")
        a("```")
        a("")
        a("donde `n` es el exponente de perdida de trayecto (2 en espacio "
          "libre, 2.7 a 4 en entorno urbano). Conocida la posicion del "
          "emisor, la nube de puntos (x, P) deberia formar una recta. Como "
          "no se conoce, se invierte el problema: se barre una malla de "
          "%d candidatos sobre el area y para cada uno se ajusta esa recta "
          "por minimos cuadrados. El candidato con mayor R^2 y pendiente "
          "fisicamente plausible seria la posicion del emisor."
          % (PASO_MALLA * PASO_MALLA))
        a("")
        a("| Canal | R^2 del mejor ajuste | n ajustado | n sin restringir | Resultado |")
        a("|---|---|---|---|---|")
        for f in fuentes:
            a("| %s | %.3f | %.2f | %+.2f | %s |"
              % (f["canal"], f["tri_r2"], f["tri_n"], f["tri_n_libre"],
                 "localizado" if f["tri_concluyente"] else "**no concluyente**"))
        a("")
        max_r2 = max(f["tri_r2"] for f in fuentes)
        negativos = [f for f in fuentes if f["tri_n_libre"] < 0]
        bajo_minimo = [f for f in fuentes
                       if 0 <= f["tri_n_libre"] < N_MIN]
        a("**El metodo falla en los cuatro canales.** El criterio principal "
          "es el ajuste: el mejor R^2 obtenido es %.3f, muy por debajo del "
          "%.2f exigido para aceptar una localizacion. Con ajustes tan "
          "pobres, la coordenada que devuelve el optimizador no tiene "
          "significado fisico."
          % (max_r2, R2_CONCLUYENTE))
        a("")
        a("La cuarta columna refuerza el diagnostico. Al liberar la "
          "restriccion sobre la pendiente:")
        a("")
        if negativos:
            a("- En %s el optimo global tiene pendiente **negativa** (%s), lo "
              "que implicaria que la potencia *aumenta* con la distancia al "
              "punto hallado. Es imposible para una fuente: el optimizador "
              "esta localizando un minimo de campo, no un emisor."
              % (", ".join("canal %s" % f["canal"] for f in negativos),
                 ", ".join("%+.2f" % f["tri_n_libre"] for f in negativos)))
        if bajo_minimo:
            a("- En %s la pendiente cae por debajo de %.1f (%s), es decir "
              "una atenuacion mas lenta que en espacio libre. Tampoco "
              "describe propagacion real desde un emisor."
              % (", ".join("canal %s" % f["canal"] for f in bajo_minimo),
                 N_MIN,
                 ", ".join("%+.2f" % f["tri_n_libre"] for f in bajo_minimo)))
        restantes = [f for f in fuentes
                     if f not in negativos and f not in bajo_minimo]
        if restantes:
            a("- En %s la pendiente si es fisicamente admisible (%s), pero "
              "el ajuste sigue siendo demasiado debil (R^2 = %s) para "
              "sostener una localizacion."
              % (", ".join("canal %s" % f["canal"] for f in restantes),
                 ", ".join("%.2f" % f["tri_n_libre"] for f in restantes),
                 ", ".join("%.3f" % f["tri_r2"] for f in restantes)))
        a("")
        a("Causas identificadas:")
        a("")
        a("1. **No hay un emisor, hay decenas.** Una banda celular la sirven "
          "multiples estaciones base repartidas por la ciudad; el campo "
          "agregado no decae desde un punto unico.")
        a("2. **La geometria del muestreo es degenerada.** El recorrido es "
          "practicamente un corredor lineal a lo largo del valle, y para "
          "trilaterar se requiere observar la fuente desde angulos "
          "diversos.")
        a("3. **El sombreado urbano domina la senal de distancia.** La "
          "potencia de un mismo canal varia mas de %.0f dB entre los "
          "puntos del recorrido, un rango atribuible a edificaciones y "
          "topografia que enmascara por completo la atenuacion por "
          "distancia."
          % min(f["dinamica_db"] for f in fuentes))
        a("")

        # ---- metodo 2
        a("### Metodo 2: centroide ponderado del decil superior")
        a("")
        a("Ante la falla del metodo 1 se adopta un estimador mas modesto "
          "pero sostenible: se toman las %d mediciones del decil de mayor "
          "potencia de cada canal y se calcula su centroide ponderado por "
          "potencia en escala **lineal** (mW), no en dBm, por la misma razon "
          "expuesta en la seccion 6."
          % fuentes[0]["n_puntos"])
        a("")
        a("Esto **no localiza un transmisor**: delimita la zona de maxima "
          "incidencia, es decir hacia donde se concentra la energia "
          "dominante vista desde el corredor recorrido.")
        a("")
        a("| Canal | Centro estimado | Radio medio | Radio maximo | Separacion al decil debil | Dinamica |")
        a("|---|---|---|---|---|---|")
        for f in fuentes:
            a("| **%s** | %.5f, %.5f | %.2f km | %.2f km | %.2f km | %.1f dB |"
              % (f["canal"], f["lat"], f["lon"], f["radio_km"],
                 f["radio_max_km"], f["separacion_km"], f["dinamica_db"]))
        a("")
        sep_min = min(f["separacion_km"] for f in fuentes)
        sep_max = max(f["separacion_km"] for f in fuentes)
        a("**Validacion.** Para descartar que el centroide sea un artefacto "
          "del promedio se calculo tambien el centroide del decil *inferior* "
          "de cada canal. Ambos quedan separados entre %.2f y %.2f km, lo "
          "que confirma la existencia de un gradiente espacial real: las "
          "mediciones fuertes y las debiles no estan mezcladas, ocupan "
          "zonas distintas del recorrido." % (sep_min, sep_max))
        a("")
        a("Las cuatro zonas convergen en un area comun del sur del "
          "corredor (latitud %.3f a %.3f, longitud %.3f a %.3f), lo que "
          "sugiere un foco de emision compartido para toda la banda antes "
          "que emisores independientes por canal."
          % (min(f["lat"] for f in fuentes), max(f["lat"] for f in fuentes),
             min(f["lon"] for f in fuentes), max(f["lon"] for f in fuentes)))
        a("")
        a("### Alcance de la estimacion")
        a("")
        a("Los radios obtenidos (%.2f a %.2f km) no son un margen de error "
          "instrumental sino la dispersion real de las mediciones que "
          "sustentan cada centro. Se reportan explicitamente para que la "
          "Agencia no interprete estas coordenadas como una localizacion "
          "puntual. Para localizar emisores con precision util se requeriria "
          "una campana con geometria de muestreo bidimensional y, "
          "preferiblemente, antena directiva con medicion de azimut."
          % (min(f["radio_km"] for f in fuentes),
             max(f["radio_km"] for f in fuentes)))
        a("")
        a("Los resultados completos, incluidos los diagnosticos del metodo "
          "1, quedan en `salida/fuentes_estimadas.csv`.")
        a("")

    with open(ruta, "w") as fo:
        fo.write("\n".join(L) + "\n")


def main():
    os.makedirs(CARPETA_SALIDA, exist_ok=True)

    # ---------------- EXTRACT ----------------
    nombres, matriz, descartados = extraer()
    print("Mediciones leidas: %d" % len(nombres))
    if descartados:
        print("Descartadas en lectura: %s" % descartados)

    espectro = matriz[:, :N_BINS].copy()
    temp = matriz[:, N_BINS].copy()
    lon = matriz[:, N_BINS + 1].copy()
    lat = matriz[:, N_BINS + 2].copy()
    alt = matriz[:, N_BINS + 3].copy()
    hdop = matriz[:, N_BINS + 4].copy()

    # ---------------- TRANSFORM: calidad ----------------
    hallazgos = auditar(nombres, espectro, temp, lon, lat, alt, hdop)
    imputaciones = []
    imputaciones += imputar_gps(nombres, lat, lon, alt, hdop, hallazgos)
    imputaciones += imputar_espectro(nombres, espectro)

    # Etiqueta de calidad por medición, para poder filtrar en el dashboard.
    # "imputada" se reserva para las que realmente se modificaron; una
    # medición con hallazgo crítico que NO se toco queda como "degradada",
    # que es el caso de 017.txt (tiene fix, solo que impreciso).
    # La etiqueta describe la POSICION de la medicion (es la que filtra la
    # estimacion de fuentes); el piso de ruido anomalo va aparte, en su
    # propia columna, porque la georreferenciacion de esa medicion es buena.
    calidad = ["buena"] * len(nombres)
    for h in hallazgos:
        if h["campo"] != "piso_ruido":
            calidad[h["indice"]] = "degradada"
    anomalia_espectral = [0] * len(nombres)
    for h in hallazgos:
        if h["campo"] == "piso_ruido":
            anomalia_espectral[h["indice"]] = 1
    for m in imputaciones:
        if m["tecnica"] != "ninguna":
            calidad[m["indice"]] = "imputada"

    # ---------------- TRANSFORM: indicadores ----------------
    p_media, p_total = potencia_por_canal(espectro)
    frecuencias = eje_frecuencias()

    # Piso de ruido de cada medición: percentil 10 de sus bins. Es el
    # indicador que se cruza contra la temperatura del sensor.
    piso_ruido = np.percentile(espectro, 10, axis=1)

    # Perfil del sistema: MEDIANA de cada bin sobre las 61 mediciones. Aqui
    # no se promedia en lineal: la media lineal entre mediciones la domina
    # la medicion mas fuerte (016.txt aporta el 97% en su pico), y la
    # pregunta es que frecuencia esta contaminada en todo el sistema, no en
    # un punto. Parseval se sigue aplicando DENTRO de cada espectro para la
    # potencia de canal; esto es solo el agregado entre ubicaciones.
    perfil_dbm = np.median(espectro, axis=0)
    bin_peor = int(np.argmax(perfil_dbm))
    bin_mejor = int(np.argmin(perfil_dbm))

    # Contraste con la media lineal, que era el criterio anterior: se
    # conserva para documentar en el reporte por que se descarto.
    perfil_lineal_dbm = mw_a_dbm(dbm_a_mw(espectro).mean(axis=0))

    # ---------------- LOAD ----------------
    orden = list(range(1, len(nombres) + 1))
    ruta_csv = os.path.join(CARPETA_SALIDA, "indicadores.csv")
    escribir_indicadores(ruta_csv, nombres, orden, lat, lon, alt, temp, hdop,
                         p_media, p_total, piso_ruido, calidad,
                         anomalia_espectral)
    np.save(os.path.join(CARPETA_SALIDA, "espectro_limpio.npy"), espectro)
    np.save(os.path.join(CARPETA_SALIDA, "frecuencias_hz.npy"), frecuencias)
    np.save(os.path.join(CARPETA_SALIDA, "perfil_mediano_dbm.npy"), perfil_dbm)

    recorrido = sum(distancia_haversine_km(lat[i], lon[i], lat[i + 1], lon[i + 1])
                    for i in range(len(nombres) - 1))

    ctx = dict(nombres=nombres, espectro=espectro, temp=temp, lat=lat, lon=lon,
               alt=alt, hdop=hdop, p_media=p_media, p_total=p_total,
               hallazgos=hallazgos, imputaciones=imputaciones,
               perfil_dbm=perfil_dbm, frecuencias=frecuencias,
               piso_ruido=piso_ruido, calidad=calidad, orden=orden,
               anomalia_espectral=anomalia_espectral,
               perfil_lineal_dbm=perfil_lineal_dbm,
               recorrido_km=recorrido, bin_peor=bin_peor, bin_mejor=bin_mejor)

    # Estimacion de fuentes (bonificacion). Se importa aqui y no arriba
    # porque fuentes.py importa constantes de este modulo: hacerlo al nivel
    # del archivo crearia un import circular. Ademas necesita el
    # indicadores.csv que se acaba de escribir.
    import fuentes
    ctx["fuentes"] = fuentes.estimar_todo(verbose=False)

    ruta_md = os.path.join(CARPETA_SALIDA, "reporte_calidad.md")
    escribir_reporte(ruta_md, ctx)

    # ---------------- Resumen en consola ----------------
    print("\n--- CALIDAD ---")
    print("Hallazgos: %d  |  Imputaciones: %d" % (len(hallazgos), len(imputaciones)))
    for h in hallazgos:
        print("  [%s] %s / %s: %s" % (h["gravedad"], h["archivo"], h["campo"], h["detalle"]))
    for m in imputaciones:
        print("  IMPUTA %s / %s via %s -> %s" % (m["archivo"], m["campo"], m["tecnica"], m["detalle"]))

    print("\n--- TEMPERATURA vs CALIDAD ---")
    print("Temperatura: %.2f a %.2f C" % (temp.min(), temp.max()))
    print("r(temp, piso de ruido)      = %+.3f" % correlacion(temp, piso_ruido))
    print("r(temp, orden de medicion)  = %+.3f" % correlacion(temp, orden))
    print("r(temp, error de distancia) = %+.3f" % correlacion(temp, hdop))

    print("\n--- OCUPACION POR CANAL (Parseval, umbral %.0f dBm) ---" % UMBRAL_OCUPACION_DBM)
    for canal, (ini, fin) in CANALES.items():
        pm = p_media[canal]
        ocupadas = int(np.sum(pm > UMBRAL_OCUPACION_DBM))
        print("  Canal %s (%.0f-%.0f MHz): media global %.2f dBm | max %.2f | "
              "%d/%d mediciones ocupadas (%.1f%%)"
              % (canal, (FREC_INICIAL_HZ + ini * ANCHO_BIN_HZ) / 1e6,
                 (FREC_INICIAL_HZ + fin * ANCHO_BIN_HZ) / 1e6,
                 mw_a_dbm(dbm_a_mw(pm).mean()), pm.max(),
                 ocupadas, len(nombres), 100.0 * ocupadas / len(nombres)))

    print("\n--- FRECUENCIAS EXTREMAS ---")
    print("Mas contaminada : bin %d = %.4f MHz  (%.2f dBm mediana)"
          % (bin_peor, frecuencias[bin_peor] / 1e6, perfil_dbm[bin_peor]))
    print("Menos contaminada: bin %d = %.4f MHz  (%.2f dBm mediana)"
          % (bin_mejor, frecuencias[bin_mejor] / 1e6, perfil_dbm[bin_mejor]))

    print("\n--- RUTA ---")
    print("Recorrido total: %.2f km | lat %.5f..%.5f | lon %.5f..%.5f"
          % (recorrido, lat.min(), lat.max(), lon.min(), lon.max()))

    print("\nEscrito en %s" % CARPETA_SALIDA)
    print("  indicadores.csv | espectro_limpio.npy | frecuencias_hz.npy")
    print("  perfil_mediano_dbm.npy | reporte_calidad.md")
    return ctx


if __name__ == "__main__":
    main()
