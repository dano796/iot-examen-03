"""Genera las graficas estaticas del informe escrito.

El dashboard es interactivo y no sirve para un documento impreso, asi que
las figuras que exige el enunciado se producen aparte, en PNG, a partir de
los mismos arreglos que dejo el ETL en salida/.

Figura obligatoria del enunciado:

    01_frecuencias_extremas.png
        "Grafica de la frecuencia mas contaminada y la menos contaminada
        en todo el sistema"

Figuras de apoyo, para sustentar las secciones 3 a 5 del informe:

    02_potencia_por_canal.png    comparacion de los cuatro canales
    03_temperatura.png           temperatura vs orden y vs piso de ruido
    04_ruta.png                  recorrido de la estacion movil

Uso:
    .venv/bin/python graficas.py      ->  salida/graficas/*.png
"""

import csv
import os

import matplotlib
matplotlib.use("Agg")          # backend sin ventana: solo escribe archivos
import matplotlib.pyplot as plt
import numpy as np

from etl import (ANCHO_BIN_HZ, CANALES, CARPETA_SALIDA, FREC_INICIAL_HZ,
                 UMBRAL_OCUPACION_DBM, correlacion, dbm_a_mw, mw_a_dbm)

CARPETA_GRAFICAS = os.path.join(CARPETA_SALIDA, "graficas")

# Paleta pensada para impresion: fondo blanco y colores que se distinguen
# tambien en escala de grises.
COLOR_PICO = "#c0392b"
COLOR_LIMPIA = "#1e8449"
COLOR_PERFIL = "#34495e"
COLOR_UMBRAL = "#e67e22"
COLORES_CANAL = {"A": "#5dade2", "B": "#58d68d", "C": "#ec7063", "D": "#f5b041"}

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


def cargar():
    """Lee lo que produjo el ETL."""
    filas = list(csv.DictReader(open(os.path.join(CARPETA_SALIDA, "indicadores.csv"))))
    d = dict(
        filas=filas,
        archivo=[f["archivo"] for f in filas],
        orden=np.array([int(f["orden"]) for f in filas]),
        lat=np.array([float(f["latitud"]) for f in filas]),
        lon=np.array([float(f["longitud"]) for f in filas]),
        temp=np.array([float(f["temperatura"]) for f in filas]),
        piso=np.array([float(f["piso_ruido_dbm"]) for f in filas]),
        calidad=[f["calidad"] for f in filas],
    )
    for c in CANALES:
        d["p_%s" % c] = np.array([float(f["p_media_%s" % c]) for f in filas])
        d["ocupado_%s" % c] = np.array([int(f["ocupado_%s" % c]) for f in filas])
    d["espectro"] = np.load(os.path.join(CARPETA_SALIDA, "espectro_limpio.npy"))
    d["frecuencias"] = np.load(os.path.join(CARPETA_SALIDA, "frecuencias_hz.npy"))
    d["perfil"] = np.load(os.path.join(CARPETA_SALIDA, "perfil_mediano_dbm.npy"))
    d["bin_peor"] = int(np.argmax(d["perfil"]))
    d["bin_mejor"] = int(np.argmin(d["perfil"]))
    return d


def guardar(fig, nombre):
    ruta = os.path.join(CARPETA_GRAFICAS, nombre)
    fig.savefig(ruta, bbox_inches="tight")
    plt.close(fig)
    print("  %s" % nombre)
    return ruta


# --------------------------------------------------------------------------
# Figura 1: frecuencia mas y menos contaminada  (exigida por el enunciado)
# --------------------------------------------------------------------------

def figura_frecuencias_extremas(d):
    """Tres paneles: contexto de toda la banda y detalle de cada extremo.

    El panel superior situa ambas frecuencias dentro del espectro completo;
    los inferiores amplian +-0.5 MHz alrededor de cada una para que se vea
    la forma de la portadora y no solo un punto.
    """
    frec = d["frecuencias"] / 1e6
    perfil = d["perfil"]
    bp, bm = d["bin_peor"], d["bin_mejor"]

    fig = plt.figure(figsize=(11, 8))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.35, 1], hspace=0.35, wspace=0.22)

    # ---- panel superior: banda completa
    ax = fig.add_subplot(gs[0, :])
    for canal, (ini, fin) in CANALES.items():
        ax.axvspan(frec[ini], frec[fin - 1], color=COLORES_CANAL[canal],
                   alpha=0.13, zorder=0)
        ax.text((frec[ini] + frec[fin - 1]) / 2, perfil.max() + 2.5,
                "Canal %s" % canal, ha="center", fontsize=10, fontweight="bold",
                color="#555")

    ax.plot(frec, perfil, color=COLOR_PERFIL, linewidth=0.9,
            label="Perfil mediano de las %d mediciones" % len(d["archivo"]))
    ax.axhline(UMBRAL_OCUPACION_DBM, color=COLOR_UMBRAL, linestyle="--",
               linewidth=1.2,
               label="Umbral de ocupacion (%.0f dBm)" % UMBRAL_OCUPACION_DBM)

    ax.plot(frec[bp], perfil[bp], "v", color=COLOR_PICO, markersize=11,
            zorder=5, label="Mas contaminada: %.3f MHz (%.1f dBm)"
                            % (frec[bp], perfil[bp]))
    ax.plot(frec[bm], perfil[bm], "^", color=COLOR_LIMPIA, markersize=11,
            zorder=5, label="Menos contaminada: %.3f MHz (%.1f dBm)"
                            % (frec[bm], perfil[bm]))

    ax.annotate("", xy=(frec[bp], perfil[bp]), xytext=(frec[bp], perfil[bm]),
                arrowprops=dict(arrowstyle="<->", color="#7f8c8d", lw=1.1))
    ax.text(frec[bp] + 0.35, (perfil[bp] + perfil[bm]) / 2,
            "%.1f dB" % (perfil[bp] - perfil[bm]),
            color="#7f8c8d", fontsize=10, fontweight="bold", va="center")

    ax.set_xlim(frec[0], frec[-1])
    ax.set_ylim(perfil.min() - 15, perfil.max() + 6)
    ax.set_xlabel("Frecuencia (MHz)")
    ax.set_ylabel("Potencia mediana (dBm)")
    ax.set_title("Ocupacion tipica de la banda 840 - 860 MHz (mediana por frecuencia)")
    ax.legend(loc="lower center", ncol=2, fontsize=8.5,
              framealpha=0.95, borderpad=0.7)

    # ---- paneles inferiores: detalle de cada extremo
    for col, (b, color, titulo) in enumerate((
            (bp, COLOR_PICO, "Detalle: frecuencia mas contaminada"),
            (bm, COLOR_LIMPIA, "Detalle: frecuencia menos contaminada"))):
        axd = fig.add_subplot(gs[1, col])
        margen = int(0.5e6 / ANCHO_BIN_HZ)          # +-0.5 MHz
        lo, hi = max(0, b - margen), min(len(frec), b + margen + 1)

        axd.plot(frec[lo:hi], perfil[lo:hi], color=COLOR_PERFIL, linewidth=1.3)
        axd.fill_between(frec[lo:hi], perfil.min() - 4, perfil[lo:hi],
                         color=color, alpha=0.16)
        axd.axvline(frec[b], color=color, linestyle=":", linewidth=1.5)
        axd.plot(frec[b], perfil[b], "o", color=color, markersize=8, zorder=5)
        axd.axhline(UMBRAL_OCUPACION_DBM, color=COLOR_UMBRAL, linestyle="--",
                    linewidth=1)
        axd.annotate("%.3f MHz\n%.1f dBm" % (frec[b], perfil[b]),
                     xy=(frec[b], perfil[b]), xytext=(8, -4),
                     textcoords="offset points", fontsize=9,
                     fontweight="bold", color=color)
        axd.set_xlabel("Frecuencia (MHz)")
        axd.set_ylabel("Potencia (dBm)")
        axd.set_title(titulo, fontsize=10.5)
        axd.set_ylim(perfil.min() - 4, perfil.max() + 4)

    fig.suptitle("Frecuencias extremas del sistema", fontsize=14,
                 fontweight="bold", y=0.985)
    return guardar(fig, "01_frecuencias_extremas.png")


# --------------------------------------------------------------------------
# Figura 2: potencia por canal
# --------------------------------------------------------------------------

def figura_canales(d):
    """Barras de potencia media global y dispersion por canal."""
    canales = list(CANALES)
    globales = [float(mw_a_dbm(dbm_a_mw(d["p_%s" % c]).mean())) for c in canales]
    ocupacion = [100.0 * d["ocupado_%s" % c].sum() / len(d["archivo"])
                 for c in canales]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))

    # --- izquierda: potencia media con el umbral
    base = UMBRAL_OCUPACION_DBM - 12
    barras = ax1.bar(canales, [v - base for v in globales], bottom=base,
                     color=[COLORES_CANAL[c] for c in canales],
                     edgecolor="#2c3e50", linewidth=0.8)
    ax1.axhline(UMBRAL_OCUPACION_DBM, color=COLOR_UMBRAL, linestyle="--",
                linewidth=1.3,
                label="Umbral (%.0f dBm)" % UMBRAL_OCUPACION_DBM)
    for b, v in zip(barras, globales):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.8, "%.1f" % v,
                 ha="center", fontsize=10, fontweight="bold")
    ax1.set_ylabel("Potencia media por Parseval (dBm)")
    ax1.set_xlabel("Canal")
    ax1.set_title("Potencia de ocupacion por canal")
    ax1.set_ylim(base, max(globales) + 8)
    ax1.legend(fontsize=9)

    # --- derecha: dispersion de las 61 mediciones
    datos = [d["p_%s" % c] for c in canales]
    partes = ax2.boxplot(datos, tick_labels=canales, patch_artist=True,
                          widths=0.55)
    for parche, c in zip(partes["boxes"], canales):
        parche.set_facecolor(COLORES_CANAL[c])
        parche.set_alpha(0.65)
    for mediana in partes["medians"]:
        mediana.set_color("#2c3e50")
        mediana.set_linewidth(1.6)
    ax2.axhline(UMBRAL_OCUPACION_DBM, color=COLOR_UMBRAL, linestyle="--",
                linewidth=1.3)
    for i, pct in enumerate(ocupacion):
        ax2.text(i + 1, max(datos[i]) + 2.5, "%.0f%%" % pct, ha="center",
                 fontsize=9.5, fontweight="bold", color="#2c3e50")
    ax2.set_ylabel("Potencia por medicion (dBm)")
    ax2.set_xlabel("Canal")
    ax2.set_title("Dispersion y % de puntos ocupados")

    fig.suptitle("Comparacion de los cuatro canales de 5 MHz",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    return guardar(fig, "02_potencia_por_canal.png")


# --------------------------------------------------------------------------
# Figura 3: temperatura
# --------------------------------------------------------------------------

def figura_temperatura(d):
    """Sustenta la seccion 4: la temperatura sigue al recorrido, no al lugar."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    r_orden = correlacion(d["temp"], d["orden"])
    r_piso = correlacion(d["temp"], d["piso"])

    ax1.plot(d["orden"], d["temp"], "o-", color="#c0392b", markersize=4,
             linewidth=1.2)
    ax1.set_xlabel("Orden de la medicion (001 -> 061)")
    ax1.set_ylabel("Temperatura del sensor (C)")
    ax1.set_title("Temperatura a lo largo de la jornada\n(r = %+.3f)" % r_orden,
                  fontsize=11)

    ax2.scatter(d["temp"], d["piso"], c=d["orden"], cmap="plasma", s=38,
                edgecolor="#2c3e50", linewidth=0.4)
    coef = np.polyfit(d["temp"], d["piso"], 1)
    xs = np.linspace(d["temp"].min(), d["temp"].max(), 50)
    ax2.plot(xs, np.polyval(coef, xs), "--", color="#2c3e50", linewidth=1.3)
    ax2.set_xlabel("Temperatura del sensor (C)")
    ax2.set_ylabel("Piso de ruido, percentil 10 (dBm)")
    ax2.set_title("Temperatura vs piso de ruido\n(r = %+.3f, incidencia debil)"
                  % r_piso, fontsize=11)
    barra = fig.colorbar(ax2.collections[0], ax=ax2)
    barra.set_label("Orden de medicion", fontsize=9)

    fig.suptitle("Incidencia de la temperatura del sensor en la calidad",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    return guardar(fig, "03_temperatura.png")


# --------------------------------------------------------------------------
# Figura 4: ruta
# --------------------------------------------------------------------------

def figura_ruta(d):
    """Recorrido de la estacion, coloreado por potencia del canal peor."""
    peor = max(CANALES, key=lambda c: float(dbm_a_mw(d["p_%s" % c]).mean()))
    pot = d["p_%s" % peor]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5.2))

    # --- izquierda: ruta con calidad del dato
    ax1.plot(d["lon"], d["lat"], "-", color="#95a5a6", linewidth=1.2, zorder=1)
    colores = {"buena": "#1e8449", "degradada": "#e67e22", "imputada": "#c0392b"}
    for etiqueta, color in colores.items():
        idx = [i for i, q in enumerate(d["calidad"]) if q == etiqueta]
        if idx:
            ax1.scatter(d["lon"][idx], d["lat"][idx], c=color, s=45,
                        edgecolor="white", linewidth=0.6, zorder=3,
                        label="%s (%d)" % (etiqueta, len(idx)))
    for etiqueta, idx, desplazamiento in (("inicio", 0, (14, 10)),
                                          ("fin", -1, (-30, -16))):
        ax1.annotate(etiqueta, (d["lon"][idx], d["lat"][idx]),
                     textcoords="offset points", xytext=desplazamiento,
                     fontsize=9, fontweight="bold",
                     arrowprops=dict(arrowstyle="-", lw=0.8, color="#555"))
    ax1.set_xlabel("Longitud")
    ax1.set_ylabel("Latitud")
    ax1.set_title("Ruta y calidad del dato")
    ax1.legend(fontsize=8.5)
    ax1.set_aspect("equal", adjustable="datalim")

    # --- derecha: misma ruta coloreada por potencia
    disp = ax2.scatter(d["lon"], d["lat"], c=pot, cmap="inferno", s=55,
                       edgecolor="#2c3e50", linewidth=0.4, zorder=3)
    ax2.plot(d["lon"], d["lat"], "-", color="#bdc3c7", linewidth=1, zorder=1)
    barra = fig.colorbar(disp, ax=ax2)
    barra.set_label("Potencia canal %s (dBm)" % peor, fontsize=9)
    ax2.set_xlabel("Longitud")
    ax2.set_ylabel("Latitud")
    ax2.set_title("Potencia del canal %s sobre el recorrido" % peor)
    ax2.set_aspect("equal", adjustable="datalim")

    fig.suptitle("Recorrido de la estacion movil de monitoreo",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    return guardar(fig, "04_ruta.png")


def main():
    os.makedirs(CARPETA_GRAFICAS, exist_ok=True)
    d = cargar()
    print("Generando graficas en %s" % CARPETA_GRAFICAS)
    figura_frecuencias_extremas(d)
    figura_canales(d)
    figura_temperatura(d)
    figura_ruta(d)
    print("Listo.")


if __name__ == "__main__":
    main()
