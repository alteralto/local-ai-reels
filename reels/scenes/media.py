"""Медиа-сцены: картинка (image), демонстрация интерфейса по скриншоту (screen), видеоклип (video).

Файлы берутся из папки проекта (пути в параметрах — от ctx.project_dir) и попадают в папку монтажа через
SceneOut.files под именем «<uid>-<имя>», на него и ссылается HTML сцены.

Видео: HyperFrames 0.8.75 сам вытаскивает кадры <video class="clip"> (фаза video_extract) и подставляет их
при съёмке; вложенный в div сцены <video> работает, предки можно двигать GSAP. data-start у <video> —
абсолютное время монтажа (не от начала сцены), data-media-start — с какой секунды файла, data-playback-rate —
скорость 0.1…10. У видео обязателен id.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

from .base import SceneCtx, SceneDoc, SceneError, SceneOut, esc, num, rich, scene
from .common import T, as_lines, fit, kick_js, kicker, reveal, times

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}
FRAMES = ("none", "card", "phone", "laptop")
ENTERS = ("fade", "rise", "slam", "slide", "none")
LEFT, RIGHT, TOP, BOTTOM = 40, 1040, 160, 1450      # где может стоять главный объект кадра
PAD = {"none": 0, "card": 0, "phone": 20, "laptop": 18}   # толщина рамки вокруг картинки
CURSOR = 54          # высота стрелки курсора на экране, px
# стрелка курсора 22×32: тёмная с белой обводкой — видна и на светлом, и на тёмном интерфейсе
ARROW = ('<svg viewBox="-2 -2 26 36"><path d="M0 0 L0 27 L7 20.5 L12 31.5 L16.5 29.5 L11.5 18.8 L20.5 18.8 Z" '
         'fill="#111" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/></svg>')


# --- файлы --------------------------------------------------------------------------------------------
def _src(ctx: SceneCtx, p: dict, exts: set[str], key: str = "src") -> tuple[Path, str]:
    """Файл из параметра key: (путь в проекте, имя в папке монтажа)."""
    if not p.get(key):
        raise ValueError(f"нужен {key} — путь к файлу от папки проекта")
    rel = str(p[key])
    f = Path(rel) if Path(rel).is_absolute() else ctx.project_dir / rel
    if f.suffix.lower() not in exts:
        raise SceneError(f"блок {ctx.block}: {key} «{rel}» — нужен файл {', '.join(sorted(exts))}")
    if not f.is_file():
        raise SceneError(f"блок {ctx.block}: нет файла {f}")
    stem = re.sub(r"[^a-z0-9]+", "-", f.stem.lower()).strip("-")[:40] or "media"
    return f, f"{ctx.uid}-{stem}{f.suffix.lower()}"


def image_size(f: Path) -> tuple[float, float]:
    """Ширина и высота картинки по заголовку файла (PNG, JPEG, GIF, WebP, SVG) — без Pillow."""
    data = f.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"GIF8":
        return struct.unpack("<HH", data[6:10])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if kind == b"VP8L":
            b = int.from_bytes(data[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xD8, 0x01, 0xFF) or 0xD0 <= m <= 0xD7:
                i += 2 if m != 0xFF else 1
                continue
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    if f.suffix.lower() == ".svg":
        head = data[:4000].decode("utf-8", "ignore")
        vb = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', head)
        if vb:
            return float(vb.group(1)), float(vb.group(2))
        w = re.search(r'\swidth\s*=\s*["\']([\d.]+)(px)?["\']', head)
        h = re.search(r'\sheight\s*=\s*["\']([\d.]+)(px)?["\']', head)
        if w and h:
            return float(w.group(1)), float(h.group(1))
        return 1000.0, 1000.0
    raise SceneError(f"не удалось прочитать размер картинки {f}")


def video_info(f: Path) -> tuple[float, float, float | None]:
    """(ширина, высота, длительность) видео через ffprobe; без ffprobe — 1080×1920 и длительность неизвестна."""
    if not shutil.which("ffprobe"):
        return 1080.0, 1920.0, None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "json", str(f)],
                           capture_output=True, text=True, timeout=20, check=True)
        info = json.loads(r.stdout)
        st = (info.get("streams") or [{}])[0]
        dur = float(info.get("format", {}).get("duration") or 0) or None
        return float(st["width"]), float(st["height"]), dur
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return 1080.0, 1920.0, None


# --- общая раскладка: заголовок-стикер, рамка, появление ------------------------------------------------
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _title(ctx: SceneCtx, p: dict) -> tuple[str, str, str, float]:
    """Строки-стикеры над медиа (как в промо: плашка цвета акцента). (html, css, js, низ заголовка)."""
    lines = as_lines(p.get("title"))
    html, css, js = kicker(p), [], kick_js(p)
    y = 205.0 if p.get("kicker") else 175.0
    if not lines:
        return html, "", js, (195.0 if p.get("kicker") else TOP)
    size = min(fit(x, float(p.get("title_size", 78)), width=880, k=.6, lo=36) for x in lines)
    lh = round(size * 1.28)
    for i, (ln, at) in enumerate(zip(lines, times(ctx, p.get("title_at"), len(lines), .2, .35))):
        html += f'<div class="big stk tl{i}"><span>{rich(ln)}</span></div>'
        css.append(f"{ctx.sel} .tl{i}{{top:{y:.0f}px;font-size:{size}px;height:{lh}px;"
                   f"left:{70 + (i % 2) * 34}px}}")
        js += reveal(f".tl{i}", at)
        y += lh + 4
    css.append(f"{ctx.sel} .stk{{right:auto;padding:0 6px;transform:rotate(-2deg);transform-origin:0 50%}}\n"
               f"{ctx.sel} .stk span{{background:var(--accent);color:var(--deep);padding:.06em .22em .1em;"
               f"line-height:1.1}}\n"
               f"{ctx.sel} .stk em{{color:var(--accent);background:var(--deep);padding:0 .12em}}")
    return html, "\n".join(css), js, y + 30


def _box(p: dict, frame: str, aspect: float, top: float, default_w: float = 920) -> tuple[float, float, float, float]:
    """(x, y, w, h) содержимого рамки (без её толщины) в кадре; по умолчанию — по центру по горизонтали,
    по вертикали — в середине свободной зоны под заголовком. aspect = высота / ширина."""
    pad = PAD[frame]
    extra = 50 if frame == "laptop" else 0               # подставка ноутбука шире экрана
    w = float(p.get("w") or default_w)
    w = _clamp(w, 120, RIGHT - LEFT - 2 * (pad + extra))
    h = float(p["h"]) if p.get("h") else w * aspect
    room = BOTTOM - top - 2 * pad - (34 if frame == "laptop" else 0)
    if h > room:                                          # высокая картинка: ужимаем по высоте
        if not p.get("h") and not p.get("w"):
            w, h = max(120.0, room / aspect), room
        h = min(h, room)
    x = float(p["x"]) if p.get("x") is not None else (1080 - w) / 2
    y = float(p["y"]) if p.get("y") is not None else top + pad + max(0.0, (room - h) / 2)
    x = _clamp(x, LEFT + pad + extra, RIGHT - pad - extra - w)
    y = _clamp(y, TOP + pad, BOTTOM - pad - h)
    return x, y, w, h


def _frame_css(ctx: SceneCtx, frame: str, x: float, y: float, w: float, h: float) -> str:
    s, pad = ctx.sel, PAD[frame]
    css = [f"{s} .med{{position:absolute;left:{x - pad:.0f}px;top:{y - pad:.0f}px;width:{w + 2 * pad:.0f}px;"
           f"height:{h + 2 * pad:.0f}px;padding:{pad}px}}",
           f"{s} .vp{{position:relative;width:{w:.0f}px;height:{h:.0f}px;overflow:hidden}}",
           f"{s} .shot{{position:absolute;left:0;top:0;transform-origin:0 0}}",
           f"{s} .shot img,{s} .shot video{{display:block}}"]
    if frame == "card":
        css += [f"{s} .med{{background:#fff;border-radius:22px;box-shadow:0 36px 90px rgba(0,0,0,.5)}}",
                f"{s} .vp{{border-radius:22px;background:#fff}}"]
    elif frame == "phone":
        css += [f"{s} .med{{background:#0d0e12;border-radius:62px;box-shadow:0 36px 90px rgba(0,0,0,.5),"
                f"inset 0 0 0 3px #3a3d46}}",
                f"{s} .vp{{border-radius:44px;background:#000}}",
                f"{s} .notch{{position:absolute;left:50%;top:{pad + 16}px;width:150px;height:36px;margin-left:-75px;"
                f"border-radius:18px;background:#0d0e12;z-index:3}}"]
    elif frame == "laptop":
        css += [f"{s} .med{{background:#1b1d23;border-radius:26px 26px 6px 6px;box-shadow:0 36px 90px rgba(0,0,0,.5)}}",
                f"{s} .vp{{border-radius:6px;background:#fff}}",
                f"{s} .base{{position:absolute;left:-50px;right:-50px;bottom:-34px;height:34px;"
                f"border-radius:4px 4px 30px 30px;background:linear-gradient(#e3e6ec,#9da3ad)}}",
                f"{s} .base::after{{content:\"\";position:absolute;left:50%;top:0;width:160px;height:12px;"
                f"margin-left:-80px;border-radius:0 0 12px 12px;background:#b7bcc5}}"]
    else:
        css.append(f"{s} .vp{{border-radius:{0 if (w >= 1080 and h >= 1920) else 18}px}}")
    return "\n".join(css)


def _frame_html(frame: str, inner: str) -> str:
    deco = {"phone": '<i class="notch"></i>', "laptop": '<i class="base"></i>'}.get(frame, "")
    return f'<div class="med"><div class="vp">{inner}</div>{deco}</div>'


def _enter_js(kind: str, at: float) -> str:
    if kind not in ENTERS:
        raise ValueError(f"enter: {kind} — допустимо {', '.join(ENTERS)}")
    sel, pos = 'S+" .med"', T(at)
    return {
        "fade": f'tl.fromTo({sel},{{opacity:0}},{{opacity:1,duration:.5,ease:"power1.out"}},{pos});\n',
        "rise": f'tl.fromTo({sel},{{opacity:0,y:140}},{{opacity:1,y:0,duration:.6,ease:"power3.out"}},{pos});\n',
        "slam": (f'tl.fromTo({sel},{{opacity:0,scale:1.45,rotation:-5}},{{opacity:1,scale:1,rotation:0,duration:.42,'
                 f'ease:"back.out(1.4)"}},{pos});\n'),
        "slide": f'tl.fromTo({sel},{{x:1120}},{{x:0,duration:.6,ease:"expo.out"}},{pos});\n',
        "none": "",
    }[kind]


def _frame_of(p: dict, default: str) -> str:
    frame = str(p.get("frame") or default)
    if frame not in FRAMES:
        raise ValueError(f"frame: {frame} — допустимо {', '.join(FRAMES)}")
    return frame


def _pair(v, name: str, n: int) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ValueError(f"{name}: нужен список из {n} чисел")
    return [float(x) for x in v]


# --- image ---------------------------------------------------------------------------------------------
@scene("image", SceneDoc(
    "Картинка (png/jpg/webp/svg): на весь кадр или в рамке (карточка, телефон, ноутбук); медленный наезд или "
    "Ken Burns; сверху 1–2 строки-стикера.",
    {"src": "путь к картинке от папки проекта. Обязательно",
     "frame": "none (по умолчанию) | card (белая карточка с тенью) | phone | laptop",
     "fit": "cover (по умолчанию: заполнить, обрезая края) | contain (вписать целиком)",
     "x, y, w, h": "положение и размер картинки в кадре, px; без w при frame none — на весь кадр 1080×1920, "
                   "иначе ширина 920 по центру, высота — по пропорциям картинки",
     "zoom": "медленный наезд за время блока: [1, 1.15] (масштаб в начале и в конце)",
     "kenburns": "наезд с панорамой: {from: [масштаб, x%, y%], to: [масштаб, x%, y%]} — точка картинки (в %) "
                 "в центре кадра; вместо zoom",
     "enter": "появление: fade (по умолчанию) | rise | slam | slide | none",
     "at": "якорь появления; по умолчанию 0.15 с",
     "title": "1–2 строки-стикера над картинкой; *слово* — инверсией; необязательно",
     "title_at": "якорь заголовка или список якорей по строкам",
     "kicker": "надзаголовок; необязательно"},
    {"src": "docs/media/gallery/stat.png", "frame": "card", "title": ["Так выглядит", "*сцена stat*"],
     "title_at": ["выглядит", "сцена"], "enter": "rise", "at": "выглядит", "zoom": [1, 1.12]}))
def image(ctx: SceneCtx, p: dict) -> SceneOut:
    f, name = _src(ctx, p, IMAGE_EXT)
    iw, ih = image_size(f)
    frame = _frame_of(p, "none")
    fit_ = str(p.get("fit") or "cover")
    if fit_ not in ("cover", "contain"):
        raise ValueError("fit: cover или contain")
    html, css, js, top = _title(ctx, p)
    full = frame == "none" and not p.get("w")
    if full:
        x, y, w, h = 0.0, 0.0, 1080.0, 1920.0
    else:
        x, y, w, h = _box(p, frame, ih / iw, top)
    t0 = ctx.at(p.get("at"), .15)
    span = max(1.0, ctx.length + .6 - t0)               # движение камеры — до конца блока (и чуть дальше склейки)
    img_css = f"{ctx.sel} .shot{{width:{w:.0f}px;height:{h:.0f}px}}\n" \
              f"{ctx.sel} .shot img{{width:100%;height:100%;object-fit:{fit_}}}"
    move = ""
    if p.get("kenburns"):
        kb = p["kenburns"]
        if not isinstance(kb, dict):
            raise ValueError("kenburns: {from: [масштаб, x%, y%], to: [масштаб, x%, y%]}")
        a, b = _pair(kb.get("from", [1, 50, 50]), "kenburns.from", 3), _pair(kb.get("to", [1.2, 50, 50]), "kenburns.to", 3)

        def cam(s, fx, fy):
            s = _clamp(s, 1, 4)
            return (_clamp(w / 2 - s * w * fx / 100, w - s * w, 0), _clamp(h / 2 - s * h * fy / 100, h - s * h, 0), s)
        (ax, ay, as_), (bx, by, bs) = cam(*a), cam(*b)
        img_css += f"\n{ctx.sel} .shot{{transform:translate({ax:.1f}px,{ay:.1f}px) scale({num(as_)})}}"
        move = (f'tl.fromTo(S+" .shot",{{x:{num(ax)},y:{num(ay)},scale:{num(as_)}}},{{x:{num(bx)},y:{num(by)},'
                f'scale:{num(bs)},duration:{num(span)},ease:"sine.inOut"}},{T(t0)});\n')
    elif p.get("zoom"):
        z0, z1 = _pair(p["zoom"], "zoom", 2)
        img_css += f"\n{ctx.sel} .shot{{transform-origin:50% 50%}}"
        move = (f'tl.fromTo(S+" .shot",{{scale:{num(_clamp(z0, .5, 4))}}},{{scale:{num(_clamp(z1, .5, 4))},'
                f'duration:{num(span)},ease:"none"}},{T(t0)});\n')
    html += _frame_html(frame, f'<div class="shot"><img src="{esc(name)}" alt=""></div>')
    css = "\n".join(x for x in (css, _frame_css(ctx, frame, x, y, w, h), img_css) if x)
    js += _enter_js(str(p.get("enter") or "fade"), t0) + move
    return SceneOut(html, css, js, {name: f})


# --- screen --------------------------------------------------------------------------------------------
def _region(v, iw: float, ih: float, name: str) -> tuple[float, float, float, float]:
    rx, ry, rw, rh = _pair(v, name, 4)
    rx, ry = _clamp(rx, 0, iw - 1), _clamp(ry, 0, ih - 1)
    return rx, ry, _clamp(rw, 1, iw - rx), _clamp(rh, 1, ih - ry)


@scene("screen", SceneDoc(
    "Демонстрация интерфейса по скриншоту (можно длинному): карточка с тенью, камера плавно наезжает на части "
    "экрана по якорям, курсор-стрелка ходит и кликает (круги от клика), рамки подсветки. Сверху 1–2 строки-стикера.",
    {"src": "путь к скриншоту от папки проекта (png/jpg/webp/svg). Обязательно",
     "frame": "card (по умолчанию) | none | phone | laptop",
     "x, y, w, h": "окно экрана в кадре, px; по умолчанию ширина 920 по центру, высота — по пропорциям, "
                   "но не ниже зоны 1450 (длинный скриншот показывается сверху и прокручивается фокусом)",
     "focus": "шаги камеры: [{at, region: [x, y, w, h] в пикселях скриншота, zoom: 1…3 (по умолчанию — "
              "вписать region), dur: длительность перехода, с (0.9)}]; region на всю ширину и zoom 1 — прокрутка",
     "cursor": "курсор: [{at, x, y, click: true}] — в пикселях скриншота; к точке приходит к моменту at, "
               "click — круг от клика",
     "highlight": "рамки акцентом: [{at, region: [x, y, w, h], until: якорь исчезновения, dim: true — "
                  "затемнить остальное}]",
     "enter": "появление карточки: rise (по умолчанию) | fade | slam | slide | none",
     "at": "якорь появления; по умолчанию 0.15 с",
     "title": "1–2 строки-стикера над экраном; *слово* — инверсией; необязательно",
     "title_at": "якорь заголовка или список якорей",
     "kicker": "надзаголовок; необязательно"},
    {"src": "app/desktop/screenshot.png", "title": ["Один клик —", "*ролик готов*"], "title_at": ["клик", "готов"],
     "h": 860,
     "focus": [{"at": "этапы", "region": [300, 125, 1050, 60]},
               {"at": "дубли", "region": [310, 190, 1030, 300]},
               {"at": "подтвердить", "region": [310, 400, 440, 100], "zoom": 2}],
     "cursor": [{"at": "дубли", "x": 1260, "y": 333}, {"at": "подтвердить", "x": 445, "y": 465, "click": True}],
     "highlight": [{"at": "дубли", "region": [326, 264, 990, 46], "until": "подтвердить"}]}))
def screen(ctx: SceneCtx, p: dict) -> SceneOut:
    f, name = _src(ctx, p, IMAGE_EXT)
    iw, ih = map(float, image_size(f))
    frame = _frame_of(p, "card")
    html, css, js, top = _title(ctx, p)
    pad = PAD[frame]
    # высота окна по умолчанию — по пропорциям, но в свободной зоне; длинный скриншот обрезается снизу
    q = dict(p)
    if not q.get("h"):
        w0 = _clamp(float(q.get("w") or 920), 120, RIGHT - LEFT - 2 * pad)
        q["w"], q["h"] = w0, min(w0 * ih / iw, BOTTOM - top - 2 * pad - (34 if frame == "laptop" else 0))
    x, y, vw, vh = _box(q, frame, ih / iw, top)
    s0 = max(vw / iw, vh / ih)                          # масштаб «без зума»: скриншот закрывает всё окно

    def cam(region, zoom):
        rx, ry, rw, rh = region
        z = _clamp(float(zoom), 1, 3) if zoom is not None else _clamp(min(vw / (rw * s0), vh / (rh * s0)), 1, 3)
        s = s0 * z
        tx = _clamp(vw / 2 - s * (rx + rw / 2), vw - s * iw, 0)
        ty = _clamp(vh / 2 - s * (ry + rh / 2), vh - s * ih, 0)
        return tx, ty, s, z

    # начало: верх скриншота, по центру по горизонтали
    cur_cam = cam((0, 0, iw, min(ih, vh / s0)), 1)
    shot_css = (f"{ctx.sel} .shot{{width:{iw:.0f}px;height:{ih:.0f}px;"
                f"transform:translate({cur_cam[0]:.1f}px,{cur_cam[1]:.1f}px) scale({num(cur_cam[2])})}}\n"
                f"{ctx.sel} .shot img{{width:{iw:.0f}px;height:{ih:.0f}px}}")
    t0 = ctx.at(p.get("at"), .15)
    move = ""
    steps = list(p.get("focus") or [])
    stimes = [ctx.at(st.get("at"), t0 + .8 + i) for i, st in enumerate(steps)]
    zooms: list[tuple[float, float, float]] = []      # (время, длительность, зум) — для курсора
    for i, (st, a) in enumerate(zip(steps, stimes)):
        if "region" not in st:
            raise ValueError(f"focus[{i}]: нужен region [x, y, w, h]")
        nxt = cam(_region(st["region"], iw, ih, f"focus[{i}].region"), st.get("zoom"))
        d = float(st.get("dur", .9))
        if i + 1 < len(stimes):
            d = min(d, max(.25, stimes[i + 1] - a))
        a = max(a, t0 + .3)
        move += (f'tl.fromTo(S+" .shot",{{x:{num(cur_cam[0])},y:{num(cur_cam[1])},scale:{num(cur_cam[2])}}},'
                 f'{{x:{num(nxt[0])},y:{num(nxt[1])},scale:{num(nxt[2])},duration:{num(d)},ease:"power2.inOut"}},'
                 f'{T(a)});\n')
        zooms.append((a, d, nxt[3]))
        cur_cam = nxt

    over = ""                                            # курсор, клики и рамки — внутри .shot, в пикселях скриншота
    over_css = []
    k = 1 / s0                                           # 1 px экрана при зуме 1 в пикселях скриншота
    for i, hl in enumerate(p.get("highlight") or []):
        rx, ry, rw, rh = _region(hl.get("region"), iw, ih, f"highlight[{i}].region")
        over += f'<i class="hl hl{i}"></i>'
        shade = f";box-shadow:0 0 0 {max(iw, ih) * 2:.0f}px rgba(0,0,0,.42)" if hl.get("dim") else ""
        over_css.append(f"{ctx.sel} .hl{i}{{left:{rx - 6 * k:.0f}px;top:{ry - 6 * k:.0f}px;width:{rw + 12 * k:.0f}px;"
                        f"height:{rh + 12 * k:.0f}px{shade}}}")
        ha = ctx.at(hl.get("at"), t0 + 1)
        move += (f'tl.fromTo(S+" .hl{i}",{{opacity:0,scale:1.06}},{{opacity:1,scale:1,duration:.35,'
                 f'ease:"power2.out"}},{T(ha)});\n')
        if hl.get("until") is not None:
            move += f'tl.to(S+" .hl{i}",{{opacity:0,duration:.3}},{T(max(ha + .4, ctx.at(hl.get("until"), ha + 2)))});\n'
    if over_css:
        over_css.append(f"{ctx.sel} .hl{{position:absolute;opacity:0;border:{6 * k:.1f}px solid var(--accent);"
                        f"border-radius:{12 * k:.1f}px;transform-origin:50% 50%}}")

    pts = list(p.get("cursor") or [])
    if pts:
        def zoom_at(tt: float) -> float:
            z = 1.0
            for a, d, zz in zooms:
                if tt >= a + d:
                    z = zz
            return z
        ctimes = [ctx.at(pt.get("at"), t0 + .8 + i) for i, pt in enumerate(pts)]
        xy = [(_clamp(float(pt["x"]), 0, iw), _clamp(float(pt["y"]), 0, ih)) for pt in pts]
        # стрелка появляется чуть правее и ниже первой точки и подъезжает к ней
        sx, sy = xy[0][0] + 120 * k, xy[0][1] + 160 * k
        appear = max(t0 + .2, ctimes[0] - 1.0)
        z0 = zoom_at(appear)
        over += f'<i class="cur">{ARROW}</i>'
        over_css.append(f"{ctx.sel} .cur{{position:absolute;left:0;top:0;width:{CURSOR * 26 / 36 * k:.1f}px;"
                        f"height:{CURSOR * k:.1f}px;opacity:0;z-index:5;transform-origin:0 0;"
                        f"transform:translate({sx:.0f}px,{sy:.0f}px) scale({num(1 / z0)})}}\n"
                        f"{ctx.sel} .cur svg{{display:block;width:100%;height:100%;overflow:visible;"
                        f"transform-origin:0 0;filter:drop-shadow(0 3px 4px rgba(0,0,0,.35))}}\n"
                        f"{ctx.sel} .rp{{position:absolute;width:{110 * k:.0f}px;height:{110 * k:.0f}px;"
                        f"margin:{-55 * k:.0f}px 0 0 {-55 * k:.0f}px;border-radius:50%;opacity:0;"
                        f"border:{7 * k:.1f}px solid var(--accent);background:rgba(255,255,255,.25)}}")
        move += f'tl.fromTo(S+" .cur",{{opacity:0}},{{opacity:1,duration:.25}},{T(appear)});\n'
        prev, pa = (sx, sy), appear
        for i, ((cx, cy), a) in enumerate(zip(xy, ctimes)):
            start = max(pa + .1, a - .75)
            d = max(.2, a - start)
            move += (f'tl.fromTo(S+" .cur",{{x:{num(prev[0])},y:{num(prev[1])}}},{{x:{num(cx)},y:{num(cy)},'
                     f'duration:{num(d)},ease:"power2.inOut"}},{T(a - d)});\n')
            if pts[i].get("click"):
                over += f'<i class="rp rp{i}"></i>'
                over_css.append(f"{ctx.sel} .rp{i}{{left:{cx:.0f}px;top:{cy:.0f}px}}")
                move += (f'tl.fromTo(S+" .cur svg",{{scale:1}},{{scale:.8,duration:.08,yoyo:true,repeat:1}},{T(a)});\n'
                         f'tl.fromTo(S+" .rp{i}",{{opacity:.95,scale:.15}},{{opacity:0,scale:1.5,duration:.6,'
                         f'ease:"power2.out"}},{T(a)});\n')
            prev, pa = (cx, cy), a
        # стрелка не растёт вместе с камерой: обратный масштаб в такт каждому шагу фокуса
        zprev = z0
        for a, d, zz in zooms:
            if a + d > appear and zz != zprev:
                move += (f'tl.fromTo(S+" .cur",{{scale:{num(1 / zprev)}}},{{scale:{num(1 / zz)},duration:{num(d)},'
                         f'ease:"power2.inOut"}},{T(a)});\n')
                zprev = zz

    html += _frame_html(frame, f'<div class="shot"><img src="{esc(name)}" alt="">{over}</div>')
    css = "\n".join(x for x in (css, _frame_css(ctx, frame, x, y, vw, vh), shot_css, "\n".join(over_css)) if x)
    js += _enter_js(str(p.get("enter") or "rise"), t0) + move
    return SceneOut(html, css, js, {name: f})


# --- video ---------------------------------------------------------------------------------------------
@scene("video", SceneDoc(
    "Видеоклип или запись экрана (mp4/webm) в рамке или на весь кадр; играет с начала блока, без звука "
    "(по умолчанию), можно ускорить. Сверху 1–2 строки-стикера.",
    {"src": "путь к видео от папки проекта (mp4/webm/mov). Обязательно",
     "frame": "card (по умолчанию) | none | phone | laptop",
     "fit": "cover (по умолчанию) | contain",
     "x, y, w, h": "положение и размер в кадре, px; без w при frame none — на весь кадр; иначе ширина 920 "
                   "по центру, высота по пропорциям видео",
     "start": "с какой секунды файла начинать; по умолчанию 0",
     "speed": "скорость 0.1…10; по умолчанию 1",
     "muted": "true (по умолчанию) — без звука ролика; false — звук клипа идёт в микс",
     "volume": "громкость звука клипа при muted: false; по умолчанию 0.5",
     "enter": "появление: fade (по умолчанию) | rise | slam | slide | none",
     "at": "якорь появления рамки; по умолчанию 0.15 с (видео играет с начала блока)",
     "title": "1–2 строки-стикера над видео; *слово* — инверсией; необязательно",
     "title_at": "якорь заголовка или список якорей",
     "kicker": "надзаголовок; необязательно"},
    {"src": "local-model-test/results/qwen3-vl-8b/reel-fixed.mp4", "frame": "phone", "w": 520, "start": 1,
     "speed": 1.5, "title": ["Готовый ролик", "*за один прогон*"], "title_at": ["готовый", "прогон"],
     "enter": "slam", "at": "готовый"}))
def video(ctx: SceneCtx, p: dict) -> SceneOut:
    f, name = _src(ctx, p, VIDEO_EXT)
    vw0, vh0, src_dur = video_info(f)
    frame = _frame_of(p, "card")
    fit_ = str(p.get("fit") or "cover")
    if fit_ not in ("cover", "contain"):
        raise ValueError("fit: cover или contain")
    html, css, js, top = _title(ctx, p)
    if frame == "none" and not p.get("w"):
        x, y, w, h = 0.0, 0.0, 1080.0, 1920.0
    else:
        x, y, w, h = _box(p, frame, vh0 / vw0, top)
    start = max(0.0, float(p.get("start") or 0))
    speed = _clamp(float(p.get("speed") or 1), .1, 10)
    # клип живёт весь блок и немного после (на склейке сцена ещё видна); родительская сцена скрывается сама
    dur = ctx.length + 1.0
    if src_dur:
        if start >= src_dur:
            raise SceneError(f"блок {ctx.block}: start {num(start)} с за концом видео ({num(src_dur)} с)")
        dur = min(dur, (src_dur - start) / speed)
    muted = p.get("muted", True) is not False
    audio = (' muted data-volume="0"' if muted else
             f' data-has-audio="true" data-volume="{num(_clamp(float(p.get("volume", .5)), 0, 2))}"')
    tag = (f'<video id="{ctx.uid}-video" class="clip" src="{esc(name)}" data-start="{num(ctx.start)}" '
           f'data-duration="{num(dur)}" data-media-start="{num(start)}" data-playback-rate="{num(speed)}"'
           f'{audio} playsinline preload="auto"></video>')
    html += _frame_html(frame, f'<div class="shot">{tag}</div>')
    vcss = (f"{ctx.sel} .shot{{width:{w:.0f}px;height:{h:.0f}px}}\n"
            f"{ctx.sel} .shot video{{width:100%;height:100%;object-fit:{fit_};background:#000}}")
    css = "\n".join(x for x in (css, _frame_css(ctx, frame, x, y, w, h), vcss) if x)
    js += _enter_js(str(p.get("enter") or "fade"), ctx.at(p.get("at"), .15))
    return SceneOut(html, css, js, {name: f})
