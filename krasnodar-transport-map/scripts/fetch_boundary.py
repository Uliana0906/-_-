# -*- coding: utf-8 -*-
"""Загрузка границы Краснодарского края из OpenStreetMap (Nominatim).

Результат: data/region.geojson — Polygon/MultiPolygon в WGS84,
упрощённый на стороне Nominatim (polygon_threshold).
"""
import json
import os
import urllib.request
import urllib.parse

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "region.geojson")
UA = "kk-transport-map/1.0 (student research project)"
QUERY = "Краснодарский край"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    q = urllib.parse.quote(QUERY)
    url = (
        "https://nominatim.openstreetmap.org/search?q={}&format=json"
        "&polygon_geojson=1&polygon_threshold=0.0015&limit=5".format(q)
    )
    results = fetch(url)
    cand = [
        r for r in results
        if r.get("osm_type") == "relation"
        and r.get("class") == "boundary"
        and "район" not in r.get("display_name", "").lower()
    ]
    if not cand:
        raise SystemExit("Не найдена граница края: %s" % [r.get("display_name") for r in results])
    best = max(cand, key=lambda r: r.get("rank_address", 0))
    print("Выбрано:", best["display_name"], "relation", best["osm_id"])
    print("Точек в геометрии:", len(json.dumps(best["geojson"])))

    feature = {
        "type": "Feature",
        "properties": {
            "name": "Краснодарский край",
            "osm_id": best["osm_id"],
            "source": "OpenStreetMap (Nominatim), упрощено polygon_threshold=0.0015",
        },
        "geometry": best["geojson"],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": [feature]}, f, ensure_ascii=False)
    print("Сохранено:", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
