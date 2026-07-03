#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
suri_to_json.py
Convierte CLIENTES_GEOLOCALIZADOS_SURI.xlsx (clientes del sistema anterior,
SURI) en public/suri.json, con la misma forma que data.json, para que el
mapa pueda distinguir "Odoo" de "SURI" dentro de la vista Clientes.

La planilla no trae el nombre de provincia (el campo 'provi' es un código
interno de SURI, no estandarizado), así que la provincia/zona se calcula
geométricamente: se reusan los mismos polígonos de provincias/partidos que
ya están embebidos en public/index.html (la constante GEO), haciendo un
point-in-polygon con la latitud/longitud de cada cliente.

Corré:  python suri_to_json.py
"""
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(BASE_DIR, "CLIENTES_GEOLOCALIZADOS_SURI.xlsx")
INDEX_HTML = os.path.join(BASE_DIR, "public", "index.html")
OUTPUT_PATH = os.path.join(BASE_DIR, "public", "suri.json")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

# ------------------------------------------------------------------
# Provincia -> Zona (mismo mapeo que odoo_to_map.py / export_competencia_json.py)
# ------------------------------------------------------------------
MAP_PROVS = ["Buenos Aires", "CABA", "Catamarca", "Chaco", "Chubut", "Cordoba", "Corrientes",
 "Entre Rios", "Formosa", "Jujuy", "La Pampa", "La Rioja", "Mendoza", "Misiones", "Neuquen",
 "Rio Negro", "Salta", "San Juan", "San Luis", "Santa Cruz", "Santa Fe",
 "Santiago del Estero", "Tierra del Fuego", "Tucuman"]

PROV2ZONA = {
 "Jujuy": "NOA", "Salta": "NOA", "Tucuman": "NOA", "Catamarca": "NOA", "Santiago del Estero": "NOA", "La Rioja": "NOA",
 "Formosa": "NEA", "Chaco": "NEA", "Corrientes": "NEA", "Misiones": "NEA",
 "Mendoza": "CUYO", "San Juan": "CUYO", "San Luis": "CUYO",
 "Cordoba": "PAMPEANA", "Santa Fe": "PAMPEANA", "Entre Rios": "PAMPEANA", "La Pampa": "PAMPEANA", "Buenos Aires": "PAMPEANA",
 "CABA": "AMBA",
 "Neuquen": "PATAGONICA", "Rio Negro": "PATAGONICA", "Chubut": "PATAGONICA", "Santa Cruz": "PATAGONICA", "Tierra del Fuego": "PATAGONICA",
}
ZONA_ORDER = ["PAMPEANA", "AMBA", "CUYO", "NEA", "NOA", "PATAGONICA"]
VENDEDOR_SURI = "Sistema anterior (SURI)"

# ------------------------------------------------------------------
# 1) Point-in-polygon contra los mismos polígonos que dibuja el mapa
#    (se extraen de la constante GEO embebida en public/index.html).
# ------------------------------------------------------------------
def load_geo():
    html = open(INDEX_HTML, encoding="utf-8").read()
    m = re.search(r"const GEO = (\{.*?\});", html, re.S)
    if not m:
        raise SystemExit("No se encontró 'const GEO = {...};' en public/index.html")
    return json.loads(m.group(1))["features"]


def bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def prep_feature(f):
    geom = f["geometry"]
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    prepped = []
    for poly in polys:
        if not poly:
            continue
        outer = poly[0]
        prepped.append({"bbox": bbox(outer), "outer": outer, "holes": poly[1:]})
    return {"name": f["properties"]["name"], "polys": prepped}


def feature_contains(feat, x, y):
    for poly in feat["polys"]:
        x0, y0, x1, y1 = poly["bbox"]
        if x < x0 or x > x1 or y < y0 or y > y1:
            continue
        if point_in_ring(x, y, poly["outer"]):
            if any(point_in_ring(x, y, h) for h in poly["holes"]):
                continue
            return True
    return False


geo_features = [prep_feature(f) for f in load_geo()]
partido_features = [f for f in geo_features if f["name"] not in MAP_PROVS]
province_features = [f for f in geo_features if f["name"] in MAP_PROVS and f["name"] != "Buenos Aires"]
buenosaires_feature = next(f for f in geo_features if f["name"] == "Buenos Aires")


def locate(lng, lat):
    for f in partido_features:
        if feature_contains(f, lng, lat):
            return "Buenos Aires", "AMBA"
    for f in province_features:
        if feature_contains(f, lng, lat):
            name = f["name"]
            zona = "AMBA" if name == "CABA" else PROV2ZONA.get(name, "Sin zona")
            return name, zona
    if feature_contains(buenosaires_feature, lng, lat):
        return "Buenos Aires", "PAMPEANA"
    return None, None


def main():
    df = pd.read_excel(XLSX_PATH, sheet_name="Sheet1")

    total = len(df)
    sin_geo, sin_prov = 0, 0
    clients = []

    def clean(v, default=""):
        if pd.isna(v):
            return default
        return str(v).strip()

    for _, row in df.iterrows():
        lat, lng = row.get("latitud"), row.get("longitud")
        if pd.isna(lat) or pd.isna(lng):
            sin_geo += 1
            continue
        prov, zona = locate(float(lng), float(lat))
        if not prov:
            sin_prov += 1
            continue
        codigo = clean(row.get("codigo"))
        name = clean(row.get("nombre")) or clean(row.get("fantasia")) or f"Cliente SURI {codigo}"
        direc = clean(row.get("direc"))
        locali = clean(row.get("locali"))
        clients.append({
            "id": "suri-" + (codigo or str(_)),
            "name": name,
            "lat": round(float(lat), 5),
            "lng": round(float(lng), 5),
            "vendedor": VENDEDOR_SURI,
            "zona": zona,
            "provincia": prov,
            "direccion": ", ".join(p for p in [direc, locali] if p),
            "codigo": codigo,
            "cuit": clean(row.get("cuit")),
            "telefono": clean(row.get("telefono")),
            "email": clean(row.get("email")),
            "origen": "SURI",
        })

    zonas = {z: sorted({c["provincia"] for c in clients if c["zona"] == z}) for z in ZONA_ORDER}
    zonas = {z: provs for z, provs in zonas.items() if provs}

    vendedores = {VENDEDOR_SURI: {"zona": max(zonas, key=lambda z: sum(1 for c in clients if c["zona"] == z))}} if clients else {}
    prov2zona = {p: PROV2ZONA[p] for p in MAP_PROVS if p in PROV2ZONA}

    data = {"zonas": zonas, "vendedores": vendedores, "prov2zona": prov2zona, "clients": clients}

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    print(f"OK · {total} en Sheet1 · {len(clients)} en el mapa · {len(zonas)} zonas")
    if sin_geo:
        print(f"  ⚠ {sin_geo} sin coordenadas (latitud/longitud vacías)")
    if sin_prov:
        print(f"  ⚠ {sin_prov} sin provincia reconocida (punto fuera de todos los polígonos)")
    print(f"  Escrito en: {os.path.abspath(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
