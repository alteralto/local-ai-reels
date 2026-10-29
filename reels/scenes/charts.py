"""Инфографика: fraction (доля иконками), bars (столбики), compare (два значения), timeline (вехи)."""
from __future__ import annotations

import math

from .base import SceneCtx, SceneDoc, SceneOut, esc, num, rich, scene
from .common import T, fit, fmt_num, jstr, kick_js, kicker, reveal, times

ICONS = {
    # (svg-фигура в клетке, ширина, высота, шаг по x, шаг по y)
    "person": ('<circle cx="40" cy="40" r="28"/><path d="M0 160 Q0 82 40 82 Q80 82 80 160 Z"/>', 80, 160, 115, 200),
    "dot": ('<circle cx="40" cy="40" r="34"/>', 80, 80, 100, 100),
    "square": ('<rect x="4" y="4" width="72" height="72" rx="10"/>', 80, 80, 100, 100),
}


@scene("fraction", SceneDoc(
    "Доля иконками: total фигурок (человечки, точки, квадраты), highlight из них подсвечиваются акцентом; "
    "сверху крупная доля, снизу подпись.",
    {"total": "сколько фигурок, 2…100. Обязательно",
     "highlight": "сколько подсветить. Обязательно",
     "icon": "person | dot | square; по умолчанию person (для total > 20 — dot)",
     "side": "какие подсветить: end (последние) | start (первые); по умолчанию end",
     "value": "крупный текст над иконками («*1* из 5»); по умолчанию «highlight из total»",
     "label": "подпись под иконками; необязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь подсветки и появления value; по умолчанию 40%",
     "intro": "якорь появления фигурок; по умолчанию 0.3 с"},
    {"kicker": "RSPH · Taking the P*** · май 2019", "total": 5, "highlight": 1, "value": "*1* из 5",
     "label": "выходит из дома реже, чем хочет —\nбоится не найти туалет", "at": "каждый пятый"}))
def fraction(ctx: SceneCtx, p: dict) -> SceneOut:
    n, k = int(p.get("total", 0)), int(p.get("highlight", -1))
    if not 2 <= n <= 100:
        raise ValueError("total: от 2 до 100")
    if not 0 <= k <= n:
        raise ValueError("highlight: от 0 до total")
    icon = p.get("icon") or ("person" if n <= 20 else "dot")
    if icon not in ICONS:
        raise ValueError(f"icon: одно из {', '.join(ICONS)}")
    shape, iw, ih, sx, sy = ICONS[icon]
    cols = n if n <= 6 else (math.ceil(n / 2) if n <= 12 else min(10, math.ceil(math.sqrt(n * 1.6))))
    rows = math.ceil(n / cols)
    vw, vh = (cols - 1) * sx + iw, (rows - 1) * sy + ih
    top, bottom = 620, 1200 if p.get("label") else 1380
    s = min(860 / vw, (bottom - top) / vh, 1.6)
    w, h = vw * s, vh * s
    on = set(range(n - k, n)) if p.get("side", "end") == "end" else set(range(k))
    figs = "".join(f'<g class="ic{" on" if i in on else ""}" transform="translate({(i % cols) * sx},{(i // cols) * sy})">'
                   f'{shape}</g>' for i in range(n))
    value = p.get("value") or f"*{k}* из {n}"
    vs = fit(value, 200, k=.62, lo=72)
    html = (f'<div class="big val"><span>{rich(value)}</span></div>'
            f'<svg class="icons" viewBox="0 0 {vw} {vh}">{figs}</svg>')
    css = (f"{ctx.sel} .val{{top:{max(270, 600 - vs * 1.12):.0f}px;font-size:{vs}px;height:{vs * 1.12:.0f}px}}\n"
           f"{ctx.sel} .icons{{position:absolute;left:{(1080 - w) / 2:.0f}px;top:{top}px;width:{w:.0f}px;height:{h:.0f}px;"
           f"overflow:visible}}\n{ctx.sel} .icons .ic{{fill:var(--ink)}}")
    t_in, t0 = ctx.at(p.get("intro"), .3), ctx.at(p.get("at"), ctx.at("40%"))
    th = ctx.theme
    js = kick_js(p)
    js += (f'tl.fromTo(S+" .ic",{{opacity:0,y:30}},{{opacity:1,y:0,duration:.35,stagger:{num(min(.08, 1.2 / n))},'
           f'ease:"power3.out"}},{T(t_in)});\n')
    js += reveal(".val", t0, .5)
    if k:
        js += f'tl.fromTo(S+" .ic.on",{{fill:{jstr(th.ink)}}},{{fill:{jstr(th.accent)},duration:.25}},{T(t0 + .1)});\n'
        if k < n:
            js += f'tl.fromTo(S+" .ic:not(.on)",{{opacity:1}},{{opacity:.35,duration:.4}},{T(t0 + .4)});\n'
    if p.get("label"):
        ls = fit(p["label"], 40, k=.62, lo=26)
        html += f'<div class="note mono">{rich(p["label"])}</div>'
        css += (f"\n{ctx.sel} .note{{position:absolute;left:80px;right:80px;top:{top + h + 60:.0f}px;"
                f"font-size:{ls}px;line-height:1.4}}")
        js += f'tl.fromTo(S+" .note",{{opacity:0}},{{opacity:1,duration:.35}},{T(t0 + .5)});\n'
    return SceneOut(kicker(p) + html, css, js)


@scene("bars", SceneDoc(
    "Горизонтальные столбики: растут по очереди, у конца — значение. Акцентный столбик выделен цветом.",
    {"items": "список {label, value, accent: bool, text: подпись значения вместо числа}; 2…6 штук. Обязательно",
     "unit": "единица после числа («%», « км»); необязательно",
     "max": "значение во всю ширину; по умолчанию наибольшее из items",
     "title": "строка заголовка над столбиками; необязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь начала роста (дальше шаг 0.2 с) или список якорей по столбикам; по умолчанию 0.4 с"},
    {"kicker": "реже выходят из дома", "title": "Кого держит *поводок*",
     "items": [{"label": "все взрослые", "value": 20}, {"label": "с болезнями, требующими частого туалета",
                                                        "value": 43, "accent": True}],
     "unit": " %", "at": ["каждый пятый", "двоих из пяти"]}))
def bars(ctx: SceneCtx, p: dict) -> SceneOut:
    items = p.get("items") or []
    if not 1 <= len(items) <= 8:
        raise ValueError("items: от 1 до 8 столбиков")
    vals = [float(it["value"]) for it in items]
    mx = float(p.get("max") or max(vals) or 1)
    unit = str(p.get("unit", ""))
    html, css, js = kicker(p), [], kick_js(p)
    top = 300
    ts = times(ctx, p.get("at"), len(items), .4, .2)
    if p.get("title"):
        tsz = fit(p["title"], 88)
        html += f'<div class="big ttl"><span>{rich(p["title"])}</span></div>'
        css.append(f"{ctx.sel} .ttl{{top:260px;font-size:{tsz}px;height:{tsz * 1.12:.0f}px}}")
        js += reveal(".ttl", max(0, min(.3, ts[0] - .3)))
        top = 260 + tsz * 1.1 + 80
    row = min(220, (1400 - top) / len(items))
    top += max(0, (1400 - top - row * len(items)) * .4)
    bh = int(min(72, row * .38))
    ls = int(min(34, row * .2))
    for i, (it, v) in enumerate(zip(items, vals)):
        txt = it.get("text") or (fmt_num(v) + unit)
        width = max(6, 680 * min(v, mx) / mx)
        acc = " acc" if it.get("accent") else ""
        y = top + i * row
        html += (f'<div class="lab lab{i} mono">{esc(it.get("label", ""))}</div>'
                 f'<div class="bar bar{i}{acc}"></div><div class="bv bv{i}{acc}">{esc(txt)}</div>')
        css.append(f"{ctx.sel} .lab{i}{{top:{y:.0f}px}} {ctx.sel} .bar{i}{{top:{y + ls * 1.5 + 8:.0f}px;width:{width:.0f}px}}"
                   f" {ctx.sel} .bv{i}{{top:{y + ls * 1.5 + 8 + bh / 2:.0f}px;left:{80 + width + 24:.0f}px}}")
        js += (f'tl.fromTo(S+" .lab{i}",{{opacity:0}},{{opacity:1,duration:.3}},{T(ts[i] - .1)})'
               f'.fromTo(S+" .bar{i}",{{scaleX:0}},{{scaleX:1,duration:.7,ease:"power3.out"}},{T(ts[i])})'
               f'.fromTo(S+" .bv{i}",{{opacity:0,x:-20}},{{opacity:1,x:0,duration:.35}},{T(ts[i] + .45)});\n')
    vs = int(min(56, bh * .9))
    css.append(f"{ctx.sel} .lab{{position:absolute;left:80px;right:80px;font-size:{ls}px;opacity:.85;white-space:nowrap;"
               f"overflow:hidden;text-overflow:ellipsis}}\n"
               f"{ctx.sel} .bar{{position:absolute;left:80px;height:{bh}px;background:var(--ink);transform-origin:0 50%}}\n"
               f"{ctx.sel} .bar.acc{{background:var(--accent)}}\n"
               f"{ctx.sel} .bv{{position:absolute;font-size:{vs}px;font-weight:800;transform:translateY(-50%);"
               f"white-space:nowrap;line-height:1}}\n{ctx.sel} .bv.acc{{color:var(--accent)}}")
    return SceneOut(html, "\n".join(css), js)


@scene("compare", SceneDoc(
    "Сравнение двух значений колонками с разделителем «vs»; акцентная сторона выделена.",
    {"left": "{label, value, note} — левая колонка. Обязательно",
     "right": "{label, value, note} — правая колонка. Обязательно",
     "accent": "left | right | none — какую сторону выделить; по умолчанию right",
     "title": "строка заголовка; необязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь левой колонки; по умолчанию 0.3 с",
     "right_at": "якорь правой колонки; по умолчанию at + 0.8 с"},
    {"kicker": "у кого город короче", "left": {"label": "все", "value": "1 из 5", "note": "реже выходят из дома"},
     "right": {"label": "больные", "value": "2 из 5", "note": "город = круги вокруг уборных"},
     "at": "каждый пятый", "right_at": "двоих из пяти"}))
def compare(ctx: SceneCtx, p: dict) -> SceneOut:
    L, R = p.get("left"), p.get("right")
    if not isinstance(L, dict) or not isinstance(R, dict):
        raise ValueError("нужны left и right: {label, value, note}")
    acc = p.get("accent", "right")
    html, css, js = kicker(p), [], kick_js(p)
    top = 560
    t0 = ctx.at(p.get("at"), .3)
    t1 = ctx.at(p.get("right_at"), t0 + .8)
    if p.get("title"):
        tsz = fit(p["title"], 88)
        html += f'<div class="big ttl"><span>{rich(p["title"])}</span></div>'
        css.append(f"{ctx.sel} .ttl{{top:260px;font-size:{tsz}px;height:{tsz * 1.12:.0f}px}}")
        js += reveal(".ttl", max(0, min(.3, t0 - .3)))
        top = max(top, 260 + tsz * 1.1 + 90)
    size = min(fit(str(c.get("value", "")), 150, width=400, k=.62, lo=48) for c in (L, R))
    for side, c, x, tt in (("left", L, 80, t0), ("right", R, 580, t1)):
        a = " acc" if acc == side else ""
        html += (f'<div class="col {side}{a}"><div class="cl mono">{esc(c.get("label", ""))}</div>'
                 f'<div class="cv">{rich(c.get("value", ""))}</div>'
                 + (f'<div class="cn">{rich(c["note"])}</div>' if c.get("note") else "") + "</div>")
        css.append(f"{ctx.sel} .col.{side}{{left:{x}px}}")
        js += f'tl.fromTo(S+" .col.{side}",{{opacity:0,y:50}},{{opacity:1,y:0,duration:.45,ease:"power3.out"}},{T(tt)});\n'
    css.append(f"{ctx.sel} .col{{position:absolute;top:{top:.0f}px;width:420px;padding-top:28px;border-top:6px solid var(--muted)}}\n"
               f"{ctx.sel} .col.acc{{border-top-color:var(--accent)}}\n"
               f"{ctx.sel} .cl{{font-size:30px;text-transform:uppercase;opacity:.85}}\n"
               f"{ctx.sel} .cv{{font-size:{size}px;font-weight:800;line-height:1.05;letter-spacing:-.03em;margin:22px 0 26px}}\n"
               f"{ctx.sel} .col.acc .cv{{color:var(--accent)}}\n"
               f"{ctx.sel} .cn{{font-size:36px;font-weight:700;line-height:1.25}}\n"
               f"{ctx.sel} .vs{{position:absolute;left:500px;background:var(--bg);top:{top + 70:.0f}px;width:80px;height:80px;border-radius:50%;"
               f"border:3px solid var(--ink);font-size:30px;display:flex;align-items:center;justify-content:center}}")
    html += '<div class="vs mono">vs</div>'
    js += f'tl.fromTo(S+" .vs",{{opacity:0,scale:0}},{{opacity:1,scale:1,duration:.35,ease:"back.out(3)"}},{T(t0 + .3)});\n'
    return SceneOut(html, "\n".join(css), js)


@scene("timeline", SceneDoc(
    "Вертикальная шкала времени: линия прорисовывается, вехи появляются по очереди.",
    {"points": "список {label (год/дата), text, at}; 2…5 штук; *слово* — акцентом. Обязательно",
     "kicker": "надзаголовок; необязательно",
     "at": "якорь первой вехи (дальше шаг 0.7 с) или список якорей; у вехи свой at важнее"},
    {"kicker": "как город сжимается", "points": [
        {"label": "1 из 5", "text": "все взрослые", "at": "каждый пятый"},
        {"label": "2 из 5", "text": "люди с *частыми* походами в туалет", "at": "двоих из пяти"},
        {"label": "0", "text": "туалетов в генплане", "at": "генплан"}]}))
def timeline(ctx: SceneCtx, p: dict) -> SceneOut:
    pts = p.get("points") or []
    if not 1 <= len(pts) <= 6:
        raise ValueError("points: от 1 до 6 вех")
    pts = [x if isinstance(x, dict) else {"text": x} for x in pts]
    ts = times(ctx, p.get("at"), len(pts), .4, .7)
    ts = [ctx.at(x.get("at"), d) for x, d in zip(pts, ts)]
    top = 320
    row = min(270, 1080 / len(pts))
    longest = max(len(str(x.get("text", ""))) for x in pts)
    size = int(max(34, min(58, 780 * 2 / (longest * .6), row / 3.2)))
    html, css, js = kicker(p), [], kick_js(p)
    line_h = row * (len(pts) - 1) + 20
    html += '<div class="ln"></div>'
    js += (f'tl.fromTo(S+" .ln",{{scaleY:0}},{{scaleY:1,duration:{num(max(.4, ts[-1] - ts[0] + .3))},ease:"none"}},'
           f'{T(ts[0])});\n')
    for i, (x, tt) in enumerate(zip(pts, ts)):
        y = top + i * row
        html += (f'<div class="pt pt{i}"></div><div class="ev ev{i}"><div class="el mono">{esc(x.get("label", ""))}</div>'
                 f'<div class="et">{rich(x.get("text", ""))}</div></div>')
        css.append(f"{ctx.sel} .pt{i}{{top:{y - 4:.0f}px}} {ctx.sel} .ev{i}{{top:{y - 14:.0f}px}}")
        js += (f'tl.fromTo(S+" .pt{i}",{{scale:0}},{{scale:1,duration:.35,ease:"back.out(3)"}},{T(tt)})'
               f'.fromTo(S+" .ev{i}",{{opacity:0,x:-30}},{{opacity:1,x:0,duration:.4,ease:"power3.out"}},{T(tt + .08)});\n')
    css.append(f"{ctx.sel} .ln{{position:absolute;left:108px;top:{top + 10}px;width:6px;height:{line_h:.0f}px;"
               f"background:var(--ink);transform-origin:50% 0;opacity:.6}}\n"
               f"{ctx.sel} .pt{{position:absolute;left:88px;width:46px;height:46px;border-radius:50%;"
               f"background:var(--bg);border:8px solid var(--accent)}}\n"
               f"{ctx.sel} .ev{{position:absolute;left:180px;right:80px}}\n"
               f"{ctx.sel} .el{{font-size:36px;color:var(--accent);font-weight:600}}\n"
               f"{ctx.sel} .et{{font-size:{size}px;font-weight:800;line-height:1.1;letter-spacing:-.02em;margin-top:8px}}")
    return SceneOut(html, "\n".join(css), js)
