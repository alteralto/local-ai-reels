"""Город и архитектура для шаблона illustration: луна, солнце, силуэт города, дома, фонарь, земля, облако, дерево.

Стиль — плоский плакат: заливки без обводок, ограниченная палитра (кремовый, красный, почти чёрный, тёмно-красный
силуэт), боковые грани темнее фасада (2.5D), крупное «зерно» внутри фигур (SVG-фильтр feTurbulence → крапинки).
viewBox каждого пропа примерно равен его типичному размеру в кадре, поэтому зерно везде одной крупности.
"""
from __future__ import annotations

import json
import math
import random
import re

from ..base import num
from . import PropDoc, PropOut, prop

CREAM = "#ece0ca"
INK = "#1d1c1c"
RED = "#cc1418"
BRICK = "#c8171c"
LIT = "#f6d46b"
THEME_VARS = {"bg", "deep", "ink", "accent", "alt", "muted"}
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\([\d\s.,%]+\)|[a-zA-Z]{3,20}|var\(--[a-z][a-z0-9-]*\)")


def color(v, default: str) -> str:
    """Цвет из параметров пропа: имя переменной темы (accent, ink, bg, deep, alt), #hex, rgb(), имя цвета."""
    if v is None or v == "":
        return default
    s = str(v).strip()
    if s in THEME_VARS:
        return f"var(--{s})"
    if not _COLOR.fullmatch(s):
        raise ValueError(f"цвет «{s}»: #hex, rgb(…), имя цвета или переменная темы ({', '.join(sorted(THEME_VARS))})")
    return s


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False).replace("<", "\\u003c")


def _f(x: float) -> str:
    return num(round(x, 1))


def grain(fid: str, dark: float = .5, light: float = .25, freq: float = .75, seed: int = 3) -> str:
    """Фильтр «зерно»: тёмные и светлые крапинки только внутри фигуры (композиция in SourceGraphic)."""
    return (f'<filter id="{fid}" x="-2%" y="-2%" width="104%" height="104%" color-interpolation-filters="sRGB">'
            f'<feTurbulence type="fractalNoise" baseFrequency="{freq}" numOctaves="2" seed="{seed}" result="n"/>'
            f'<feColorMatrix in="n" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  16 0 0 0 -10.2" result="d"/>'
            f'<feComponentTransfer in="d" result="d1"><feFuncA type="linear" slope="{num(dark)}"/></feComponentTransfer>'
            f'<feComposite in="d1" in2="SourceGraphic" operator="in" result="d2"/>'
            f'<feColorMatrix in="n" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 -16 0 0 5.6" result="l"/>'
            f'<feComponentTransfer in="l" result="l1"><feFuncA type="linear" slope="{num(light)}"/></feComponentTransfer>'
            f'<feComposite in="l1" in2="SourceGraphic" operator="in" result="l2"/>'
            f'<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="d2"/><feMergeNode in="l2"/></feMerge></filter>')


def _svg(uid: str, w: float, h: float, body: str, defs: str = "") -> str:
    return (f'<svg class="{uid}" viewBox="0 0 {_f(w)} {_f(h)}" xmlns="http://www.w3.org/2000/svg" '
            f'preserveAspectRatio="xMidYMax meet" overflow="visible"><defs>{defs}</defs>{body}</svg>')


def _shade(d: str, fill: str, k: float = .3) -> str:
    """Грань темнее основного цвета (работает и для var(--…)): заливка + полупрозрачный чёрный сверху."""
    return f'<path d="{d}" fill="{fill}"/><path d="{d}" fill="#000" opacity="{num(k)}"/>'


def _rect(x, y, w, h) -> str:
    return f"M{_f(x)} {_f(y)}h{_f(w)}v{_f(h)}h{_f(-w)}z"


# --- действия: fn(sel, t) → JS; t — абсолютное время на таймлайне -------------------------------------------
def _act_rise(cls: str, stagger: float = .05, dur: float = .7):
    def fn(sel: str, t: float) -> str:
        return (f'tl.fromTo({_q(sel + " ." + cls)},{{scaleY:0,transformOrigin:"50% 100%"}},'
                f'{{scaleY:1,duration:{num(dur)},ease:"back.out(1.3)",stagger:{num(stagger)}}},{num(t)});\n')
    return fn


def _act_lights(cls: str, each: float = .07):
    def fn(sel: str, t: float) -> str:
        return (f'tl.fromTo({_q(sel + " ." + cls)},{{opacity:0}},{{opacity:1,duration:.12,ease:"steps(2)",'
                f'stagger:{num(each)}}},{num(t)});\n')
    return fn


def _act_glow(cls: str):
    def fn(sel: str, t: float) -> str:
        s = _q(sel + " ." + cls)
        return (f'tl.fromTo({s},{{opacity:0}},{{opacity:1,duration:.06}},{num(t)})'
                f'.to({s},{{opacity:.25,duration:.06}},{num(t + .12)})'
                f'.to({s},{{opacity:1,duration:.05}},{num(t + .22)})'
                f'.to({s},{{opacity:.5,duration:.05}},{num(t + .34)})'
                f'.to({s},{{opacity:1,duration:.2}},{num(t + .42)});\n')
    return fn


def _act_pop(cls: str):
    def fn(sel: str, t: float) -> str:
        return (f'tl.fromTo({_q(sel + " ." + cls)},{{scale:0,opacity:0,transformOrigin:"50% 50%"}},'
                f'{{scale:1,opacity:1,duration:.6,ease:"back.out(1.6)"}},{num(t)});\n')
    return fn


def _windows_lit(rnd: random.Random, rects: list[tuple], share: float, cls: str, fill: str = LIT) -> str:
    """Светящиеся окна поверх тёмных: доля share, в случайном (но воспроизводимом) порядке — так они и зажигаются."""
    pick = [r for r in rects if rnd.random() < share]
    rnd.shuffle(pick)
    return "".join(f'<path class="{cls}" d="{d}" fill="{fill}"/>' for d in (r[0] for r in pick))


# --- небо ----------------------------------------------------------------------------------------------------
@prop("moon", PropDoc("Большая кремовая луна с зерном; может быть полумесяцем",
                      {"color": "цвет диска; по умолчанию кремовый",
                       "crescent": "true — полумесяц (вырез цветом фона, cut — цвет выреза, по умолчанию bg)",
                       "halo": "true — мягкий ореол (для ночи)"},
                      actions=["rise", "glow"]))
def moon(p: dict, uid: str) -> PropOut:
    S = 600
    c = color(p.get("color"), CREAM)
    defs = grain(f"{uid}-g", dark=.7, light=.1, freq=.66, seed=5)
    body = ""
    if p.get("halo"):
        defs += (f'<radialGradient id="{uid}-h"><stop offset=".55" stop-color="{c}" stop-opacity=".35"/>'
                 f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>')
        body += f'<circle class="{uid}-halo" cx="300" cy="300" r="300" fill="url(#{uid}-h)"/>'
    disc = f'<circle cx="300" cy="300" r="250" fill="{c}"/>'
    if p.get("crescent"):
        defs += (f'<mask id="{uid}-m"><rect x="0" y="0" width="600" height="600" fill="#fff"/>'
                 f'<circle cx="400" cy="230" r="215" fill="#000"/></mask>')
        disc = f'<g mask="url(#{uid}-m)">{disc}</g>'
    # к краю диска крапинок больше (как у плакатной луны): градиент прозрачности → только крапинки
    defs += (f'<radialGradient id="{uid}-r"><stop offset=".55" stop-color="#000" stop-opacity="0"/>'
             f'<stop offset="1" stop-color="#000" stop-opacity=".7"/></radialGradient>'
             f'<filter id="{uid}-sp" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">'
             f'<feTurbulence type="fractalNoise" baseFrequency=".62" numOctaves="2" seed="8" result="n"/>'
             f'<feColorMatrix in="n" type="matrix" values="0 0 0 0 .22  0 0 0 0 .16  0 0 0 0 .12  14 0 0 0 -7.6"/>'
             f'<feComposite in2="SourceGraphic" operator="in"/></filter>')
    rim = f'<circle cx="300" cy="300" r="250" fill="url(#{uid}-r)" filter="url(#{uid}-sp)"/>'
    if p.get("crescent"):
        rim = f'<g mask="url(#{uid}-m)">{rim}</g>'
    body += f'<g class="{uid}-disc"><g filter="url(#{uid}-g)">{disc}</g>{rim}</g>'
    return PropOut(_svg(uid, S, S, body, defs), actions={
        "rise": lambda sel, t: (f'tl.fromTo({_q(sel + " ." + uid + "-disc")},{{y:260,opacity:0}},'
                                f'{{y:0,opacity:1,duration:1.4,ease:"power2.out"}},{num(t)});\n'),
        "glow": lambda sel, t: (f'tl.fromTo({_q(sel + " svg")},{{filter:"brightness(1)"}},'
                                f'{{filter:"brightness(1.18)",duration:.5,yoyo:true,repeat:1}},{num(t)});\n')},
        w=S, h=S)


@prop("sun", PropDoc("Солнце: диск с лучами-клиньями",
                     {"color": "цвет; по умолчанию accent", "rays": "число лучей, по умолчанию 12"},
                     actions=["rise", "spin"]))
def sun(p: dict, uid: str) -> PropOut:
    S = 520
    c = color(p.get("color"), "var(--accent)")
    n = max(0, min(36, int(p.get("rays", 12))))
    rays = ""
    for i in range(n):
        a = 2 * math.pi * i / n
        da = math.pi / n * .45
        pts = [(260 + 175 * math.cos(a - da), 260 + 175 * math.sin(a - da)),
               (260 + 252 * math.cos(a), 260 + 252 * math.sin(a)),
               (260 + 175 * math.cos(a + da), 260 + 175 * math.sin(a + da))]
        rays += '<path d="M' + "L".join(f"{_f(x)} {_f(y)}" for x, y in pts) + 'z"/>'
    body = (f'<g filter="url(#{uid}-g)" fill="{c}"><g class="{uid}-rays">{rays}</g>'
            f'<circle class="{uid}-disc" cx="260" cy="260" r="160"/></g>')
    return PropOut(_svg(uid, S, S, body, grain(f"{uid}-g", .55, .15)), actions={
        "rise": lambda sel, t: (f'tl.fromTo({_q(sel + " svg")},{{y:220,opacity:0}},'
                                f'{{y:0,opacity:1,duration:1.4,ease:"power2.out"}},{num(t)});\n'),
        "spin": lambda sel, t: (f'tl.fromTo({_q(sel + " ." + uid + "-rays")},{{rotation:0,svgOrigin:"260 260"}},'
                                f'{{rotation:90,duration:6,ease:"none"}},{num(t)});\n')},
        w=S, h=S)


@prop("stars", PropDoc("Звёзды: россыпь мелких точек на ночном небе (кадр 1080 шириной)",
                       {"count": "сколько звёзд, по умолчанию 40", "color": "цвет, по умолчанию кремовый",
                        "seed": "вариант россыпи"},
                       actions=["twinkle"]))
def stars(p: dict, uid: str) -> PropOut:
    W, H = 1080, 900
    rnd = random.Random(int(p.get("seed", 11)))
    c = color(p.get("color"), CREAM)
    n = max(1, min(200, int(p.get("count", 40))))
    dots = "".join(f'<circle class="{uid}-s" cx="{_f(rnd.uniform(10, W - 10))}" cy="{_f(rnd.uniform(10, H - 10))}" '
                   f'r="{_f(rnd.choice([2, 2.5, 3, 3.5, 5]))}"/>' for _ in range(n))
    return PropOut(_svg(uid, W, H, f'<g fill="{c}">{dots}</g>'), actions={
        "twinkle": lambda sel, t: (f'tl.fromTo({_q(sel + " ." + uid + "-s")},{{opacity:1}},{{opacity:.25,duration:.4,'
                                   f'yoyo:true,repeat:3,stagger:{{each:.05,from:"edges"}}}},{num(t)});\n')},
        w=W, h=H)


@prop("cloud", PropDoc("Плоское облако из кругов", {"color": "цвет; по умолчанию кремовый"}, actions=["drift"]))
def cloud(p: dict, uid: str) -> PropOut:
    W, H = 340, 150
    c = color(p.get("color"), CREAM)
    body = (f'<g filter="url(#{uid}-g)" fill="{c}"><circle cx="110" cy="92" r="52"/><circle cx="185" cy="70" r="66"/>'
            f'<circle cx="255" cy="98" r="44"/><rect x="58" y="96" width="240" height="54" rx="27"/></g>'
            f'<rect x="58" y="128" width="240" height="22" rx="11" fill="#000" opacity=".12"/>')
    return PropOut(_svg(uid, W, H, body, grain(f"{uid}-g", .55, .1)), actions={
        "drift": lambda sel, t: (f'tl.fromTo({_q(sel + " svg")},{{x:0}},{{x:-90,duration:6,ease:"none"}},{num(t)});\n')},
        w=W, h=H)


# --- силуэт города -------------------------------------------------------------------------------------------
def _stalin(cx: float, base: float, s: float) -> list[str]:
    """Сталинская высотка: ступенчатые ярусы, крылья, шпиль со звездой. Возвращает пути (низ — base)."""
    parts = [
        _rect(cx - 150 * s, base - 230 * s, 300 * s, 230 * s),        # стилобат с крыльями
        _rect(cx - 95 * s, base - 390 * s, 190 * s, 170 * s),
        _rect(cx - 62 * s, base - 500 * s, 124 * s, 120 * s),
        _rect(cx - 40 * s, base - 575 * s, 80 * s, 80 * s),
        _rect(cx - 24 * s, base - 625 * s, 48 * s, 55 * s),
        f"M{_f(cx - 16 * s)} {_f(base - 622 * s)}L{_f(cx)} {_f(base - 770 * s)}L{_f(cx + 16 * s)} {_f(base - 622 * s)}z",
        _rect(cx - 4 * s, base - 812 * s, 8 * s, 50 * s),
        _rect(cx - 170 * s, base - 150 * s, 30 * s, 150 * s),
        _rect(cx + 140 * s, base - 150 * s, 30 * s, 150 * s),
        _rect(cx - 112 * s, base - 260 * s, 18 * s, 40 * s), _rect(cx + 94 * s, base - 260 * s, 18 * s, 40 * s),
        _rect(cx - 30 * s, base - 690 * s, 60 * s, 7 * s),            # перекладина шпиля
    ]
    return parts


@prop("skyline", PropDoc(
    "Силуэт города во всю ширину кадра: башни в два ряда, сталинская высотка со шпилем, купол; ночью — окна",
    {"color": "цвет силуэта; по умолчанию deep (тёмный оттенок фона)",
     "lit": "true — окна горят жёлтым (ночь); false — окна спрятаны, их зажигает действие lights",
     "density": "плотность застройки 0.5…2, по умолчанию 1", "spire": "высотка со шпилем, по умолчанию true",
     "seed": "вариант застройки (число)"},
    actions=["rise", "lights"]))
def skyline(p: dict, uid: str) -> PropOut:
    W, H = 1080, 820
    rnd = random.Random(int(p.get("seed", 7)))
    c = color(p.get("color"), "var(--deep)")
    dens = max(.5, min(2.0, float(p.get("density", 1))))
    spire = p.get("spire", True) is not False
    sx = W * .7
    back, front, wins = [], [], []

    def windows(x, y, w, h, step=34):
        for wy in range(int(y + 26), int(y + h - 30), step):
            for wx in range(int(x + 16), int(x + w - 24), 30):
                wins.append((_rect(wx, wy, 11, 14),))

    x = -30.0
    while x < W:   # задний ряд: высокие узкие башни
        w = rnd.uniform(110, 190) / dens
        h = rnd.uniform(340, 560)
        back.append(f'<path class="{uid}-t" d="{_rect(x, H - h, w, h)}"/>')
        x += w + rnd.uniform(-10, 30) / dens
    x = -20.0
    while x < W:   # передний ряд: ниже и шире, иногда купол или ступенчатый верх
        w = rnd.uniform(150, 250) / dens
        if spire and x < sx + 150 and x + w > sx - 170:   # место под высотку
            x = sx + 150
            continue
        h = rnd.uniform(190, 380)
        kind = rnd.random()
        d = _rect(x, H - h, w, h)
        if kind < .25:   # купол
            r = w / 2
            d += f"M{_f(x)} {_f(H - h + 1)}A{_f(r)} {_f(r * .85)} 0 0 1 {_f(x + w)} {_f(H - h + 1)}z"
        elif kind < .4:  # ступень
            d += _rect(x + w * .25, H - h - 50, w * .5, 51)
        front.append(f'<path class="{uid}-t" d="{d}"/>')
        windows(x, H - h, w, h)
        x += w + rnd.uniform(-40, 10)
    if spire:
        sp = "".join(_stalin(sx, H, 1.0))
        front.append(f'<path class="{uid}-t" d="{sp}"/>')
        windows(sx - 85, H - 380, 170, 380, 40)
    lit = bool(p.get("lit"))
    lw = _windows_lit(rnd, wins, .28, f"{uid}-win")
    body = (f'<g filter="url(#{uid}-g)"><g fill="{c}">{"".join(back)}</g>'
            f'<g fill="#fff" opacity=".1">{"".join(back)}</g>'
            f'<g fill="{c}">{"".join(front)}</g><g style="opacity:{1 if lit else 0}" class="{uid}-wins">{lw}</g></g>')
    css = ""
    return PropOut(_svg(uid, W, H, body, grain(f"{uid}-g", .45, .18)), css, actions={
        "rise": _act_rise(f"{uid}-t", .025, .8),
        "lights": lambda sel, t: (f'tl.set({_q(sel + " ." + uid + "-wins")},{{opacity:1}},{num(t)});\n'
                                  + _act_lights(f"{uid}-win", .025)(sel, t))},
        w=W, h=H)


# --- дома ----------------------------------------------------------------------------------------------------
def _box(uid: str, w: float, h: float, face: str, depth: float = 16, roof: str | None = INK, roof_h: float = 14) -> str:
    """Фасад w×h на viewBox (w+depth)×(h+roof_h): боковая грань справа темнее, сверху тёмная крыша."""
    top = roof_h
    side = f"M{_f(w)} {_f(top)}L{_f(w + depth)} {_f(top - depth * .5 + 4)}V{_f(top + h - 6)}L{_f(w)} {_f(top + h)}z"
    out = _shade(side, face, .32)
    out += f'<path d="{_rect(0, top, w, h)}" fill="{face}"/>'
    if roof:
        out += (f'<path d="M-6 {_f(top)}h{_f(w + 12)}l{_f(depth)} {_f(-depth * .5 + 4)}V{_f(top - roof_h + 2)}'
                f'H{_f(depth - 6)}L-6 {_f(top - roof_h + depth * .5)}z" fill="{roof}"/>')
    return out


def _house_actions(uid: str, lit_cls: str | None = None) -> dict:
    acts = {"rise": _act_rise(f"{uid}-b", 0, .8)}
    if lit_cls:
        acts["lights"] = _act_lights(lit_cls, .08)
    return acts


@prop("building-classic", PropDoc(
    "Классическое здание: фронтон, антаблемент, колонны, ступени — кремовый фасад, тёмные детали",
    {"color": "цвет фасада, по умолчанию кремовый", "detail": "цвет колонн и фронтона, по умолчанию почти чёрный",
     "columns": "число колонн 4…8, по умолчанию 6"},
    actions=["rise"]))
def building_classic(p: dict, uid: str) -> PropOut:
    w, h, dep, rh = 320.0, 360.0, 16.0, 0.0
    face = color(p.get("color"), CREAM)
    det = color(p.get("detail"), INK)
    n = max(4, min(8, int(p.get("columns", 6))))
    side = f"M{_f(w)} 70L{_f(w + dep)} 66V{_f(h - 4)}L{_f(w)} {_f(h)}z"
    parts = [_shade(side, face, .32), f'<path d="{_rect(0, 0, w, h)}" fill="{face}"/>']
    # фронтон: тёмный треугольник с кремовым тимпаном
    parts.append(f'<path d="M14 96L160 20L306 96z" fill="{det}"/><path d="M52 85L160 35L268 85z" fill="{face}"/>')
    parts.append(f'<path d="{_rect(14, 100, 292, 16)}{_rect(30, 122, 260, 8)}" fill="{det}"/>')   # антаблемент
    cw, x0, x1 = 17.0, 44.0, 276.0
    step = (x1 - x0 - cw) / (n - 1)
    cols = "".join(_rect(x0 + i * step, 142, cw, 168) for i in range(n))
    parts.append(f'<path d="{cols}" fill="{det}"/>')
    parts.append(f'<path d="{_rect(22, 316, 276, 10)}{_rect(10, 331, 300, 11)}{_rect(0, 347, 320, 13)}" fill="{det}"/>')
    body = f'<g class="{uid}-b" filter="url(#{uid}-g)">{"".join(parts)}</g>'
    return PropOut(_svg(uid, w + dep, h + rh, body, grain(f"{uid}-g", .55, .12)),
                   actions=_house_actions(uid), w=w + dep, h=h + rh)


@prop("building-brick", PropDoc(
    "Кирпичный дом: кремовая стена в красную кладку, тёмные арочные окна (ночью могут гореть)",
    {"color": "цвет кирпича, по умолчанию красный", "wall": "цвет стены/швов, по умолчанию кремовый",
     "floors": "этажей 2…6, по умолчанию 3", "lit": "доля горящих окон 0…1 (true = 0.5), по умолчанию 0"},
    actions=["rise", "lights"]))
def building_brick(p: dict, uid: str) -> PropOut:
    floors = max(2, min(6, int(p.get("floors", 3))))
    w, dep = 270.0, 16.0
    h = 40 + floors * 95.0
    wall = color(p.get("wall"), CREAM)
    brick = color(p.get("color"), BRICK)
    rnd = random.Random(len(uid) * 31 + floors)
    side = f"M{_f(w)} {_f(8)}L{_f(w + dep)} {_f(4)}V{_f(h - 4)}L{_f(w)} {_f(h)}z"
    parts = [_shade(side, brick, .38), f'<path d="{_rect(0, 0, w, h)}" fill="{wall}"/>']
    bricks = []
    bh, bw = 15.0, 40.0
    for r, y in enumerate(range(4, int(h) - 4, 20)):
        off = -bw / 2 if r % 2 else 0.0
        x = off + 3
        while x < w:
            if rnd.random() > .12:   # пара выпавших кирпичей — живее
                x0, x1 = max(x, 3), min(x + bw - 5, w - 3)
                if x1 - x0 > 6:
                    bricks.append(_rect(x0, y, x1 - x0, bh))
            x += bw
    parts.append(f'<path d="{"".join(bricks)}" fill="{brick}"/>')
    wins = []
    ww, wh = 52.0, 70.0
    for f in range(floors):
        y = 34 + f * 95.0
        for cx in (w * .3, w * .7):
            x = cx - ww / 2
            d = (f"M{_f(x)} {_f(y + wh)}V{_f(y + ww / 2)}A{_f(ww / 2)} {_f(ww / 2)} 0 0 1 {_f(x + ww)} {_f(y + ww / 2)}"
                 f"V{_f(y + wh)}z")
            wins.append((d,))
    # белые откосы-подоконники под окнами, затем сами окна
    sills = "".join(_rect(cx - ww / 2 - 6, 34 + f * 95 + wh, ww + 12, 7) for f in range(floors) for cx in (w * .3, w * .7))
    parts.append(f'<path d="{sills}" fill="{wall}"/>')
    parts.append(f'<path d="{"".join(d for (d,) in wins)}" fill="{INK}"/>')
    lit = p.get("lit") or 0
    share = .5 if lit is True else max(0.0, min(1.0, float(lit)))
    lw = _windows_lit(random.Random(7), wins, share or 1.0, f"{uid}-lit")
    parts.append(f'<g class="{uid}-lw" style="opacity:{1 if share else 0}">{lw}</g>')
    body = f'<g class="{uid}-b" filter="url(#{uid}-g)">{"".join(parts)}</g>'
    acts = _house_actions(uid)
    acts["lights"] = lambda sel, t: (f'tl.set({_q(sel + " ." + uid + "-lw")},{{opacity:1}},{num(t)});\n'
                                     + _act_lights(f"{uid}-lit", .1)(sel, t))
    return PropOut(_svg(uid, w + dep, h, body, grain(f"{uid}-g", .5, .14)), actions=acts, w=w + dep, h=h)


@prop("panel-block", PropDoc(
    "Панельный дом: кремовый фасад, сетка тёмных окон, красная дверь, тёмная крыша, боковая грань",
    {"color": "цвет фасада, по умолчанию кремовый", "door": "цвет двери, по умолчанию красный",
     "floors": "этажей 3…14, по умолчанию 6", "cols": "окон в ряду 2…8, по умолчанию 4",
     "lit": "доля горящих окон 0…1 (true = 0.4), по умолчанию 0"},
    actions=["rise", "lights"]))
def panel_block(p: dict, uid: str) -> PropOut:
    floors = max(3, min(14, int(p.get("floors", 6))))
    cols = max(2, min(8, int(p.get("cols", 4))))
    w, dep = 70.0 + cols * 52, 18.0
    fh = 50.0
    h = 40 + floors * fh + 50
    face = color(p.get("color"), CREAM)
    door = color(p.get("door"), RED)
    side = f"M{_f(w)} {_f(10)}L{_f(w + dep)} {_f(4)}V{_f(h - 6)}L{_f(w)} {_f(h)}z"
    parts = [_shade(side, face, .36), f'<path d="{_rect(0, 0, w, h)}" fill="{face}"/>']
    wins = []
    for f in range(floors):
        for c in range(cols):
            wins.append((_rect(36 + c * 52 + 2, 34 + f * fh, 34, 28),))
    parts.append(f'<path d="{"".join(d for (d,) in wins)}" fill="{INK}"/>')
    parts.append(f'<path d="{_rect(w / 2 - 22, h - 58, 44, 58)}" fill="{door}"/>')
    lit = p.get("lit") or 0
    share = .4 if lit is True else max(0.0, min(1.0, float(lit)))
    lw = _windows_lit(random.Random(floors * 13 + cols), wins, share or .45, f"{uid}-lit")
    parts.append(f'<g class="{uid}-lw" style="opacity:{1 if share else 0}">{lw}</g>')
    body = f'<g class="{uid}-b" filter="url(#{uid}-g)">{"".join(parts)}</g>'
    acts = _house_actions(uid)
    acts["lights"] = lambda sel, t: (f'tl.set({_q(sel + " ." + uid + "-lw")},{{opacity:1}},{num(t)});\n'
                                     + _act_lights(f"{uid}-lit", .06)(sel, t))
    return PropOut(_svg(uid, w + dep, h, body, grain(f"{uid}-g", .5, .14)), actions=acts, w=w + dep, h=h)


@prop("tower", PropDoc(
    "Высокая башня: узкий корпус с полосами окон, ступенчатая верхушка и антенна",
    {"color": "цвет корпуса, по умолчанию deep", "floors": "этажей 8…30, по умолчанию 18",
     "lit": "доля горящих окон 0…1 (true = 0.35), по умолчанию 0"},
    actions=["rise", "lights"]))
def tower(p: dict, uid: str) -> PropOut:
    floors = max(8, min(30, int(p.get("floors", 18))))
    w, dep, fh = 170.0, 18.0, 30.0
    top = 130.0
    h = top + floors * fh + 20
    face = color(p.get("color"), "var(--deep)")
    side = f"M{_f(w)} {_f(top + 6)}L{_f(w + dep)} {_f(top)}V{_f(h - 6)}L{_f(w)} {_f(h)}z"
    parts = [_shade(side, face, .35), f'<path d="{_rect(0, top, w, h - top)}" fill="{face}"/>',
             f'<path d="{_rect(22, top - 50, w - 44, 51)}{_rect(48, top - 92, w - 96, 43)}{_rect(w / 2 - 3, 0, 6, 40)}" '
             f'fill="{face}"/>',
             f'<path d="{_rect(22, top - 50, w - 44, 51)}{_rect(48, top - 92, w - 96, 43)}" fill="#000" opacity=".12"/>']
    wins = []
    for f in range(floors):
        for c in range(4):
            wins.append((_rect(20 + c * 35, top + 22 + f * fh, 22, 14),))
    parts.append(f'<path d="{"".join(d for (d,) in wins)}" fill="#000" opacity=".28"/>')
    lit = p.get("lit") or 0
    share = .35 if lit is True else max(0.0, min(1.0, float(lit)))
    lw = _windows_lit(random.Random(floors * 7), wins, share or .35, f"{uid}-lit")
    parts.append(f'<g class="{uid}-lw" style="opacity:{1 if share else 0}">{lw}</g>')
    parts.append(f'<circle class="{uid}-beacon" cx="{_f(w / 2)}" cy="4" r="7" fill="{RED}"/>')
    body = f'<g class="{uid}-b" filter="url(#{uid}-g)">{"".join(parts)}</g>'
    acts = _house_actions(uid)
    acts["lights"] = lambda sel, t: (f'tl.set({_q(sel + " ." + uid + "-lw")},{{opacity:1}},{num(t)});\n'
                                     + _act_lights(f"{uid}-lit", .04)(sel, t))
    return PropOut(_svg(uid, w + dep, h, body, grain(f"{uid}-g", .45, .16)), actions=acts, w=w + dep, h=h)


# --- улица ---------------------------------------------------------------------------------------------------
@prop("streetlamp", PropDoc(
    "Уличный фонарь: тёмный столб, фонарь, жёлтый конус света и ореол",
    {"color": "цвет столба, по умолчанию почти чёрный", "light": "цвет света, по умолчанию тёплый жёлтый",
     "on": "свет включён с начала, по умолчанию true (false — включит действие glow)"},
    actions=["glow"]))
def streetlamp(p: dict, uid: str) -> PropOut:
    W, H = 360, 640
    pole = color(p.get("color"), INK)
    lt = color(p.get("light"), LIT)
    on = p.get("on", True) is not False
    defs = (f'<linearGradient id="{uid}-c" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{lt}" '
            f'stop-opacity=".55"/><stop offset="1" stop-color="{lt}" stop-opacity=".04"/></linearGradient>'
            f'<radialGradient id="{uid}-h"><stop offset="0" stop-color="{lt}" stop-opacity=".7"/>'
            f'<stop offset="1" stop-color="{lt}" stop-opacity="0"/></radialGradient>'
            + grain(f"{uid}-g", .4, .12))
    cx = W / 2
    light = (f'<g class="{uid}-light" style="opacity:{1 if on else 0}">'
             f'<path d="M{_f(cx - 22)} 96L{_f(cx - 170)} {H}H{_f(cx + 170)}L{_f(cx + 22)} 96z" fill="url(#{uid}-c)"/>'
             f'<circle cx="{_f(cx)}" cy="88" r="70" fill="url(#{uid}-h)"/>'
             f'<path d="M{_f(cx - 30)} 62h60l-10 32h-40z" fill="{lt}"/></g>')
    lamp = (f'<g filter="url(#{uid}-g)" fill="{pole}">'
            f'<path d="M{_f(cx - 46)} 62h92l-18 -30h-56z"/><path d="{_rect(cx - 6, 18, 12, 16)}"/>'
            f'<path d="{_rect(cx - 9, 96, 18, H - 130)}"/><path d="{_rect(cx - 20, H - 44, 40, 44)}"/>'
            f'<path d="{_rect(cx - 14, H - 70, 28, 28)}"/><path d="{_rect(cx - 13, 92, 26, 10)}"/></g>')
    return PropOut(_svg(uid, W, H, light + lamp, defs), actions={"glow": _act_glow(f"{uid}-light")}, w=W, h=H)


@prop("ground", PropDoc(
    "Полоса земли во всю ширину кадра с крупным зерном (асфальт, тротуар)",
    {"color": "цвет, по умолчанию почти чёрный", "height": "высота полосы в единицах viewBox, по умолчанию 420",
     "edge": "верхний край: flat (ровный) или rough (неровный), по умолчанию flat"},
    actions=["rise"]))
def ground(p: dict, uid: str) -> PropOut:
    W = 1080
    H = max(40, min(1920, int(p.get("height", 420))))
    c = color(p.get("color"), INK)
    if str(p.get("edge", "flat")) == "rough":
        rnd = random.Random(5)
        d = f"M-20 {H + 40}" + "".join(f"L{x} {_f(rnd.uniform(0, 14))}" for x in range(-20, W + 37, 36)) + f"V{H + 40}z"
    else:
        d = _rect(-20, 0, W + 40, H + 40)
    body = (f'<g class="{uid}-b" filter="url(#{uid}-g)"><path d="{d}" fill="{c}"/>'
            f'<path d="{_rect(-20, 0, W + 40, 10)}" fill="#000" opacity=".18"/></g>')
    return PropOut(_svg(uid, W, H, body, grain(f"{uid}-g", .35, .75, .7, 9)),
                   actions={"rise": _act_rise(f"{uid}-b", 0, .6)}, w=W, h=H)


@prop("tree", PropDoc(
    "Дерево: ствол и круглая крона в два тона",
    {"color": "цвет кроны, по умолчанию deep", "trunk": "цвет ствола, по умолчанию почти чёрный",
     "shape": "round (круглая) или poplar (тополь), по умолчанию round"},
    actions=["sway", "pop"]))
def tree(p: dict, uid: str) -> PropOut:
    W, H = 220, 340
    leaf = color(p.get("color"), "var(--deep)")
    trunk = color(p.get("trunk"), INK)
    if str(p.get("shape", "round")) == "poplar":
        crown = '<ellipse cx="110" cy="140" rx="62" ry="132"/>'
        shade = '<path d="M110 8A62 132 0 0 1 110 272z" fill="#000" opacity=".2"/>'
    else:
        crown = '<circle cx="110" cy="120" r="92"/><circle cx="62" cy="160" r="52"/><circle cx="160" cy="160" r="52"/>'
        shade = ('<path d="M110 28A92 92 0 0 1 200 150A52 52 0 0 1 150 212L110 212z" fill="#000" opacity=".2"/>')
    body = (f'<g filter="url(#{uid}-g)"><path d="M100 340L104 200h12l4 140z" fill="{trunk}"/>'
            f'<g class="{uid}-crown"><g fill="{leaf}">{crown}</g>{shade}</g></g>')
    return PropOut(_svg(uid, W, H, body, grain(f"{uid}-g", .45, .18)), actions={
        "sway": lambda sel, t: (f'tl.fromTo({_q(sel + " ." + uid + "-crown")},{{rotation:-3,svgOrigin:"110 300"}},'
                                f'{{rotation:3,duration:.9,ease:"sine.inOut",yoyo:true,repeat:3}},{num(t)});\n'),
        "pop": _act_pop(f"{uid}-crown")}, w=W, h=H)
