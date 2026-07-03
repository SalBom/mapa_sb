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

La consulta al BCRA (situación crediticia / cheques rechazados) es
100% a demanda: el endpoint /bcra-lookup consulta UN cliente por vez,
cuando alguien aprieta el botón "Consultar BCRA" en el popup del mapa.
No hay ningún proceso de fondo que recorra los ~15.000 clientes.
"""
import datetime
import http.server
import json
import os
import re
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xmlrpc.client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
PORT = int(os.environ.get("PORT", "8000"))
REFRESH_MINUTES = float(os.environ.get("REFRESH_MINUTES", "30"))

ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "")

BCRA_API_BASE = "https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas"
BCRA_DELAY_MS = int(os.environ.get("BCRA_DELAY_MS", "800"))
BCRA_TIMEOUT = float(os.environ.get("BCRA_TIMEOUT", "15"))


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


def normalize_cuit(v):
    if not v:
        return None
    digits = re.sub(r"\D", "", str(v))
    if len(digits) < 11:
        return None
    return digits[-11:]


def _bcra_get(path):
    """dict con la respuesta, {} si el BCRA confirmó que no hay registro
    (404/400), o None si hubo un error (red, 429, 5xx)."""
    req = urllib.request.Request(BCRA_API_BASE + path, headers={
        "User-Agent": "mapa-sb/1.0 (mapa de territorios SAL-BOM)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=BCRA_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 400):
            return {}
        log(f"bcra: status {e.code} en {path}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"bcra: error de red en {path}: {e}")
        return None
    except Exception as e:
        log(f"bcra: error inesperado en {path}: {e}")
        return None


def _bcra_situacion_actual(deudas_json):
    """(situacion, entidad) del período más reciente, tomando la PEOR
    situación (la más alta) entre todos los bancos de ese período."""
    periodos = (deudas_json or {}).get("results", {}).get("periodos") or []
    if not periodos:
        return None, None
    periodo_actual = max(periodos, key=lambda p: p.get("periodo", ""))
    entidades = periodo_actual.get("entidades") or []
    if not entidades:
        return None, None
    peor = max(entidades, key=lambda e: e.get("situacion") or 0)
    return peor.get("situacion"), peor.get("entidad")


def _bcra_cantidad_rechazados(cheques_json):
    causales = (cheques_json or {}).get("results", {}).get("causales") or []
    total = 0
    for causal in causales:
        for ent in causal.get("entidades") or []:
            total += len(ent.get("detalle") or [])
    return total


def bcra_lookup(cuit):
    """Consulta un solo CUIT contra la API del BCRA (situación + cheques
    rechazados). Se usa una vez por clic en "Consultar BCRA" del mapa, así
    que no hace falta caché ni corridas masivas de fondo."""
    deudas = _bcra_get(f"/{cuit}")
    time.sleep(BCRA_DELAY_MS / 1000)
    cheques = _bcra_get(f"/ChequesRechazados/{cuit}")
    if deudas is None or cheques is None:
        raise RuntimeError("La API del BCRA no respondió correctamente")
    situacion, entidad = _bcra_situacion_actual(deudas)
    cant = _bcra_cantidad_rechazados(cheques)
    return {
        "situacion": situacion,
        "entidad": entidad,
        "cant_cheques": cant,
        "tiene_rechazados": cant > 0,
    }


class MapHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                creds = json.loads(body)
            except Exception:
                self._json_response(400, {"ok": False, "error": "JSON inválido"})
                return
            user = creds.get("user", "")
            password = creds.get("password", "")
            if not user or not password:
                self._json_response(400, {"ok": False, "error": "Faltan credenciales"})
                return
            if not ODOO_URL or not ODOO_DB:
                self._json_response(500, {"ok": False, "error": "ODOO_URL/ODOO_DB no configuradas"})
                return
            try:
                common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
                uid = common.authenticate(ODOO_DB, user, password, {})
            except Exception as e:
                log(f"login: error conectando a Odoo: {e}")
                self._json_response(502, {"ok": False, "error": "No se pudo conectar a Odoo"})
                return
            if uid:
                log(f"login: OK para {user} (uid={uid})")
                self._json_response(200, {"ok": True, "user": user})
            else:
                log(f"login: FALLÓ para {user}")
                self._json_response(401, {"ok": False, "error": "Usuario o contraseña incorrectos"})
            return

        if self.path == "/bcra-lookup":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
            except Exception:
                self._json_response(400, {"ok": False, "error": "JSON inválido"})
                return
            cuit = normalize_cuit(payload.get("cuit"))
            if not cuit:
                self._json_response(400, {"ok": False, "error": "CUIT inválido"})
                return
            try:
                info = bcra_lookup(cuit)
            except Exception as e:
                log(f"bcra: error consultando CUIT {cuit}: {e}")
                self._json_response(502, {"ok": False, "error": "No se pudo consultar al BCRA"})
                return
            self._json_response(200, {"ok": True, "cuit": cuit, **info})
            return

    def _json_response(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def main():
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    threading.Thread(target=sync_forever, daemon=True).start()

    os.chdir(PUBLIC_DIR)
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), MapHandler) as httpd:
        log(f"http: sirviendo {PUBLIC_DIR} en el puerto {PORT} "
            f"(refresco cada {REFRESH_MINUTES} min)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
