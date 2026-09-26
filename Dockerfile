# Dashboard del Examen 03 listo para un servidor (EC2 u otro).
#
# El ETL corre durante la construccion: los .npy que consume el dashboard
# no se versionan, asi que la imagen los genera a partir del dataset y no
# depende de lo que haya en salida/ en la maquina que construye.
FROM python:3.12-slim

WORKDIR /app

COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

COPY etl.py fuentes.py dashboard.py ./
COPY medidas_2026_20/ medidas_2026_20/
RUN python etl.py

# El servidor corre sin privilegios: lee salida/ y gunicorn usa su home
# para el socket de control.
RUN useradd --create-home --shell /usr/sbin/nologin dashboard
USER dashboard

EXPOSE 8050
CMD ["gunicorn", "--bind", "0.0.0.0:8050", "--workers", "2", "--timeout", "60", "dashboard:server"]
