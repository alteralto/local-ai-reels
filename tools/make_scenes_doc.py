"""Собирает docs/scenes.md из реестра шаблонов сцен: каталог всегда совпадает с кодом.

python tools/make_scenes_doc.py          (проверка в CI: --check — падает, если файл устарел)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reels.draft import catalog_text  # noqa: E402
from reels.themes import THEMES  # noqa: E402

HEAD = """# Шаблоны сцен

Сцена задаётся у блока в `reel.yaml`: `scene: {type: <шаблон>, …параметры}`. Список в терминале:
`python -m reels scenes`, подробно об одном: `python -m reels scenes <имя>`.

**Якоря.** Время анимации привязано к словам диктора: `at: "каждый пятый"` — начало фразы, `"туалет@end"` — конец
слова, `"хочет+0.3"` — со сдвигом, `"40%"` — доля длины блока, `1.5` — секунды от начала блока. Якорь, которого
нет в тексте блока, — ошибка на этапе `check`, а не тихая рассинхронизация в ролике.

**Текст.** `*слово*` — акцентный цвет темы, перевод строки — `\\n`. Всё, что ниже 1450 px, занято субтитрами:
шаблоны сами подбирают кегль под длину строк, чтобы текст не вылезал за поля.

## Галерея

Кадры сняты с примеров ниже (тема blueprint, конец анимации, без субтитров). Пересобрать:
`python tools/make_gallery.py`.

![Все шаблоны сцен](media/gallery/scenes.png)

| headline | stat | counter | quote | list |
|---|---|---|---|---|
| ![headline](media/gallery/headline.png) | ![stat](media/gallery/stat.png) | ![counter](media/gallery/counter.png) | ![quote](media/gallery/quote.png) | ![list](media/gallery/list.png) |
| **fraction** | **bars** | **compare** | **timeline** | **map** |
| ![fraction](media/gallery/fraction.png) | ![bars](media/gallery/bars.png) | ![compare](media/gallery/compare.png) | ![timeline](media/gallery/timeline.png) | ![map](media/gallery/map.png) |

<details><summary>Все шаблоны в движении (GIF)</summary>

![Шаблоны в движении](media/gallery/scenes.gif)

</details>

## Темы

`theme:` в reel.yaml — имя ({themes}) или словарь с заменой отдельных цветов:
`theme: {base: night, accent: "#00e0a4"}`.

![Темы оформления](media/gallery/themes.png)

## Свободная сцена `custom`

Когда шаблонов мало: свой HTML/CSS/JS строкой или файлами проекта (`html_file`, `css_file`, `js_file`). JS
выполняется с переменными `S` (селектор сцены), `t` (начало блока) и `tl` (общий таймлайн GSAP); в CSS `{S}`
заменяется селектором сцены. Ошибка в JS одной сцены не ломает остальной ролик: каждая сцена изолирована, а JS с
синтаксической ошибкой отключается на сборке с предупреждением. Пример — `examples/povodok/scenes/`.

## Каталог
"""


def build() -> str:
    return HEAD.replace("{themes}", ", ".join(f"`{t}`" for t in THEMES)) + "\n" + catalog_text(include_custom=True)


if __name__ == "__main__":
    out = ROOT / "docs" / "scenes.md"
    text = build()
    if "--check" in sys.argv:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            sys.exit("docs/scenes.md устарел: python tools/make_scenes_doc.py")
        print("docs/scenes.md актуален")
    else:
        out.write_text(text, encoding="utf-8")
        print(out)
