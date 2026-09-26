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
# medidaprueba.txt (ensayo del operador sin fix de GPS), medidapureba2.txt
# (ensayo a ~0.5 km del punto de partida, con el equipo aun frio: 38.1 C) y
# ANTENNA1.csv (barrido S11 de la antena, otro formato)
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

# Ganancia del USRP durante la campana, fija (medidas_2026_20/medir_celular.py,
# set_gain(40)): sin control automatico, un emisor muy cercano satura el
# front-end en lugar de hacer que el receptor baje su ganancia.
GANANCIA_RECEPTOR_DB = 40

# Estaciones base identificadas en Google Street View junto a las dos zonas
# de mayor potencia del recorrido. No salen del dataset: son la verificacion
# externa de lo que senalan los datos (secciones 1 y 8 del reporte).
ESTACIONES_BASE = [
    dict(nombre="Guayabal", lat=6.201370, lon=-75.584742),
    dict(nombre="Sur", lat=6.168150, lon=-75.608361),
]


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
    """Vuelca el informe tecnico a Markdown.

    Se genera desde los mismos arreglos que alimentan el CSV, para que el
    informe no pueda desincronizarse de los datos: si cambia un umbral o una
    regla de imputacion, basta volver a correr el ETL. Sigue el orden de los
    entregables del enunciado y prefiere la prosa a las tablas.
    """
    nombres = ctx["nombres"]
    espectro = ctx["espectro"]
    lat, lon, alt = ctx["lat"], ctx["lon"], ctx["alt"]
    p_media, p_total = ctx["p_media"], ctx["p_total"]
    imputaciones = ctx["imputaciones"]
    hallazgos = ctx["hallazgos"]
    calidad = ctx["calidad"]
    pisos = ctx["piso_ruido"]
    anom = np.array(ctx["anomalia_espectral"], dtype=bool)
    perfil, frec = ctx["perfil_dbm"], ctx["frecuencias"]
    bp, bm = ctx["bin_peor"], ctx["bin_mejor"]
    fuentes = ctx.get("fuentes")
    n = len(nombres)
    L = []
    a = L.append

    def banda(c):
        ini, fin = CANALES[c]
        return "%.0f–%.0f MHz" % ((FREC_INICIAL_HZ + ini * ANCHO_BIN_HZ) / 1e6,
                                  (FREC_INICIAL_HZ + fin * ANCHO_BIN_HZ) / 1e6)

    def glob(v):
        return float(mw_a_dbm(dbm_a_mw(v).mean()))

    def ocup(c, mascara=None):
        v = p_media[c] if mascara is None else p_media[c][mascara]
        return int(np.sum(v > UMBRAL_OCUPACION_DBM))

    ranking = sorted(CANALES, key=lambda c: glob(p_media[c]), reverse=True)
    peor, mejor = ranking[0], ranking[-1]
    ocup_bp = 100.0 * np.mean(espectro[:, bp] > UMBRAL_OCUPACION_DBM)
    ocup_bm = 100.0 * np.mean(espectro[:, bm] > UMBRAL_OCUPACION_DBM)
    piso_tipico = float(np.median(pisos))
    anomalas = list(np.where(anom)[0])

    # Estaciones base: medicion mas cercana a cada una.
    cercania = []
    for eb in ESTACIONES_BASE:
        d = np.array([distancia_haversine_km(eb["lat"], eb["lon"], lat[i], lon[i])
                      for i in range(n)])
        i1, i2 = np.argsort(d)[:2]
        cercania.append(dict(eb=eb, i=int(i1), d_m=1000 * d[i1],
                             j=int(i2), d2_m=1000 * d[i2]))

    a("# Ocupación del espectro 840–860 MHz en el occidente de Medellín")
    a("")

    # ------------------------------------------------------------- resumen
    a("## Resumen")
    a("")
    a("Se analizaron %d mediciones de espectro tomadas por una estación móvil "
      "a lo largo de %.1f km del occidente de Medellín. La banda está "
      "contaminada de forma desigual: el canal %s (%s) supera el umbral de "
      "%.0f dBm en el %.0f%% del recorrido y no debe asignarse, mientras que "
      "el canal %s (%s) es el más limpio, con ocupación en el %.0f%% de los "
      "puntos, y es el recomendado para nuevas asignaciones. La frecuencia "
      "más contaminada del sistema es %.3f MHz y la más limpia %.3f MHz. "
      "Las dos zonas de mayor potencia coinciden con estaciones base "
      "celulares ubicadas en Google Street View."
      % (n, ctx["recorrido_km"], peor, banda(peor), UMBRAL_OCUPACION_DBM,
         100.0 * ocup(peor) / n, mejor, banda(mejor), 100.0 * ocup(mejor) / n,
         frec[bp] / 1e6, frec[bm] / 1e6))
    a("")

    # --------------------------------------------------------------- calidad
    a("## 1. Calidad de los datos")
    a("")
    a("La campaña consta de %d archivos (001.txt a %s), cada uno con %d "
      "columnas: %d valores de potencia en dBm entre 840 y 860 MHz, a %.2f kHz "
      "por valor, seguidos de temperatura del sensor, longitud, latitud, "
      "altura y error de distancia del GPS. Todos tienen la estructura "
      "correcta y ninguno de los %d valores de potencia es nulo, infinito o "
      "está fuera del rango físico del receptor. Se dejaron fuera de la serie "
      "tres archivos que no son mediciones del recorrido: medidaprueba.txt, un "
      "ensayo del operador sin señal GPS; medidapureba2.txt, otro ensayo "
      "tomado a unos 0.5 km del punto de partida con el equipo todavía frío "
      "(38.1 °C, por debajo de toda la serie), y ANTENNA1.csv, la "
      "caracterización de la antena."
      % (n, nombres[-1], N_COLUMNAS, N_BINS, ANCHO_BIN_HZ / 1e3, n * N_BINS))
    a("")

    por_archivo = {}
    for h in hallazgos:
        por_archivo.setdefault(h["archivo"], []).append(h)
    hdop = ctx["hdop"]
    a("La auditoría encontró problemas en %d mediciones; las otras %d están "
      "limpias. El error de distancia del GPS, que se interpreta como HDOP, "
      "tiene una mediana de %.1f en la campaña."
      % (len(por_archivo), n - len(por_archivo), float(np.median(hdop))))
    a("")
    for archivo, hs in por_archivo.items():
        i = nombres.index(archivo)
        campos = {h["campo"] for h in hs}
        if "gps" in campos:
            a("- %s perdió la señal GPS: latitud, longitud y altura llegaron en "
              "cero. Su espectro es válido, así que se imputó la posición "
              "(sección 2)." % archivo)
        elif "piso_ruido" in campos:
            a("- %s tiene el piso de ruido en %.1f dBm, %.1f dB por encima del "
              "típico de la campaña (%.1f dBm): no hay ningún tramo de la "
              "banda en silencio. Se conserva y se marca como anomalía "
              "espectral." % (archivo, pisos[i], pisos[i] - piso_tipico,
                              piso_tipico))
        elif "hdop" in campos:
            a("- %s tiene error de distancia %.1f, unas %.0f veces el típico. "
              "Tiene posición, solo que imprecisa, y es coherente con sus "
              "vecinas en la ruta; se conserva como degradada y se excluye "
              "únicamente de la estimación de fuentes."
              % (archivo, hdop[i], hdop[i] / float(np.median(hdop))))
    a("")

    if anomalas and len(cercania) >= 2:
        g, s = cercania[0], cercania[1]
        a("La causa del piso anómalo se verificó con Google Street View: "
          "hay una estación base celular (%.6f, %.6f) a %.0f m de %s; "
          "ninguna otra medición pasó a menos de %.0f m de ella. El receptor "
          "operó con ganancia fija de %d dB, sin control automático, y al "
          "pasar al pie de la antena se saturó. Una segunda estación (%.6f, "
          "%.6f) queda a %.0f m de %s, la medición con el segundo piso más "
          "alto (%+.1f dB): allí la señal es fuerte pero el receptor no llegó "
          "a saturarse y la medición es válida."
          % (g["eb"]["lat"], g["eb"]["lon"], g["d_m"], nombres[g["i"]],
             g["d2_m"], GANANCIA_RECEPTOR_DB, s["eb"]["lat"], s["eb"]["lon"],
             s["d_m"], nombres[s["i"]], pisos[s["i"]] - piso_tipico))
        a("")
        a("La medición saturada no se corrige ni se descarta: su posición es "
          "correcta y registra un hecho real, un emisor a pocos metros. Pero "
          "sus potencias están infladas, así que no se deja que decida los "
          "indicadores del sistema: la frecuencia más contaminada se calcula "
          "con la mediana entre mediciones (sección 6) y su efecto sobre la "
          "potencia media de los canales se reporta aparte (sección 5).")
        a("")

    n_imp = sum(1 for m in imputaciones if m["tecnica"] != "ninguna")
    a("En resumen, de las %d mediciones %d quedan como buenas, %d imputada, "
      "%d degradada y %d marcada por saturación. Se corrigieron %d valores, "
      "todos de posición; ningún valor de espectro fue modificado."
      % (n, calidad.count("buena") - len(anomalas), calidad.count("imputada"),
         calidad.count("degradada"), len(anomalas), 3 * n_imp))
    a("")

    # ------------------------------------------------------------ imputacion
    separaciones = np.array([
        distancia_haversine_km(lat[i], lon[i], lat[i + 1], lon[i + 1])
        for i in range(n - 1)])
    a("## 2. Técnicas de imputación")
    a("")
    for m in imputaciones:
        if m["tecnica"] == "ninguna":
            continue
        i = m["indice"]
        vecinos = m["tecnica"].split("entre ")[-1]
        a("Solo se imputó la posición de %s, por interpolación lineal entre "
          "sus vecinas %s. La estación se desplaza de forma continua y los "
          "archivos siguen el orden del recorrido, así que el punto perdido "
          "está necesariamente en el tramo que une a sus vecinas. La posición "
          "imputada es %.5f, %.5f a %.0f m de altura. El error máximo es la "
          "mitad de ese tramo, ±%.2f km, del mismo orden que la separación "
          "típica entre mediciones consecutivas (%.2f km)."
          % (m["archivo"], vecinos, lat[i], lon[i], alt[i],
             m["error_max_km"], float(np.median(separaciones))))
        a("")
    a("Se eligió la interpolación lineal porque es la que menos supone: "
      "descartar la medición perdía un espectro válido, el vecino más "
      "cercano ignora que el vehículo se movió, una curva suave supone "
      "velocidades que no se conocen y los archivos no traen marca de tiempo "
      "para reconstruir la velocidad real.")
    a("")
    if anomalas:
        i = anomalas[0]
        med_vec = [float(np.median(espectro[k])) for k in (i - 1, i + 1) if 0 <= k < n]
        a("Las otras dos mediciones con problemas no se imputan. La de HDOP "
          "alto tiene una posición real y reemplazarla por un promedio "
          "destruiría información. La saturada tampoco admite imputación: "
          "sus vecinas en la ruta difieren %.0f dB entre sí aun estando a "
          "menos de un kilómetro, de modo que un espectro interpolado no "
          "representaría lo que había en ese punto."
          % abs(med_vec[0] - med_vec[-1]))
        a("")

    # ------------------------------------------------------------- ruta
    ini, fin = 0, n - 1
    sur = int(np.argmin(lat))
    a("## 3. Ruta de la estación móvil")
    a("")
    a("El orden de los archivos es el orden del recorrido, lo que permite "
      "reconstruir la ruta sin marcas de tiempo. La estación hizo un "
      "circuito de %.1f km: salió del norte del sector (%s, %.5f, %.5f), "
      "bajó hasta el punto más al sur en %s (%.5f, %.5f) y regresó hacia el "
      "norte por un trazado más oriental hasta %s. La altura varió entre "
      "%.0f y %.0f m."
      % (ctx["recorrido_km"], nombres[ini], lat[ini], lon[ini], nombres[sur],
         lat[sur], lon[sur], nombres[fin], alt.min(), alt.max()))
    a("")
    a("![Recorrido de la estación móvil](graficas/04_ruta.png)")
    a("")
    a("*Figura 1. Recorrido con la calidad del dato de cada punto (izquierda) "
      "y coloreado por la potencia del canal más contaminado (derecha).*")
    a("")

    # ------------------------------------------------------ temperatura
    temp = ctx["temp"]
    r_piso = correlacion(temp, pisos)
    r_orden = correlacion(temp, ctx["orden"])
    r_hdop = correlacion(temp, hdop)
    a("## 4. Incidencia de la temperatura del sensor")
    a("")
    a("La temperatura del sensor subió de %.1f a %.1f °C durante la campaña, "
      "casi en línea recta con el avance del recorrido (correlación de "
      "%+.2f con el orden de las mediciones). Es el calentamiento del propio "
      "equipo, no una variable ambiental. Su relación con la calidad del "
      "dato es débil: contra el piso de ruido la correlación es %+.2f, "
      "compatible con un leve aumento del ruido térmico del receptor, y "
      "contra el error del GPS es %+.2f, es decir, ninguna. La temperatura "
      "no compromete las mediciones y no se descartó ninguna por esta causa."
      % (temp.min(), temp.max(), r_orden, r_piso, r_hdop))
    a("")
    a("![Incidencia de la temperatura](graficas/03_temperatura.png)")
    a("")
    a("*Figura 2. Evolución de la temperatura durante la campaña (izquierda) "
      "y su relación con el piso de ruido (derecha).*")
    a("")

    # -------------------------------------------------------- canales
    cambios = 0
    peor_caso = None
    for c, (i0, i1) in CANALES.items():
        ingenuo = espectro[:, i0:i1].mean(axis=1)
        for k in range(n):
            if ingenuo[k] <= UMBRAL_OCUPACION_DBM < p_media[c][k]:
                cambios += 1
                dif = float(p_media[c][k] - ingenuo[k])
                if peor_caso is None or dif > peor_caso[0]:
                    peor_caso = (dif, nombres[k], c)
    offset = 10.0 * math.log10(BINS_POR_CANAL)

    a("## 5. Contaminación por canal")
    a("")
    a("La banda se divide en cuatro canales consecutivos de 5 MHz, de %d "
      "valores cada uno. La potencia media de cada canal en cada punto se "
      "calcula con la sumatoria de Parseval: los dBm se pasan a mW, se "
      "promedian los %d valores y el resultado vuelve a dBm para compararlo "
      "con el umbral de %.0f dBm. Promediar los dBm directamente daría la "
      "media geométrica y escondería los picos: con ese atajo, %d de las %d "
      "combinaciones de medición y canal pasarían de ocupadas a libres%s. "
      "Se usa la potencia media y no la total porque sumar %d valores añade "
      "%.0f dB por pura aritmética y pondría sobre el umbral %d de las %d "
      "combinaciones."
      % (BINS_POR_CANAL, BINS_POR_CANAL, UMBRAL_OCUPACION_DBM, cambios,
         n * len(CANALES),
         (", y en el peor caso (%s, canal %s) el error llega a %.0f dB"
          % (peor_caso[1], peor_caso[2], peor_caso[0])) if peor_caso else "",
         BINS_POR_CANAL, offset,
         sum(int(np.sum(p_total[c] > UMBRAL_OCUPACION_DBM)) for c in CANALES),
         n * len(CANALES)))
    a("")

    columna_sin = anom.any()
    if columna_sin:
        a("| Canal | Banda | Puntos ocupados | Potencia media | Sin la medición saturada |")
        a("|---|---|---|---|---|")
    else:
        a("| Canal | Banda | Puntos ocupados | Potencia media |")
        a("|---|---|---|---|")
    for c in CANALES:
        fila = "| %s | %s | %d de %d (%.0f%%) | %.1f dBm |" % (
            c, banda(c), ocup(c), n, 100.0 * ocup(c) / n, glob(p_media[c]))
        if columna_sin:
            fila += " %.1f dBm |" % glob(p_media[c][~anom])
        a(fila)
    a("")

    orden_sin = sorted(CANALES, key=lambda c: glob(p_media[c][~anom]), reverse=True)
    a("El canal %s es el más contaminado: supera el umbral en %d de los %d "
      "puntos y su potencia media es %.1f dB mayor que la del canal más "
      "limpio. El canal %s es el menos contaminado, ocupado en %d puntos. El "
      "orden de mayor a menor es %s."
      % (peor, ocup(peor), n, glob(p_media[peor]) - glob(p_media[mejor]),
         mejor, ocup(mejor), ", ".join(ranking)))
    if columna_sin:
        a("")
        a("La potencia media es un promedio lineal entre puntos, y en ella "
          "pesa mucho la medición saturada. Sin ella el canal %s baja %.1f "
          "dB, pero el orden de los canales %s y el número de puntos "
          "ocupados, que es el criterio de decisión, no depende de ese "
          "efecto."
          % (max(CANALES, key=lambda c: glob(p_media[c]) - glob(p_media[c][~anom])),
             max(glob(p_media[c]) - glob(p_media[c][~anom]) for c in CANALES),
             "se mantiene" if orden_sin == ranking else "cambia"))
    a("")

    # Pico de DC del USRP en la frecuencia central (850 MHz, bin 512).
    def realce(k):
        return float(np.median(espectro[:, k] - 0.5 * (espectro[:, k - 2] + espectro[:, k + 2])))
    if realce(512) > float(np.percentile([realce(k) for k in range(2, N_BINS - 2)], 99)):
        sin_dc = espectro.copy()
        for k in range(508, 517):
            w = (k - 507) / 10.0
            sin_dc[:, k] = espectro[:, 507] + w * (espectro[:, 517] - espectro[:, 507])
        p_sin_dc, _ = potencia_por_canal(sin_dc)
        def cambio(c, primero):
            antes, despues = ocup(c), int(np.sum(p_sin_dc[c] > UMBRAL_OCUPACION_DBM))
            nombre = "el canal %s" % c if primero else "el %s" % c
            if antes == despues:
                return "%s se mantiene en %d" % (nombre, antes)
            return "%s pasa de %d a %d%s" % (nombre, antes, despues,
                                            " puntos ocupados" if primero else "")
        a("Se descartó que la contaminación del canal C sea un artefacto del "
          "receptor. El USRP deja un pequeño pico en su frecuencia central, "
          "850 MHz, justo en la frontera entre B y C; al retirarlo, %s y %s. "
          "El efecto es marginal y el espectro se conserva sin modificar."
          % (cambio("C", True), cambio("B", False)))
        a("")
    a("![Comparación de los cuatro canales](graficas/02_potencia_por_canal.png)")
    a("")
    a("*Figura 3. Potencia media de cada canal frente al umbral (izquierda) y "
      "dispersión de las %d mediciones (derecha).*" % n)
    a("")

    # -------------------------------------------------- frecuencias extremas
    umbral_lobulo = perfil[bp] - 10.0
    izq = bp
    while izq > 0 and perfil[izq - 1] >= umbral_lobulo:
        izq -= 1
    der = bp
    while der < N_BINS - 1 and perfil[der + 1] >= umbral_lobulo:
        der += 1
    lineal = ctx["perfil_lineal_dbm"]
    bl = int(np.argmax(lineal))
    estable = sum(
        int(np.argmax(np.median(np.delete(espectro, k, axis=0), axis=0)) == bp)
        for k in range(n))

    a("## 6. Frecuencias más y menos contaminadas")
    a("")
    a("La frecuencia más contaminada de todo el sistema es %.3f MHz, en el "
      "canal %s: su potencia mediana es %.1f dBm y supera el umbral en el "
      "%.0f%% del recorrido. No es un valor aislado sino parte de un bloque "
      "continuo de unos %.1f MHz (%.2f–%.2f MHz), el ancho típico de una "
      "portadora celular. La menos contaminada es %.3f MHz, en el canal %s, "
      "con mediana de %.1f dBm y solo %.0f%% de puntos sobre el umbral. "
      "Entre ambas hay %.1f dB de diferencia."
      % (frec[bp] / 1e6, "ABCD"[bp // BINS_POR_CANAL], perfil[bp], ocup_bp,
         (der - izq + 1) * ANCHO_BIN_HZ / 1e6, frec[izq] / 1e6, frec[der] / 1e6,
         frec[bm] / 1e6, "ABCD"[bm // BINS_POR_CANAL], perfil[bm], ocup_bm,
         perfil[bp] - perfil[bm]))
    a("")
    a("Para comparar frecuencias entre puntos se usa la mediana y no la media, "
      "porque la pregunta es qué frecuencia está contaminada en todo el "
      "recorrido y no en un solo lugar. La media la decide la medición "
      "saturada, que la llevaría a %.3f MHz; la mediana señala la misma "
      "frecuencia aunque se retire cualquiera de las %d mediciones (%d de %d "
      "pruebas)." % (frec[bl] / 1e6, n, estable, n))
    a("")
    a("![Frecuencias más y menos contaminadas](graficas/01_frecuencias_extremas.png)")
    a("")
    a("*Figura 4. Potencia mediana de la banda con la frecuencia más y la "
      "menos contaminada señaladas, y el detalle de cada una.*")
    a("")

    # ----------------------------------------------------- recomendacion
    otros = [c for c in ranking if c not in (peor, mejor)]
    oc = {c: set(np.where(p_media[c] > UMBRAL_OCUPACION_DBM)[0]) for c in CANALES}
    a("## 7. Recomendación técnica para la ANE")
    a("")
    a("Con base en la ocupación medida en los %d puntos del recorrido, se "
      "recomienda a la Agencia:" % n)
    a("")
    a("- **Canal %s (%s): priorizarlo para nuevas asignaciones.** Es el más "
      "limpio de la banda, ocupado solo en el %.0f%% de los puntos."
      % (mejor, banda(mejor), 100.0 * ocup(mejor) / n))
    for c in reversed(otros):
        es_segundo = c == otros[-1]
        compartidos = len(oc[c] & oc[[x for x in otros if x != c][0]])
        if es_segundo:
            otro = [x for x in otros if x != c][0]
            a("- **Canal %s (%s): utilizable como segunda opción.** Está "
              "ocupado en el %.0f%% de los puntos, y %s también lo están en "
              "el canal %s: ambos responden a los mismos emisores, y en el "
              "resto del recorrido el canal está libre."
              % (c, banda(c), 100.0 * ocup(c) / n,
                 ("todos esos puntos" if compartidos == ocup(c)
                  else "%d de sus %d puntos ocupados" % (compartidos, ocup(c))),
                 otro))
        else:
            a("- **Canal %s (%s): no recomendado para despliegues nuevos sin "
              "coordinación.** Está ocupado en el %.0f%% de los puntos, tiene "
              "la segunda potencia más alta de la banda y limita con el canal "
              "%s, con riesgo de interferencia de canal adyacente."
              % (c, banda(c), 100.0 * ocup(c) / n, peor))
    a("- **Canal %s (%s): no asignar.** Está ocupado en el %.0f%% del "
      "recorrido; cualquier asignación nueva sufriría interferencia en "
      "prácticamente toda el área medida."
      % (peor, banda(peor), 100.0 * ocup(peor) / n))
    a("")
    a("Dentro del plan, conviene evitar la vecindad de %.3f MHz y usar %.3f "
      "MHz como referencia de piso de ruido en futuras campañas. El estudio "
      "tiene dos límites: cubre %.1f km del occidente de la ciudad y no es "
      "extrapolable al resto del Valle de Aburrá, y son mediciones puntuales "
      "a lo largo de un recorrido, que no capturan la variación de la "
      "ocupación según la hora."
      % (frec[bp] / 1e6, frec[bm] / 1e6, ctx["recorrido_km"]))
    a("")

    # --------------------------------------------------- bonificacion
    if fuentes:
        from fuentes import FRACCION_DECIL, R2_CONCLUYENTE, zona_incidencia

        a("## 8. Bonificación: origen de la contaminación")
        a("")
        max_r2 = max(f["tri_r2"] for f in fuentes)
        negativos = [f["canal"] for f in fuentes if f["tri_n_libre"] < 0]
        sep_eb = distancia_haversine_km(ESTACIONES_BASE[0]["lat"], ESTACIONES_BASE[0]["lon"],
                                        ESTACIONES_BASE[1]["lat"], ESTACIONES_BASE[1]["lon"])
        a("El primer intento fue la trilateración: suponer un único emisor "
          "cuya potencia cae con el logaritmo de la distancia y buscar, en "
          "una malla sobre la ciudad, el punto que mejor explica las %d "
          "mediciones. No funcionó en ningún canal: el mejor ajuste explica "
          "el %.0f%% de la variación, lejos del %.0f%% exigido%s. La razón es "
          "que no hay un emisor sino varios, y en este recorrido se "
          "identificaron dos estaciones base a %.1f km una de otra. A eso se "
          "suma que la ruta es casi una línea, lo que impide ver la fuente "
          "desde ángulos distintos, y que los edificios hacen variar la "
          "potencia más de %.0f dB entre puntos."
          % (fuentes[0]["n_usados"], 100 * max_r2, 100 * R2_CONCLUYENTE,
             (", y en %s el ajuste indica que la potencia crecería con la "
              "distancia" % ("los canales " + ", ".join(negativos)
                              if len(negativos) > 1 else "el canal " + negativos[0]))
             if negativos else "",
             sep_eb, min(f["dinamica_db"] for f in fuentes)))
        a("")

        utiles = np.array([q == "buena" for q in calidad])
        movs = []
        for c in CANALES:
            args = (lat[utiles], lon[utiles], p_media[c][utiles])
            ref = zona_incidencia(*args)
            for fr in (0.15, 0.20, 0.25, 0.33, 0.50):
                z = zona_incidencia(*args, fraccion=fr)
                movs.append(distancia_haversine_km(ref["lat"], ref["lon"], z["lat"], z["lon"]))
        a("Se usó entonces un estimador más simple: el centro de las %d "
          "mediciones más fuertes de cada canal (el %.0f%% superior), "
          "ponderado por su potencia en mW. No localiza una antena, sino la "
          "zona desde donde llega la energía dominante. Los centros obtenidos "
          "son %s. El resultado no depende del tamaño del grupo: con entre el "
          "15%% y el 50%% de las mediciones ningún centro se mueve más de "
          "%.1f km, porque en escala lineal los puntos débiles casi no pesan."
          % (fuentes[0]["n_puntos"], 100 * FRACCION_DECIL,
             "; ".join("canal %s en %.5f, %.5f" % (f["canal"], f["lat"], f["lon"])
                       for f in fuentes),
             max(movs)))
        a("")

        for f in fuentes:
            f["_dist_eb"] = [distancia_haversine_km(eb["lat"], eb["lon"], f["lat"], f["lon"])
                             for eb in ESTACIONES_BASE]
        cerca = min(fuentes, key=lambda f: min(f["_dist_eb"]))
        k0 = int(np.argmin(cerca["_dist_eb"]))
        apuntan = [f for f in fuentes if f is not cerca and min(f["_dist_eb"]) < 1.0]
        mezcla = [f for f in fuentes if min(f["_dist_eb"]) >= 1.0]
        texto = ("Las estaciones base ubicadas en Street View validan el método. "
                 "El centro del canal %s queda a %.0f m de la antena %s, "
                 "prácticamente sobre ella."
                 % (cerca["canal"], 1000 * min(cerca["_dist_eb"]),
                    "de Guayabal" if k0 == 0 else "Sur"))
        if apuntan:
            texto += (" Los de %s apuntan a la misma antena, a menos de %.1f km."
                      % (" y ".join("%s" % f["canal"] for f in apuntan),
                         max(min(f["_dist_eb"]) for f in apuntan)))
        if mezcla:
            texto += (" El del canal %s, en cambio, cae entre las dos antenas, "
                      "a %s de cada una: ese canal recibe energía de ambas y "
                      "el promedio las mezcla. Es el límite de este método "
                      "cuando hay más de un emisor."
                      % (", ".join(f["canal"] for f in mezcla),
                         " y ".join("%.1f km" % d for d in mezcla[0]["_dist_eb"])))
        a(texto)
        a("")
        a("Para localizar cada emisor con precisión haría falta un recorrido "
          "que rodee las zonas de mayor potencia, en lugar de atravesarlas, "
          "y una antena directiva que mida desde qué dirección llega la señal.")
        a("")

    with open(ruta, "w", encoding="utf-8") as fo:
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
