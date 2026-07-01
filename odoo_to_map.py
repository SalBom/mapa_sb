#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
odoo_to_map.py
Saca los clientes geolocalizados de Odoo y genera 'data.json' para el mapa de territorios.
No requiere instalar nada: usa solo la librería estándar.

Configuración: creá un archivo ".env" en la MISMA carpeta que este script, con:
    ODOO_URL=https://salbom.adhoc.ar
    ODOO_DB=salbom.adhoc.ar
    ODOO_USER=Bombita
    ODOO_KEY=tu_api_key_nueva
Después corré:  python  odoo_to_map.py
"""

import os, re, json, unicodedata, xmlrpc.client
from datetime import date

# Carpeta del script (para encontrar .env y escribir data.json aunque
# se ejecute desde otra ubicación, p. ej. el Programador de tareas).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# data.json se escribe en ./public, que es la carpeta que sirve app.py.
# Así el script NUNCA queda expuesto por HTTP (solo lo de adentro de public/).
OUTPUT_DIR = os.path.join(BASE_DIR, "public")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ------------------------------------------------------------------
# 0) Leer el archivo .env (parser mínimo, sin librerías externas)
# ------------------------------------------------------------------
def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_dotenv(os.path.join(BASE_DIR, ".env"))

def need(name):
    val = os.environ.get(name)
    if not val:
        raise SystemExit(
            f"Falta {name}. Creá un archivo .env (junto a odoo_to_map.py) con:\n"
            "  ODOO_URL=https://salbom.adhoc.ar\n"
            "  ODOO_DB=salbom.adhoc.ar\n"
            "  ODOO_USER=Bombita\n"
            "  ODOO_KEY=tu_api_key_nueva")

    return val

# ------------------------------------------------------------------
# 1) Conexión
# ------------------------------------------------------------------
URL  = need("ODOO_URL").rstrip("/")
DB   = need("ODOO_DB")
USER = need("ODOO_USER")
KEY  = need("ODOO_KEY")

common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
uid = common.authenticate(DB, USER, KEY, {})
if not uid:
    raise SystemExit("No se pudo autenticar. Revisá ODOO_DB (en Adhoc a veces no es "
                     "igual al dominio), ODOO_USER (debe ser el login) y la API key.")
models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")

def search_read(model, domain, fields, limit=0):
    return models.execute_kw(DB, uid, KEY, model, "search_read",
                             [domain], {"fields": fields, "limit": limit})

# ------------------------------------------------------------------
# 2) Provincia (Odoo) -> nombre del GeoJSON del mapa
#    El mapa usa nombres sin acentos y CABA/Tierra del Fuego cortos.
# ------------------------------------------------------------------
MAP_PROVS = ["Buenos Aires","CABA","Catamarca","Chaco","Chubut","Cordoba","Corrientes",
 "Entre Rios","Formosa","Jujuy","La Pampa","La Rioja","Mendoza","Misiones","Neuquen",
 "Rio Negro","Salta","San Juan","San Luis","Santa Cruz","Santa Fe",
 "Santiago del Estero","Tierra del Fuego","Tucuman"]

def _strip(s):
    s = re.sub(r"\s*\([^)]*\)", "", s)   # quita sufijos tipo " (AR)" que agrega Adhoc
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()

# index sin acentos -> nombre del mapa
_PROV_INDEX = {_strip(p): p for p in MAP_PROVS}
# casos que no matchean por nombre directo:
_PROV_OVERRIDE = {
    _strip("Ciudad Autónoma de Buenos Aires"): "CABA",
    _strip("Capital Federal"): "CABA",
    _strip("Tierra del Fuego, Antártida e Islas del Atlántico Sur"): "Tierra del Fuego",
}
def norm_prov(odoo_name):
    if not odoo_name: return None
    k = _strip(odoo_name)
    return _PROV_OVERRIDE.get(k) or _PROV_INDEX.get(k)

# ------------------------------------------------------------------
# 3) Provincia -> Zona  (editá esto con TUS zonas; es lo único geográfico fijo)
#
#    OJO con AMBA: NO es una provincia. Según la definición oficial, AMBA =
#    CABA + 40 partidos del conurbano. El RESTO de la provincia de Buenos
#    Aires es PAMPEANA. Por eso, para los clientes bonaerenses la zona NO se
#    decide por provincia sino por el PARTIDO (campo 'city' de Odoo).
#    Acá abajo, "Buenos Aires" queda mapeada a PAMPEANA como valor por
#    defecto (y como color de fondo del polígono); los clientes que caen en
#    un partido de AMBA se reclasifican a AMBA en el paso 4.
# ------------------------------------------------------------------
PROV2ZONA = {
 "Jujuy":"NOA","Salta":"NOA","Tucuman":"NOA","Catamarca":"NOA","Santiago del Estero":"NOA","La Rioja":"NOA",
 "Formosa":"NEA","Chaco":"NEA","Corrientes":"NEA","Misiones":"NEA",
 "Mendoza":"CUYO","San Juan":"CUYO","San Luis":"CUYO",
 "Cordoba":"PAMPEANA","Santa Fe":"PAMPEANA","Entre Rios":"PAMPEANA","La Pampa":"PAMPEANA","Buenos Aires":"PAMPEANA",
 "CABA":"AMBA",
 "Neuquen":"PATAGONICA","Rio Negro":"PATAGONICA","Chubut":"PATAGONICA","Santa Cruz":"PATAGONICA","Tierra del Fuego":"PATAGONICA",
}

# Orden de la leyenda (como en la tabla de zonas)
ZONA_ORDER = ["PAMPEANA","AMBA","CUYO","NEA","NOA","PATAGONICA"]

# --- Partidos de AMBA (CABA + 40 municipios) -----------------------
# Normalizador de nombres de partido: saca " (AR)", acentos, puntos,
# pasa a minúsculas y expande abreviaturas (Gral. -> General, etc.).
def _ncity(s):
    s = re.sub(r"\s*\([^)]*\)", "", s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace(".", " ")
    s = re.sub(r"\bgral\b", "general", s)
    s = re.sub(r"\bgrl\b", "general", s)
    s = re.sub(r"\bpdte\b", "presidente", s)
    s = re.sub(r"\bpte\b", "presidente", s)
    s = re.sub(r"\bcnel\b", "coronel", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# Lista canónica de los 40 partidos + alias frecuentes (forma corta que a
# veces guarda Odoo). Todo se normaliza con _ncity, así que no importan
# acentos ni mayúsculas.
_AMBA_NAMES = [
 "Almirante Brown","Avellaneda","Berazategui","Berisso","Brandsen","Campana",
 "Cañuelas","Ensenada","Escobar","Esteban Echeverría","Exaltación de la Cruz",
 "Ezeiza","Florencio Varela","General Las Heras","General Rodríguez",
 "General San Martín","Hurlingham","Ituzaingó","José C. Paz","La Matanza",
 "La Plata","Lanús","Lomas de Zamora","Luján","Malvinas Argentinas",
 "Marcos Paz","Merlo","Moreno","Morón","Quilmes","Pilar","Presidente Perón",
 "San Fernando","San Isidro","San Miguel","San Vicente","Tigre",
 "Tres de Febrero","Vicente López","Zárate",
 # alias / formas cortas:
 "Coronel Brandsen","Las Heras","Rodríguez","San Martín",
]
AMBA_PARTIDOS = {_ncity(n) for n in _AMBA_NAMES}

def zona_for(prov, city):
    """Zona de un cliente. BA se decide por partido (city); el resto, por provincia."""
    if prov == "CABA":
        return "AMBA"
    if prov == "Buenos Aires":
        return "AMBA" if _ncity(city) in AMBA_PARTIDOS else "PAMPEANA"
    return PROV2ZONA.get(prov, "Sin zona")

# ------------------------------------------------------------------
# 4) Traer los clientes
#    Ajustá el dominio a tu caso (acá: clientes con coordenadas cargadas).
# ------------------------------------------------------------------
domain = [
    # --- Mismo filtro que usás en Contactos (los ~1492 clientes) ---
    ("customer_rank", ">", 0),          # Rango del cliente > 0
    ("sale_type", "not in", [6]),       # Tipo de pedido no está en [Pedido Local / 00009]
    ("create_uid", "!=", 34),           # Creado por != Omar Carluccio
    ("user_id", "not in", [34]),        # Vendedor no está en [Omar Carluccio]
    ("user_id", "!=", False),           # Tiene vendedor
    # La geolocalización NO se filtra acá: traemos los 1492 y, más abajo,
    # contamos cuántos todavía no tienen coordenadas (esos no se dibujan
    # hasta correr "Geolocalizar" en Odoo). Así el total cuadra con Odoo.
]
fields = ["name", "partner_latitude", "partner_longitude",
          "user_id", "state_id", "street", "city"]

partners = search_read("res.partner", domain, fields)

def m2o_name(v):  # many2one llega como [id, "Nombre"] o False
    return v[1] if isinstance(v, (list, tuple)) and len(v) == 2 else None

clients, sin_geo, sin_prov, sin_vend = [], 0, 0, 0
prov_no_reconocidas = {}   # diagnóstico: qué devuelve Odoo y no matchea
ba_amba, ba_pamp = 0, 0    # diagnóstico: cómo se repartió Buenos Aires
ba_city_pamp = {}          # partidos de BA que NO matchearon AMBA (revisar nombres)
for p in partners:
    lat, lng = p.get("partner_latitude"), p.get("partner_longitude")
    if not lat or not lng:               # 0.0 o False => todavía sin geolocalizar
        sin_geo += 1
        continue
    raw = m2o_name(p.get("state_id"))    # nombre crudo de la provincia en Odoo
    prov = norm_prov(raw)
    if not prov:
        sin_prov += 1
        key = raw if raw else "(state_id VACÍO)"
        prov_no_reconocidas[key] = prov_no_reconocidas.get(key, 0) + 1
        continue                         # sin provincia válida no se ubica en zona
    vend = m2o_name(p.get("user_id")) or "Sin asignar"
    if vend == "Sin asignar":
        sin_vend += 1
    zona = zona_for(prov, p.get("city"))
    if prov == "Buenos Aires":           # auditoría del reparto AMBA / PAMPEANA
        if zona == "AMBA":
            ba_amba += 1
        else:
            ba_pamp += 1
            key = (p.get("city") or "").strip() or "(city VACÍO)"
            ba_city_pamp[key] = ba_city_pamp.get(key, 0) + 1
    clients.append({
        "id": p["id"],
        "name": p["name"],
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "vendedor": vend,
        "zona": zona,
        "provincia": prov,
        "direccion": ", ".join(x for x in [p.get("street"), p.get("city")] if x),
    })

# ------------------------------------------------------------------
# 5) Última factura de venta por cliente (account.move out_invoice posted)
# ------------------------------------------------------------------
partner_ids = [c["id"] for c in clients]
print(f"  Consultando facturas de venta para {len(partner_ids)} clientes...")
invoices = search_read("account.move", [
    ("move_type", "=", "out_invoice"),
    ("state", "=", "posted"),
    ("partner_id", "in", partner_ids),
], ["partner_id", "invoice_date"])

last_invoice = {}
for inv in invoices:
    pid = inv["partner_id"][0] if isinstance(inv["partner_id"], (list, tuple)) else inv["partner_id"]
    d = inv.get("invoice_date")
    if d and (pid not in last_invoice or d > last_invoice[pid]):
        last_invoice[pid] = d

today = date.today()
def calc_estado(fecha_str):
    if not fecha_str:
        return "SIN FACTURA"
    d = date.fromisoformat(fecha_str) if isinstance(fecha_str, str) else fecha_str
    diff = (today.year - d.year) * 12 + (today.month - d.month)
    if diff < 3:
        return "ACTIVO"
    if diff < 4:
        return "RIESGO MEDIO"
    if diff < 6:
        return "RIESGO ALTO"
    return "PERDIDO"

est_count = {}
for c in clients:
    uf = last_invoice.get(c["id"])
    c["ultima_factura"] = str(uf) if uf else None
    c["estado"] = calc_estado(uf)
    est_count[c["estado"]] = est_count.get(c["estado"], 0) + 1

print(f"  Facturas procesadas: {len(invoices)} registros · últimas de {len(last_invoice)} clientes")
print(f"  Estados: {', '.join(f'{e}={n}' for e, n in sorted(est_count.items()))}")

# ------------------------------------------------------------------
# 6) Reconstruir la estructura que usa el mapa (zonas / vendedores / prov2vend)
# ------------------------------------------------------------------
zonas = {z: sorted({c["provincia"] for c in clients if c["zona"] == z}) for z in ZONA_ORDER}
zonas = {z: provs for z, provs in zonas.items() if provs}      # solo zonas con datos

# Zona de cada vendedor = la zona más frecuente entre sus clientes.
# (Tomar "la del primer cliente" fallaba si un vendedor mezcla AMBA y PAMPEANA.)
from collections import Counter
_vz = {}
for c in clients:
    _vz.setdefault(c["vendedor"], Counter())[c["zona"]] += 1
vendedores = {v: {"zona": cnt.most_common(1)[0][0]} for v, cnt in _vz.items()}

prov2vend = {}
for c in clients:                                              # vendedor "representativo" por provincia
    prov2vend.setdefault(c["provincia"], c["vendedor"])

# Provincia -> zona, para pintar el fondo de cada polígono por zona.
# (Buenos Aires queda PAMPEANA; CABA, AMBA. El detalle AMBA real se ve en los puntos.)
prov2zona = {p: PROV2ZONA[p] for p in MAP_PROVS if p in PROV2ZONA}

data = {"zonas": zonas, "vendedores": vendedores, "prov2vend": prov2vend,
        "prov2zona": prov2zona, "clients": clients}

with open(os.path.join(OUTPUT_DIR, "data.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

print(f"OK · {len(partners)} en el filtro · {len(clients)} en el mapa · "
      f"{len(vendedores)} vendedores · {len(zonas)} zonas")
if sin_geo:  print(f"  ⚠ {sin_geo} sin geolocalizar (correr 'Geolocalizar' en Odoo)")
if sin_prov: print(f"  ⚠ {sin_prov} sin provincia reconocida (revisar state_id)")
if sin_vend: print(f"  ⚠ {sin_vend} sin vendedor asignado")

# Reparto de Buenos Aires (AMBA por partido vs PAMPEANA el resto)
if ba_amba or ba_pamp:
    print(f"\n  Buenos Aires: {ba_amba} en AMBA (partidos) · {ba_pamp} en PAMPEANA (resto)")
if ba_city_pamp:
    print("  Partidos de BA que NO matchearon AMBA (cayeron en PAMPEANA).")
    print("  Revisá que ninguno de estos sea en realidad de AMBA mal escrito:")
    for nombre, n in sorted(ba_city_pamp.items(), key=lambda x: -x[1]):
        print(f"    {n:5d}  ->  {nombre!r}")

if prov_no_reconocidas:
    print("\n  Nombres de provincia que Odoo devuelve y el script NO reconoce")
    print("  (copiá y pegá esta lista para armar el mapeo exacto):")
    for nombre, n in sorted(prov_no_reconocidas.items(), key=lambda x: -x[1]):
        print(f"    {n:5d}  ->  {nombre!r}")
