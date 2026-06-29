#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py
Sirve el mapa (carpeta ./public) y, en paralelo, corre odoo_to_map.py
una vez al arrancar y después cada REFRESH_MINUTES minutos, para que
data.json se mantenga al día sin tocar nada a mano.

Pensado para Railway: lee el puerto de la variable PORT (la inyecta
Railway solo) y las credenciales de Odoo de variables de entorno
(las que configures en la pestaña "Variables" del servicio).

Si una corrida de odoo_to_map.py falla (ej: Odoo no responde), el
mapa sigue sirviendo el último data.json bueno: el script solo
sobrescribe el archivo si termina OK, nunca lo deja a medio escribir.
"""
import datetime
import http.server
import os
import subprocess
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
PORT = int(os.environ.get("PORT", "8000"))
REFRESH_MINUTES = float(os.environ.get("REFRESH_MINUTES", "30"))


def log(msg):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def run_sync():
    log("sync: ejecutando odoo_to_map.py...")
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(BASE_DIR, "odoo_to_map.py")],
            capture_output=True, text=True, timeout=600,
        )
        if result.stdout.strip():
            log("sync: " + result.stdout.strip().replace("\n", " | "))
        if result.returncode != 0:
            log(f"sync: ERROR (código {result.returncode}) -> {result.stderr.strip()}")
        else:
            log("sync: OK, data.json actualizado")
    except subprocess.TimeoutExpired:
        log("sync: TIMEOUT, Odoo no respondió a tiempo")
    except Exception as e:
        log(f"sync: EXCEPCIÓN inesperada: {e}")


def sync_forever():
    run_sync()  # primera carga, no bloquea el server (corre en su propio hilo)
    period = max(1.0, REFRESH_MINUTES) * 60
    while True:
        time.sleep(period)
        run_sync()


def main():
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    threading.Thread(target=sync_forever, daemon=True).start()

    os.chdir(PUBLIC_DIR)  # solo esta carpeta queda expuesta por HTTP
    handler = http.server.SimpleHTTPRequestHandler
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler) as httpd:
        log(f"http: sirviendo {PUBLIC_DIR} en el puerto {PORT} "
            f"(refresco cada {REFRESH_MINUTES} min)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
