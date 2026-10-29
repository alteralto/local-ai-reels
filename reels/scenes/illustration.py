"""Иллюстрация: плоская плакатная картинка из пропов (reels/scenes/props), собранная слоями и оживлённая GSAP."""
from __future__ import annotations

import re

from .base import SceneCtx, SceneDoc, SceneOut, num, scene
from .common import T
from .props import render_prop
from .props.city import color

ENTER = {"pop", "rise", "drop", "slide-left", "slide-right", "fade", "none"}
BACK_Z = {"moon": 0, "sun": 0, "stars": 0, "cloud": 1, "skyline": 1, "ground": 2}   # z по умолчанию для фона
MAX_LAYERS = 24


def _scope(css: str, sel: str) -> str:
    """CSS пропа → правила внутри сцены: каждый селектор начинается с селектора сцены."""
    if "@" in css:
        raise ValueError("CSS пропа: @-правила не поддерживаются")
    return re.sub(r"(^|})\s*([^{}]+?)\s*{",
                  lambda m: m.group(1) + ",".join(x if x.strip().startswith(sel) else f"{sel} {x.strip()}"
                                                  for x in m.group(2).split(",")) + "{", css)


def _enter_js(cls: str, kind: str, t0: float, h: float) -> str:
    s = f'S+" .{cls}"'
    if kind == "none":
        return ""
    if kind == "pop":
        return (f'tl.fromTo({s},{{scale:.2,opacity:0,transformOrigin:"50% 100%"}},'
                f'{{scale:1,opacity:1,duration:.55,ease:"back.out(2)"}},{T(t0)});\n')
    if kind == "rise":
        return (f'tl.fromTo({s},{{y:{num(min(max(h * .5, 120), 520))},opacity:0}},'
                f'{{y:0,opacity:1,duration:.8,ease:"power3.out"}},{T(t0)});\n')
    if kind == "drop":
        return (f'tl.fromTo({s},{{y:-{num(min(h + 200, 900))},opacity:1}},'
                f'{{y:0,duration:.75,ease:"bounce.out"}},{T(t0)});\n')
    if kind in ("slide-left", "slide-right"):
        dx = 1200 if kind == "slide-left" else -1200
        return f'tl.fromTo({s},{{x:{dx}}},{{x:0,duration:.8,ease:"power3.out"}},{T(t0)});\n'
    return f'tl.fromTo({s},{{opacity:0}},{{opacity:1,duration:.6}},{T(t0)});\n'


GRAIN_SVG = ('<svg class="grain" viewBox="0 0 1080 1920" preserveAspectRatio="none"><filter id="{u}-fg" x="0" y="0" '
             'width="100%" height="100%" color-interpolation-filters="sRGB"><feTurbulence type="fractalNoise" '
             'baseFrequency=".78" numOctaves="2" seed="21" result="n"/><feColorMatrix in="n" type="matrix" '
             'values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  16 0 0 0 -10.4" result="d"/><feColorMatrix in="n" type="matrix" '
             'values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 -16 0 0 5.2" result="l"/><feComponentTransfer in="l" '
             'result="l1"><feFuncA type="linear" slope=".45"/></feComponentTransfer><feMerge><feMergeNode in="d"/>'
             '<feMergeNode in="l1"/></feMerge></filter><rect width="1080" height="1920" filter="url(#{u}-fg)"/></svg>')


@scene("illustration", SceneDoc(
    "Плоская плакатная иллюстрация из пропов (луна, силуэт города, дома, фонарь, кабинка, человек…): слои с "
    "положением, появлением и действиями пропов по якорям диктора, лёгкое покачивание и медленный наезд камеры. "
    "Можно во весь кадр; главное держите в зоне y 160…1450.",
    {"layers": "список слоёв (до 24) {prop, x, y, w, z, flip, rotate, anchor, params, enter, at, actions, float, "
               "parallax}. prop — имя пропа (см. PROPS). x, y — точка привязки в px кадра 1080×1920 (по умолчанию "
               "низ-центр пропа: anchor bottom; ещё center, top). w — ширина в px (высота по пропорциям пропа). "
               "shadow: true или {length 0…1.5, skew градусы, opacity} — длинная тень (ночь, фонарь). "
               "z — порядок (фон moon/sun/stars 0, skyline/cloud 1, ground 2, прочее 10+номер). flip — зеркально. "
               "rotate — наклон в градусах (плакатный «развал» домов: −4…4). params — параметры пропа. "
               "enter — pop|rise|drop|slide-left|slide-right|fade|none (по умолчанию rise), at — якорь появления "
               "(по умолчанию лесенкой от начала блока). actions — [{do: действие пропа, at: якорь}] (rise, lights, "
               "glow, open…). float: true — лёгкое покачивание. parallax 0…1 — медленный дрейф влево",
     "bg": "фон сцены: переменная темы (bg, deep, ink, accent, alt) или цвет «#cc1418»; по умолчанию bg",
     "ground": "полоса земли {color, height (px, по умолчанию 380), edge: flat|rough, at}; необязательно",
     "grain": "зерно поверх всего кадра 0…1, по умолчанию 0.5 (0 — выключить)",
     "camera": "медленный наезд {zoom: [от, до], at, duration, origin: [x, y]}; по умолчанию без наезда"},
    {"bg": "#cc1418", "ground": {"color": "#1d1c1c", "height": 540, "at": 0},
     "camera": {"zoom": [1, 1.05], "at": 0},
     "layers": [
         {"prop": "moon", "x": 560, "y": 1130, "w": 720, "enter": "fade", "at": 0, "parallax": .3},
         {"prop": "skyline", "x": 540, "y": 1400, "w": 1080, "params": {"color": "#8e0d10"}, "enter": "none",
          "actions": [{"do": "rise", "at": 0.1}]},
         {"prop": "panel-block", "x": 190, "y": 1480, "w": 330, "rotate": -3, "at": "панельки"},
         {"prop": "building-classic", "x": 540, "y": 1455, "w": 360, "rotate": 1, "at": "классика"},
         {"prop": "building-brick", "x": 880, "y": 1490, "w": 320, "rotate": 4, "at": "кирпич",
          "actions": [{"do": "lights", "at": "вечер"}]}]}))
def illustration(ctx: SceneCtx, p: dict) -> SceneOut:
    layers = p.get("layers") or []
    if not isinstance(layers, list) or not layers:
        raise ValueError("нужен layers: список слоёв {prop, x, y, w, …}")
    if len(layers) > MAX_LAYERS:
        raise ValueError(f"слоёв больше {MAX_LAYERS}")
    S = ctx.sel
    html, css, js = [], [], []
    bg = color(p.get("bg"), "var(--bg)")
    css.append(f"{S}{{background:{bg};background-image:none}}\n"
               f"{S} .cam{{position:absolute;inset:0}}\n"
               f"{S} .lay{{position:absolute}}\n{S} .lay .mv,{S} .lay .fl{{position:relative;width:100%;height:100%}}\n"
               f"{S} .lay svg{{position:absolute;inset:0;display:block;width:100%;height:100%;overflow:visible}}\n"
               f"{S} .grain{{position:absolute;inset:0;width:1080px;height:1920px;pointer-events:none;z-index:90}}")
    items = []
    g = p.get("ground")
    if g:
        g = g if isinstance(g, dict) else {}
        gh = float(g.get("height", 380))
        items.append({"prop": "ground", "x": 540, "y": 1920, "w": 1080, "z": g.get("z", 2), "enter": "none",
                      "params": {"color": g.get("color"), "height": gh * 1.0, "edge": g.get("edge", "flat")},
                      "actions": [{"do": "rise", "at": g["at"]}] if g.get("at") is not None else []})
    items += layers
    end = ctx.length + 1.0
    for i, ly in enumerate(items):
        if not isinstance(ly, dict) or not ly.get("prop"):
            raise ValueError(f"слой {i + 1}: нужен prop")
        name = str(ly["prop"])
        uid = f"{ctx.uid}-l{i}"
        out = render_prop(name, ly.get("params") or {}, uid)
        w = float(ly.get("w", out.w))
        h = w * out.h / out.w
        x, y = float(ly.get("x", 540)), float(ly.get("y", 1450))
        anchor = str(ly.get("anchor", "bottom"))
        top = y - h if anchor == "bottom" else y - h / 2 if anchor == "center" else y
        z = int(ly.get("z", BACK_Z.get(name, 10 + i)))
        tf = []
        if ly.get("flip"):
            tf.append("scaleX(-1)")
        if ly.get("rotate"):
            tf.append(f"rotate({num(float(ly['rotate']))}deg)")
        cls = f"l{i}"
        shadow = ""
        sh = ly.get("shadow")
        if sh:   # длинная тень: копия пропа, залитая чёрным, опрокинутая и скошенная от низа
            sh = sh if isinstance(sh, dict) else {}
            ln, sk = float(sh.get("length", .45)), float(sh.get("skew", -55))
            shadow = f'<div class="sh">{out.svg}</div>'
            css.append(f"{S} .{cls} .sh{{position:absolute;inset:0;transform-origin:50% 100%;"
                       f"transform:scaleY({num(-ln)}) skewX({num(-sk)}deg);filter:brightness(0);"
                       f"opacity:{num(float(sh.get('opacity', .35)))}}}")
        html.append(f'<div class="lay {cls}"><div class="mv"><div class="fl">{shadow}{out.svg}</div></div></div>')
        css.append(f"{S} .{cls}{{left:{x - w / 2:.1f}px;top:{top:.1f}px;width:{w:.1f}px;height:{h:.1f}px;z-index:{z}}}"
                   + (f"\n{S} .{cls} .fl{{transform:{' '.join(tf)};transform-origin:50% 100%}}" if tf else ""))
        if out.css:
            css.append(_scope(out.css, S))
        kind = str(ly.get("enter", "rise" if name != "ground" else "none"))
        if kind not in ENTER:
            raise ValueError(f"слой {i + 1}: enter «{kind}» — одно из {', '.join(sorted(ENTER))}")
        t0 = ctx.at(ly.get("at"), .15 + .18 * i)
        js.append(_enter_js(cls, kind, t0, h))
        sel = f"{S} .{cls}"
        for a in ly.get("actions") or []:
            if not isinstance(a, dict) or not a.get("do"):
                raise ValueError(f"слой {i + 1}: действие — {{do: имя, at: якорь}}")
            act = str(a["do"])
            if act not in out.actions:
                have = ", ".join(sorted(out.actions)) or "нет"
                raise ValueError(f"слой {i + 1} ({name}): нет действия «{act}». Есть: {have}")
            js.append(out.actions[act](sel, ctx.start + max(0.0, ctx.at(a.get("at"), t0 + .6))))
        if ly.get("float"):
            reps = max(1, int(end / 1.6))
            js.append(f'tl.fromTo(S+" .{cls} .mv",{{y:0}},{{y:-14,duration:1.6,ease:"sine.inOut",yoyo:true,'
                      f'repeat:{reps}}},{T(t0 + .4)});\n')
        par = max(0.0, min(1.0, float(ly.get("parallax") or 0)))
        if par:
            js.append(f'tl.fromTo(S+" .{cls} .mv",{{x:0}},{{x:{num(-90 * par)},duration:{num(end)},ease:"none"}},t);\n')
    html = [f'<div class="cam">{"".join(html)}</div>']
    gr = max(0.0, min(1.0, float(p.get("grain", .5))))
    if gr:
        html.append(GRAIN_SVG.replace("{u}", ctx.uid))
        css.append(f"{S} .grain{{opacity:{num(gr)}}}")
    cam = p.get("camera")
    if cam:
        z0, z1 = (list(cam.get("zoom") or [1, 1.08]) + [1.08])[:2]
        ox, oy = (list(cam.get("origin") or [540, 960]) + [960])[:2]
        tc = ctx.at(cam.get("at"), 0)
        dur = float(cam.get("duration", max(1.0, ctx.length - tc + .5)))
        js.append(f'tl.fromTo(S+" .cam",{{scale:{num(float(z0))},transformOrigin:"{num(float(ox))}px '
                  f'{num(float(oy))}px"}},{{scale:{num(float(z1))},duration:{num(dur)},ease:"sine.inOut"}},{T(tc)});\n')
    return SceneOut("".join(html), "\n".join(css), "".join(js))
