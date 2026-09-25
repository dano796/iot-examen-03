"""Ensambla el entregable final en PDF a partir del reporte en Markdown.

Toma salida/reporte_calidad.md, le antepone una portada, le anexa las
capturas del dashboard y lo imprime a PDF con numeracion de paginas.

La conversion es Markdown -> HTML -> PDF usando el motor de impresion de
Chrome, que respeta CSS de paginacion (`@page`, `page-break-*`) mucho mejor
que las librerias de PDF puras.

Requisitos previos:

    python3 etl.py                    genera el reporte y los indicadores
    .venv/bin/python graficas.py      genera las figuras del informe
    .venv/bin/python capturas.py      genera las capturas del dashboard

Uso:
    .venv/bin/python informe.py       ->  salida/Informe_Examen03.pdf
"""

import base64
import os
import re
import sys
from datetime import date

import markdown
from playwright.sync_api import sync_playwright

from etl import CARPETA_SALIDA

# --------------------------------------------------------------------------
# Datos de portada. AJUSTAR antes de entregar.
# --------------------------------------------------------------------------
AUTOR = "Daniel Ortiz"
ASIGNATURA = "Internet de las Cosas"
DOCENTE = "Leonardo Betancur"
INSTITUCION = "Universidad Pontificia Bolivariana"
FACULTAD = ("Escuela de Ingenierias - Facultad de Ingenieria en Tecnologias "
            "de la Informacion y la Comunicacion")
EVALUACION = "Examen / Trabajo 3 - ETL y toma de decisiones"
FECHA_ENTREGA = "28 de septiembre de 2026"

TITULO = "Analisis de ocupacion del espectro radioelectrico"
SUBTITULO = ("Banda 840 - 860 MHz, sector occidental de Medellin<br>"
             "Estudio tecnico para la Agencia Nacional del Espectro")

RUTA_MD = os.path.join(CARPETA_SALIDA, "reporte_calidad.md")
RUTA_PDF = os.path.join(CARPETA_SALIDA, "Informe_Examen03.pdf")
RUTA_HTML = os.path.join(CARPETA_SALIDA, "Informe_Examen03.html")
CARPETA_CAPTURAS = os.path.join(CARPETA_SALIDA, "capturas_dashboard")

CHROME_SISTEMA = next((r for r in ("/opt/google/chrome/chrome",
                                   "/usr/bin/google-chrome-stable",
                                   "/usr/bin/chromium")
                       if os.path.exists(r)), None)

# Titulo legible de cada captura del dashboard, por prefijo de archivo.
TITULOS_CAPTURAS = {
    "01": "Ubicacion y ruta de las mediciones",
    "02": "Mapa de calor del canal A (840 - 845 MHz)",
    "03": "Mapa de calor del canal B (845 - 850 MHz)",
    "04": "Mapa de calor del canal C (850 - 855 MHz)",
    "05": "Mapa de calor del canal D (855 - 860 MHz)",
    "06": "Mapa de calor de la temperatura del sistema de sensado",
    "07": "Mapa de calor de la frecuencia mas contaminada",
}

CSS = """
@page { size: Letter; margin: 18mm 16mm 20mm 16mm; }
@page :first { margin: 0; }

* { box-sizing: border-box; }
body {
  font-family: "Georgia", "Times New Roman", serif;
  font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; margin: 0;
}

/* ---------------- portada ---------------- */
.portada {
  height: 100vh; display: flex; flex-direction: column;
  justify-content: center; padding: 0 26mm;
  page-break-after: always; text-align: center;
}
.portada .institucion {
  font-size: 12pt; font-weight: bold; letter-spacing: 0.5px;
  text-transform: uppercase; color: #333;
}
.portada .facultad {
  font-size: 9.5pt; color: #555; margin-top: 6px; line-height: 1.5;
}
.portada .regla { border: none; border-top: 2px solid #1a1a1a; margin: 28px 0; }
.portada h1 {
  font-size: 22pt; margin: 0 0 10px 0; line-height: 1.3; border: none;
}
.portada .subtitulo { font-size: 12pt; color: #444; line-height: 1.6; }
.portada .meta {
  margin-top: 42px; font-size: 10.5pt; line-height: 2;
  display: inline-block; text-align: left;
}
.portada .meta b { display: inline-block; min-width: 110px; }
.portada .pie {
  margin-top: 46px; font-size: 9pt; color: #666;
}

/* ---------------- cuerpo ---------------- */
h1, h2, h3 { font-family: "Helvetica Neue", Arial, sans-serif; color: #111; }
h2 {
  font-size: 14pt; margin: 26px 0 10px 0; padding-bottom: 5px;
  border-bottom: 1.5px solid #333; page-break-after: avoid;
}
h3 {
  font-size: 11.5pt; margin: 18px 0 7px 0; color: #333;
  page-break-after: avoid;
}
p { margin: 8px 0; text-align: justify; }

table {
  border-collapse: collapse; width: 100%; margin: 12px 0;
  font-size: 9pt; page-break-inside: avoid;
  font-family: "Helvetica Neue", Arial, sans-serif;
}
th {
  background: #2c3e50; color: #fff; padding: 6px 8px;
  text-align: left; font-weight: 600;
}
td { padding: 5px 8px; border-bottom: 1px solid #ddd; }
tr:nth-child(even) td { background: #f6f7f9; }

code {
  font-family: "Consolas", "Courier New", monospace; font-size: 9pt;
  background: #f0f2f5; padding: 1px 4px; border-radius: 3px;
}
pre {
  background: #f6f7f9; border-left: 3px solid #2c3e50; padding: 9px 12px;
  font-size: 9pt; overflow-x: auto; page-break-inside: avoid;
}
pre code { background: none; padding: 0; }

ul, ol { margin: 8px 0; padding-left: 22px; }
li { margin: 4px 0; text-align: justify; }

img { max-width: 100%; display: block; margin: 12px auto 4px auto; }

/* pie de figura: el parrafo en cursiva que sigue a una imagen */
p > em:only-child {
  display: block; text-align: center; font-size: 8.5pt; color: #555;
}

.anexo { page-break-before: always; }
.captura { page-break-inside: avoid; margin-bottom: 20px; }
.captura h3 { margin-bottom: 6px; }
.captura img { border: 1px solid #ccc; max-height: 215mm;
               width: auto; }
"""


def imagen_embebida(ruta):
    """Devuelve la imagen como data URI.

    Se embeben en el HTML en lugar de referenciarlas por ruta para que el
    documento sea un archivo unico y para que Chrome no tenga que resolver
    rutas relativas al imprimir.
    """
    with open(ruta, "rb") as fo:
        datos = base64.b64encode(fo.read()).decode("ascii")
    return "data:image/png;base64,%s" % datos


def portada():
    return """
<div class="portada">
  <div class="institucion">%s</div>
  <div class="facultad">%s</div>
  <hr class="regla">
  <h1>%s</h1>
  <div class="subtitulo">%s</div>
  <div class="meta">
    <div><b>Asignatura:</b> %s</div>
    <div><b>Evaluacion:</b> %s</div>
    <div><b>Docente:</b> %s</div>
    <div><b>Estudiante:</b> %s</div>
    <div><b>Entrega:</b> %s</div>
  </div>
  <div class="pie">Documento generado el %s a partir de los datos procesados
  por <code>etl.py</code>. Todas las cifras provienen de la misma corrida.</div>
</div>
""" % (INSTITUCION, FACULTAD, TITULO, SUBTITULO, ASIGNATURA, EVALUACION,
       DOCENTE, AUTOR, FECHA_ENTREGA, date.today().strftime("%d/%m/%Y"))


def anexo_capturas():
    """Anexo con las vistas del dashboard exigidas por el enunciado."""
    if not os.path.isdir(CARPETA_CAPTURAS):
        return ""
    archivos = sorted(f for f in os.listdir(CARPETA_CAPTURAS)
                      if f.endswith(".png"))
    if not archivos:
        return ""

    partes = ['<div class="anexo">',
              "<h2>Anexo A. Dashboard interactivo</h2>",
              "<p>El programa entregado es un servidor web construido con "
              "Dash y Plotly que se ejecuta con "
              "<code>python dashboard.py</code> y publica el tablero en "
              "<code>http://127.0.0.1:8050</code>. Las vistas siguientes "
              "corresponden a las cinco visualizaciones exigidas en el "
              "enunciado. Las capturas se generan automaticamente con "
              "<code>capturas.py</code>.</p>"]
    for archivo in archivos:
        titulo = TITULOS_CAPTURAS.get(archivo[:2], archivo)
        partes.append('<div class="captura"><h3>%s</h3><img src="%s"></div>'
                      % (titulo, imagen_embebida(os.path.join(CARPETA_CAPTURAS,
                                                              archivo))))
    partes.append("</div>")
    return "\n".join(partes)


def convertir_markdown(texto):
    """Markdown -> HTML, embebiendo las imagenes referenciadas."""
    html = markdown.markdown(
        texto, extensions=["tables", "fenced_code", "sane_lists"])

    # Las figuras vienen como <img src="graficas/xx.png">: se reemplazan por
    # el contenido embebido.
    def reemplazar(m):
        ruta = os.path.join(CARPETA_SALIDA, m.group(1))
        if not os.path.exists(ruta):
            print("  ! falta la figura %s" % m.group(1))
            return m.group(0)
        return 'src="%s"' % imagen_embebida(ruta)

    return re.sub(r'src="(graficas/[^"]+)"', reemplazar, html)


def main():
    if not os.path.exists(RUTA_MD):
        print("Falta %s. Corre primero `python3 etl.py`." % RUTA_MD)
        return 1

    with open(RUTA_MD, encoding="utf-8") as fo:
        md = fo.read()

    # El titulo H1 del Markdown lo reemplaza la portada.
    md = re.sub(r"^# .*\n", "", md, count=1)

    print("Ensamblando informe...")
    cuerpo = convertir_markdown(md)
    anexo = anexo_capturas()

    documento = ("<!DOCTYPE html><html lang=\"es\"><head>"
                 "<meta charset=\"utf-8\"><title>%s</title>"
                 "<style>%s</style></head><body>%s%s%s</body></html>"
                 % (TITULO, CSS, portada(), cuerpo, anexo))

    with open(RUTA_HTML, "w") as fo:
        fo.write(documento)
    print("  HTML: %s (%.1f MB)"
          % (RUTA_HTML, os.path.getsize(RUTA_HTML) / 1e6))

    with sync_playwright() as p:
        navegador = p.chromium.launch(executable_path=CHROME_SISTEMA,
                                      args=["--no-sandbox"])
        pagina = navegador.new_page()
        pagina.goto("file://" + os.path.abspath(RUTA_HTML))

        # Con varios MB de imagenes embebidas, Chrome llega a imprimir antes
        # de terminar de decodificarlas y salen paginas en blanco. Se espera
        # a que cada <img> reporte complete y con ancho distinto de cero.
        pagina.wait_for_function(
            """() => {
                const imgs = [...document.images];
                return imgs.length > 0 &&
                       imgs.every(i => i.complete && i.naturalWidth > 0);
            }""",
            timeout=120000)
        n_imgs = pagina.evaluate("document.images.length")
        print("  %d imagenes cargadas" % n_imgs)
        pagina.wait_for_timeout(1500)
        pagina.pdf(
            path=RUTA_PDF, format="Letter", print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                '<div style="font-size:8pt;color:#777;width:100%;'
                'text-align:center;font-family:Georgia,serif;">'
                '<span class="pageNumber"></span> / '
                '<span class="totalPages"></span></div>'),
            margin=dict(top="18mm", bottom="20mm", left="16mm", right="16mm"))
        navegador.close()

    print("  PDF : %s (%.1f MB)" % (RUTA_PDF, os.path.getsize(RUTA_PDF) / 1e6))
    print("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
