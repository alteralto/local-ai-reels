"""API шаблонов сцен. Шаблон — функция (ctx, params) → SceneOut, зарегистрированная декоратором @scene.

Кадр 1080×1920. Зоны (px от верха): надзаголовок .kicker — 150; графика и крупный текст — 260…1450;
ниже 1450 — субтитры (их рисует compose) и подпись источника: туда сцена ничего не ставит. Поля слева/справа — 80.

Сцена получает корневой div (id = ctx.uid, класс "scene") и пишет внутрь только своё содержимое.
CSS сцены обязан начинаться с селектора ctx.sel (стили не должны протекать в другие блоки).
JS сцены выполняется внутри блока { const S='#b2', t=<начало блока>; … } и пишет только в общий таймлайн tl:
    tl.fromTo(S+" .x", {...}, {...}, t + 1.25);
Числа времени — секунды от начала блока, удобнее всего через ctx.at("слово диктора").
Ошибка в JS одной сцены ломает анимацию всего ролика (таймлайн общий), поэтому шаблоны генерируют JS сами,
а параметры из reel.yaml попадают в HTML только через esc()/rich().
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..timing import anchor


@dataclass
class Theme:
    name: str
    bg: str = "#123a7a"        # фон сцен
    deep: str = "#0b2350"      # тёмный оттенок фона (затемнения, подложки)
    ink: str = "#e8f0ff"       # основной цвет текста и линий
    accent: str = "#ffd23f"    # акцент: выделенные слова, текущее слово субтитров, полоска прогресса
    alt: str = "#ff6b5b"       # второй акцент (линии, «плохо»)
    muted: str = "rgba(232,240,255,.22)"   # вспомогательные линии, неактивные элементы
    font: str = 'Manrope, "Segoe UI", sans-serif'
    mono: str = '"IBM Plex Mono", Consolas, monospace'
    fonts_url: str = ("https://fonts.googleapis.com/css2?family=Manrope:wght@500;700;800"
                      "&family=IBM+Plex+Mono:wght@500;600&display=block")
    grid: bool = True          # чертёжная сетка 40 px на фоне сцен


@dataclass
class SceneCtx:
    block: int                 # номер блока в сценарии, с 1
    uid: str                   # id корня сцены: "b2"
    start: float               # начало блока на таймлайне монтажа, с
    length: float              # длина речи блока, с
    words: list[dict]          # слова блока {"w","s","e"}, время от начала блока
    text: str                  # текст диктора блока
    theme: Theme
    project_dir: Path          # для шаблона custom: файлы сцен лежат в проекте
    position: int = 0          # место блока в монтаже, с 0
    count: int = 1             # сколько блоков в монтаже

    @property
    def last(self) -> bool:
        """Блок последний в монтаже: призыв к действию уместен только здесь."""
        return self.position == self.count - 1

    @property
    def sel(self) -> str:
        return f"#{self.uid}"

    def at(self, spec, default: float = 0.0) -> float:
        """Время от начала блока по якорю (см. timing.anchor): "слово", "фраза@end+0.2", "40%", 1.5, None."""
        return anchor(self.words, spec, default)


@dataclass
class SceneOut:
    html: str
    css: str = ""
    js: str = ""


@dataclass
class SceneDoc:
    """Описание шаблона для людей и для LLM (reels draft): назначение, параметры, пример."""
    summary: str
    params: dict[str, str]     # имя → описание (тип, обязательность, по умолчанию)
    example: dict = field(default_factory=dict)


SceneFn = Callable[[SceneCtx, dict], SceneOut]
REGISTRY: dict[str, tuple[SceneFn, SceneDoc]] = {}


def scene(name: str, doc: SceneDoc):
    def deco(fn: SceneFn) -> SceneFn:
        REGISTRY[name] = (fn, doc)
        return fn
    return deco


class SceneError(ValueError):
    pass


def render_scene(ctx: SceneCtx, params: dict) -> SceneOut:
    kind = params.get("type", "headline")
    if kind not in REGISTRY:
        raise SceneError(f"блок {ctx.block}: неизвестный тип сцены «{kind}». Есть: {', '.join(sorted(REGISTRY))}")
    fn, _ = REGISTRY[kind]
    try:
        return fn(ctx, {k: v for k, v in params.items() if k != "type"})
    except SceneError:
        raise
    except (KeyError, ValueError, TypeError) as e:
        raise SceneError(f"блок {ctx.block}, сцена {kind}: {e}") from e


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def rich(s) -> str:
    """Текст из reel.yaml → HTML: экранирование, *слово* → <em>слово</em> (акцент), перевод строки → <br>."""
    out = esc(s)
    out = re.sub(r"\*(.+?)\*", r"<em>\1</em>", out)
    return out.replace("\n", "<br>")


def num(x) -> str:
    """Число для JS: без экспоненты и лишних нулей."""
    return f"{float(x):.3f}".rstrip("0").rstrip(".") or "0"
