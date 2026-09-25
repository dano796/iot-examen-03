"""Estimacion por extrapolacion del origen geografico de la contaminacion.

Bonificacion del Examen 03: ubicar en el mapa de donde sale la energia que
contamina cada canal.

Se aplican DOS metodos y se reportan ambos, porque el primero -que es el
rigurosamente correcto si hubiera un unico emisor- resulta no concluyente
con estos datos, y ese resultado negativo es en si mismo un hallazgo.

METODO 1: inversion log-distancia (trilateracion por minimos cuadrados)
----------------------------------------------------------------------
La potencia recibida de un emisor fijo cae segun el modelo log-distancia:

    P(d) = P0 - 10 * n * log10(d)   =>   P = P0 + n * x  con x = -10*log10(d)

Se barre una malla de candidatos sobre el area; para cada uno se calculan
las distancias a los puntos medidos y se ajusta esa recta por minimos
cuadrados. El candidato cuya nube (x, P) quede mas cerca de una recta con
pendiente n fisicamente plausible seria la posicion del emisor.

METODO 2: centroide ponderado del decil superior
------------------------------------------------
Se toman las mediciones del decil mas alto de potencia del canal y se
calcula su centroide ponderado por potencia lineal. No localiza un
transmisor: delimita la ZONA DE MAXIMA INCIDENCIA, es decir hacia donde
esta la energia dominante vista desde el corredor recorrido. La dispersion
de esos puntos alrededor del centroide se reporta como radio de
incertidumbre.

POR QUE EL METODO 1 FALLA AQUI
------------------------------
Una banda celular no tiene un emisor sino decenas de estaciones base
repartidas por la ciudad, de modo que el campo agregado no decae desde un
punto unico. A eso se suma que el recorrido es practicamente un corredor
lineal y que la dinamica local supera los 60 dB por sombreado urbano, con
lo que el problema inverso queda mal condicionado. La evidencia concreta
esta en la salida: dejando n libre, el mejor ajuste da pendiente NEGATIVA
(la potencia creceria con la distancia), lo que significa que el
optimizador encuentra un minimo local de campo y no una fuente.
"""

import csv
import math
import os

import numpy as np

from etl import CANALES, CARPETA_SALIDA

# Malla de busqueda del metodo 1. Se abre mas que el recorrido medido porque
# el emisor casi con seguridad esta fuera del corredor.
LAT_MIN, LAT_MAX = 6.05, 6.40
LON_MIN, LON_MAX = -75.75, -75.45
PASO_MALLA = 160          # 160 x 160 = 25600 candidatos

# Exponente de perdida de trayecto aceptable. Por debajo de 1.5 la senal
# caeria mas lento que en espacio libre y por encima de 6 el ajuste ya no
# describe propagacion sino ruido.
N_MIN, N_MAX = 1.5, 6.0

# R^2 minimo para considerar que una trilateracion localizo algo.
R2_CONCLUYENTE = 0.60

# Fraccion superior de mediciones que define la zona de maxima incidencia.
FRACCION_DECIL = 0.10

D_MINIMA_KM = 0.02
RADIO_TIERRA_KM = 6371.0


def haversine_vectorizado(lat_ref, lon_ref, lats, lons):
    """Distancia en km de un punto a un arreglo de puntos."""
    p1 = math.radians(lat_ref)
    p2 = np.radians(lats)
    dp = p2 - p1
    dl = np.radians(lons - lon_ref)
    a = np.sin(dp / 2.0) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2 * RADIO_TIERRA_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def ajustar_recta(x, y):
    """Minimos cuadrados y = a + b*x. Devuelve (a, b, r2)."""
    n = len(x)
    sx, sy = x.sum(), y.sum()
    sxx = float((x * x).sum())
    sxy = float((x * y).sum())
    den = n * sxx - sx * sx
    if abs(den) < 1e-12:
        return 0.0, 0.0, 0.0
    b = (n * sxy - sx * sy) / den
    a = (sy - b * sx) / n
    pred = a + b * x
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return a, b, r2


def trilaterar(lats, lons, potencias_dbm, n_libre=False):
    """Metodo 1: barre la malla buscando el mejor ajuste log-distancia.

    Con n_libre=True no se exige que la pendiente sea fisicamente plausible;
    sirve de diagnostico: si el optimo global tiene n negativo, el modelo de
    fuente unica no aplica a estos datos.
    """
    rejilla_lat = np.linspace(LAT_MIN, LAT_MAX, PASO_MALLA)
    rejilla_lon = np.linspace(LON_MIN, LON_MAX, PASO_MALLA)
    mejor = dict(lat=None, lon=None, r2=-1.0, n=None, p0=None)

    for lat_c in rejilla_lat:
        for lon_c in rejilla_lon:
            d = np.maximum(haversine_vectorizado(lat_c, lon_c, lats, lons), D_MINIMA_KM)
            x = -10.0 * np.log10(d)
            p0, n, r2 = ajustar_recta(x, potencias_dbm)
            if r2 > mejor["r2"] and (n_libre or N_MIN <= n <= N_MAX):
                mejor = dict(lat=float(lat_c), lon=float(lon_c),
                             r2=float(r2), n=float(n), p0=float(p0))
    return mejor


def zona_incidencia(lats, lons, potencias_dbm, fraccion=FRACCION_DECIL):
    """Metodo 2: centroide ponderado por potencia del decil superior.

    Se pondera en escala LINEAL (mW) y no en dBm: la ponderacion debe ser
    proporcional a la energia, no a su logaritmo. Los pesos se normalizan
    respecto al maximo del subconjunto para que el calculo no dependa de la
    escala absoluta.
    """
    k = max(3, int(round(len(potencias_dbm) * fraccion)))
    top = np.argsort(potencias_dbm)[::-1][:k]

    pesos = np.power(10.0, (potencias_dbm[top] - potencias_dbm[top].max()) / 10.0)
    lat_c = float(np.sum(lats[top] * pesos) / np.sum(pesos))
    lon_c = float(np.sum(lons[top] * pesos) / np.sum(pesos))

    dispersion = haversine_vectorizado(lat_c, lon_c, lats[top], lons[top])

    # Contraste: centroide del decil INFERIOR. Si ambos centroides caen en el
    # mismo sitio, el canal no tiene gradiente espacial y la estimacion no
    # significa nada.
    bajo = np.argsort(potencias_dbm)[:k]
    lat_b = float(lats[bajo].mean())
    lon_b = float(lons[bajo].mean())
    separacion = float(haversine_vectorizado(lat_c, lon_c,
                                             np.array([lat_b]), np.array([lon_b]))[0])

    return dict(lat=lat_c, lon=lon_c,
                n_puntos=int(k),
                radio_km=float(dispersion.mean()),
                radio_max_km=float(dispersion.max()),
                lat_debil=lat_b, lon_debil=lon_b,
                separacion_km=separacion,
                dinamica_db=float(potencias_dbm.max() - potencias_dbm.min()))


def estimar_todo(verbose=True):
    """Corre los dos metodos sobre los cuatro canales y escribe el CSV."""
    filas = list(csv.DictReader(open(os.path.join(CARPETA_SALIDA, "indicadores.csv"))))

    # Se excluyen las mediciones sin posicion confiable: una fuente se
    # localiza por geometria, asi que un punto mal georreferenciado desplaza
    # la estimacion. Quedan fuera 017.txt (HDOP 17.3) y la imputada.
    utiles = [f for f in filas if f["calidad"] == "buena"]
    lats = np.array([float(f["latitud"]) for f in utiles])
    lons = np.array([float(f["longitud"]) for f in utiles])
    n_usados, n_total = len(utiles), len(filas)
    if verbose:
        print("Puntos usados: %d de %d\n" % (n_usados, n_total))

    resultados = []
    for canal in CANALES:
        pot = np.array([float(f["p_media_%s" % canal]) for f in utiles])

        tri = trilaterar(lats, lons, pot)
        diag = trilaterar(lats, lons, pot, n_libre=True)
        zona = zona_incidencia(lats, lons, pot)
        concluyente = tri["r2"] >= R2_CONCLUYENTE and diag["n"] > 0

        if verbose:
            print("Canal %s" % canal)
            print("  [1] trilateracion : R2=%.3f n=%.2f -> %s"
                  % (tri["r2"], tri["n"],
                     "(%.5f, %.5f)" % (tri["lat"], tri["lon"]) if concluyente
                     else "NO CONCLUYENTE (optimo global con n=%+.2f)" % diag["n"]))
            print("  [2] zona incidencia: (%.5f, %.5f) radio %.2f km | "
                  "separacion al decil debil %.2f km | dinamica %.1f dB"
                  % (zona["lat"], zona["lon"], zona["radio_km"],
                     zona["separacion_km"], zona["dinamica_db"]))

        resultados.append(dict(
            canal=canal,
            lat=zona["lat"], lon=zona["lon"],
            radio_km=zona["radio_km"], radio_max_km=zona["radio_max_km"],
            n_puntos=zona["n_puntos"], separacion_km=zona["separacion_km"],
            dinamica_db=zona["dinamica_db"],
            lat_debil=zona["lat_debil"], lon_debil=zona["lon_debil"],
            tri_r2=tri["r2"], tri_n=tri["n"], tri_n_libre=diag["n"],
            tri_concluyente=int(concluyente),
            n_usados=n_usados, n_total=n_total))

    salida = os.path.join(CARPETA_SALIDA, "fuentes_estimadas.csv")
    with open(salida, "w", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=list(resultados[0].keys()))
        w.writeheader()
        for r in resultados:
            w.writerow({k: ("%.6f" % v if isinstance(v, float) else v)
                        for k, v in r.items()})
    if verbose:
        print("\nEscrito %s" % salida)
    return resultados


def main():
    return estimar_todo(verbose=True)


if __name__ == "__main__":
    main()
