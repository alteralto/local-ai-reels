# Как помочь проекту

Спасибо, что заглянули! Баг-репорты, новые шаблоны сцен, темы и правки документации — всё кстати.
Пишите issue или PR на русском или английском.

## Окружение разработчика

Нужны Python 3.10+, Node.js 22+ и ffmpeg 6+ в PATH; для приложений — Rust stable.

```bash
git clone https://github.com/alteralto/local-ai-reels && cd local-ai-reels
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                               # конвейер без моделей + pytest, ruff, pre-commit
pip install -e ".[ml]"                                # по желанию: F5-TTS, RUAccent, faster-whisper, MusicGen
pre-commit install                                    # ruff и проверки файлов перед коммитом
python -m reels doctor                                # что за железо и чего не хватает
```

Вместо ручной установки можно `bash install.sh` / `install.ps1` (см. README). Для работы над шаблонами и рендером
GPU не нужен: `examples/povodok` и `examples/povodok-templates` используют готовую озвучку из `example/`.

```bash
python -m reels run examples/povodok-templates --yes --edit linear
python -m reels run examples/povodok-templates --only snapshot      # только кадры и автопроверка
```

## Проверки

```bash
ruff check .                                   # линтер (как в lint.yml)
python -m pytest                               # быстрые тесты: без моделей, сети и рендера
REELS_INTEGRATION=1 python -m pytest           # + интеграционные: сборка и рендер примеров (Node.js, ffmpeg, минуты)
python tools/make_scenes_doc.py --check        # docs/scenes.md совпадает с кодом шаблонов (как в CI)
```

Быстрые тесты должны проходить за секунды и не трогать сеть. Всё, что запускает HyperFrames, Chrome или модели,
помечайте как интеграционное (пропуск без `REELS_INTEGRATION=1`).

Rust — рабочее пространство `app/` (`reels-core`, `reels-app`) и отдельный пакет `app/desktop`:

```bash
(cd app && cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace)
(cd app/desktop && cargo clippy --all-targets -- -D warnings && cargo test)
```

Интерфейс настольного приложения можно смотреть без Rust: `app/desktop/dist/index.html` в браузере
(подробно — [app/desktop/README.md](app/desktop/README.md)).

## Как добавить шаблон сцены

Шаблон — один модуль (или одна функция в подходящем модуле) в `reels/scenes/`, зарегистрированный декоратором
`@scene` с описанием `SceneDoc`. Описание видят люди (`reels scenes`, [docs/scenes.md](docs/scenes.md)) и локальная
LLM в `reels draft`, поэтому пишите его понятно и с примером.

```python
from .base import SceneCtx, SceneDoc, SceneOut, esc, num, rich, scene


@scene("badge", SceneDoc(
    "Крупная плашка с одним словом, выезжает по якорю.",
    {"text": "текст плашки; *слово* — акцентом. Обязательно",
     "at": "якорь появления; по умолчанию 0.3 с"},
    {"text": "*Важно*", "at": "каждый пятый"}))
def badge(ctx: SceneCtx, p: dict) -> SceneOut:
    if not p.get("text"):
        raise ValueError("нужен text")
    t0 = ctx.at(p.get("at"), 0.3)
    s = ctx.sel
    return SceneOut(
        html=f'<div class="badge">{rich(p["text"])}</div>',
        css=f"{s} .badge{{position:absolute;left:80px;right:80px;top:700px;font-size:120px;font-weight:800}}"
            f"{s} .badge em{{color:{ctx.theme.accent};font-style:normal}}",
        js=f'tl.fromTo(S+" .badge", {{y:60, opacity:0}}, {{y:0, opacity:1, duration:.5}}, t + {num(t0)});')
```

Модуль импортируется в `reels/scenes/__init__.py`. Правила:

- **CSS только внутри сцены:** каждый селектор начинается с `ctx.sel` (`#b2 ...`). Стили не должны протекать
  в другие блоки и в субтитры.
- **JS только в общий таймлайн:** `tl.fromTo(S + " .x", {...}, {...}, t + <секунды>)`. Никаких `gsap.to`,
  `setTimeout`, обработчиков событий и глобальных переменных. Таймлайн общий на весь ролик — ошибка в одной
  сцене ломает анимацию всех блоков.
- **Время — от начала блока**, лучше через `ctx.at("слово диктора")`, а не захардкоженные секунды.
- **Параметры из `reel.yaml` попадают в HTML только через `esc()` / `rich()`**, а в JS — только числами через
  `num()`. Строки из YAML в JS не вставляются.
- **Раскладка:** кадр 1080×1920, графика и крупный текст — в полосе y 260…1450 px, поля 80 px слева и справа.
  Ниже 1450 — субтитры и подпись источника, туда сцена ничего не ставит. Длинный текст уменьшается, а не вылезает.
- **Цвета — только из `ctx.theme`**, чтобы шаблон работал во всех темах (`blueprint`, `night`, `paper`, `mono`).
- **Ошибки параметров** — `ValueError` с понятным русским текстом: конвейер покажет его с номером блока.

Перед PR:

1. добавьте тест в `tests/` (рендер HTML/CSS/JS шаблона на примере из `SceneDoc`, проверка селекторов и `tl.`);
2. прогоните пример с новой сценой до `snapshot` и посмотрите кадры — автопроверка вёрстки должна быть чистой;
3. пересоберите каталог и галерею: `python tools/make_scenes_doc.py` и `python tools/make_gallery.py`.

## Стиль кода

- Python 3.10+, аннотации типов, `from __future__ import annotations`; строки до 120 символов.
- Модули маленькие и с понятными контрактами (см. [docs/architecture.md](docs/architecture.md)); этап конвейера —
  функция `stage(project, ctx)`.
- Внешние программы — через `reels.util.run`/`ff`; ошибка внешней программы — `ToolError` с подсказкой, что
  сделать пользователю.
- **Сообщения для пользователя — по-русски**, коротко и с действием («нет ffmpeg в PATH — установите или укажите
  FFMPEG_DIR»). Комментарии в коде — тоже по-русски. Идентификаторы — по-английски.
- Без новых тяжёлых зависимостей в базовой установке: всё, что тянет torch или модели, — в extra `[ml]`.
- Не коммитьте модели, рендеры и `build/` (см. `.gitignore`).

## Pull request

- Одна тема — один PR; опишите, что и зачем, приложите кадр или GIF, если меняется картинка.
- `ruff check .` и `python -m pytest` зелёные; для изменений рендера — ещё и `REELS_INTEGRATION=1`;
  для `app/` — `cargo fmt`, `clippy` и `cargo test`.
- Новое поведение — в [CHANGELOG.md](CHANGELOG.md), раздел «Unreleased».
- Картинки README пересобираются одной командой: `python tools/make_media.py` (после
  `python -m reels run examples/povodok --yes`).
- Релиз: `python tools/bump_version.py X.Y.Z` (версия в `pyproject.toml`, `reels/__init__.py` и раздел
  CHANGELOG), затем тег `vX.Y.Z` — пакет соберёт и опубликует `release.yml`.

Участвуя в проекте, вы соглашаетесь с [правилами поведения](CODE_OF_CONDUCT.md).
