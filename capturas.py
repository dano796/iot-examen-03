"""Captura automatica de las vistas del dashboard para el informe escrito.

El enunciado exige que el programa muestre cinco visualizaciones. Como el
documento es estatico, aqui se recorre el dashboard capa por capa y se
guarda una imagen de cada una, de forma reproducible: si cambia el
dashboard basta volver a correr este script.

Requiere que el servidor este arriba:

    .venv/bin/python dashboard.py     (en otra terminal)
    .venv/bin/python capturas.py

Deja los PNG en salida/capturas_dashboard/.
"""

import os
import sys

from playwright.sync_api import sync_playwright

from etl import CARPETA_SALIDA

URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8050/")

# Playwright normalmente descarga su propio Chromium, pero en este entorno
# la descarga esta bloqueada. Se reutiliza el Chrome del sistema si existe.
CHROME_SISTEMA = next((r for r in ("/opt/google/chrome/chrome",
                                   "/usr/bin/google-chrome-stable",
                                   "/usr/bin/chromium")
                       if os.path.exists(r)), None)
CARPETA = os.path.join(CARPETA_SALIDA, "capturas_dashboard")

ANCHO, ALTO = 1500, 1000

# (archivo, texto de la opcion, titulo, capturar pagina completa)
VISTAS = [
    ("01_ubicaciones_y_ruta.png", "Ubicaciones", "Ubicacion y ruta de las mediciones", True),
    ("02_canal_A.png", "Canal A", "Mapa de calor del canal A", False),
    ("03_canal_B.png", "Canal B", "Mapa de calor del canal B", False),
    ("04_canal_C.png", "Canal C", "Mapa de calor del canal C", False),
    ("05_canal_D.png", "Canal D", "Mapa de calor del canal D", False),
    ("06_temperatura.png", "Temperatura", "Mapa de calor de la temperatura", False),
    ("07_frecuencia_pico.png", "Frecuencia mas", "Mapa de calor de la frecuencia mas contaminada", False),
]

# Script que abre el desplegable de Dash y elige una opcion por su texto.
# El componente es un boton con listbox, no un <select>, asi que no sirve
# select_option: hay que simular la interaccion.
JS_SELECCIONAR = """
async (texto) => {
  document.querySelector('#capa').click();
  await new Promise(r => setTimeout(r, 400));
  const opciones = [...document.querySelectorAll('[role="option"]')];
  const destino = opciones.find(o => o.textContent.includes(texto));
  if (!destino) return { ok: false, vistas: opciones.map(o => o.textContent) };
  destino.click();
  await new Promise(r => setTimeout(r, 2500));
  return { ok: true, seleccion: document.querySelector('#capa-value').textContent };
}
"""


def main():
    os.makedirs(CARPETA, exist_ok=True)

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            executable_path=CHROME_SISTEMA,
            args=["--no-sandbox"])
        pagina = navegador.new_page(viewport=dict(width=ANCHO, height=ALTO))

        try:
            pagina.goto(URL, timeout=15000)
        except Exception:
            print("No se pudo abrir %s.\nLevanta el servidor con "
                  "`.venv/bin/python dashboard.py` y vuelve a intentar." % URL)
            navegador.close()
            return 1

        # El primer render de Plotly tarda: se espera al mapa y un margen.
        pagina.wait_for_selector("#mapa .js-plotly-plot", timeout=30000)
        pagina.wait_for_timeout(3500)

        print("Capturando en %s" % CARPETA)
        for archivo, texto, titulo, completa in VISTAS:
            resultado = pagina.evaluate(JS_SELECCIONAR, texto)
            if not resultado.get("ok"):
                print("  ! no se encontro la opcion '%s' (hay: %s)"
                      % (texto, resultado.get("vistas")))
                continue
            pagina.wait_for_timeout(1200)
            pagina.screenshot(path=os.path.join(CARPETA, archivo),
                              full_page=completa)
            print("  %-28s %s" % (archivo, titulo))

        navegador.close()
    print("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
