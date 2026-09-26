"""Dashboard interactivo de ocupacion de espectro 840-860 MHz (Examen 03).

Servidor web (Dash) que visualiza el resultado del ETL sobre la ciudad de
Medellin:

    - Ubicacion de cada una de las 61 mediciones
    - Ruta seguida por la estacion movil
    - Mapa de calor por canal (A, B, C, D)
    - Mapa de calor de la temperatura del sistema de sensado
    - Mapa de calor de la frecuencia mas contaminada
    - Zona estimada de la fuente de contaminacion (bonificacion)

Consume lo que dejo etl.py en salida/. No recalcula nada: si hace falta
regenerar los indicadores hay que correr primero `python3 etl.py`, que a su
vez corre la estimacion de fuentes.

Uso:
    .venv/bin/python dashboard.py        ->  http://127.0.0.1:8050
    docker compose up -d --build         ->  http://<servidor>/  (gunicorn)

HOST y PORT se pueden cambiar por variables de entorno. En el contenedor lo
sirve gunicorn a traves de `server`, no app.run().
"""

import csv
import os

import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

from etl import (ANCHO_BIN_HZ, CANALES, CARPETA_SALIDA, FREC_INICIAL_HZ,
                 UMBRAL_OCUPACION_DBM, dbm_a_mw, mw_a_dbm)

# --------------------------------------------------------------------------
# Carga de datos
# --------------------------------------------------------------------------

def cargar():
    """Lee todo lo que produjo el ETL y lo deja en arreglos de numpy."""
    filas = list(csv.DictReader(open(os.path.join(CARPETA_SALIDA, "indicadores.csv"))))

    datos = dict(
        archivo=[f["archivo"] for f in filas],
        orden=np.array([int(f["orden"]) for f in filas]),
        lat=np.array([float(f["latitud"]) for f in filas]),
        lon=np.array([float(f["longitud"]) for f in filas]),
        alt=np.array([float(f["altura"]) for f in filas]),
        temp=np.array([float(f["temperatura"]) for f in filas]),
        hdop=np.array([float(f["error_distancia"]) for f in filas]),
        calidad=[f["calidad"] for f in filas],
    )
    for c in CANALES:
        datos["p_%s" % c] = np.array([float(f["p_media_%s" % c]) for f in filas])
        datos["ocupado_%s" % c] = np.array([int(f["ocupado_%s" % c]) for f in filas])

    datos["espectro"] = np.load(os.path.join(CARPETA_SALIDA, "espectro_limpio.npy"))
    datos["frecuencias"] = np.load(os.path.join(CARPETA_SALIDA, "frecuencias_hz.npy"))
    datos["perfil"] = np.load(os.path.join(CARPETA_SALIDA, "perfil_mediano_dbm.npy"))

    # Bin mas y menos contaminado de todo el sistema.
    datos["bin_peor"] = int(np.argmax(datos["perfil"]))
    datos["bin_mejor"] = int(np.argmin(datos["perfil"]))
    # Potencia de la frecuencia pico en cada punto medido: esa es la capa
    # "mapa de calor para la frecuencia mas contaminada".
    datos["p_pico"] = datos["espectro"][:, datos["bin_peor"]]

    ruta_fuentes = os.path.join(CARPETA_SALIDA, "fuentes_estimadas.csv")
    datos["fuentes"] = (list(csv.DictReader(open(ruta_fuentes)))
                        if os.path.exists(ruta_fuentes) else [])
    return datos


D = cargar()

CENTRO_LAT = float(D["lat"].mean())
CENTRO_LON = float(D["lon"].mean())

# Capas disponibles: cada una define que variable se pinta y con que escala.
# "escala_fija" fuerza el rango de color para que los cuatro canales sean
# comparables entre si de un vistazo.
CAPAS = {
    "ruta": dict(
        etiqueta="Ubicaciones y ruta de la estacion",
        variable=None, unidad="", escala=None, escala_fija=None,
        explicacion="Los 61 puntos donde la estacion movil se detuvo a medir, "
                    "unidos en orden cronologico. El color indica si la "
                    "POSICION de la medicion es confiable, no la potencia "
                    "recibida: el espectro esta integro en las 61.",
        leyenda=[("#3fb950", "Buena: GPS y espectro correctos"),
                 ("#d29922", "Degradada: posicion imprecisa (HDOP alto)"),
                 ("#f85149", "Imputada: GPS sin fix, posicion interpolada")]),
    "canal_A": dict(
        etiqueta="Canal A - 840 a 845 MHz", variable="p_A",
        unidad="dBm", escala="Inferno", escala_fija=True,
        explicacion="Potencia de ocupacion del canal A en cada punto, "
                    "integrada por Parseval sobre sus 256 bins. Zonas claras "
                    "= mas contaminado.", leyenda=None),
    "canal_B": dict(
        etiqueta="Canal B - 845 a 850 MHz", variable="p_B",
        unidad="dBm", escala="Inferno", escala_fija=True,
        explicacion="Potencia de ocupacion del canal B en cada punto, "
                    "integrada por Parseval sobre sus 256 bins. Zonas claras "
                    "= mas contaminado.", leyenda=None),
    "canal_C": dict(
        etiqueta="Canal C - 850 a 855 MHz", variable="p_C",
        unidad="dBm", escala="Inferno", escala_fija=True,
        explicacion="Potencia de ocupacion del canal C en cada punto, "
                    "integrada por Parseval sobre sus 256 bins. Zonas claras "
                    "= mas contaminado.", leyenda=None),
    "canal_D": dict(
        etiqueta="Canal D - 855 a 860 MHz", variable="p_D",
        unidad="dBm", escala="Inferno", escala_fija=True,
        explicacion="Potencia de ocupacion del canal D en cada punto, "
                    "integrada por Parseval sobre sus 256 bins. Zonas claras "
                    "= mas contaminado.", leyenda=None),
    "temperatura": dict(
        etiqueta="Temperatura del sistema de sensado", variable="temp",
        unidad="C", escala="Turbo", escala_fija=False,
        explicacion="Temperatura interna del receptor USRP en cada parada. "
                    "El gradiente sigue el orden del recorrido, no la "
                    "geografia: es el equipo calentandose durante la jornada.",
        leyenda=None),
    "pico": dict(
        etiqueta="Frecuencia mas contaminada del sistema", variable="p_pico",
        unidad="dBm", escala="Inferno", escala_fija=False,
        explicacion="Potencia recibida unicamente en el bin de 19.53 kHz mas "
                    "contaminado de toda la banda (mayor mediana entre las "
                    "mediciones). Muestra donde se siente con mas fuerza.",
        leyenda=None),
}

# Rango comun de color para los cuatro canales.
_todas_pot = np.concatenate([D["p_%s" % c] for c in CANALES])
RANGO_CANALES = (float(np.percentile(_todas_pot, 2)), float(_todas_pot.max()))

COLOR_FONDO = "#11151c"
COLOR_PANEL = "#1a2029"
COLOR_TEXTO = "#e6edf3"
COLOR_TENUE = "#8b949e"
COLOR_ACENTO = "#58a6ff"


# --------------------------------------------------------------------------
# Figuras
# --------------------------------------------------------------------------

def figura_mapa(clave_capa, mostrar_ruta, mostrar_puntos, mostrar_fuente, radio):
    """Construye el mapa: densidad + ruta + puntos + fuente estimada."""
    capa = CAPAS[clave_capa]
    fig = go.Figure()

    if capa["variable"] is not None:
        valores = D[capa["variable"]]

        # Densitymap suma pesos, asi que no admite valores negativos (los dBm
        # se cancelarian). Se normaliza a 0..1 conservando el orden: el color
        # de la densidad es relativo, y la escala en dBm reales se muestra
        # en los puntos superpuestos.
        lo = float(np.percentile(valores, 2))
        hi = float(valores.max())
        peso = np.clip((valores - lo) / (hi - lo if hi > lo else 1.0), 0.0, 1.0)

        fig.add_trace(go.Densitymap(
            lat=D["lat"], lon=D["lon"], z=peso,
            radius=radio, colorscale=capa["escala"],
            opacity=0.75, showscale=False,
            hoverinfo="skip", name="densidad"))

    if mostrar_ruta:
        # La ruta va en el orden 001 -> 061, que es el orden cronologico.
        fig.add_trace(go.Scattermap(
            lat=D["lat"], lon=D["lon"], mode="lines",
            line=dict(width=2, color=COLOR_ACENTO),
            opacity=0.6, hoverinfo="skip", name="Ruta"))

    if mostrar_puntos:
        if capa["variable"] is not None:
            valores = D[capa["variable"]]
            rango = RANGO_CANALES if capa["escala_fija"] else (
                float(valores.min()), float(valores.max()))
            marcador = dict(size=9, color=valores, colorscale=capa["escala"],
                            cmin=rango[0], cmax=rango[1],
                            colorbar=dict(title=capa["unidad"],
                                          tickfont=dict(color=COLOR_TEXTO, size=14),
                                          title_font=dict(color=COLOR_TEXTO, size=14)))
            texto = ["%s<br>%s: %.2f %s<br>%.5f, %.5f<br>calidad del dato: %s"
                     % (a, capa["etiqueta"], v, capa["unidad"], la, lo_, q)
                     for a, v, la, lo_, q in zip(D["archivo"], valores,
                                                 D["lat"], D["lon"], D["calidad"])]
        else:
            # Capa "ruta": el color distingue la calidad del dato.
            mapa_color = {"buena": "#3fb950", "degradada": "#d29922",
                          "imputada": "#f85149"}
            marcador = dict(size=9, color=[mapa_color[q] for q in D["calidad"]])
            texto = ["%s (#%d)<br>%.5f, %.5f<br>alt %.0f m | HDOP %.1f<br>calidad del dato: %s"
                     % (a, o, la, lo_, al, h, q)
                     for a, o, la, lo_, al, h, q in zip(
                         D["archivo"], D["orden"], D["lat"], D["lon"],
                         D["alt"], D["hdop"], D["calidad"])]

        fig.add_trace(go.Scattermap(
            lat=D["lat"], lon=D["lon"], mode="markers",
            marker=marcador, text=texto, hoverinfo="text",
            customdata=np.arange(len(D["lat"])), name="Mediciones"))

    if mostrar_fuente and D["fuentes"] and clave_capa.startswith("canal_"):
        canal = clave_capa[-1]
        f = next((x for x in D["fuentes"] if x["canal"] == canal), None)
        if f:
            lat_f, lon_f = float(f["lat"]), float(f["lon"])
            detalle = ("Zona estimada de la fuente - canal %s<br>"
                       "Centroide ponderado del decil de mayor potencia<br>"
                       "Radio medio: %.2f km | Radio maximo: %.2f km<br>"
                       "Separacion al decil debil: %.2f km<br>"
                       "Trilateracion log-distancia: no concluyente (R2=%.2f)"
                       % (canal, float(f["radio_km"]), float(f["radio_max_km"]),
                          float(f["separacion_km"]), float(f["tri_r2"])))

            fig.add_trace(go.Scattermap(
                lat=[lat_f], lon=[lon_f], mode="markers",
                marker=dict(size=20, color="#ff7b72", opacity=0.95),
                hovertext=[detalle], hoverinfo="text",
                name="Fuente estimada %s" % canal))

    fig.update_layout(
        map=dict(style="open-street-map",
                 center=dict(lat=CENTRO_LAT, lon=CENTRO_LON), zoom=11.3),
        margin=dict(l=0, r=0, t=0, b=0), height=640,
        showlegend=False, paper_bgcolor=COLOR_FONDO)
    return fig


def figura_espectro(indice):
    """Espectro completo de una medicion, con los cuatro canales sombreados."""
    frec = D["frecuencias"] / 1e6
    fig = go.Figure()

    # Bandas de los canales, para leer de una vez en que bloque cae cada pico.
    # La etiqueta va abajo y centrada: arriba chocaba con las marcas de las
    # frecuencias extrema, que se dibujan al tope del area.
    for i, (canal, (ini, fin)) in enumerate(CANALES.items()):
        fig.add_vrect(x0=frec[ini], x1=frec[fin - 1],
                      fillcolor=["#58a6ff", "#3fb950", "#f85149", "#d29922"][i],
                      opacity=0.07, line_width=0,
                      annotation_text="Canal %s" % canal,
                      annotation_position="bottom",
                      annotation_font_color=COLOR_TENUE,
                      annotation_font_size=14)

    fig.add_trace(go.Scatter(
        x=frec, y=D["perfil"], mode="lines", name="Perfil mediano",
        line=dict(color=COLOR_TENUE, width=1, dash="dot")))

    if indice is not None:
        fig.add_trace(go.Scatter(
            x=frec, y=D["espectro"][indice], mode="lines",
            name=D["archivo"][indice], line=dict(color=COLOR_ACENTO, width=1.3)))

    fig.add_hline(y=UMBRAL_OCUPACION_DBM, line=dict(color="#ff7b72", dash="dash"),
                  annotation_text="umbral %.0f dBm" % UMBRAL_OCUPACION_DBM,
                  annotation_font_color="#ff7b72")

    # Marcas de la frecuencia mas y menos contaminada del sistema.
    for b, color, etiqueta in ((D["bin_peor"], "#ff7b72", "mas contaminada"),
                               (D["bin_mejor"], "#3fb950", "menos contaminada")):
        fig.add_vline(x=frec[b], line=dict(color=color, width=1, dash="dot"),
                      annotation_text="%.3f MHz (%s)" % (frec[b], etiqueta),
                      annotation_font_color=color, annotation_font_size=13)

    fig.update_layout(
        xaxis_title="Frecuencia (MHz)", yaxis_title="Potencia (dBm)",
        margin=dict(l=55, r=15, t=30, b=45), height=330,
        paper_bgcolor=COLOR_PANEL, plot_bgcolor=COLOR_PANEL,
        font=dict(color=COLOR_TEXTO, size=14),
        legend=dict(orientation="h", y=1.12, x=0),
        xaxis=dict(gridcolor="#2a313c"), yaxis=dict(gridcolor="#2a313c"))
    return fig


def figura_canales(indice):
    """Barras de potencia por canal: global y, si hay seleccion, del punto."""
    canales = list(CANALES)
    globales = [float(mw_a_dbm(dbm_a_mw(D["p_%s" % c]).mean())) for c in canales]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=canales, y=globales, name="Promedio del sistema",
                         marker_color=COLOR_TENUE,
                         text=["%.1f" % v for v in globales], textposition="outside"))
    if indice is not None:
        puntuales = [float(D["p_%s" % c][indice]) for c in canales]
        fig.add_trace(go.Bar(x=canales, y=puntuales, name=D["archivo"][indice],
                             marker_color=COLOR_ACENTO,
                             text=["%.1f" % v for v in puntuales],
                             textposition="outside"))

    fig.add_hline(y=UMBRAL_OCUPACION_DBM, line=dict(color="#ff7b72", dash="dash"))
    fig.update_layout(
        yaxis_title="Potencia media (dBm)", barmode="group",
        margin=dict(l=55, r=15, t=30, b=35), height=330,
        paper_bgcolor=COLOR_PANEL, plot_bgcolor=COLOR_PANEL,
        font=dict(color=COLOR_TEXTO, size=14),
        legend=dict(orientation="h", y=1.14, x=0),
        xaxis=dict(gridcolor="#2a313c"), yaxis=dict(gridcolor="#2a313c"))
    return fig


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Interfaz
# --------------------------------------------------------------------------

# Config comun de todos los graficos. responsive=True es obligatorio: sin el,
# Plotly conserva el ancho del primer render y al achicar la ventana el
# contenido se desborda y aparece cortado.
CONFIG_GRAFICO = dict(displayModeBar=False, responsive=True)

EST_PANEL = dict(background=COLOR_PANEL, borderRadius="10px",
                 border="1px solid #262d38", overflow="hidden")


def panel(titulo, descripcion, contenido, estilo=None):
    """Envuelve cualquier bloque en un panel con titulo y una linea que
    explica que se esta viendo. La idea es que nadie tenga que preguntar
    que significa cada grafico."""
    est = dict(EST_PANEL)
    est.update(estilo or {})
    return html.Div([
        html.Div([
            html.Div(titulo, style=dict(color=COLOR_TEXTO, fontSize="16.5px",
                                        fontWeight="600")),
            html.Div(descripcion, style=dict(color=COLOR_TENUE,
                                             fontSize="14px",
                                             marginTop="3px",
                                             lineHeight="1.5")),
        ], style=dict(padding="12px 16px", borderBottom="1px solid #262d38")),
        html.Div(contenido, style=dict(padding="4px")),
    ], style=est)


def tarjeta(titulo, valor, detalle, nota):
    """Indicador de cabecera: el numero grande mas que significa."""
    return html.Div([
        html.Div(titulo, style=dict(color=COLOR_TENUE, fontSize="13px",
                                    textTransform="uppercase",
                                    letterSpacing="0.6px")),
        html.Div(valor, style=dict(color=COLOR_TEXTO, fontSize="30px",
                                   fontWeight="600", margin="4px 0 2px 0")),
        html.Div(detalle, style=dict(color=COLOR_TEXTO, fontSize="14px")),
        html.Div(nota, style=dict(color=COLOR_TENUE, fontSize="13px",
                                  marginTop="4px", lineHeight="1.45")),
    ], style=dict(background=COLOR_PANEL, padding="13px 15px",
                  borderRadius="10px", border="1px solid #262d38",
                  flex="1", minWidth="190px"))


ocupacion = {c: 100.0 * D["ocupado_%s" % c].sum() / len(D["lat"]) for c in CANALES}
peor_canal = max(CANALES, key=lambda c: float(dbm_a_mw(D["p_%s" % c]).mean()))
mejor_canal = min(CANALES, key=lambda c: float(dbm_a_mw(D["p_%s" % c]).mean()))
frec_pico = D["frecuencias"][D["bin_peor"]] / 1e6
frec_limpia = D["frecuencias"][D["bin_mejor"]] / 1e6
banda_de = {c: "%.0f-%.0f MHz" % (
    (FREC_INICIAL_HZ + ini * ANCHO_BIN_HZ) / 1e6,
    (FREC_INICIAL_HZ + fin * ANCHO_BIN_HZ) / 1e6)
    for c, (ini, fin) in CANALES.items()}

app = Dash(__name__, title="Ocupacion de espectro 840-860 MHz")
# Aplicacion WSGI (Flask) que sirve gunicorn en el contenedor.
server = app.server

# html y body traen fondo blanco por defecto y asoma por los bordes al hacer
# scroll. Se fija aqui porque el estilo del layout solo cubre su propio div.
app.index_string = """<!DOCTYPE html>
<html>
  <head>
    {%metas%}<title>{%title%}</title>{%favicon%}{%css%}
    <style>
      html, body {
        margin: 0; padding: 0;
        background: """ + COLOR_FONDO + """;
      }
      ::-webkit-scrollbar { width: 10px; height: 10px; }
      ::-webkit-scrollbar-track { background: """ + COLOR_FONDO + """; }
      ::-webkit-scrollbar-thumb { background: #2a313c; border-radius: 5px; }
    </style>
  </head>
  <body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
  </body>
</html>"""

app.layout = html.Div([

    # ---------------------------------------------------------- cabecera
    html.Div([
        html.H1("Ocupacion de espectro 840 - 860 MHz",
                style=dict(margin="0 0 5px 0", fontSize="32px",
                           color=COLOR_TEXTO)),
        html.Div([
            "Estudio tecnico para la ",
            html.B("Agencia Nacional del Espectro"),
            " sobre la contaminacion de la banda celular en Medellin. "
            "Una estacion movil de monitoreo "
            "(receptor USRP + GPS) se detuvo en ",
            html.B("%d puntos" % len(D["lat"])),
            " y en cada uno midio los 20 MHz completos divididos en 1024 "
            "canales de %.2f kHz. El objetivo es decir cuales de los cuatro "
            "bloques de 5 MHz (A, B, C y D) estan libres para asignar."
            % (ANCHO_BIN_HZ / 1e3),
        ], style=dict(color=COLOR_TENUE, fontSize="15.5px", maxWidth="1050px",
                      lineHeight="1.6")),
    ], style=dict(marginBottom="16px")),

    # ------------------------------------------------------- indicadores
    html.Div([
        tarjeta("Canal mas contaminado", peor_canal, banda_de[peor_canal],
                "Supera el umbral de %.0f dBm en el %.1f%% del recorrido. "
                "No se recomienda asignarlo."
                % (UMBRAL_OCUPACION_DBM, ocupacion[peor_canal])),
        tarjeta("Canal mas limpio", mejor_canal, banda_de[mejor_canal],
                "Solo %.1f%% de los puntos superan el umbral. Es la mejor "
                "opcion para un despliegue nuevo." % ocupacion[mejor_canal]),
        tarjeta("Frecuencia pico", "%.3f MHz" % frec_pico,
                "%.1f dBm mediana" % D["perfil"][D["bin_peor"]],
                "Supera el umbral en el %.1f%% del recorrido. La mas limpia "
                "esta en %.3f MHz (%.1f dBm)."
                % (100.0 * np.mean(D["p_pico"] > UMBRAL_OCUPACION_DBM),
                   frec_limpia, D["perfil"][D["bin_mejor"]])),
        tarjeta("Calidad del dataset",
                "%d / %d" % (D["calidad"].count("buena"), len(D["lat"])),
                "mediciones sin defectos",
                "%d imputada por GPS sin fix y %d degradada por posicion "
                "imprecisa. Ningun valor de espectro resulto corrupto."
                % (D["calidad"].count("imputada"),
                   D["calidad"].count("degradada"))),
    ], style=dict(display="flex", gap="10px", marginBottom="10px",
                  flexWrap="wrap")),

    # ------------------------------------------------- controles + mapa
    html.Div([

        html.Div([
            html.Div([
                html.Div("Controles", style=dict(color=COLOR_TEXTO,
                                                 fontSize="16.5px",
                                                 fontWeight="600")),
                html.Div("Elige que variable se pinta sobre el mapa.",
                         style=dict(color=COLOR_TENUE, fontSize="14px",
                                    marginTop="3px")),
            ], style=dict(padding="12px 16px",
                          borderBottom="1px solid #262d38")),

            html.Div([
                html.Label("Capa a visualizar",
                           style=dict(color=COLOR_TEXTO, fontSize="15px",
                                      fontWeight="600", display="block",
                                      marginBottom="5px")),
                dcc.Dropdown(
                    id="capa",
                    options=[dict(label=v["etiqueta"], value=k)
                             for k, v in CAPAS.items()],
                    value="canal_C", clearable=False,
                    style=dict(background="#fff", color="#000")),
                html.Div(id="capa_explicacion",
                         style=dict(color=COLOR_TENUE, fontSize="14px",
                                    marginTop="8px", lineHeight="1.5")),

                html.Hr(style=dict(borderColor="#262d38", margin="16px 0")),

                html.Label("Radio del mapa de calor",
                           style=dict(color=COLOR_TEXTO, fontSize="15px",
                                      fontWeight="600", display="block")),
                html.Div("Cuantos pixeles se difumina cada medicion. Radio "
                         "pequeno muestra puntos aislados; radio grande "
                         "sugiere cobertura continua entre ellos.",
                         style=dict(color=COLOR_TENUE, fontSize="14px",
                                    margin="4px 0 10px 0", lineHeight="1.5")),
                dcc.Slider(id="radio", min=10, max=60, step=5, value=30,
                           allow_direct_input=False,
                           marks={i: dict(label=str(i),
                                          style=dict(color=COLOR_TENUE,
                                                     fontSize="13.5px"))
                                  for i in range(10, 61, 10)}),

                html.Hr(style=dict(borderColor="#262d38", margin="16px 0")),

                html.Label("Capas superpuestas",
                           style=dict(color=COLOR_TEXTO, fontSize="15px",
                                      fontWeight="600", display="block")),
                html.Div("Elementos que se dibujan encima del mapa de calor. "
                         "Desmarca para ver el mapa sin obstruccion.",
                         style=dict(color=COLOR_TENUE, fontSize="14px",
                                    margin="4px 0 8px 0", lineHeight="1.5")),
                dcc.Checklist(
                    id="opciones",
                    options=[
                        dict(label=" Linea de la ruta recorrida",
                             value="ruta"),
                        dict(label=" Punto de cada medicion", value="puntos"),
                        dict(label=" Zona estimada de la fuente",
                             value="fuente"),
                    ],
                    value=["ruta", "puntos", "fuente"],
                    inputStyle=dict(marginRight="7px", accentColor=COLOR_ACENTO,
                                    width="14px", height="14px",
                                    verticalAlign="middle"),
                    labelStyle=dict(display="flex", alignItems="center",
                                    marginBottom="7px", color=COLOR_TEXTO,
                                    fontSize="15.5px", cursor="pointer"),
                    style=dict(marginTop="2px")),
                html.Div("La zona de la fuente solo aplica a los canales "
                         "A, B, C y D.",
                         style=dict(color=COLOR_TENUE, fontSize="13.5px",
                                    marginTop="2px", fontStyle="italic")),

                html.Hr(style=dict(borderColor="#262d38", margin="16px 0")),

                html.Div(id="detalle", style=dict(color=COLOR_TENUE,
                                                  fontSize="14px",
                                                  lineHeight="1.7")),
            ], style=dict(padding="14px 16px")),
        ], style=dict(**EST_PANEL, width="340px", flexShrink="0")),

        html.Div(panel(
            "Mapa de la ciudad de Medellin",
            "Cada medicion se difumina segun su potencia para formar el mapa "
            "de calor. Los puntos superpuestos muestran el valor exacto en "
            "dBm segun la barra de color de la derecha. Haz clic en "
            "cualquiera para analizarlo abajo.",
            dcc.Graph(id="mapa", config=CONFIG_GRAFICO,
                      style=dict(height="640px"))),
            style=dict(flex="1", minWidth="0")),

    ], style=dict(display="flex", gap="10px", marginBottom="10px",
                  alignItems="stretch")),

    # ----------------------------------------------------- graficos base
    html.Div([
        html.Div(panel(
            "Espectro completo: potencia en cada frecuencia",
            "Eje X: las 1024 frecuencias medidas, de 840 a 860 MHz. "
            "Eje Y: cuanta potencia hay en cada una. La linea punteada gris "
            "es la mediana de las %d mediciones; al hacer clic en el mapa se "
            "superpone en azul el espectro de ese punto. Las cuatro franjas "
            "de color son los canales A, B, C y D, y la linea roja el umbral "
            "de %.0f dBm por encima del cual un canal se considera ocupado."
            % (len(D["lat"]), UMBRAL_OCUPACION_DBM),
            dcc.Graph(id="espectro", config=CONFIG_GRAFICO,
                      style=dict(height="360px"))),
            style=dict(flex="2", minWidth="0")),

        html.Div(panel(
            "Comparacion de los cuatro canales",
            "Potencia media de cada bloque de 5 MHz calculada con la "
            "sumatoria de Parseval. Las barras grises son el promedio de "
            "todo el sistema; al hacer clic en el mapa aparece en azul el "
            "valor de ese punto. Barra por encima de la linea roja = canal "
            "contaminado.",
            dcc.Graph(id="canales", config=CONFIG_GRAFICO,
                      style=dict(height="360px"))),
            style=dict(flex="1", minWidth="0")),
    ], style=dict(display="flex", gap="10px", alignItems="stretch")),

    html.Div("Datos: 61 mediciones de espectro tomadas con estacion movil "
             "(USRP + GNU Radio + GPS). Procesamiento: etl.py (limpieza, "
             "imputacion e integracion por Parseval) y fuentes.py "
             "(estimacion de origen).",
             style=dict(color=COLOR_TENUE, fontSize="13.5px",
                        marginTop="14px", textAlign="center")),

], style=dict(background=COLOR_FONDO, minHeight="100vh", padding="20px",
              fontFamily="system-ui, -apple-system, sans-serif",
              boxSizing="border-box"))


@app.callback(
    Output("mapa", "figure"),
    Output("espectro", "figure"),
    Output("canales", "figure"),
    Output("detalle", "children"),
    Output("capa_explicacion", "children"),
    Input("capa", "value"),
    Input("opciones", "value"),
    Input("radio", "value"),
    Input("mapa", "clickData"))
def actualizar(clave_capa, opciones, radio, click):
    # El indice de la medicion seleccionada viene en customdata del scatter.
    indice = None
    if click and click.get("points"):
        cd = click["points"][0].get("customdata")
        if cd is not None:
            indice = int(cd)

    capa = CAPAS[clave_capa]
    mapa = figura_mapa(clave_capa, "ruta" in opciones, "puntos" in opciones,
                       "fuente" in opciones, radio)

    # Explicacion de la capa activa, debajo del selector.
    explicacion = [capa["explicacion"]]
    if capa["leyenda"]:
        explicacion.append(html.Div([
            html.Div([
                html.Span(style=dict(display="inline-block", width="9px",
                                     height="9px", borderRadius="50%",
                                     background=color, marginRight="7px")),
                html.Span(texto, style=dict(fontSize="13.5px")),
            ], style=dict(display="flex", alignItems="center",
                          marginTop="5px"))
            for color, texto in capa["leyenda"]
        ], style=dict(marginTop="8px")))

    # Panel de estadisticas de la capa activa.
    lineas = []
    if capa["variable"] is not None:
        v = D[capa["variable"]]
        lineas += [html.B("Valores de esta capa"), html.Br(),
                   "Minimo: %.2f %s" % (v.min(), capa["unidad"]), html.Br(),
                   "Maximo: %.2f %s" % (v.max(), capa["unidad"]), html.Br(),
                   "Promedio: %.2f %s" % (v.mean(), capa["unidad"]), html.Br()]
        if clave_capa.startswith("canal_"):
            c = clave_capa[-1]
            n_oc = int(D["ocupado_%s" % c].sum())
            lineas += [html.Br(),
                       html.B("Veredicto: "),
                       "ocupado en %d de %d puntos (%.1f%%)"
                       % (n_oc, len(D["lat"]), 100.0 * n_oc / len(D["lat"]))]

            # Datos de la fuente estimada, que sobre el mapa no caben.
            f = next((x for x in D["fuentes"] if x["canal"] == c), None)
            if f and "fuente" in opciones:
                lineas += [
                    html.Br(), html.Br(),
                    html.B("Zona estimada de la fuente"), html.Br(),
                    "Centro: %.5f, %.5f" % (float(f["lat"]), float(f["lon"])),
                    html.Br(),
                    "Radio medio: %.2f km" % float(f["radio_km"]), html.Br(),
                    "Radio maximo: %.2f km" % float(f["radio_max_km"]),
                    html.Br(),
                    "Trilateracion: no concluyente (R2=%.2f)"
                    % float(f["tri_r2"])]
    else:
        lineas += [html.B("Recorrido"), html.Br(),
                   "%d puntos en orden cronologico" % len(D["lat"]), html.Br(),
                   "Inicio: %s | Fin: %s" % (D["archivo"][0], D["archivo"][-1])]

    if indice is not None:
        lineas += [
            html.Hr(style=dict(borderColor="#262d38", margin="12px 0")),
            html.B("Medicion seleccionada: %s" % D["archivo"][indice]),
            html.Br(),
            "Punto %d de %d del recorrido" % (D["orden"][indice], len(D["lat"])),
            html.Br(),
            "Temperatura: %.2f C" % D["temp"][indice], html.Br(),
            "Altura: %.0f m" % D["alt"][indice], html.Br(),
            "Error GPS (HDOP): %.1f" % D["hdop"][indice], html.Br(),
            "Calidad del dato: %s" % D["calidad"][indice]]
    else:
        lineas += [
            html.Hr(style=dict(borderColor="#262d38", margin="12px 0")),
            html.I("Haz clic en un punto del mapa para ver su detalle y su "
                   "espectro completo.")]

    return mapa, figura_espectro(indice), figura_canales(indice), lineas, explicacion


if __name__ == "__main__":
    app.run(debug=False, host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8050")))
