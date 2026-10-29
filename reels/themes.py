"""Темы оформления: цвета и шрифты сцен. Compose выводит их CSS-переменными (theme_css), шаблоны пишут var(--accent) и т. п."""
from __future__ import annotations

import dataclasses
import re

from .scenes.base import Theme

THEMES: dict[str, Theme] = {
    # синька из примера «Поводок»: синий фон, сетка 40 px, жёлтый акцент
    "blueprint": Theme("blueprint"),
    # почти чёрный фон, неоновый акцент
    "night": Theme("night", bg="#0b0d12", deep="#05060a", ink="#eef2f7", accent="#c6ff2e", alt="#ff3d8b",
                   muted="rgba(238,242,247,.18)"),
    # светлая бумага, тёмные чернила, красный акцент, без сетки
    "paper": Theme("paper", bg="#f4efe4", deep="#e4dccb", ink="#1d1a16", accent="#d7372b", alt="#1f4fa3",
                   muted="rgba(29,26,22,.18)", grid=False),
    # чёрно-белая с одним акцентом
    "mono": Theme("mono", bg="#0e0e0e", deep="#000000", ink="#ffffff", accent="#ff4d2e", alt="#9a9a9a",
                  muted="rgba(255,255,255,.2)", grid=False),
}

_FIELDS = {f.name for f in dataclasses.fields(Theme)}
_UNSAFE = re.compile(r"[;{}<>]")


def get_theme(spec=None) -> Theme:
    """Тема по имени или словарю {base: имя, accent: "#...", ...}. None/"" → blueprint."""
    if isinstance(spec, Theme):
        return spec
    if spec is None or spec == "":
        spec = "blueprint"
    over: dict = {}
    if isinstance(spec, dict):
        over = {k: v for k, v in spec.items() if k != "base"}
        spec = spec.get("base") or "blueprint"
    if not isinstance(spec, str) or spec not in THEMES:
        raise ValueError(f"неизвестная тема «{spec}». Есть: {', '.join(sorted(THEMES))}")
    bad = set(over) - _FIELDS - {"name"}
    if bad:
        raise ValueError(f"theme: неизвестные поля {', '.join(sorted(bad))}. Есть: {', '.join(sorted(_FIELDS))}")
    for k, v in over.items():
        if k != "grid" and _UNSAFE.search(str(v)):
            raise ValueError(f"theme.{k}: недопустимые символы в «{v}»")
    if "grid" in over:
        over["grid"] = bool(over["grid"])
    return dataclasses.replace(THEMES[spec], **over)


def theme_css(theme: Theme) -> str:
    """Блок :root{--bg:…} для страницы ролика."""
    pairs = [("bg", theme.bg), ("deep", theme.deep), ("ink", theme.ink), ("accent", theme.accent), ("alt", theme.alt),
             ("muted", theme.muted), ("font", theme.font), ("mono", theme.mono)]
    return ":root{" + ";".join(f"--{k}:{v}" for k, v in pairs) + "}"


__all__ = ["THEMES", "get_theme", "theme_css"]
