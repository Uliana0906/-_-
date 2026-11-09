# -*- coding: utf-8 -*-
"""Подготовка данных для карты транспортной мобильности Краснодарского края.

Вход:  C:\\Users\\user\\Downloads\\краснодарский_край_транспорт_2025 (1).csv
Выход: data/settlements-points.geojson  — точки (27 НП со значением показателя)
       data/settlements-posts.geojson   — 8-угольные полигоны-«столбики»
                                           (свойства height_m — высота верстового
                                           столбика, radius_m — радиус основания)

Правила кодирования (по ТЗ):
  A. Высота столбика  — линейная шкала «поездки на человека в год»
     h = max(MIN_H, v / MAX_V * MAX_H)   (максимум → MAX_H, минимум читаемости MIN_H)
  B. Диаметр основания — квадратный корень численности населения
     d = max(D_MIN, D_MAX * sqrt(pop / POP_MAX))   (площадь ∝ населению)
"""
import csv
import json
import math
import os

CSV_PATH = r"C:\Users\user\Downloads\краснодарский_край_транспорт_2025 (1).csv"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# ── Шкала высоты (метры на карте) ────────────────────────────────────────────
MIN_H = 80.0      # минимальная высота столбика — маленькие значения не исчезают
MAX_H = 1200.0    # высота столбика НП с максимальным значением показателя

# ── Шкала диаметра основания (метры на карте) ────────────────────────────────
D_MIN = 150.0    # минимальный диаметр платформы
D_MAX = 700.0    # диаметр платформы для НП с максимальным населением

# ── Преувеличение footprint'а столбика ───────────────────────────────────────
# Реальные диаметры (150–700 м) на обзорном зуме субпиксельны. Геометрия
# призмы строится в A_W раз шире (множитель одинаков для всех НП — пропорции
# √-шкалы сохраняются); основания-круги в build_map.py используют тот же
# множитель, поэтому столбик всегда «стоит» на своём основании.
A_W = 5.0

N_SIDES = 8      # 8-угольная призма — лаконичный «верстовой столбик»


def fnum(raw):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def ru_num(value, decimals=1):
    if value is None:
        return ""
    s = f"{value:,.{decimals}f}"
    return s.replace(",", " ").replace(".", ",")


def octagon(lon, lat, radius_m):
    """8-угольный полигон радиуса radius_m вокруг (lon, lat), WGS84."""
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    ring = []
    for i in range(N_SIDES):
        a = math.radians(22.5 + 360.0 * i / N_SIDES)
        ring.append([
            round(lon + dlon * math.cos(a), 6),
            round(lat + dlat * math.sin(a), 6),
        ])
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def main():
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    picked = []
    skipped = {"no_value": [], "no_coords": []}
    for r in rows:
        name = (r["НП"] or "").strip()
        lat = fnum(r.get("Широта"))
        lon = fnum(r.get("Долгота"))
        trips = fnum(r.get("Поездок на человека в год"))
        if trips is None:
            skipped["no_value"].append(name)
            continue
        if lat is None or lon is None:
            skipped["no_coords"].append(name)
            continue
        picked.append({
            "name": name,
            "oktmo": (r.get("ОКТМО") or "").strip(),
            "pop": fnum(r.get("Население")),
            "trips": trips,
            "buses": fnum(r.get("Автобусы, шт.")),
            "passengers": fnum(r.get("Перевезено пассажиров, тыс. чел.")),
            "paxkm": fnum(r.get("Пассажирооборот, тыс. пасс-км")),
            "lon": lon,
            "lat": lat,
        })

    if not picked:
        raise SystemExit("Нет записей со значением показателя!")
    trips_max = max(p["trips"] for p in picked)
    pop_max = max((p["pop"] or 0) for p in picked)

    point_feats, post_feats = [], []
    for p in picked:
        v = p["trips"]
        height = max(MIN_H, v / trips_max * MAX_H)
        pop = p["pop"] or 0
        diameter = max(D_MIN, D_MAX * math.sqrt(pop / pop_max)) if pop > 0 else D_MIN
        radius = diameter / 2.0

        props = {
            "name": p["name"],
            "oktmo": p["oktmo"],
            "pop": pop,
            "pop_f": ru_num(pop, 0),
            "trips": round(v, 2),
            "trips_f": ru_num(v, 1),
            "height_m": round(height, 1),
            "radius_m": round(radius, 1),
            "buses_f": ru_num(p["buses"], 0),
            "passengers_f": ru_num(p["passengers"], 1),
            "paxkm_f": ru_num(p["paxkm"], 0),
            "source": "Росстат, 2025",
        }
        point_feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(p["lon"], 6), round(p["lat"], 6)]},
            "properties": props,
        })
        post_feats.append({
            "type": "Feature",
            "geometry": octagon(p["lon"], p["lat"], radius * A_W),
            "properties": {
                "name": p["name"],
                "height_m": props["height_m"],
                # витринные поля — popup навешен и на 3D-слои
                "trips_f": props["trips_f"],
                "pop_f": props["pop_f"],
                "buses_f": props["buses_f"],
                "passengers_f": props["passengers_f"],
                "paxkm_f": props["paxkm_f"],
            },
        })

    with open(os.path.join(OUT_DIR, "settlements-points.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": point_feats}, f, ensure_ascii=False)
    with open(os.path.join(OUT_DIR, "settlements-posts.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": post_feats}, f, ensure_ascii=False)

    print("НП с показателем на карте:", len(point_feats))
    print("Скрыто (нет значения):", len(skipped["no_value"]))
    print("Макс. поездки:", ru_num(trips_max), "| макс. население:", ru_num(pop_max, 0))
    print("Высоты:", ru_num(MIN_H, 0), "-", ru_num(MAX_H, 0), "м; диаметры:", ru_num(D_MIN, 0), "-", ru_num(D_MAX, 0), "м; footprint x", ru_num(A_W, 0))


if __name__ == "__main__":
    main()
