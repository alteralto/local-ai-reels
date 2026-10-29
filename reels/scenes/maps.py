"""Карта города (map) и свободная сцена (custom)."""
from __future__ import annotations

import re

from .base import SceneCtx, SceneDoc, SceneOut, esc, num, rich, scene
from .common import T, as_lines, fit, kick_js, kicker, reveal, times

# квартальная сетка: улицы через 100 и пара диагоналей (как в примере «Поводок»)
GRID = "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="900"/><line x1="0" y1="{x}" x2="900" y2="{x}"/>'
               for x in range(50, 900, 100)) + \
    '<line x1="0" y1="820" x2="900" y2="120"/><line x1="0" y1="160" x2="640" y2="900"/>'
PATH_RE = re.compile(r"[MLHVCSQTAZmlhvcsqtaz0-9 ,.\-]+")


def _xy(pt: dict) -> tuple[float, float]:
    x, y = float(pt["x"]), float(pt["y"])
    if not (0 <= x <= 900 and 0 <= y <= 900):
        raise ValueError(f"точка ({x}, {y}) вне карты 0…900")
    return x, y


@scene("map", SceneDoc(
    "Схема города в стиле чертежа: сетка улиц, точки с подписями, круги радиусов, маршрут; сверху 1–2 строки заголовка.",
    {"points": "список {x, y, label, accent: bool} в координатах 0…900; короткая подпись (≤4 знака, «WC») — в плашке",
     "radius": "радиус кругов вокруг точек (кроме accent) в единицах карты 0…900; необязательно",
     "dim": "true — затемнить всё вне кругов (нужен radius); по умолчанию false",
     "route": "линия маршрута: SVG-путь d («M40 520 C 220 470, …»); необязательно",
     "title": "1–2 строки заголовка над картой; *слово* — акцентом; необязательно",
     "title_at": "якорь заголовка или список якорей по строкам",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь появления точек; по умолчанию 0.4 с",
     "route_at": "якорь прорисовки маршрута; по умолчанию 0.8 с",
     "radius_at": "якорь кругов; по умолчанию 50%"},
    {"kicker": "город · радиус свободы", "title": ["Невидимый", "*поводок*."], "title_at": ["невидимый", "поводок"],
     "points": [{"x": 250, "y": 250, "label": "WC"}, {"x": 650, "y": 300, "label": "WC"},
                {"x": 470, "y": 430, "label": "я", "accent": True}],
     "radius": 200, "radius_at": "длину", "route": "M40 520 C 220 470, 330 380, 450 420 S 700 560, 860 380"}))
def map_(ctx: SceneCtx, p: dict) -> SceneOut:
    pts = [x for x in (p.get("points") or [])]
    title = as_lines(p.get("title"))
    html, css, js = kicker(p), [], kick_js(p)
    top = 300
    if title:
        tsz = min(fit(x, 96) for x in title)
        tts = times(ctx, p.get("title_at"), len(title), .4, .5)
        for i, (ln, tt) in enumerate(zip(title, tts)):
            html += f'<div class="big tl{i}"><span>{rich(ln)}</span></div>'
            css.append(f"{ctx.sel} .tl{i}{{top:{260 + i * tsz * 1.05:.0f}px;font-size:{tsz}px;height:{tsz * 1.12:.0f}px}}")
            js += reveal(f".tl{i}", tt)
        top = 260 + len(title) * tsz * 1.05 + 50
    size = min(900, 1440 - top)
    r = float(p.get("radius") or 0)
    rp = [_xy(x) for x in pts if not x.get("accent")]   # круги — вокруг обычных точек, не вокруг «я»
    dim = bool(p.get("dim")) and r > 0
    svg = ['<svg class="map" viewBox="0 0 900 900">']
    if dim:
        holes = "".join(f'<circle class="hole" cx="{x}" cy="{y}" r="0" fill="black"/>' for x, y in rp)
        svg.append(f'<defs><mask id="{ctx.uid}-reach"><rect width="900" height="900" fill="white"/>{holes}</mask></defs>')
    svg.append(f'<g class="grid">{GRID}</g>')
    if p.get("route"):
        d = str(p["route"])
        if not PATH_RE.fullmatch(d):
            raise ValueError("route: SVG-путь из команд M L C S Q A Z и чисел")
        svg.append(f'<path class="route" d="{esc(d)}" pathLength="1"/>')
    if dim:
        svg.append(f'<rect class="dim" width="900" height="900" mask="url(#{ctx.uid}-reach)"/>')
    if r:
        svg += [f'<circle class="ring" cx="{x}" cy="{y}" r="0"/>' for x, y in rp]
    for i, pt in enumerate(pts):
        x, y = _xy(pt)
        lab = str(pt.get("label", ""))
        a = " acc" if pt.get("accent") else ""
        if lab and len(lab) <= 4 and not pt.get("accent"):
            w = 26 + 18 * len(lab)
            svg.append(f'<g class="pt box{a}"><rect x="{x - w / 2:.0f}" y="{y - 24}" width="{w}" height="48" rx="8"/>'
                       f'<text x="{x}" y="{y + 9}" text-anchor="middle">{esc(lab)}</text></g>')
        else:
            tx = f'<text class="pl" x="{x + 30}" y="{y + 10}">{esc(lab)}</text>' if lab else ""
            svg.append(f'<g class="pt dot{a}"><circle cx="{x}" cy="{y}" r="18"/>{tx}</g>')
    svg.append("</svg>")
    html += "".join(svg)
    th = ctx.at(p.get("at"), .4)
    js += f'tl.fromTo(S+" .grid",{{opacity:0}},{{opacity:1,duration:.6}},t+.05);\n'
    if pts:
        js += (f'tl.fromTo(S+" .pt",{{opacity:0,scale:.4,transformOrigin:"50% 50%"}},{{opacity:1,scale:1,duration:.35,'
               f'stagger:.1,ease:"back.out(3)"}},{T(th)});\n')
    if p.get("route"):
        js += (f'tl.fromTo(S+" .route",{{strokeDashoffset:1}},{{strokeDashoffset:0,duration:1.2,ease:"power1.inOut"}},'
               f'{T(ctx.at(p.get("route_at"), .8))});\n')
    if r:
        tr = ctx.at(p.get("radius_at"), ctx.at("50%"))
        if dim:
            js += (f'tl.fromTo(S+" .hole",{{attr:{{r:900}}}},{{attr:{{r:{num(r)}}},duration:1.2,ease:"power2.inOut"}},{T(tr)})'
                   f'.fromTo(S+" .ring",{{attr:{{r:900}},opacity:0}},{{attr:{{r:{num(r)}}},opacity:1,duration:1.2,'
                   f'ease:"power2.inOut"}},{T(tr)}).fromTo(S+" .dim",{{opacity:0}},{{opacity:.82,duration:.6}},{T(tr)});\n')
        else:
            js += (f'tl.fromTo(S+" .ring",{{attr:{{r:0}},opacity:0}},{{attr:{{r:{num(r)}}},opacity:1,duration:.9,'
                   f'stagger:.12,ease:"power2.out"}},{T(tr)});\n')
    css.append(f"{ctx.sel} .map{{position:absolute;left:{(1080 - size) / 2:.0f}px;top:{top:.0f}px;width:{size:.0f}px;"
               f"height:{size:.0f}px;overflow:visible}}\n"
               f"{ctx.sel} .map .grid line{{stroke:var(--ink);stroke-opacity:.22;stroke-width:3}}\n"
               f"{ctx.sel} .map .route{{fill:none;stroke:var(--alt);stroke-width:12;stroke-linecap:round;"
               f"stroke-dasharray:1;stroke-dashoffset:0}}\n"
               f"{ctx.sel} .map .dim{{fill:var(--deep)}}\n"
               f"{ctx.sel} .map .ring{{fill:{'none' if dim else 'var(--accent)'};fill-opacity:.1;stroke:var(--accent);"
               f"stroke-width:4;stroke-dasharray:{'none' if dim else '14 10'}}}\n"
               f"{ctx.sel} .map .box rect{{fill:var(--ink)}}\n"
               f"{ctx.sel} .map .box text{{font:700 22px var(--mono);fill:var(--bg)}}\n"
               f"{ctx.sel} .map .dot circle{{fill:var(--ink)}} {ctx.sel} .map .acc circle{{fill:var(--accent)}}\n"
               f"{ctx.sel} .map .pl{{font:600 28px var(--mono);fill:var(--ink)}}")
    return SceneOut(html, "\n".join(css), js)


@scene("custom", SceneDoc(
    "Свободная сцена: свой HTML/CSS/JS (строкой или файлами проекта). Для сложной графики, когда шаблонов мало.",
    {"html": "HTML внутрь сцены (или html_file — путь от папки проекта)",
     "css": "CSS (или css_file); токен {S} заменяется на селектор сцены, начинайте каждое правило с {S}",
     "js": "JS (или js_file): выполняется с S (селектор сцены), t (начало блока) и tl (общий таймлайн); "
           "только tl.fromTo/tl.set с явным начальным состоянием и позициями t + секунды"},
    {"html": '<div class="big hi"><span>Свой <em>код</em></span></div>',
     "css": "{S} .hi{top:600px}",
     "js": 'tl.fromTo(S+" .hi span",{yPercent:110},{yPercent:0,duration:.45,ease:"expo.out"},t+.3);'}))
def custom(ctx: SceneCtx, p: dict) -> SceneOut:
    def part(key: str) -> str:
        if p.get(f"{key}_file"):
            f = ctx.project_dir / str(p[f"{key}_file"])
            if not f.is_file():
                raise ValueError(f"{key}_file: нет файла {f}")
            return f.read_text(encoding="utf-8")
        return str(p.get(key) or "")
    html, css, js = part("html"), part("css"), part("js")
    if not html.strip():
        raise ValueError("нужен html или html_file")
    return SceneOut(html, css.replace("{S}", ctx.sel), js)
