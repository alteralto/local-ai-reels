"""Общие детали шаблонов: подгон кегля под ширину, надзаголовок, якоря списком, строки и числа для JS."""
from __future__ import annotations

import json
import re

from .base import SceneCtx, esc, num

W = 920            # рабочая ширина кадра: 80…1000 px
TOP, BOTTOM = 260, 1450

KICK_JS = 'tl.fromTo(S+" .kicker",{opacity:0,y:-20},{opacity:1,y:0,duration:.35},t+.05);\n'


def plain(s) -> str:
    """Текст без разметки акцента — для подсчёта длины."""
    return re.sub(r"\*(.+?)\*", r"\1", str(s))


def fit(text, base: float, width: float = W, k: float = 0.56, lo: float = 34, lines: int = 1) -> int:
    """Кегль, при котором самая длинная строка (символов × k × кегль) влезает в width (с переносом на lines строк)."""
    longest = max((len(plain(x)) for x in str(text).split("\n")), default=1) or 1
    return int(max(lo, min(base, width * lines / (longest * k))))


def as_lines(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return str(v).split("\n")


def times(ctx: SceneCtx, at, n: int, start: float = 0.3, step: float = 0.45) -> list[float]:
    """n моментов: at — якорь первого (дальше шаг step) или список якорей по одному на элемент."""
    if isinstance(at, (list, tuple)):
        out: list[float] = []
        for i in range(n):
            prev = out[-1] + step if out else start
            out.append(ctx.at(at[i], prev) if i < len(at) else prev)
        return out
    t0 = ctx.at(at, start)
    return [t0 + i * step for i in range(n)]


def T(x: float) -> str:
    """Позиция на таймлайне: t + секунды (не раньше начала блока)."""
    return "t+" + num(max(0.0, float(x)))


def jstr(s) -> str:
    """Строка-литерал для JS (данные, не код)."""
    return json.dumps(str(s), ensure_ascii=False).replace("<", "\\u003c").replace("/", "\\/")


def kicker(params: dict) -> str:
    k = params.get("kicker")
    return f'<div class="kicker mono">{esc(k)}</div>' if k else ""


def kick_js(params: dict) -> str:
    return KICK_JS if params.get("kicker") else ""


def fmt_num(v, decimals: int | None = None) -> str:
    """Число по-русски: разряды тонким пробелом, дробь через запятую."""
    v = float(v)
    if decimals is None:
        decimals = 0 if v == int(v) else len(f"{v:.3f}".rstrip("0").split(".")[1])
    return f"{v:,.{decimals}f}".replace(",", "\u2009").replace(".", ",")


def reveal(sel: str, at: float, dur: float = .45) -> str:
    """Выезд строки .big снизу (span внутри .big)."""
    return f'tl.fromTo(S+" {sel} span",{{yPercent:110}},{{yPercent:0,duration:{num(dur)},ease:"expo.out"}},{T(at)});\n'
