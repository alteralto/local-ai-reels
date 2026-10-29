"""Иллюстрации-«пропсы» для шаблона illustration: плоская графика в духе плаката, рисуется SVG-кодом.

Проп — функция (params: dict, uid: str) → PropOut. uid — уникальный префикс (например "b2s1-l0"): им начинаются
все id и классы внутри SVG, чтобы два одинаковых пропа в кадре не мешали друг другу.

    @prop("kiosk", PropDoc("Уличная кабинка WC с дверью", {"color": "цвет стен"}, actions=["open", "peek", "knock"]))
    def kiosk(p, uid): ...

PropOut.svg — целый элемент <svg viewBox="0 0 W H" …>; шаблон illustration сам задаёт ему положение и размер.
PropOut.actions — {имя: fn(sel, t) → JS}: анимации пропа по команде из reel.yaml («дверь открылась на слове
"тук-тук"»). sel — CSS-селектор корня пропа, t — абсолютное время на таймлайне (уже с началом блока),
JS пишет только в tl (tl.fromTo / tl.to / tl.set) с позициями t + …, как и шаблоны сцен.
Цвета по умолчанию — CSS-переменные темы (var(--accent), var(--ink), var(--bg), var(--deep), var(--alt)).
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PropOut:
    svg: str
    css: str = ""
    actions: dict = field(default_factory=dict)   # имя → fn(sel: str, t: float) -> str (JS)
    w: float = 100                                 # пропорции viewBox: шаблон считает высоту по ширине
    h: float = 100


@dataclass
class PropDoc:
    summary: str
    params: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)


PROPS: dict[str, tuple[Callable[[dict, str], PropOut], PropDoc]] = {}


def prop(name: str, doc: PropDoc):
    def deco(fn):
        PROPS[name] = (fn, doc)
        return fn
    return deco


def render_prop(name: str, params: dict, uid: str) -> PropOut:
    if name not in PROPS:
        raise ValueError(f"неизвестный проп «{name}». Есть: {', '.join(sorted(PROPS))}")
    return PROPS[name][0](params or {}, uid)


for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")

__all__ = ["PROPS", "PropDoc", "PropOut", "prop", "render_prop"]
