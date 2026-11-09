# -*- coding: utf-8 -*-
"""Сборка интерактивной карты «Поездки на человека в год, Краснодарский край, 2025».

LLMaps (points-sized рецепт) + MapLibre:
  • подложка CartoDB Positron (tiles="carto-light");
  • каждый НП — «верстовой столбик»: fill-extrusion (высота ∝ поездкам, линейно,
    с минимальной высотой) на круглом основании (CircleLayer, диаметр ∝ √населения);
  • контур границы края, подписи, hover-подсказки, легенда — custom JS/HTML/CSS
    через официальные hook'и Map.add_custom_js / add_custom_html / add_custom_css.

Выход: index.html в корне проекта (автономный файл, данные встроены).
"""
import json
import math
import os

from llmaps import Map
from llmaps.components import Controls
from llmaps.layers import CircleLayer, FillLayer
from llmaps.sources import FileSource

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "index.html")

# ── Фирменные цвета (референс fclm.ru — «Локомотив»: зелёный/красный/белый) ──
GREEN_DARK = "#1B4D3E"    # тело столбика, обводки, заголовки
GREEN_LIGHT = "#4C8A66"   # верхняя грань столбика
GREEN_TINT = "#F2F6F3"    # заливка платформы
RED = "#E31E24"           # акцент
INK = "#22302B"           # основной текст

MIN_H, MAX_H = 80.0, 1200.0   # м, шкала высоты (из prepare_data.py)
D_MIN, D_MAX = 150.0, 700.0   # м, шкала диаметра основания (реальные)
A_W = 5.0                     # множитель footprint'а (как в prepare_data.py)
K_H = 60.0                    # множитель высоты столбика (единообразный)
LAT0 = 45.2                   # опорная широта для перевода метров в пиксели


def radius_px_stops():
    """Стопы interpolate для circle-radius: r_px(z) = A_W·radius_m / m_per_px(z),
    с нижним порогом 2 px и капом 30 px (всё внутри стопов — zoom допустим
    только в корневом interpolate)."""
    m_world = 40075016.686 * math.cos(math.radians(LAT0))  # м по параллели
    stops = []
    z = 5.5
    while z <= 13.01:
        f = A_W * 512.0 * (2.0 ** z) / m_world
        stops += [round(z, 1), ["min", 30.0, ["max", 2.0, ["*", f, ["get", "radius_m"]]]]]
        z += 0.5
    return ["interpolate", ["linear"], ["zoom"]] + stops

# ═════════════════════════════════ Данные ════════════════════════════════════
with open(os.path.join(DATA, "settlements-points.geojson"), encoding="utf-8") as f:
    pts = json.load(f)["features"]
POP_MAX = max(p["properties"]["pop"] for p in pts if p["properties"]["pop"])
TRIPS_MAX = max(p["properties"]["trips"] for p in pts)


def circle_radius_expr():
    """Диаметр основания ∝ √населения (площадь ∝ численности), минимум 4.5 px.

    Внимание: применяется в custom JS (ensureLayers), т.к. слои с data-driven
    выражениями при начальной загрузке LLMaps отбрасываются — см. комментарий в JS.
    """
    r = ["/", ["get", "pop"], float(POP_MAX)]
    band = lambda k: ["*", k, ["sqrt", r]]
    return [
        "max", 4.5,
        ["interpolate", ["linear"], ["zoom"],
         5.5, band(7.0), 7.0, band(10.0), 8.5, band(15.0),
         10.0, band(22.0), 12.0, band(34.0)],
    ]


# ═════════════════════════ Custom JS (3D-столбики, подписи, popup) ═══════════
# Важно: слои с data-driven выражениями, добавленные в момент начальной загрузки
# LLMaps, молча отбрасываются; те же спеки, добавленные чуть позже, прилипают.
# Поэтому все «умные» слои добавляет идемпотентный ensure()-цикл: раз в секунду
# он добирает отсутствующие слои и переживает скрытие/восстановление вкладки.
JS = r"""
(function () {
  'use strict';
  var POP_MAX = __POP_MAX__;

  function ensure(map, id, spec, beforeId) {
    if (map.getLayer(id)) return true;
    try {
      var before = beforeId && map.getLayer(beforeId) ? beforeId : undefined;
      map.addLayer(spec, before);
      return !!map.getLayer(id);
    } catch (e) { return false; }
  }

  function ensureLayers(map) {
    var ok = true;
    /* контур границы края */
    ok = ensure(map, 'region-outline', { id: 'region-outline', type: 'line', source: 'region',
      paint: { 'line-color': '#1B4D3E',
        'line-width': ['interpolate', ['linear'], ['zoom'], 5.5, 1.2, 8, 2.0, 10, 2.8],
        'line-opacity': 0.9 } }, 'posts-carrier') && ok;
    /* круглые основания: диаметр ∝ √населения, перевод м→px сделан в Python
       (radius_px_stops); zoom допустим только в корневом interpolate — вся
       арифметика ('min'/'max') вынесена ВНУТРЬ стопов. */
    ok = ensure(map, 'settlements-base', { id: 'settlements-base', type: 'circle', source: 'settlements',
      paint: { 'circle-color': '#F2F6F3', 'circle-opacity': 0.95,
        'circle-stroke-color': '#1B4D3E', 'circle-stroke-width': 1.2, 'circle-stroke-opacity': 0.85,
        'circle-radius': __RADIUS_STOPS__ } },
      'posts-3d') && ok;
    /* верстовые столбики: тело + светлая верхняя грань.
       Высота: height_m × K_H (константа — чистое data-выражение); footprint
       уже ×A_W в геометрии, поэтому пропорции столбика не зависят от зума.
       Столбики плавно растворяются к зумам 10–11 (глубокий городской зум). */
    var H = ['*', __K_H__, ['get', 'height_m']];
    var fade = ['interpolate', ['linear'], ['zoom'], 10, 0.96, 11, 0];
    ok = ensure(map, 'posts-3d', { id: 'posts-3d', type: 'fill-extrusion', source: 'posts',
      paint: { 'fill-extrusion-color': '#1B4D3E', 'fill-extrusion-height': H,
        'fill-extrusion-base': 0, 'fill-extrusion-opacity': fade,
        'fill-extrusion-vertical-gradient': true } }) && ok;
    ok = ensure(map, 'posts-cap', { id: 'posts-cap', type: 'fill-extrusion', source: 'posts',
      paint: { 'fill-extrusion-color': '#4C8A66',
        'fill-extrusion-height': ['+', ['*', __K_H__, ['get', 'height_m']],
                                    ['*', __K_H__ * 0.18, ['get', 'height_m']]],
        'fill-extrusion-base': H,
        'fill-extrusion-opacity': fade } }) && ok;
    /* подписи НП: тёмно-зелёные с белым гало, приоритет крупным */
    ok = ensure(map, 'settlements-labels', { id: 'settlements-labels', type: 'symbol',
      source: 'settlements', minzoom: 5.8,
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 5.8, 10.5, 8, 11.5, 10, 13],
        'text-anchor': 'bottom', 'text-offset': [0, -1.35], 'text-padding': 3,
        'text-max-width': 10, 'symbol-sort-key': ['-', ['get', 'pop']],
        'text-allow-overlap': false },
      paint: { 'text-color': '#1B4D3E', 'text-halo-color': '#FFFFFF',
        'text-halo-width': 1.6, 'text-halo-blur': 0.4 } }) && ok;
    return ok;
  }

  function bindInteractions(map) {
    if (map.__kkPopBound) return;
    map.__kkPopBound = true;
    var popup = null;
    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
    function row(label, val, unit) {
      if (val === '' || val == null) return '';
      return '<tr><td>' + label + '</td><td><b>' + esc(val) + '</b>' +
             (unit ? ' <span class="kk-u">' + unit + '</span>' : '') + '</td></tr>';
    }
    function showPopup(ev) {
      var f = ev.features && ev.features[0];
      if (!f) return;
      var p = f.properties || {};
      var html =
        '<div class="kk-pop">' +
          '<div class="kk-pop-h"><span>' + esc(p.name) + '</span>' +
            '<span class="kk-pop-badge">' + esc(p.trips_f) + '</span></div>' +
          '<table class="kk-pop-t">' +
            '<tr class="kk-pop-main"><td>Поездок на человека в год</td><td><b>' +
              esc(p.trips_f) + '</b></td></tr>' +
            row('Население', p.pop_f, 'чел.') +
            row('Автобусы', p.buses_f, 'шт.') +
            row('Перевезено пассажиров', p.passengers_f, 'тыс. чел.') +
            row('Пассажирооборот', p.paxkm_f, 'тыс. пасс-км') +
          '</table>' +
          '<div class="kk-pop-src">Росстат · 2025</div>' +
        '</div>';
      if (!popup) popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, maxWidth: '280px' });
      popup.setLngLat(ev.lngLat).setHTML(html).addTo(map);
      map.getCanvas().style.cursor = 'pointer';
    }
    function hidePopup() {
      if (popup) popup.remove();
      popup = null;
      map.getCanvas().style.cursor = '';
    }
    ['settlements-base', 'posts-3d', 'posts-cap'].forEach(function (lid) {
      map.on('mousemove', lid, showPopup);
      map.on('mouseleave', lid, hidePopup);
      map.on('click', lid, showPopup);   // тап на мобильных
    });
  }

  function fitRegion(map) {
    if (map.__kkFit) return;
    map.__kkFit = true;
    if (/^#[\d.]+\/-?[\d.]+\/-?[\d.]+$/.test(window.location.hash)) {
      map.setPitch(50);
      return;
    }
    window.llmapsGetSourceData('region').then(function (geo) {
      if (!geo) return;
      var b = [-180, 90, -90, -90];
      var flat = function (c) {
        for (var k = 0; k < c.length; k++) {
          if (typeof c[k][0] === 'number') {
            if (c[k][0] < b[0]) b[0] = c[k][0];
            if (c[k][0] > b[2]) b[2] = c[k][0];
            if (c[k][1] < b[1]) b[1] = c[k][1];
            if (c[k][1] > b[3]) b[3] = c[k][1];
          } else flat(c[k]);
        }
      };
      for (var i = 0; i < geo.features.length; i++) flat(geo.features[i].geometry.coordinates);
      var cam = map.cameraForBounds([[b[0], b[1]], [b[2], b[3]]],
        { padding: { top: 190, bottom: 30, left: 30, right: 30 } });
      map.jumpTo({ center: cam.center, zoom: cam.zoom, pitch: 50, bearing: 0 });
    });
  }

  function mobilize() {
    if (window.innerWidth < 640) {
      var d = document.getElementById('kk-legend');
      if (d) d.removeAttribute('open');
    }
  }

  window.llmapsOnLayersReady(function (map) {
    var timer = setInterval(function () {
      if (ensureLayers(map)) {
        bindInteractions(map);
        fitRegion(map);
      }
    }, 700);
    /* страховка после скрытия/восстановления вкладки */
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) setTimeout(function () { ensureLayers(map); }, 900);
    });
    mobilize();
  });
})();
""".replace("__POP_MAX__", str(POP_MAX)) \
   .replace("__K_H__", repr(K_H)) \
   .replace("__RADIUS_STOPS__", json.dumps(radius_px_stops()))

# ═══════════════════════════════ Легенда (HTML) ══════════════════════════════
def bar_px(v):
    """Пиксели легенды: реальная карта высот h = MIN_H + v/TRIPS_MAX*(MAX_H-MIN_H)."""
    return max(6.0, (MIN_H + v / TRIPS_MAX * (MAX_H - MIN_H)) / MAX_H * 64.0)


def circ_px(pop):
    """Пиксели легенды: реальный диаметр d = max(D_MIN, D_MAX*sqrt(pop/POP_MAX))."""
    return max(9.4, max(D_MIN, D_MAX * (pop / POP_MAX) ** 0.5) / D_MAX * 44.0)


def ru(v, dec=1):
    s = f"{v:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s


def legend_html():
    bars = [
        (TRIPS_MAX, "с. Борисовка"),
        (100.0, ""),
        (25.0, ""),
        (min(p["properties"]["trips"] for p in pts), ""),
    ]
    circles = [(POP_MAX, "Краснодар"), (100_000.0, ""), (10_000.0, "")]
    bh = "".join(
        f'<div class="kk-bar-col"><div class="kk-bar" style="height:{bar_px(v):.0f}px"></div>'
        f'<div class="kk-bar-v">{ru(v, 1)}</div></div>'
        for v, _ in bars
    )
    ch = "".join(
        f'<div class="kk-cir-col"><div class="kk-cir" style="width:{circ_px(p):.0f}px;height:{circ_px(p):.0f}px"></div>'
        f'<div class="kk-bar-v">{ru(p / 1000, 0)} тыс.</div></div>'
        for p, _ in circles
    )
    return (
        '<details id="kk-legend" class="kk-panel kk-legend" open>'
        "<summary>Как читать карту</summary>"
        '<div class="kk-leg-block"><div class="kk-leg-t">Высота столбика — поездок на человека в год</div>'
        f'<div class="kk-bars">{bh}</div>'
        '<div class="kk-leg-note">линейная шкала, ' + ru(TRIPS_MAX) + " — максимум; минимальная высота для читаемости</div></div>"
        '<div class="kk-leg-block"><div class="kk-leg-t">Диаметр основания — население (√-шкала)</div>'
        f'<div class="kk-cirs">{ch}</div>'
        '<div class="kk-leg-note">площадь платформы ∝ численности жителей</div></div>'
        '<div class="kk-leg-foot">На карте — ' + str(len(pts)) + " НП с данными Росстата; "
        "78 НП без значения показателя скрыты.<br>Данные: Росстат, наблюдение за автобусными перевозками, 2025.</div>"
        "</details>"
    )


TITLE_HTML = (
    '<div id="kk-title" class="kk-panel kk-title">'
    '<div class="kk-tag">Краснодарский край · 2025</div>'
    "<h1>Поездки на человека в год</h1>"
    "<p>Высота верстового столбика — поездки, диаметр основания — население.</p>"
    '<div class="kk-title-src">Данные: Росстат, 2025 · подложка CartoDB Positron</div>'
    "</div>"
)

# ═══════════════════════════════ CSS (стиль «Локомотив») ═════════════════════
CSS = """
.kk-panel{position:absolute;z-index:5;background:#fff;border-radius:10px;
  box-shadow:0 4px 18px rgba(27,77,62,.18);font-family:'Segoe UI',Arial,sans-serif;color:#22302B}
.kk-title{top:12px;left:12px;max-width:320px;padding:14px 18px 12px;border-left:5px solid #E31E24}
.kk-title h1{margin:6px 0 6px;font-size:19px;line-height:1.2;color:#1B4D3E}
.kk-title p{margin:0 0 8px;font-size:12.5px;line-height:1.45;color:#4a5a53}
.kk-tag{display:inline-block;background:#1B4D3E;color:#fff;font-size:10.5px;font-weight:700;
  letter-spacing:.09em;text-transform:uppercase;padding:3px 8px;border-radius:4px}
.kk-title-src{font-size:10.5px;color:#8a978f;border-top:1px solid #e7ece9;padding-top:6px}
.kk-legend{bottom:28px;left:12px;max-width:320px;padding:0}
.kk-legend summary{cursor:pointer;padding:10px 16px;font-size:13px;font-weight:700;color:#1B4D3E;
  background:#F2F6F3;border-radius:10px 10px 0 0;list-style-position:inside}
.kk-leg-block{padding:10px 16px 4px}
.kk-leg-t{font-size:11.5px;font-weight:600;color:#22302B;margin-bottom:8px}
.kk-bars,.kk-cirs{display:flex;align-items:flex-end;gap:16px;padding:2px 2px 0}
.kk-bar{width:14px;border-radius:3px 3px 0 0;background:linear-gradient(180deg,#4C8A66 8%,#1B4D3E 60%)}
.kk-cir{border:1.4px solid #1B4D3E;background:#F2F6F3;border-radius:50%}
.kk-bar-v{font-size:10px;color:#5a6a62;margin-top:4px;text-align:center;white-space:nowrap}
.kk-bar-col,.kk-cir-col{display:flex;flex-direction:column;align-items:center;justify-content:flex-end}
.kk-cir-col{padding-bottom:2px}
.kk-leg-note{font-size:10px;color:#8a978f;margin:6px 0 2px}
.kk-leg-foot{font-size:10px;color:#8a978f;border-top:1px solid #e7ece9;margin-top:8px;padding:8px 16px 10px;line-height:1.5}
.kk-pop{font-family:'Segoe UI',Arial,sans-serif;min-width:190px}
.kk-pop-h{display:flex;justify-content:space-between;align-items:center;gap:12px;
  background:#1B4D3E;color:#fff;padding:8px 12px;border-radius:8px 8px 0 0;font-size:13.5px;font-weight:700;
  border-bottom:3px solid #E31E24}
.kk-pop-badge{background:#E31E24;border-radius:5px;padding:2px 8px;font-size:12.5px;white-space:nowrap}
.kk-pop-t{width:100%;border-collapse:collapse;font-size:12px;color:#22302B}
.kk-pop-t td{padding:5px 12px;border-bottom:1px solid #eef2ef}
.kk-pop-t td:last-child{text-align:right;white-space:nowrap}
.kk-pop-main td{color:#1B4D3E}
.kk-u{color:#8a978f;font-size:10.5px}
.kk-pop-src{font-size:10px;color:#8a978f;padding:6px 12px 8px;text-align:right}
.maplibregl-popup-content{padding:0!important;border-radius:8px!important;overflow:hidden}
@media (max-width:640px){
  .kk-title{max-width:230px;padding:10px 14px 9px}
  .kk-title h1{font-size:15px}
  .kk-title p{font-size:11px}
  .kk-legend{max-width:250px}
}
"""


def main():
    src_region = FileSource(id="region", path=os.path.join(DATA, "region.geojson"))
    src_posts = FileSource(id="posts", path=os.path.join(DATA, "settlements-posts.geojson"))
    src_sett = FileSource(id="settlements", path=os.path.join(DATA, "settlements-points.geojson"))

    region_fill = FillLayer(
        id="region-fill", source=src_region,
        fill_color=GREEN_DARK, fill_opacity=0.045,
        stroke_color=GREEN_DARK, stroke_width=1,
    )
    posts_carrier = FillLayer(  # невидимый носитель источника posts (3D добавит JS)
        id="posts-carrier", source=src_posts,
        fill_color=GREEN_DARK, fill_opacity=0.0, stroke_color=None,
    )
    sett_carrier = CircleLayer(  # невидимый носитель источника settlements
        id="settlements-carrier", source=src_sett,
        radius=0.01, color=GREEN_DARK, opacity=0.0,
    )

    m = Map(
        center=[38.97, 44.93], zoom=6.3,
        title="Поездки на человека в год — Краснодарский край, 2025",
        # CartoDB Positron, обслуживаемый OpenFreeMap: тот же стиль Carto,
        # но без API-ключа CARTO (их растровые тайлы закрыты ключом) и с глифами
        style_url="https://tiles.openfreemap.org/styles/positron",
        tiles="carto-light", locale="ru-RU",
        embedded=True, use_compression=True,
    )
    m.add_layer(region_fill)
    m.add_layer(posts_carrier)
    m.add_layer(sett_carrier)
    m.add_component(Controls(zoom=True, scale=True, fullscreen=True))
    m.add_custom_css(CSS)
    m.add_custom_html(TITLE_HTML + legend_html())
    m.add_custom_js(JS)
    m.save(OUT)
    print("Сохранено:", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
