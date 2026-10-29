"""Плакатные наклейки: stickers (строки-наклейки лесенкой), tag (одна бирка), cover (титульная карточка).

Стиль «плакат» (темы poster, poster-cream, poster-night, poster-black): каждая строка — свой прямоугольный ярлык
без скругления, тяжёлый гротеск капслоком, строки сдвинуты зигзагом, вся стопка повёрнута на −5°. Строка
«впечатывается» (slam): появляется крупной (×1.3), полупрозрачной и размытой, чуть сильнее повёрнутой и за 0.18 с
встаёт на место. Цвета ярлыков — переменные темы --sticker-*/--label-*/--accent-* (reels/themes.py).

Раскладка считается здесь же по оценке ширины строки (ширина букв Montserrat 800 в em), поэтому кегль подбирается
с запасом: самая широкая строка влезает в 900 px, стопка с поворотом — в x 40…1040, y 160…1450.
"""
from __future__ import annotations

import math

from .base import SceneCtx, SceneDoc, SceneOut, num, rich, scene
from .common import T, as_lines, kick_js, kicker, plain

X0, WIDTH, FIT_W = 80, 920, 900          # стопка: слева 80 px, ширина 920; строка с полями — не шире 900
Y_MIN, Y_MAX = 160, 1450                 # выше 150 — шапка бренда, ниже 1450 — субтитры
STEP = .18                               # шаг между строками без своего at, с
STYLES = ("white", "black", "accent", "outline", "plain")
# размер → (наибольший кегль, капслок, поля ярлыка в em: верх, бок, низ)
SIZES = {"L": (150, True, (.07, .2, .05)), "M": (96, True, (.07, .2, .05)), "S": (58, False, (.04, .3, .14))}
ZIGZAG = (0, .5, .12, .62, .3)           # доля свободного места слева у строки i (по кругу)

_WIDE = set("ШЩЖМЮФЫWMЭ@%")
_WIDE_LOW = set("шщжмюыфwm")
_NARROW = set(" .,:;!?'’\"«»()-–—/|Iil1")


def _em(text: str, caps: bool) -> float:
    """Оценка ширины строки в em (Montserrat 800, letter-spacing −0.01em); с запасом."""
    s = plain(text)
    s = s.upper() if caps else s
    w = 0.0
    for ch in s:
        if ch in _WIDE or ch in _WIDE_LOW:
            w += 1.02 if ch.isupper() else .86
        elif ch in _NARROW:
            w += .32
        elif ch.isdigit():
            w += .62
        elif ch.isupper():
            w += .8
        else:
            w += .64
    return max(w, .5)


def _num(v, name: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: нужно число, а не «{v}»") from None


def _item(raw, size: str = "L", style: str = "white") -> dict:
    it = dict(raw) if isinstance(raw, dict) else {"text": raw}
    it["text"] = str(it.get("text") or "")
    if not it["text"].strip():
        raise ValueError("пустая строка наклейки")
    it["size"] = str(it.get("size") or size).upper()
    it["style"] = str(it.get("style") or style).lower()
    if it["size"] not in SIZES:
        raise ValueError(f"size: L, M или S, а не «{it['size']}»")
    if it["style"] not in STYLES:
        raise ValueError(f"style: {', '.join(STYLES)}, а не «{it['style']}»")
    it["shift"] = _num(it.get("shift", 0), "shift")
    return it


def _caps(it: dict) -> bool:
    return bool(it.get("caps", SIZES[it["size"]][1]))


def _layout(items: list[dict], p: dict, kick: bool) -> tuple[list[dict], float, float, float]:
    """Кегль, x, y каждой строки внутри стопки. → (строки, верх стопки, высота стопки, наклон)."""
    tilt = max(-12.0, min(12.0, _num(p.get("tilt", -5), "tilt")))
    align = str(p.get("align", "left"))
    stagger = str(p.get("stagger", "zigzag"))
    if align not in ("left", "center"):
        raise ValueError(f"align: left или center, а не «{align}»")
    if stagger not in ("zigzag", "stairs", "none"):
        raise ValueError(f"stagger: zigzag, stairs или none, а не «{stagger}»")
    # общий кегль на класс размера: самая широкая строка класса влезает в FIT_W
    size: dict[str, float] = {}
    for it in items:
        base, _, (_, px, _) = SIZES[it["size"]]
        fs = min(base, FIT_W / (_em(it["text"], _caps(it)) + 2 * px))
        size[it["size"]] = min(size.get(it["size"], base), fs)
    sin = math.sin(math.radians(abs(tilt)))
    ext = WIDTH / 2 * sin + 8                     # на сколько поворот поднимает/опускает углы стопки
    avail = Y_MAX - 20 - (Y_MIN + 100 if kick else Y_MIN) - 2 * ext
    for _ in range(2):                            # вторая попытка — с уменьшенным кеглем, если стопка не влезла
        y, out = 0.0, []
        for i, it in enumerate(items):
            fs = size[it["size"]]
            _, _, (pt, px, pb) = SIZES[it["size"]]
            pad_x = 0 if it["style"] == "plain" else px
            w = fs * (_em(it["text"], _caps(it)) + 2 * pad_x)
            h = fs * (1 + pt + pb)
            if i:
                prev = out[-1]
                y += 4 if prev["size"] == it["size"] == "L" else round(.12 * min(fs, prev["fs"]) + 6)
            out.append({**it, "fs": fs, "w": w, "h": h, "y": y})
            y += h
        if y <= avail:
            break
        k = avail / y
        size = {c: s * k for c, s in size.items()}
    height = y
    n = len(out)
    for i, it in enumerate(out):
        slack = max(0.0, WIDTH - it["w"])
        if align == "center":
            x = slack / 2 + (0 if stagger == "none" else (1 if i % 2 else -1) * min(40.0, slack / 2))
        elif stagger == "zigzag":
            x = slack * ZIGZAG[i % len(ZIGZAG)]
        elif stagger == "stairs":
            x = slack * (i / (n - 1) if n > 1 else 0) * .9
        else:
            x = 0.0
        it["x"] = max(0.0, min(x + it["shift"], slack))
    pos = p.get("position", "center")
    lo = (Y_MIN + 100 if kick else Y_MIN) + ext
    hi = Y_MAX - ext - height
    if pos == "top":
        top = max(lo, 280.0)
    elif pos == "center":
        top = 820 - height / 2
    elif pos == "bottom":
        top = hi - 30
    else:
        try:
            top = float(pos)
        except (TypeError, ValueError):
            raise ValueError(f"position: top, center, bottom или y в px, а не «{pos}»") from None
    return out, max(lo, min(top, hi)), height, tilt


def _css(ctx: SceneCtx) -> str:
    s = ctx.sel
    return "\n".join([
        f"{s} .stk{{position:absolute;transform-origin:50% 50%}}",
        f"{s} .ln{{position:absolute;left:0;top:0;white-space:nowrap;font-family:var(--font);font-weight:800;"
        f"line-height:1;letter-spacing:-.01em;background:var(--sticker-bg);color:var(--sticker-ink);"
        f"transform-origin:50% 50%}}",
        f"{s} .ln span{{display:block}}",
        f"{s} .ln.L,{s} .ln.M,{s} .ln.up{{text-transform:uppercase}}",
        f"{s} .ln.L,{s} .ln.M{{padding:.07em .2em .05em}}",
        f"{s} .ln.S{{padding:.04em .3em .14em}}",
        f"{s} .ln.black{{background:var(--label-bg);color:var(--label-ink)}}",
        f"{s} .ln.accent{{background:var(--accent-bg);color:var(--accent-ink)}}",
        f"{s} .ln.outline{{background:transparent;color:var(--ink);box-shadow:inset 0 0 0 .07em currentColor}}",
        f"{s} .ln.plain{{background:transparent;color:var(--ink);padding-left:0;padding-right:0}}",
        f"{s} .ln em{{font-style:normal;color:var(--sticker-em)}}",
        f"{s} .ln.black em,{s} .ln.outline em,{s} .ln.plain em{{color:var(--alt)}}",
        f"{s} .ln.accent em{{color:var(--sticker-ink)}}",
    ])


def _cls(it: dict) -> str:
    return f'{it["size"]} {it["style"]}' + (" up" if it.get("caps") and it["size"] == "S" else "")


def slam_js(sel: str, at: float) -> str:
    """«Впечатывание»: крупно (×1.32), полупрозрачно, размыто, повёрнуто — и за 0.18 с на место."""
    return (f'tl.fromTo(S+" {sel}",{{opacity:.3,scale:1.32,rotation:-4}},'
            f'{{opacity:1,scale:1,rotation:0,duration:.18,ease:"power2.in"}},{T(at)});\n')


def wipe_js(sel: str, at: float) -> str:
    return (f'tl.fromTo(S+" {sel}",{{clipPath:"inset(0% 100% 0% 0%)"}},'
            f'{{clipPath:"inset(0% 0% 0% 0%)",duration:.32,ease:"power3.out"}},{T(at)});\n')


def pop_js(sel: str, at: float) -> str:
    return (f'tl.fromTo(S+" {sel}",{{opacity:0,scale:.2,rotation:-14}},'
            f'{{opacity:1,scale:1,rotation:0,duration:.3,ease:"back.out(2.6)"}},{T(at)});\n')


def _stack(ctx: SceneCtx, items: list[dict], ts: list[float], p: dict, anims: list[str]) -> SceneOut:
    """Стопка наклеек: html/css/js. anims — slam | wipe | pop для каждой строки."""
    out, top, height, tilt = _layout(items, p, bool(p.get("kicker")))
    html, css, js = [], [_css(ctx)], [kick_js(p)]
    for i, it in enumerate(out):
        html.append(f'<div class="ln ln{i} {_cls(it)}"><span>{rich(it["text"])}</span></div>')
        css.append(f'{ctx.sel} .ln{i}{{left:{it["x"]:.0f}px;top:{it["y"]:.0f}px;font-size:{it["fs"]:.0f}px}}')
        fn = {"slam": slam_js, "wipe": wipe_js, "pop": pop_js}[anims[i]]
        js.append(fn(f".ln{i}", ts[i]))
    css.append(f"{ctx.sel} .stk{{left:{X0}px;top:{top:.0f}px;width:{WIDTH}px;height:{height:.0f}px;"
               f"transform:rotate({num(tilt)}deg)}}")
    return SceneOut(kicker(p) + f'<div class="stk">{"".join(html)}</div>', "\n".join(css), "".join(js))


def _times(ctx: SceneCtx, items: list[dict], at, start: float = .3, step: float = STEP) -> list[float]:
    """Момент каждой строки: свой at строки, иначе предыдущая + step; первая — по общему at."""
    ts: list[float] = []
    for i, it in enumerate(items):
        dflt = ctx.at(at, start) if i == 0 else ts[-1] + it.get("_gap", step)
        ts.append(ctx.at(it.get("at"), dflt) if it.get("at") is not None else dflt)
    return ts


@scene("stickers", SceneDoc(
    "Плакатные наклейки: каждая строка — свой ярлык (белый с чёрным текстом капслоком), строки зигзагом, "
    "стопка повёрнута на −5°; строки «впечатываются» по очереди. Для тем poster*, работает и в остальных.",
    {"lines": "строки: список строк или {text, at, style, size, shift}; *слово* — акцентом. Обязательно. "
              "style: white (основная) | black (бирка) | accent | outline | plain (без ярлыка); "
              "size: L (крупно, капслок) | M | S (мелко, регистр как написан); shift: сдвиг вправо, px",
     "at": "якорь первой строки; дальше каждая +0.18 с (у строки свой at важнее); по умолчанию 0.3 с",
     "step": "шаг между строками без своего at, с; по умолчанию 0.18",
     "tilt": "наклон стопки, градусы; по умолчанию −5",
     "position": "top | center | bottom | y верха в px; по умолчанию center",
     "align": "left | center; по умолчанию left",
     "stagger": "zigzag | stairs | none — сдвиг строк по горизонтали; по умолчанию zigzag",
     "slam": "true (по умолчанию) — строки впечатываются; false — проявляются слева направо",
     "kicker": "надзаголовок; необязательно"},
    {"lines": [{"text": "Что прячет", "at": "что прячет"}, "городская", "*кабинка?*",
               {"text": "тук-тук", "size": "S", "style": "black", "shift": 420, "at": "тук-тук"}],
     "position": "top"}))
def stickers(ctx: SceneCtx, p: dict) -> SceneOut:
    raw = p.get("lines") or p.get("text")
    if isinstance(raw, str):
        raw = as_lines(raw)
    if not raw:
        raise ValueError("нужен lines")
    items = [_item(x) for x in raw]
    step = _num(p.get("step", STEP), "step")
    for it in items:
        it["_gap"] = step
    ts = _times(ctx, items, p.get("at"))
    slam = p.get("slam", True) not in (False, "false", "no", 0)
    # мелкие тёмные бирки выскакивают, остальные строки впечатываются (или проявляются при slam: false)
    anims = ["pop" if it["size"] == "S" and it["style"] != "white" else "slam" if slam else "wipe" for it in items]
    return _stack(ctx, items, ts, p, anims)


@scene("tag", SceneDoc(
    "Одна маленькая бирка (чёрный ярлык, белый текст как написан) в точке x, y: выскакивает и может "
    "покачаться — «тук-тук», «БАХ», «теневая архитектура» рядом с иллюстрацией.",
    {"text": "текст бирки; *слово* — акцентом. Обязательно",
     "x": "левый край, px; по умолчанию по центру кадра",
     "y": "верх, px; по умолчанию 1180",
     "tilt": "наклон, градусы; по умолчанию −6",
     "style": "black (по умолчанию) | white | accent | outline",
     "size": "S (по умолчанию) | M | L",
     "at": "якорь появления; по умолчанию 0.3 с",
     "anim": "pop (по умолчанию, выскакивает) | slam (впечатывается)",
     "wiggle": "покачаться после появления: true (4 раза) или число качаний; по умолчанию нет"},
    {"text": "тук-тук", "x": 640, "y": 1100, "at": "тук-тук", "wiggle": True}))
def tag(ctx: SceneCtx, p: dict) -> SceneOut:
    it = _item({"text": p.get("text"), "style": p.get("style") or "black", "size": p.get("size") or "S"})
    base, caps, (pt, px, pb) = SIZES[it["size"]]
    fs = min(base, (1040 - 40) / (_em(it["text"], caps) + 2 * px))
    w, h = fs * (_em(it["text"], caps) + 2 * px), fs * (1 + pt + pb)
    tilt = max(-30.0, min(30.0, _num(p.get("tilt", -6), "tilt")))
    x = _num(p["x"], "x") if p.get("x") is not None else 540 - w / 2
    y = _num(p.get("y", 1180), "y")
    x = max(40.0, min(x, 1040 - w))
    y = max(float(Y_MIN), min(y, Y_MAX - h - 10))
    t0 = ctx.at(p.get("at"), .3)
    anim = str(p.get("anim", "pop"))
    if anim not in ("pop", "slam"):
        raise ValueError(f"anim: pop или slam, а не «{anim}»")
    html = f'<div class="stk"><div class="ln ln0 {_cls(it)}"><span>{rich(it["text"])}</span></div></div>'
    css = _css(ctx) + (f"\n{ctx.sel} .stk{{left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px;"
                       f"transform:rotate({num(tilt)}deg)}}\n{ctx.sel} .ln0{{font-size:{fs:.0f}px}}")
    js = (slam_js if anim == "slam" else pop_js)(".ln0", t0)
    wig = p.get("wiggle")
    if wig not in (None, False, 0, "false", "no"):
        n = 4 if wig is True or str(wig).lower() == "true" else max(1, min(20, int(_num(wig, "wiggle"))))
        js += (f'tl.fromTo(S+" .ln0 span",{{rotation:-6}},{{rotation:6,duration:.08,repeat:{2 * n - 1},yoyo:true,'
               f'ease:"sine.inOut"}},{T(t0 + .32)});\n'
               f'tl.set(S+" .ln0 span",{{rotation:0}},{T(t0 + .32 + .16 * n + .02)});\n')
    return SceneOut(kicker(p) + html, css, js)


@scene("cover", SceneDoc(
    "Титульная карточка-плакат: крупные наклейки названия, строка пояснения без ярлыка, бирка, второе название "
    "и мелкие бирки-теги снизу; всё впечатывается по очереди, стопка повёрнута на −5°.",
    {"title": "крупные строки названия (список или строка с \\n); *слово* — акцентом. Обязательно",
     "sub": "пояснение под названием: белый капслок без ярлыка (строка с \\n или список); необязательно",
     "label": "маленькая тёмная бирка перед вторым названием («воркшоп»); необязательно",
     "title2": "второе название (строки), крупно; необязательно",
     "tags": "мелкие тёмные бирки в конце (список строк, «теневая архитектура»); необязательно",
     "at": "якорь первой строки; по умолчанию 0.3 с",
     "sub_at": "якорь пояснения; по умолчанию после названия",
     "title2_at": "якорь второго названия; по умолчанию после бирки",
     "tags_at": "якорь бирок; по умолчанию после второго названия",
     "accent": "сколько последних строк title2 сделать акцентными (style accent); по умолчанию 0",
     "tilt": "наклон, градусы; по умолчанию −5",
     "position": "top | center | bottom | y в px; по умолчанию center",
     "kicker": "надзаголовок; необязательно"},
    {"title": ["Открытый", "город", "2026"], "sub": "Фестиваль архитектурного\nобразования и карьеры",
     "label": "воркшоп", "title2": ["Ассинтезия", "города"], "tags": ["теневая архитектура"],
     "at": "открытый город", "title2_at": "ассинтезия", "tags_at": "теневая"}))
def cover(ctx: SceneCtx, p: dict) -> SceneOut:
    title = as_lines(p.get("title"))
    if not title:
        raise ValueError("нужен title")
    items: list[dict] = []
    anims: list[str] = []
    marks: dict[int, str] = {}               # индекс строки → ключ её якоря

    def add(text, size, style, anim, gap=STEP, caps=None, mark=None):
        it = _item({"text": text, "size": size, "style": style})
        it["_gap"] = gap
        if caps is not None:
            it["caps"] = caps
        if mark and mark not in marks.values():
            marks[len(items)] = mark
        items.append(it)
        anims.append(anim)

    for ln in title:
        add(ln, "L", "white", "slam")
    for i, ln in enumerate(as_lines(p.get("sub"))):
        add(ln, "S", "plain", "wipe", gap=.12 if i else .3, caps=True, mark="sub_at")
    if p.get("label"):
        add(p["label"], "S", "black", "pop", gap=.3, caps=True)
    t2 = as_lines(p.get("title2"))
    n_acc = int(_num(p.get("accent", 0), "accent"))
    for i, ln in enumerate(t2):
        add(ln, "L", "accent" if i >= len(t2) - n_acc else "white", "slam", gap=.35 if i == 0 else STEP,
            mark="title2_at")
    for ln in as_lines(p.get("tags")):
        add(ln, "S", "black", "pop", gap=.35, mark="tags_at")
    for i, key in marks.items():
        if p.get(key) is not None:
            items[i]["at"] = p[key]
    ts = _times(ctx, items, p.get("at"))
    return _stack(ctx, items, ts, p, anims)
