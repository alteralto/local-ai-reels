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

# «Плакат»: красный фон с зерном, тяжёлый гротеск капслоком, текст — на белых наклейках (шаблоны stickers/tag/cover)
POSTER_FONT = 'Montserrat, "Arial Black", "Segoe UI", sans-serif'
POSTER_FONTS = ("https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700;800;900"
                "&family=IBM+Plex+Mono:wght@500;600&display=block")


def _poster(name: str, **kw) -> Theme:
    """Тема семейства poster: без сетки, шрифт плаката, зерно (если в Theme уже есть поле grain)."""
    th = Theme(name, font=POSTER_FONT, fonts_url=POSTER_FONTS, grid=False, **kw)
    if "grain" in {f.name for f in dataclasses.fields(Theme)}:
        th = dataclasses.replace(th, grain=.35)
    return th


THEMES.update({
    # бренд-красный, белые наклейки с чёрным текстом; акцент (выделение, текущее слово) — чёрный, второй — жёлтый
    "poster": _poster("poster", bg="#d7141a", deep="#141414", ink="#ffffff", accent="#141414", alt="#f2c23a",
                      muted="rgba(255,255,255,.25)"),
    # кремовая бумага, чёрные чернила, красный акцент
    "poster-cream": _poster("poster-cream", bg="#efe6d5", deep="#e2d6bf", ink="#141414", accent="#d7141a",
                            alt="#1f2a55", muted="rgba(20,20,20,.18)"),
    # ночной синий, кремовый текст, жёлтый акцент
    "poster-night": _poster("poster-night", bg="#1f2a55", deep="#141b3a", ink="#efe6d5", accent="#f2c23a",
                            alt="#d7141a", muted="rgba(239,230,213,.2)"),
    # почти чёрный фон под «прожектор»: жёлтый акцент, красный второй
    "poster-black": _poster("poster-black", bg="#141414", deep="#000000", ink="#ffffff", accent="#f2c23a",
                            alt="#d7141a", muted="rgba(255,255,255,.18)"),
})

# Цвета наклеек (шаблоны stickers, tag, cover): main — основная (белая, чёрный текст), tag — тёмная бирка
# (style black), accent — выделенная (style accent), em — цвет *слова* на основной наклейке.
# Темы без записи получают значения из своих цветов (_sticker_vars).
STICKERS: dict[str, dict[str, str]] = {
    "poster": {"sticker-bg": "#ffffff", "sticker-ink": "#141414", "label-bg": "#141414", "label-ink": "#ffffff",
               "accent-bg": "#ffffff", "accent-ink": "#d7141a", "sticker-em": "#d7141a"},
    "poster-cream": {"sticker-bg": "#ffffff", "sticker-ink": "#141414", "label-bg": "#141414",
                     "label-ink": "#ffffff", "accent-bg": "#d7141a", "accent-ink": "#ffffff", "sticker-em": "#d7141a"},
    "poster-night": {"sticker-bg": "#efe6d5", "sticker-ink": "#141414", "label-bg": "#141414",
                     "label-ink": "#efe6d5", "accent-bg": "#f2c23a", "accent-ink": "#141414", "sticker-em": "#d7141a"},
    "poster-black": {"sticker-bg": "#ffffff", "sticker-ink": "#141414", "label-bg": "#d7141a",
                     "label-ink": "#ffffff", "accent-bg": "#f2c23a", "accent-ink": "#141414", "sticker-em": "#d7141a"},
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


def _sticker_vars(theme: Theme) -> dict[str, str]:
    """Цвета наклеек темы: из STICKERS по имени или из основных цветов (для старых тем и своих словарей)."""
    if theme.name in STICKERS:
        return STICKERS[theme.name]
    return {"sticker-bg": theme.ink, "sticker-ink": theme.bg, "label-bg": "#141414", "label-ink": "#ffffff",
            "accent-bg": theme.accent, "accent-ink": theme.deep, "sticker-em": theme.alt}


def theme_css(theme: Theme) -> str:
    """Блок :root{--bg:…} для страницы ролика (плюс --sticker-*/--label-*/--accent-* для наклеек)."""
    pairs = [("bg", theme.bg), ("deep", theme.deep), ("ink", theme.ink), ("accent", theme.accent), ("alt", theme.alt),
             ("muted", theme.muted), ("font", theme.font), ("mono", theme.mono)]
    pairs += list(_sticker_vars(theme).items())
    return ":root{" + ";".join(f"--{k}:{v}" for k, v in pairs) + "}"


__all__ = ["STICKERS", "THEMES", "get_theme", "theme_css"]
