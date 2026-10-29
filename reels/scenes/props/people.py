"""Пропсы-персонажи и предметы в духе плаката: кабинка WC, человек, прожектор, лист оценки, штамп, звукослово,
телефон/ноутбук, курсор, звёздочка-искра и конфетти.

Стиль: плоские фигуры, 2.5D-коробки (фасад + тёмная боковина + чёрная крыша), «зерно» внутри фигур (SVG-фильтр
на feTurbulence), цвета — переменные темы. Все id и классы начинаются с uid; анимации пишут только в tl.
"""
from __future__ import annotations

import json
import math
import random

from ..base import esc, num
from . import PropDoc, PropOut, prop

INK = "#161616"        # почти чёрный: крыши, куртка, плашки звукослов
PAPER = "#f1eadb"      # кремовая бумага
CREAM = "#f3e3c3"      # месяц на двери, глаза-блики
SKIN = "#e8b18c"


# ---------- общие помощники ----------

def _js(s) -> str:
    """Строка-литерал для JS."""
    return json.dumps(str(s), ensure_ascii=False).replace("<", "\\u003c").replace("/", "\\/")


def _at(t, dt: float = 0.0) -> str:
    """Позиция на таймлайне: t — абсолютное время (число) или JS-выражение."""
    if isinstance(t, (int, float)):
        return num(max(0.0, float(t) + dt))
    return f"({t})+{num(dt)}" if dt else f"({t})"


def _plus(t, dt: float):
    """t + dt для передачи в другую функцию-действие (t — число или JS-выражение)."""
    return float(t) + dt if isinstance(t, (int, float)) else _at(t, dt)


def _q(sel: str, uid: str, name: str) -> str:
    """Селектор элемента пропа: корень sel + класс uid-name."""
    return _js(f"{sel} .{uid}-{name}")


def _f(v, d: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _col(p: dict, key: str, default: str) -> str:
    v = p.get(key)
    return esc(v) if v else default


def _grain(uid: str, freq: float = 0.9, dark: float = .32, light: float = .22, seed: int = 3) -> str:
    """Фильтр «зерно»: тёмные и светлые крапины только внутри непрозрачных пикселей фигуры."""
    return (f'<filter id="{uid}-grain" x="-5%" y="-5%" width="110%" height="110%" color-interpolation-filters="sRGB">'
            f'<feTurbulence type="fractalNoise" baseFrequency="{num(freq)}" numOctaves="2" seed="{seed}" result="n1"/>'
            '<feColorMatrix in="n1" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -14 0 0 0 5.6" result="d"/>'
            f'<feComponentTransfer in="d" result="d2"><feFuncA type="linear" slope="{num(dark)}"/></feComponentTransfer>'
            f'<feTurbulence type="fractalNoise" baseFrequency="{num(freq * 1.1)}" numOctaves="2" seed="{seed + 7}" result="n2"/>'
            '<feColorMatrix in="n2" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  14 0 0 0 -8.6" result="l"/>'
            f'<feComponentTransfer in="l" result="l2"><feFuncA type="linear" slope="{num(light)}"/></feComponentTransfer>'
            '<feMerge result="sp"><feMergeNode in="d2"/><feMergeNode in="l2"/></feMerge>'
            '<feComposite in="sp" in2="SourceAlpha" operator="in" result="spk"/>'
            '<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="spk"/></feMerge></filter>')


def _svg(uid: str, w: float, h: float, defs: str, body: str, extra_cls: str = "") -> str:
    return (f'<svg class="{uid}-svg {extra_cls}" viewBox="0 0 {num(w)} {num(h)}" xmlns="http://www.w3.org/2000/svg" '
            f'overflow="visible"><defs>{defs}</defs>{body}</svg>')


def _text_css(uid: str) -> str:
    return (f".{uid}-svg{{overflow:visible}}"
            f".{uid}-t{{font-family:var(--font);font-weight:800;letter-spacing:.01em}}")


def _shade(x: float, y: float, w: float, h: float, a: float, extra: str = "") -> str:
    return f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" fill="#000" opacity="{num(a)}" {extra}/>'


def _star_path(cx: float, cy: float, r: float, waist: float = .22) -> str:
    """Четырёхлучевая звёздочка с вогнутыми сторонами."""
    pts = []
    for k in range(4):
        a = math.pi / 2 * k - math.pi / 2
        tip = (cx + r * math.cos(a), cy + r * math.sin(a))
        b = a + math.pi / 4
        mid = (cx + r * waist * math.cos(b), cy + r * waist * math.sin(b))
        pts.append((tip, mid))
    d = f"M{num(pts[0][0][0])},{num(pts[0][0][1])}"
    for k in range(4):
        _tip, mid = pts[k]
        nxt = pts[(k + 1) % 4][0]
        d += f" Q{num(mid[0])},{num(mid[1])} {num(nxt[0])},{num(nxt[1])}"
    return d + "Z"


def _pop_js(q: str, t, dur: float = .45) -> str:
    return (f'tl.fromTo({q},{{opacity:0,scale:0,transformOrigin:"50% 50%"}},'
            f'{{opacity:1,scale:1,duration:{num(dur)},ease:"back.out(2.4)"}},{_at(t)});\n')


# ---------- 1. кабинка WC ----------

@prop("kiosk", PropDoc(
    "Уличная кабинка WC (2.5D): красный фасад, тёмная боковина, чёрная крыша с трубой, дверь с месяцем",
    {"color": "цвет стен (по умолчанию var(--alt))", "label": "надпись на фасаде («WC»)",
     "door_text": "что нарисовано на двери по команде sign («5»)", "roof": "цвет крыши",
     "open": "true — дверь приоткрыта с самого начала", "eyes": "true — глаза видны с начала (вместе с open)"},
    actions=["knock", "open", "peek", "close", "jump", "sign"]))
def kiosk(p: dict, uid: str) -> PropOut:
    W, H = 320, 420
    col = _col(p, "color", "var(--alt)")
    roof = _col(p, "roof", INK)
    label = esc(p.get("label", "WC"))
    sign = esc(p.get("door_text", "5"))
    start_open = bool(p.get("open"))
    start_eyes = bool(p.get("eyes")) and start_open
    g = f'filter="url(#{uid}-grain)"'
    fx0, fx1, fy0, fy1 = 40, 210, 160, 410          # фасад
    dx, dy = 62, -30                                 # глубина (вверх-вправо)
    door_x, door_y, door_w, door_h = 64, 214, 122, 196
    st = lambda c: f'style="fill:{c}"'               # noqa: E731
    cx, cy, r = door_x + 34, door_y + 46, 15         # месяц
    moon = (f'<path d="M{cx},{cy - r} A{r},{r} 0 0 0 {cx},{cy + r} A{r * .62:.1f},{r} 0 0 1 {cx},{cy - r}Z" '
            f'fill="{CREAM}"/>')
    hx, hy = door_x, door_y + door_h / 2              # петля двери
    sx_, sk_ = .46, 7                                 # приоткрытая дверь: scaleX и skewY
    ajar_tf = f' transform="matrix({sx_} {sx_ * math.tan(math.radians(sk_)):.4f} 0 1 0 0)"' if start_open else ""
    body = f'''
<g class="{uid}-body">
 <g class="{uid}-box" {g}>
  <polygon points="{fx1},{fy0} {fx1 + dx},{fy0 + dy} {fx1 + dx},{fy1 + dy} {fx1},{fy1}" {st(col)}/>
  <polygon points="{fx1},{fy0} {fx1 + dx},{fy0 + dy} {fx1 + dx},{fy1 + dy} {fx1},{fy1}" fill="#000" opacity=".34"/>
  <rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" {st(col)}/>
  <rect x="{fx1 - 10}" y="{fy0}" width="10" height="{fy1 - fy0}" fill="#000" opacity=".12"/>
  <rect x="{door_x}" y="{door_y}" width="{door_w}" height="{door_h}" fill="#0d0d0d"/>
  <rect x="{door_x}" y="{door_y}" width="12" height="{door_h}" fill="#000" opacity=".5"/>
 </g>
 <g class="{uid}-eyes" opacity="{1 if start_eyes else 0}">
  <g class="{uid}-eye"><ellipse cx="{door_x + 82}" cy="{door_y + 60}" rx="10" ry="11" fill="#fff"/>
   <circle class="{uid}-pupil" cx="{door_x + 79}" cy="{door_y + 61}" r="5" fill="#0d0d0d"/></g>
  <g class="{uid}-eye"><ellipse cx="{door_x + 106}" cy="{door_y + 60}" rx="10" ry="11" fill="#fff"/>
   <circle class="{uid}-pupil" cx="{door_x + 103}" cy="{door_y + 61}" r="5" fill="#0d0d0d"/></g>
 </g>
 <g transform="translate({hx} {hy})"><g class="{uid}-door"{ajar_tf}><g transform="translate({-hx} {-hy})" {g}>
  <rect x="{door_x}" y="{door_y}" width="{door_w}" height="{door_h}" {st(col)}/>
  <rect x="{door_x}" y="{door_y}" width="{door_w}" height="{door_h}" fill="#fff" opacity=".07"/>
  <rect x="{door_x + 1.5}" y="{door_y + 1.5}" width="{door_w - 3}" height="{door_h - 3}" fill="none" stroke="#000" stroke-opacity=".28" stroke-width="3"/>
  {moon}
  <rect x="{door_x + door_w - 18}" y="{door_y + 112}" width="7" height="10" fill="{CREAM}"/>
  <g class="{uid}-sign" opacity="0">
   <circle cx="{door_x + door_w / 2}" cy="{door_y + 116}" r="26" fill="none" stroke="{CREAM}" stroke-width="5"/>
   <text class="{uid}-t" x="{door_x + door_w / 2}" y="{door_y + 117}" font-size="32" fill="{CREAM}"
    text-anchor="middle" dominant-baseline="central">{sign}</text>
  </g>
 </g>
 <g {g}>
  <polygon points="{fx0 - 14},{fy0 - 26} {fx1 + 14},{fy0 - 26} {fx1 + dx + 18},{fy0 + dy - 30} {fx0 + dx - 10},{fy0 + dy - 30}" fill="{roof}"/>
  <polygon points="{fx0 - 14},{fy0 - 26} {fx1 + 14},{fy0 - 26} {fx1 + dx + 18},{fy0 + dy - 30} {fx0 + dx - 10},{fy0 + dy - 30}" fill="#fff" opacity=".16"/>
  <polygon points="{fx1 + 14},{fy0 - 26} {fx1 + dx + 18},{fy0 + dy - 30} {fx1 + dx + 18},{fy0 + dy - 8} {fx1 + 14},{fy0 - 2}" fill="{roof}"/>
  <rect x="{fx0 - 14}" y="{fy0 - 26}" width="{fx1 - fx0 + 28}" height="24" fill="{roof}"/>
  <rect x="{fx0 + 92}" y="{fy0 - 70}" width="26" height="40" fill="{roof}"/>
  <polygon points="{fx0 + 118},{fy0 - 70} {fx0 + 140},{fy0 - 84} {fx0 + 140},{fy0 - 46} {fx0 + 118},{fy0 - 32}" fill="{roof}"/>
  <polygon points="{fx0 + 118},{fy0 - 70} {fx0 + 140},{fy0 - 84} {fx0 + 140},{fy0 - 46} {fx0 + 118},{fy0 - 32}" fill="#000" opacity=".35"/>
  <polygon points="{fx0 + 92},{fy0 - 70} {fx0 + 118},{fy0 - 70} {fx0 + 140},{fy0 - 84} {fx0 + 114},{fy0 - 84}" fill="#fff" opacity=".22"/>
  <polygon points="{fx0 + 92},{fy0 - 70} {fx0 + 118},{fy0 - 70} {fx0 + 140},{fy0 - 84} {fx0 + 114},{fy0 - 84}" fill="{roof}" opacity=".3"/>
 </g>
 <text class="{uid}-t" x="{(fx0 + fx1) / 2}" y="{fy0 + 30}" font-size="34" fill="#fff" text-anchor="middle"
  dominant-baseline="central">{label}</text>
</g>'''
    svg = _svg(uid, W, H, _grain(uid, 1.1), body)

    def knock(sel, t):
        b = _q(sel, uid, "body")
        return (f'tl.to({b},{{rotation:-2.2,transformOrigin:"50% 100%",duration:.06,ease:"power1.out",'
                f'yoyo:true,repeat:3}},{_at(t)});\n'
                f'tl.to({b},{{x:-4,duration:.05,yoyo:true,repeat:3}},{_at(t)});\n'
                f'tl.to({b},{{rotation:2.2,transformOrigin:"50% 100%",duration:.06,ease:"power1.out",'
                f'yoyo:true,repeat:3}},{_at(t, .42)});\n'
                f'tl.to({b},{{x:4,duration:.05,yoyo:true,repeat:3}},{_at(t, .42)});\n')

    def open_(sel, t):
        d = _q(sel, uid, "door")
        return (f'tl.fromTo({d},{{scaleX:1,skewY:0,svgOrigin:"0 0"}},{{scaleX:{sx_},skewY:{sk_},duration:.55,'
                f'ease:"back.out(1.6)"}},{_at(t)});\n')

    def peek(sel, t):
        e, pu = _q(sel, uid, "eyes"), _q(sel, uid, "pupil")
        ey = _q(sel, uid, "eye")
        return (f'tl.fromTo({e},{{opacity:0,x:14}},{{opacity:1,x:0,duration:.3,ease:"power2.out"}},{_at(t)});\n'
                f'tl.to({pu},{{x:-4,duration:.25,ease:"power2.inOut"}},{_at(t, .35)});\n'
                f'tl.to({pu},{{x:3,duration:.3,ease:"power2.inOut"}},{_at(t, .75)});\n'
                f'tl.to({ey},{{scaleY:.1,transformOrigin:"50% 50%",duration:.07,yoyo:true,repeat:1}},{_at(t, 1.15)});\n')

    def close(sel, t):
        d, e = _q(sel, uid, "door"), _q(sel, uid, "eyes")
        return (f'tl.to({e},{{opacity:0,x:10,duration:.15}},{_at(t)});\n'
                f'tl.to({d},{{scaleX:1,skewY:0,svgOrigin:"0 0",duration:.32,ease:"power3.in"}},{_at(t, .05)});\n'
                f'tl.to({_q(sel, uid, "body")},{{x:-3,duration:.04,yoyo:true,repeat:3}},{_at(t, .37)});\n')

    def jump(sel, t):
        b = _q(sel, uid, "body")
        o = 'transformOrigin:"50% 100%"'
        return (f'tl.to({b},{{scaleY:.82,scaleX:1.1,{o},duration:.12,ease:"power2.out"}},{_at(t)});\n'
                f'tl.to({b},{{y:-70,scaleY:1.12,scaleX:.93,{o},duration:.22,ease:"power2.out"}},{_at(t, .12)});\n'
                f'tl.to({b},{{y:0,scaleY:1,scaleX:1,{o},duration:.18,ease:"power2.in"}},{_at(t, .34)});\n'
                f'tl.to({b},{{scaleY:.86,scaleX:1.08,{o},duration:.08,ease:"power1.out",yoyo:true,repeat:1}},{_at(t, .52)});\n')

    def sign_(sel, t):
        s = _q(sel, uid, "sign")
        return _pop_js(s, t, .4)

    return PropOut(svg, _text_css(uid), {"knock": knock, "open": open_, "peek": peek, "close": close, "jump": jump,
                              "sign": sign_}, W, H)


# ---------- 2. человек ----------

def _held(kind: str, uid: str, accent: str) -> str:
    if kind == "phone":
        return (f'<g class="{uid}-held"><rect x="-17" y="-66" width="34" height="62" rx="6" fill="{INK}"/>'
                f'<rect x="-13" y="-60" width="26" height="48" rx="2" style="fill:{accent}"/>'
                f'<rect x="-13" y="-60" width="26" height="48" rx="2" fill="#fff" opacity=".35"/></g>')
    if kind == "banana":
        return (f'<g class="{uid}-held"><path d="M-6,-4 C10,-14 28,-40 26,-80 C30,-84 35,-83 36,-78 '
                f'C40,-34 22,-4 2,8 Z" fill="#f2c935"/>'
                f'<path d="M-6,-4 C10,-14 28,-40 26,-80 C24,-40 10,-16 -2,-6Z" fill="#c98f13" opacity=".7"/>'
                f'<rect x="26" y="-90" width="6" height="12" rx="2" fill="#5a3d16" transform="rotate(8 29 -84)"/></g>')
    return f'<g class="{uid}-held"></g>' if not kind else (
        f'<g class="{uid}-held"><circle cx="0" cy="-40" r="26" style="fill:{accent}"/></g>')


@prop("person", PropDoc(
    "Плоский персонаж по пояс: бородач в красной шапке, чёрных очках, чёрной куртке и белой футболке с логотипом",
    {"hat": "цвет шапки (var(--alt))", "jacket": "цвет куртки", "skin": "цвет кожи", "beard": "цвет бороды",
     "logo": "текст квадратика на футболке («ог»)", "hold": "предмет в руке: banana / phone / пусто",
     "holding": "true — предмет и поднятая рука с самого начала"},
    actions=["pop", "wave", "talk", "point", "hold", "rest"]))
def person(p: dict, uid: str) -> PropOut:
    W, H = 420, 520
    hat = _col(p, "hat", "var(--alt)")
    jacket = _col(p, "jacket", "#1c1c1e")
    skin = _col(p, "skin", SKIN)
    beard = _col(p, "beard", "#8a5634")
    logo = esc(p.get("logo", "ог"))
    kind = str(p.get("hold", "banana") or "")
    holding = bool(p.get("holding"))
    g = f'filter="url(#{uid}-grain)"'
    st = lambda c: f'style="fill:{c}"'               # noqa: E731
    up0, lo0 = (-140, -40) if holding else (-14, 10)
    body = f'''
<g class="{uid}-all">
 <g class="{uid}-torso" {g}>
  <path d="M62,520 L70,372 Q76,322 132,308 L178,296 L242,296 L288,308 Q344,322 350,372 L358,520 Z" {st(jacket)}/>
  <path d="M176,298 L244,298 L250,520 L170,520 Z" fill="#f3efe6"/>
  <path d="M176,298 L156,306 L164,392 L186,340 Z" fill="#fff" opacity=".12"/>
  <path d="M244,298 L264,306 L256,392 L234,340 Z" fill="#fff" opacity=".12"/>
  <path d="M176,298 L186,340 L176,520 L168,520 Z" {st(jacket)}/>
  <path d="M244,298 L234,340 L244,520 L252,520 Z" {st(jacket)}/>
  <rect x="194" y="398" width="32" height="30" style="fill:var(--alt)"/>
  <text class="{uid}-t" x="210" y="413" font-size="15" fill="#fff" text-anchor="middle" dominant-baseline="central">{logo}</text>
  <path d="M98,330 Q70,350 72,420 L74,468 L112,468 L114,400 Z" {st(jacket)}/>
  <path d="M98,330 Q70,350 72,420 L74,468 L84,468 L84,400 Z" fill="#000" opacity=".25"/>
  <circle cx="93" cy="478" r="19" {st(skin)}/>
  <path d="M80,486 q6,6 12,0" stroke="#000" stroke-opacity=".25" stroke-width="2.5" fill="none"/>
 </g>
 <g class="{uid}-head">
  <g {g}>
   <ellipse cx="156" cy="228" rx="11" ry="15" {st(skin)}/>
   <ellipse cx="264" cy="228" rx="11" ry="15" {st(skin)}/>
   <rect x="156" y="140" width="108" height="170" rx="46" {st(skin)}/>
   <circle cx="174" cy="240" r="11" fill="#f07c7c" opacity=".55"/>
   <circle cx="246" cy="240" r="11" fill="#f07c7c" opacity=".55"/>
   <path d="M154,212 L158,262 Q160,348 210,354 Q260,348 262,262 L266,212 L252,214 Q252,256 232,262
            Q210,252 188,262 Q168,256 168,214 Z" {st(beard)}/>
   <path d="M182,268 Q210,248 238,268 Q226,276 210,270 Q194,276 182,268 Z" {st(beard)}/>
   <path d="M182,268 Q210,248 238,268 Q226,276 210,270 Q194,276 182,268 Z" fill="#000" opacity=".25"/>
   <ellipse cx="210" cy="246" rx="10" ry="9" fill="#d98a6c"/>
  </g>
  <g class="{uid}-mouth"><ellipse cx="210" cy="284" rx="8" ry="4" fill="#3b1a10"/></g>
  <g>
   <rect x="160" y="200" width="100" height="32" rx="9" fill="#0e0e0e"/>
   <path d="M174,208 l9,9 M218,208 l9,9" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/>
  </g>
  <g {g}>
   <path d="M150,200 L150,170 Q150,106 210,104 Q270,106 270,170 L270,200 Z" {st(hat)}/>
   <rect x="146" y="168" width="128" height="34" rx="8" {st(hat)}/>
   <rect x="146" y="168" width="128" height="34" rx="8" fill="#000" opacity=".12"/>
   <path d="M152,150 H268 M150,180 H270 M150,191 H270" stroke="#000" stroke-opacity=".16" stroke-width="4"/>
   <path d="M156,132 H264" stroke="#000" stroke-opacity=".12" stroke-width="4"/>
   <ellipse cx="186" cy="126" rx="20" ry="11" fill="#fff" opacity=".3" transform="rotate(-24 186 126)"/>
  </g>
 </g>
 <g transform="translate(316,336)">
  <g class="{uid}-arm" transform="rotate({up0})">
   <rect x="-21" y="-8" width="42" height="96" rx="20" {st(jacket)} {g}/>
   <g transform="translate(0,82)">
    <g class="{uid}-fore" transform="rotate({lo0})">
     <rect x="-19" y="-6" width="38" height="84" rx="18" {st(jacket)} {g}/>
     <g transform="translate(0,92)"><g transform="rotate(180)">
      <g class="{uid}-obj" opacity="{1 if holding and kind else 0}">{_held(kind, uid, "var(--accent)")}</g>
     </g></g>
     <circle cx="0" cy="90" r="18" {st(skin)}/>
    </g>
   </g>
  </g>
 </g>
</g>'''
    svg = _svg(uid, W, H, _grain(uid, 1.0), body)
    o0 = 'svgOrigin:"0 0"'

    def arm(sel, t, up, lo, dur=.4, ease="back.out(1.7)"):
        return (f'tl.to({_q(sel, uid, "arm")},{{rotation:{up},{o0},duration:{num(dur)},ease:"{ease}"}},{_at(t)});\n'
                f'tl.to({_q(sel, uid, "fore")},{{rotation:{lo},{o0},duration:{num(dur)},ease:"{ease}"}},{_at(t)});\n')

    def pop(sel, t):
        a = _q(sel, uid, "all")
        return (f'tl.fromTo({a},{{y:260,opacity:0}},{{y:0,opacity:1,duration:.55,ease:"back.out(1.5)"}},{_at(t)});\n')

    def wave(sel, t):
        f = _q(sel, uid, "fore")
        return (arm(sel, t, -150, -30, .35)
                + f'tl.to({f},{{rotation:10,{o0},duration:.18,ease:"sine.inOut",yoyo:true,repeat:5}},{_at(t, .35)});\n'
                + arm(sel, _plus(t, 1.5), up0, lo0, .4, "power2.inOut"))

    def talk(sel, t):
        h, m = _q(sel, uid, "head"), _q(sel, uid, "mouth")
        return (f'tl.to({h},{{rotation:3,y:-5,transformOrigin:"50% 100%",duration:.2,ease:"sine.inOut",'
                f'yoyo:true,repeat:5}},{_at(t)});\n'
                f'tl.to({m},{{scaleY:2.6,scaleX:.9,transformOrigin:"50% 30%",duration:.11,ease:"none",'
                f'yoyo:true,repeat:9}},{_at(t)});\n')

    def point(sel, t):
        return arm(sel, t, -100, -6, .35)

    def hold(sel, t):
        ob = _q(sel, uid, "obj")
        return (arm(sel, t, -140, -40, .45)
                + f'tl.fromTo({ob},{{opacity:0,scale:.2,{o0}}},{{opacity:1,scale:1,duration:.4,ease:"back.out(2.5)"}},'
                  f'{_at(t, .2)});\n')

    def rest(sel, t):
        return arm(sel, t, -14, 10, .45, "power2.inOut")

    return PropOut(svg, _text_css(uid), {"pop": pop, "wave": wave, "talk": talk, "point": point, "hold": hold,
                                         "rest": rest}, W, H)


# ---------- 3. прожектор ----------

@prop("spotlight", PropDoc(
    "Жёлтый конус света сверху и светлое пятно на полу (ставить за персонажем)",
    {"color": "цвет света (var(--accent))", "spread": "ширина низа конуса, 0.3…1 (0.9)"},
    actions=["on", "flicker", "off"]))
def spotlight(p: dict, uid: str) -> PropOut:
    W, H = 500, 900
    col = _col(p, "color", "var(--accent)")
    sp = max(.3, min(1.0, _f(p.get("spread"), .9)))
    top = 46
    half = W / 2 * sp
    g = f'filter="url(#{uid}-grain)"'
    body = f'''
<g class="{uid}-beam" {g}>
 <polygon points="{W / 2 - top},0 {W / 2 + top},0 {W / 2 + half},{H - 70} {W / 2 - half},{H - 70}" style="fill:{col}"/>
 <polygon points="{W / 2 - top},0 {W / 2 - top * .2},0 {W / 2 - half * .25},{H - 70} {W / 2 - half},{H - 70}" fill="#fff" opacity=".1"/>
</g>
<g class="{uid}-pool" {g}>
 <ellipse cx="{W / 2}" cy="{H - 70}" rx="{half}" ry="{half * .2:.1f}" style="fill:{col}"/>
 <ellipse cx="{W / 2}" cy="{H - 70}" rx="{half}" ry="{half * .2:.1f}" fill="#fff" opacity=".35"/>
</g>'''
    svg = _svg(uid, W, H, _grain(uid, .9, .38, .2), body)

    def on(sel, t):
        b, pl = _q(sel, uid, "beam"), _q(sel, uid, "pool")
        return (f'tl.fromTo({b},{{scaleX:0,opacity:0,transformOrigin:"50% 0%"}},{{scaleX:1,opacity:1,duration:.5,'
                f'ease:"power3.out"}},{_at(t)});\n'
                f'tl.fromTo({pl},{{scale:0,opacity:0,transformOrigin:"50% 50%"}},{{scale:1,opacity:1,duration:.45,'
                f'ease:"back.out(1.6)"}},{_at(t, .25)});\n')

    def flicker(sel, t):
        b, pl = _q(sel, uid, "beam"), _q(sel, uid, "pool")
        js = ""
        for i, (dt, op) in enumerate([(0, .25), (.07, 1), (.16, .4), (.22, 1), (.4, .15), (.47, 1)]):
            js += f'tl.set([{b},{pl}],{{opacity:{op}}},{_at(t, dt)});\n' if i < 6 else ""
        return js

    def off(sel, t):
        b, pl = _q(sel, uid, "beam"), _q(sel, uid, "pool")
        return f'tl.to([{b},{pl}],{{opacity:0,duration:.25,ease:"power1.in"}},{_at(t)});\n'

    return PropOut(svg, "", {"on": on, "flicker": flicker, "off": off}, W, H)


# ---------- 4. лист оценки ----------

def _items(p: dict) -> list[str]:
    v = p.get("items") or ["факт", "городской угол", "хук", "монтаж", "панчлайн"]
    if isinstance(v, str):
        v = [s.strip() for s in v.replace(";", "\n").split("\n") if s.strip()]
    return [str(x) for x in v][:8]


@prop("scorecard", PropDoc(
    "Кремовый лист оценки чуть под углом: заголовок, строки критериев, пунктирные кружки под отметки",
    {"title": "заголовок («ЛИСТ ОЦЕНКИ»)", "items": "список строк", "marks": "отметки по строкам (числа, по умолчанию 5)",
     "color": "цвет штампов (var(--alt))", "paper": "цвет бумаги", "tilt": "наклон, градусы (-2)",
     "stamped": "сколько строк уже проштамповано с начала (0)", "star": "true — жёлтая звёздочка в углу"},
    actions=["stamp_1", "stamp_2", "…", "stamp_all", "big_stamp", "shake"]))
def scorecard(p: dict, uid: str) -> PropOut:
    items = _items(p)
    n = len(items)
    marks = p.get("marks") or []
    if isinstance(marks, (int, float, str)):
        marks = [marks] * n
    marks = [str(marks[i]) if i < len(marks) else (str(marks[-1]) if marks else "5") for i in range(n)]
    W = 600
    row_h = 92
    top = 190
    H = top + row_h * n + 40
    col = _col(p, "color", "var(--alt)")
    paper = _col(p, "paper", PAPER)
    title = esc(p.get("title", "ЛИСТ ОЦЕНКИ"))
    tilt = _f(p.get("tilt"), -2)
    pre = int(_f(p.get("stamped"), 0))
    g = f'filter="url(#{uid}-grain)"'
    cxm = W - 92
    rows = ""
    for i, it in enumerate(items):
        y = top + row_h * i + row_h / 2
        shown = 1 if i < pre else 0
        fs = 30 if len(it) < 16 else 26
        rows += f'''
 <text class="{uid}-t" x="52" y="{y}" font-size="30" fill="{INK}" dominant-baseline="central">{i + 1}</text>
 <text class="{uid}-t" x="90" y="{y}" font-size="{fs}" fill="{INK}" dominant-baseline="central">{esc(it.upper())}</text>
 <circle cx="{cxm}" cy="{y}" r="32" fill="none" stroke="#000" stroke-opacity=".35" stroke-width="3" stroke-dasharray="9 8"/>
 <g class="{uid}-mark {uid}-m{i + 1}" opacity="{shown}">
  <circle cx="{cxm}" cy="{y}" r="31" fill="{paper}" style="stroke:{col}" stroke-width="6"/>
  <text class="{uid}-t" x="{cxm}" y="{y + 1}" font-size="34" style="fill:{col}" text-anchor="middle"
   dominant-baseline="central">{esc(marks[i])}</text>
 </g>'''
    sy = top + row_h * (n - 1) + row_h / 2
    star = (f'<path class="{uid}-star" d="{_star_path(W - 60, 70, 34, .2)}" fill="#e9be3a" stroke="#6b5413" '
            f'stroke-width="2"/>') if p.get("star", True) else ""
    body = f'''
<g transform="rotate({num(tilt)} {W / 2} {H / 2})">
 <g class="{uid}-card">
  <rect x="14" y="16" width="{W - 20}" height="{H - 20}" fill="#000" opacity=".28"/>
  <rect x="0" y="0" width="{W - 20}" height="{H - 20}" fill="{paper}" {g}/>
  <text class="{uid}-t" x="52" y="100" font-size="38" fill="{INK}">{esc(title)}</text>
  <rect x="52" y="122" width="{W - 150}" height="6" fill="{INK}"/>
  {star}
  {rows}
  <g class="{uid}-big" opacity="0" {g}>
   <circle cx="{cxm - 20}" cy="{sy - 40}" r="120" style="fill:{col}"/>
  </g>
  <g class="{uid}-tool" opacity="0" {g}>
   <circle cx="{cxm}" cy="{top + row_h / 2}" r="44" style="fill:{col}"/>
   <circle cx="{cxm}" cy="{top + row_h / 2}" r="22" fill="#111"/>
  </g>
 </g>
</g>'''
    svg = _svg(uid, W, H, _grain(uid, .95, .3, .12), body)

    def stamp_i(i):
        y_off = row_h * i

        def fn(sel, t):
            m, tool = _q(sel, uid, f"m{i + 1}"), _q(sel, uid, "tool")
            return (f'tl.fromTo({tool},{{opacity:0,x:60,y:{num(y_off - 40)},scale:1.5,transformOrigin:"50% 50%"}},'
                    f'{{opacity:1,x:0,y:{num(y_off)},scale:1,duration:.2,ease:"power3.in"}},{_at(t)});\n'
                    f'tl.fromTo({m},{{opacity:0,scale:1.35,transformOrigin:"50% 50%"}},{{opacity:1,scale:1,duration:.22,'
                    f'ease:"back.out(3)"}},{_at(t, .2)});\n'
                    f'tl.to({tool},{{x:80,y:{num(y_off - 50)},scale:1.3,opacity:0,duration:.25,ease:"power2.in"}},'
                    f'{_at(t, .32)});\n'
                    f'tl.fromTo({_q(sel, uid, "card")},{{y:0}},{{y:3,duration:.05,yoyo:true,repeat:1}},{_at(t, .2)});\n')
        return fn

    acts = {f"stamp_{i + 1}": stamp_i(i) for i in range(n)}

    def stamp_all(sel, t):
        return "".join(acts[f"stamp_{i + 1}"](sel, _plus(t, .6 * i))
                       for i in range(n))

    def big_stamp(sel, t):
        b = _q(sel, uid, "big")
        return (f'tl.fromTo({b},{{opacity:0,scale:2.4,transformOrigin:"50% 50%"}},{{opacity:.96,scale:1,duration:.22,'
                f'ease:"power4.in"}},{_at(t)});\n' + shake(sel, _plus(t, .22)))

    def shake(sel, t):
        c = _q(sel, uid, "card")
        return (f'tl.fromTo({c},{{x:0,rotation:0}},{{x:6,rotation:.6,transformOrigin:"50% 50%",duration:.05,'
                f'ease:"none",yoyo:true,repeat:5}},{_at(t)});\n')

    acts.update({"stamp_all": stamp_all, "big_stamp": big_stamp, "shake": shake})
    return PropOut(svg, _text_css(uid), acts, W, H)


# ---------- 5. штамп ----------

@prop("stamp", PropDoc(
    "Красный оттиск штампа: кольцо с текстом (или сплошной круг)",
    {"text": "текст внутри («5»)", "color": "цвет (var(--alt))", "filled": "true — сплошной круг",
     "tilt": "наклон, градусы (-8)"},
    actions=["slam", "shake"]))
def stamp(p: dict, uid: str) -> PropOut:
    W = H = 240
    col = _col(p, "color", "var(--alt)")
    txt = esc(p.get("text", "5"))
    fs = 96 if len(str(p.get("text", "5"))) <= 2 else max(30, int(300 / max(3, len(str(p.get("text"))))))
    filled = bool(p.get("filled"))
    tilt = _f(p.get("tilt"), -8)
    g = f'filter="url(#{uid}-grain)"'
    ring = (f'<circle cx="120" cy="120" r="100" style="fill:{col}"/>'
            f'<text class="{uid}-t" x="120" y="122" font-size="{fs}" fill="#fff" text-anchor="middle" '
            f'dominant-baseline="central">{txt}</text>') if filled else (
            f'<circle cx="120" cy="120" r="96" fill="none" style="stroke:{col}" stroke-width="16"/>'
            f'<circle cx="120" cy="120" r="76" fill="none" style="stroke:{col}" stroke-width="3"/>'
            f'<text class="{uid}-t" x="120" y="122" font-size="{fs}" style="fill:{col}" text-anchor="middle" '
            f'dominant-baseline="central">{txt}</text>')
    body = f'<g transform="rotate({num(tilt)} 120 120)"><g class="{uid}-mark" {g}>{ring}</g></g>'
    svg = _svg(uid, W, H, _grain(uid, 1.0, .45, .25), body)

    def slam(sel, t):
        m = _q(sel, uid, "mark")
        return (f'tl.fromTo({m},{{opacity:0,scale:2.6,transformOrigin:"50% 50%"}},{{opacity:1,scale:1,duration:.2,'
                f'ease:"power4.in"}},{_at(t)});\n'
                f'tl.to({m},{{scale:1.06,duration:.06,yoyo:true,repeat:1,ease:"power1.out"}},{_at(t, .2)});\n')

    def shake(sel, t):
        m = _q(sel, uid, "mark")
        return f'tl.to({m},{{x:5,duration:.05,yoyo:true,repeat:5,ease:"none"}},{_at(t)});\n'

    return PropOut(svg, _text_css(uid), {"slam": slam, "shake": shake}, W, H)


# ---------- 6. звукослово ----------

@prop("burst", PropDoc(
    "Звукослово комикса: чёрная плашка с белым словом («БАХ», «тук-тук») или жёлтая звезда-взрыв с текстом",
    {"text": "слово", "style": "label (плашка) / star (звезда)", "color": "фон (label: #161616, star: var(--accent))",
     "ink": "цвет текста", "tilt": "наклон, градусы (-6)"},
    actions=["pop", "shake"]))
def burst(p: dict, uid: str) -> PropOut:
    raw = str(p.get("text", "БАХ"))
    txt = esc(raw)
    style = p.get("style", "label")
    tilt = _f(p.get("tilt"), -6)
    g = f'filter="url(#{uid}-grain)"'
    if style == "star":
        W = H = 360
        col = _col(p, "color", "var(--accent)")
        ink = _col(p, "ink", INK)
        rng = random.Random(len(raw) * 31 + 7)
        pts = []
        for k in range(28):
            a = 2 * math.pi * k / 28
            rr = (170 if k % 2 == 0 else 112) * (1 + rng.uniform(-.08, .06))
            pts.append(f"{180 + rr * math.cos(a):.1f},{180 + rr * .82 * math.sin(a):.1f}")
        fs = min(92, int(250 / max(1.0, len(raw) * .62)))
        shape = (f'<polygon points="{" ".join(pts)}" fill="{INK}" transform="translate(8 10)" opacity=".85"/>'
                 f'<polygon points="{" ".join(pts)}" style="fill:{col}" {g}/>'
                 f'<text class="{uid}-t" x="180" y="184" font-size="{fs}" style="fill:{ink}" text-anchor="middle" '
                 f'dominant-baseline="central">{txt}</text>')
        cx, cy = 180, 180
    else:
        fs = 64
        tw = max(1, len(raw)) * fs * .64
        W, H = tw + 64, fs + 52
        col = _col(p, "color", INK)
        ink = _col(p, "ink", "#fff")
        shape = (f'<rect x="0" y="0" width="{num(W)}" height="{num(H)}" style="fill:{col}" {g}/>'
                 f'<text class="{uid}-t" x="{num(W / 2)}" y="{num(H / 2 + 2)}" font-size="{fs}" style="fill:{ink}" '
                 f'text-anchor="middle" dominant-baseline="central" textLength="{num(tw)}" '
                 f'lengthAdjust="spacingAndGlyphs">{txt}</text>')
        cx, cy = W / 2, H / 2
    body = f'<g transform="rotate({num(tilt)} {num(cx)} {num(cy)})"><g class="{uid}-word">{shape}</g></g>'
    svg = _svg(uid, W, H, _grain(uid, 1.0, .3, .3), body)

    def pop(sel, t):
        w = _q(sel, uid, "word")
        return (f'tl.fromTo({w},{{opacity:0,scale:.2,rotation:-14,transformOrigin:"50% 50%"}},{{opacity:1,scale:1,'
                f'rotation:0,duration:.32,ease:"back.out(3)"}},{_at(t)});\n')

    def shake(sel, t):
        w = _q(sel, uid, "word")
        return (f'tl.to({w},{{x:6,rotation:3,transformOrigin:"50% 50%",duration:.05,yoyo:true,repeat:7,ease:"none"}},'
                f'{_at(t)});\n')

    return PropOut(svg, _text_css(uid), {"pop": pop, "shake": shake}, W, H)


# ---------- 7. телефон и ноутбук ----------

def _taps(p: dict) -> list[tuple[float, float]]:
    v = p.get("taps") or p.get("tap") or [[.5, .5]]
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
        v = [v]
    out = []
    for xy in v:
        try:
            out.append((min(1.0, max(0.0, float(xy[0]))), min(1.0, max(0.0, float(xy[1])))))
        except (TypeError, ValueError, IndexError):
            continue
    return out or [(.5, .5)]


def _screen(uid: str, p: dict, x: float, y: float, w: float, h: float, rx: float) -> tuple[str, str]:
    src = p.get("src")
    clip = f'<clipPath id="{uid}-scr"><rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="{num(rx)}"/></clipPath>'
    if src:
        img = (f'<image href="{esc(src)}" x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" '
               f'preserveAspectRatio="xMidYMid slice"/>')
    else:
        img = (f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" style="fill:var(--deep)"/>'
               + "".join(f'<rect x="{num(x + w * .1)}" y="{num(y + h * (.14 + .14 * k))}" width="{num(w * (.8 - .18 * (k % 2)))}" '
                         f'height="{num(h * .07)}" rx="{num(h * .02)}" style="fill:var(--ink)" opacity=".18"/>' for k in range(5)))
    taps = _taps(p)
    rip = "".join(
        f'<g class="{uid}-tap{i + 1}" opacity="0"><circle cx="{num(x + w * tx)}" cy="{num(y + h * ty)}" r="{num(w * .09)}" '
        f'fill="#fff" fill-opacity=".35" stroke="#fff" stroke-width="{num(w * .012)}"/></g>'
        for i, (tx, ty) in enumerate(taps))
    return clip, f'<g clip-path="url(#{uid}-scr)">{img}{rip}</g>'


def _tap_actions(uid: str, n: int) -> dict:
    def mk(i):
        def fn(sel, t):
            r = _q(sel, uid, f"tap{i + 1}")
            return (f'tl.fromTo({r},{{opacity:1,scale:.2,transformOrigin:"50% 50%"}},{{opacity:0,scale:1.8,duration:.55,'
                    f'ease:"power2.out"}},{_at(t)});\n')
        return fn
    acts = {f"tap_{i + 1}": mk(i) for i in range(n)}
    acts["tap"] = acts["tap_1"]
    return acts


@prop("phone", PropDoc(
    "Смартфон с картинкой на экране (скринкаст)",
    {"src": "файл картинки для экрана (шаблон кладёт его рядом)", "color": "цвет корпуса",
     "taps": "точки касаний [[x, y], …] в долях экрана"},
    actions=["tap", "tap_1", "…", "pop"]))
def phone(p: dict, uid: str) -> PropOut:
    W, H = 380, 760
    col = _col(p, "color", INK)
    clip, scr = _screen(uid, p, 22, 22, W - 44, H - 44, 34)
    body = f'''
<g class="{uid}-dev">
 <rect x="14" y="18" width="{W}" height="{H}" rx="56" fill="#000" opacity=".25"/>
 <rect x="0" y="0" width="{W}" height="{H}" rx="56" style="fill:{col}" filter="url(#{uid}-grain)"/>
 <rect x="4" y="4" width="{W - 8}" height="{H - 8}" rx="52" fill="none" stroke="#fff" stroke-opacity=".12" stroke-width="3"/>
 {scr}
 <rect x="{W / 2 - 52}" y="36" width="104" height="26" rx="13" style="fill:{col}"/>
 <rect x="{W}" y="170" width="6" height="70" rx="3" style="fill:{col}"/>
</g>'''
    svg = _svg(uid, W, H, _grain(uid, 1.0, .25, .25) + clip, body)
    acts = _tap_actions(uid, len(_taps(p)))
    acts["pop"] = lambda sel, t: (f'tl.fromTo({_q(sel, uid, "dev")},{{opacity:0,y:120,rotation:-6,transformOrigin:"50% 100%"}},'
                                  f'{{opacity:1,y:0,rotation:0,duration:.5,ease:"back.out(1.5)"}},{_at(t)});\n')
    return PropOut(svg, "", acts, W, H)


@prop("laptop", PropDoc(
    "Ноутбук с картинкой на экране (скринкаст)",
    {"src": "файл картинки для экрана", "color": "цвет корпуса", "taps": "точки кликов [[x, y], …] в долях экрана"},
    actions=["tap", "tap_1", "…", "pop"]))
def laptop(p: dict, uid: str) -> PropOut:
    W, H = 860, 560
    col = _col(p, "color", INK)
    sx, sy, sw, sh = 92, 26, 676, 424
    clip, scr = _screen(uid, p, sx, sy, sw, sh, 6)
    g = f'filter="url(#{uid}-grain)"'
    body = f'''
<g class="{uid}-dev">
 <rect x="70" y="0" width="720" height="476" rx="22" style="fill:{col}" {g}/>
 {scr}
 <circle cx="430" cy="13" r="4" fill="#fff" opacity=".3"/>
 <path d="M0,500 L860,500 L830,540 Q826,548 812,548 L48,548 Q34,548 30,540 Z" fill="#c9c6c0" {g}/>
 <path d="M0,500 L860,500 L856,512 L4,512 Z" fill="#fff" opacity=".5"/>
 <rect x="20" y="476" width="820" height="26" rx="6" fill="#dcd9d2" {g}/>
 <rect x="370" y="476" width="120" height="12" rx="6" fill="#000" opacity=".18"/>
</g>'''
    svg = _svg(uid, W, H, _grain(uid, .9, .22, .25) + clip, body)
    acts = _tap_actions(uid, len(_taps(p)))
    acts["pop"] = lambda sel, t: (f'tl.fromTo({_q(sel, uid, "dev")},{{opacity:0,y:80,scale:.9,transformOrigin:"50% 100%"}},'
                                  f'{{opacity:1,y:0,scale:1,duration:.5,ease:"back.out(1.5)"}},{_at(t)});\n')
    return PropOut(svg, "", acts, W, H)


# ---------- 8. курсор / рука-указатель / стрелка ----------

def _path_pts(p: dict, W: float, H: float) -> list[tuple[float, float]]:
    v = p.get("path") or [[.25, .7], [.6, .4]]
    pts = []
    for xy in v:
        try:
            pts.append((float(xy[0]) * W, float(xy[1]) * H))
        except (TypeError, ValueError, IndexError):
            continue
    return pts or [(W / 2, H / 2)]


@prop("pointer", PropDoc(
    "Курсор, рука-указатель или рисованная стрелка для UI-демо: едет по точкам path",
    {"kind": "cursor / hand / arrow", "path": "точки [[x, y], …] в долях площадки (0…1)",
     "area": "размер площадки [w, h] (1000×1000)", "seg": "секунд на отрезок (0.6)", "color": "цвет"},
    actions=["move", "click", "draw"]))
def pointer(p: dict, uid: str) -> PropOut:
    area = p.get("area") or [1000, 1000]
    try:
        W, H = float(area[0]), float(area[1])
    except (TypeError, ValueError, IndexError):
        W, H = 1000.0, 1000.0
    kind = p.get("kind", "cursor")
    seg = max(.15, _f(p.get("seg"), .6))
    pts = _path_pts(p, W, H)
    x0, y0 = pts[0]
    g = f'filter="url(#{uid}-grain)"'
    if kind == "arrow":
        col = _col(p, "color", "var(--accent)")
        if len(pts) == 1:
            pts = [(pts[0][0] - 200, pts[0][1] + 120), pts[0]]
        d = f"M{num(pts[0][0])},{num(pts[0][1])}"
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            mx, my = (ax + bx) / 2, (ay + by) / 2
            nx, ny = -(by - ay) * .18, (bx - ax) * .18
            d += f" Q{num(mx + nx)},{num(my + ny)} {num(bx)},{num(by)}"
        (ax, ay), (bx, by) = pts[-2], pts[-1]
        mx, my = (ax + bx) / 2 - (by - ay) * .18, (ay + by) / 2 + (bx - ax) * .18
        ang = math.degrees(math.atan2(by - my, bx - mx))
        body = (f'<path class="{uid}-line" d="{d}" fill="none" style="stroke:{col}" stroke-width="18" '
                f'stroke-linecap="round" pathLength="1" stroke-dasharray="1" stroke-dashoffset="0"/>'
                f'<g transform="translate({num(bx)} {num(by)}) rotate({num(ang)})"><g class="{uid}-head">'
                f'<polygon points="18,0 -34,-30 -22,0 -34,30" style="fill:{col}"/></g></g>')
        svg = _svg(uid, W, H, _grain(uid, 1.0), f'<g {g}>{body}</g>')

        def draw(sel, t):
            ln, hd = _q(sel, uid, "line"), _q(sel, uid, "head")
            return (f'tl.fromTo({ln},{{strokeDashoffset:1}},{{strokeDashoffset:0,duration:{num(seg * (len(pts) - 1))},'
                    f'ease:"power2.inOut"}},{_at(t)});\n'
                    + _pop_js(hd, _plus(t, seg * (len(pts) - 1) - .1), .3))
        return PropOut(svg, "", {"draw": draw, "move": draw}, W, H)

    col = _col(p, "color", "#fff")
    if kind == "hand":
        icon = (f'<path d="M0,0 C0,-6 10,-6 10,0 L10,30 C12,24 22,24 22,32 C24,26 34,26 34,34 C36,28 46,28 46,36 '
                f'L46,62 C46,80 36,92 20,92 L6,92 C-6,92 -12,84 -18,74 L-30,50 C-34,42 -24,36 -18,44 L0,62 Z" '
                f'style="fill:{col}" stroke="{INK}" stroke-width="5" stroke-linejoin="round" transform="translate(-5 0)"/>')
    else:
        icon = (f'<path d="M0,0 L0,72 L18,56 L32,88 L46,82 L32,51 L56,51 Z" style="fill:{col}" stroke="{INK}" '
                f'stroke-width="6" stroke-linejoin="round"/>')
    body = (f'<g class="{uid}-ptr" transform="translate({num(x0)} {num(y0)})">'
            f'<g class="{uid}-ring" opacity="0"><circle r="34" fill="none" stroke="#fff" stroke-width="6"/></g>'
            f'<g class="{uid}-icon">{icon}</g></g>')
    svg = _svg(uid, W, H, "", body)

    def click(sel, t):
        ic, rg = _q(sel, uid, "icon"), _q(sel, uid, "ring")
        return (f'tl.to({ic},{{scale:.82,svgOrigin:"0 0",duration:.08,yoyo:true,repeat:1}},{_at(t)});\n'
                f'tl.fromTo({rg},{{opacity:1,scale:.3,svgOrigin:"0 0"}},{{opacity:0,scale:1.6,duration:.45,'
                f'ease:"power2.out"}},{_at(t, .06)});\n')

    def move(sel, t):
        pt = _q(sel, uid, "ptr")
        js = f'tl.set({pt},{{x:{num(x0)},y:{num(y0)}}},{_at(t)});\n'
        for k, (x, y) in enumerate(pts[1:]):
            js += f'tl.to({pt},{{x:{num(x)},y:{num(y)},duration:{num(seg)},ease:"power2.inOut"}},{_at(t, seg * k)});\n'
        end = seg * (len(pts) - 1)
        return js + click(sel, _plus(t, end))

    return PropOut(svg, "", {"move": move, "click": click}, W, H)


# ---------- декор: искра и конфетти ----------

@prop("sparkle", PropDoc(
    "Жёлтая четырёхлучевая звёздочка-искра (как в углу листа оценки)",
    {"color": "цвет (var(--accent))", "outline": "цвет обводки"},
    actions=["twinkle", "pop"]))
def sparkle(p: dict, uid: str) -> PropOut:
    W = H = 120
    col = _col(p, "color", "var(--accent)")
    ol = _col(p, "outline", "#5c4510")
    body = (f'<g class="{uid}-star" filter="url(#{uid}-grain)"><path d="{_star_path(60, 60, 52, .2)}" style="fill:{col}" '
            f'stroke="{ol}" stroke-width="3" stroke-linejoin="round"/>'
            f'<circle cx="60" cy="60" r="5" fill="{ol}"/></g>')
    svg = _svg(uid, W, H, _grain(uid, 1.2, .3, .2), body)

    def twinkle(sel, t):
        s = _q(sel, uid, "star")
        return (f'tl.fromTo({s},{{rotation:0,scale:1,transformOrigin:"50% 50%"}},{{rotation:90,duration:.9,'
                f'ease:"back.inOut(1.6)"}},{_at(t)});\n'
                f'tl.to({s},{{scale:1.3,duration:.2,yoyo:true,repeat:1,ease:"sine.inOut"}},{_at(t, .25)});\n')

    def pop(sel, t):
        return _pop_js(_q(sel, uid, "star"), t)

    return PropOut(svg, "", {"twinkle": twinkle, "pop": pop}, W, H)


@prop("confetti", PropDoc(
    "Россыпь конфетти и искр (детерминированная): разлетается из центра",
    {"count": "сколько кусочков (24)", "seed": "зерно раскладки", "colors": "список цветов"},
    actions=["burst"]))
def confetti(p: dict, uid: str) -> PropOut:
    W, H = 600, 600
    n = max(4, min(80, int(_f(p.get("count"), 24))))
    rng = random.Random(int(_f(p.get("seed"), 7)))
    cols = p.get("colors") or ["var(--accent)", "var(--alt)", "#fff", INK]
    pieces = ""
    for i in range(n):
        a = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(120, 280)
        x, y = 300 + r * math.cos(a), 300 + r * math.sin(a)
        c = esc(cols[i % len(cols)])
        rot = rng.uniform(0, 180)
        if i % 3 == 0:
            shape = f'<path d="{_star_path(0, 0, 16, .22)}" style="fill:{c}"/>'
        elif i % 3 == 1:
            shape = f'<rect x="-10" y="-5" width="20" height="10" style="fill:{c}"/>'
        else:
            shape = f'<circle r="7" style="fill:{c}"/>'
        pieces += (f'<g transform="translate({x:.1f} {y:.1f}) rotate({rot:.0f})"><g class="{uid}-pc" '
                   f'data-dx="{-r * math.cos(a):.1f}" data-dy="{-r * math.sin(a):.1f}">{shape}</g></g>')
    svg = _svg(uid, W, H, "", pieces)

    def burst(sel, t):
        pc = _q(sel, uid, "pc")
        return (f'tl.fromTo({pc},{{opacity:0,scale:0,x:(i,el)=>+el.dataset.dx,y:(i,el)=>+el.dataset.dy,'
                f'transformOrigin:"50% 50%"}},{{opacity:1,scale:1,x:0,y:0,rotation:"+=120",duration:.7,'
                f'ease:"expo.out",stagger:.01}},{_at(t)});\n'
                f'tl.to({pc},{{opacity:0,y:"+=60",duration:.5,ease:"power1.in",stagger:.01}},{_at(t, 1.2)});\n')

    return PropOut(svg, "", {"burst": burst}, W, H)
