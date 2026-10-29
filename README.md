<h1 align="center">local-ai-reels</h1>

<p align="center"><b>Вертикальные ролики 1080×1920 с озвучкой, субтитрами, графикой и музыкой — на локальных моделях</b></p>

<p align="center">
  <a href="https://github.com/alteralto/local-ai-reels/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/alteralto/local-ai-reels/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg">
  <img alt="Node 22+" src="https://img.shields.io/badge/node-22%2B-339933.svg">
</p>

> **English:** a local, GPU-friendly conveyor for vertical AI reels / shorts. Write `reel.yaml` (or let a local LLM
> draft it), run `python -m reels run <project>` and get finished MP4s: voice cloning with F5-TTS + RUAccent stress
> marks, word timings from Whisper, motion graphics as HTML/SVG rendered by HyperFrames + GSAP, MusicGen music,
> ffmpeg mixing — with caching, human approval gates and automated layout QA. No paid APIs.
> **[Read in English →](README.en.md)**

<p align="center"><img src="docs/media/hero.gif" width="320" alt="Пример ролика: сцены-схемы, субтитры с подсветкой текущего слова, переходы между блоками"></p>

**local-ai-reels** — конвейер, который превращает короткий сценарий в готовый вертикальный ролик для Reels,
Shorts и TikTok. Голос клонируется по образцу, ударения ставятся заранее, каждое слово получает таймкод, а графика
пишется кодом (HTML + SVG + GSAP) и рендерится в MP4 в headless Chrome. Всё работает на своём компьютере, без платных
API: ролик на 25 секунд рендерится примерно за минуту на видеокарте уровня RTX 3060. Из одной озвучки получается
несколько монтажей, а человек вмешивается только в контрольных точках — или не вмешивается вовсе (`--yes`).

## Что умеет

- **Один файл — один ролик.** Сценарий, сцены, голос, музыка и монтажи описываются в `reel.yaml`; конвейер
  сам доводит их до `out/<slug>-<монтаж>.mp4` и отчёта `report.md`.
- **Клон голоса с ударениями.** F5-TTS повторяет тембр и темп образца, RUAccent размечает ударения, Whisper
  сверяет каждый дубль со сценарием — в монтаж идёт тот, что распознаётся дословно.
- **Пословные субтитры.** Фразы по 3–5 слов с подсветкой текущего слова, не рвутся на предлогах.
- **Графика кодом.** Библиотека шаблонов сцен (заголовок, число, доля, столбцы, сравнение, список, цитата, счётчик,
  шкала времени, карта) и свои сцены на HTML/SVG. Анимация привязана к словам диктора: `at: "каждый пятый"`.
- **Темы оформления:** `blueprint`, `night`, `paper`, `mono` или свой акцентный цвет.
- **Много монтажей из одной озвучки.** Блоки переставляются на таймлайне, а не синтезируются заново.
- **Музыка и звук.** Фон от MusicGen по текстовому описанию, синтезированные «вжухи» на склейках, сведение
  в −18 LUFS.
- **Кэш и контрольные точки.** Этап не перезапускается, если его входы не менялись; на четырёх точках конвейер
  ждёт подтверждения человека.
- **Автопроверка вёрстки.** До рендера кадры проверяются по DOM: выход за край, наложения, залезание в зону
  субтитров. После рендера — длительность, громкость и дословная сверка текста поверх музыки.
- **Черновик сценария локальной LLM** (LM Studio, Ollama — любой OpenAI-совместимый сервер) и **пакетный режим**
  для десятков роликов.

## Как это работает

<p align="center"><img src="docs/media/pipeline.svg" alt="Этапы: reel.yaml → check → accent → voice → align → music → build → snapshot → render → finish → out/*.mp4; контрольные точки script, accents, take, frames"></p>

| Этап | Что делает | Технология |
|---|---|---|
| `check` | лимит слов, блоки, параметры сцен, подпись источника | — |
| `accent` | ударения в сценарии, ручные правки из `voice.accents` | RUAccent `turbo3.1` |
| `voice` | эталон голоса, несколько дублей, отбор по сверке | F5-TTS 1.1 + `Misha24-10/F5-TTS_RUSSIAN`, Piper, faster-whisper |
| `align` | таймкоды каждого слова, границы блоков | faster-whisper `large-v3-turbo` |
| `music` | фон по описанию, громкость, проверка провалов | MusicGen-small |
| `build` | проект HyperFrames на каждый монтаж: сцены, субтитры, звук | HTML/SVG + GSAP 3.14 |
| `snapshot` | кадры до рендера, автопроверка вёрстки, контактный лист | HyperFrames, Chrome |
| `render` | MP4 1080×1920 | HyperFrames 0.8.75 |
| `finish` | постер в первом кадре, замеры, сверка готового звука, отчёт | ffmpeg, faster-whisper |

Контрольные точки: **script** (после `check`), **accents** (после `accent`), **take** (после `voice`),
**frames** (после `snapshot`). Подтверждение привязано к хэшу артефакта: поменялся сценарий — подтвердить нужно
заново. Подробнее — [docs/architecture.md](docs/architecture.md).

## Быстрый старт за 2 минуты

GPU и модели не нужны: в примерах используется готовая озвучка и музыка из `example/`
(`voice.engine: file`, `music.engine: file`). Нужны Python 3.10+, Node.js 22+ и ffmpeg в PATH.

```bash
git clone https://github.com/alteralto/local-ai-reels && cd local-ai-reels
pip install -e .
python -m reels run examples/povodok-templates --yes
```

Готовые ролики — в `examples/povodok-templates/out/`, рядом отчёт `report.md`. При первом запуске `npx`
скачает HyperFrames. Без видеокарты рендер медленнее: на 4 ядрах CPU — около 2 минут на монтаж.

<p align="center"><img src="docs/media/terminal.gif" width="760" alt="Запуск python -m reels run examples/povodok --yes"></p>

Два примера с одной и той же озвучкой:

| Пример | Что показывает |
|---|---|
| [`examples/povodok`](examples/povodok) | точный перенос исходного ролика «Поводок» со своими SVG-сценами (`type: custom`) |
| [`examples/povodok-templates`](examples/povodok-templates) | тот же текст, но только шаблоны из библиотеки — так выглядит ролик без единой строки HTML |

## Свой ролик

```bash
reels new my-reel --from examples/povodok-templates   # заготовка проекта
$EDITOR my-reel/reel.yaml                             # сценарий, сцены, голос, монтажи
reels check my-reel                                   # проверка до озвучки
reels run my-reel                                     # остановится на контрольной точке script
reels approve my-reel script                          # подтвердить и продолжить
reels run my-reel                                     # … accents → take → frames → готово
```

Минимальный `reel.yaml`:

```yaml
title: Невидимый поводок
theme: blueprint
source: "источник: RSPH, «Taking the P***», 2019"
blocks:
  - text: У каждого горожанина есть невидимый поводок, и длину ему отмерил город.
    scene: {type: headline, kicker: "город · радиус свободы", lines: ["Невидимый", "*поводок*."]}
  - text: Каждый пятый выходит из дома реже, чем хочет, — боится не найти туалет.
    scene: {type: fraction, total: 5, highlight: 1, value: "*1* из 5", at: "каждый пятый",
            label: "выходит из дома реже, чем хочет"}
  # …
edits: {linear: "1234", from-end: "4123", suspense: "2134"}
voice: {engine: f5, voices: [denis, dmitri]}
music: {engine: musicgen}
```

Полезные флаги `reels run`: `--yes` — автопилот без остановок, `--edit linear` — только один монтаж,
`--from voice` — перезапуск с этапа, `--only snapshot` — один этап, `--until build` — остановиться после этапа,
`--force` — игнорировать кэш.
`reels status my-reel` показывает, какие этапы готовы и какие точки ждут подтверждения.

## Черновик сценария локальной LLM

```bash
reels draft "почему в городах исчезают общественные туалеты" -o drafts/toilets --facts facts.md
```

`reels draft` спрашивает локальную модель (LM Studio, Ollama или любой OpenAI-совместимый сервер) и пишет
готовый `reel.yaml`: четыре блока «крючок → факт → поворот → панчлайн» в лимите слов, сцены — только из
библиотеки шаблонов. Факты лучше давать файлом (`--facts`), а черновик — проверять: дальше его ждёт контрольная
точка `script`. Почему модели рисуют только шаблонами, а не своими сценами, — см.
[local-model-test/](local-model-test/README.md).

## Сцены-шаблоны

| Шаблон | Для чего |
|---|---|
| `headline` | крупный заголовок с акцентным словом |
| `stat` | одно большое число с подписью |
| `fraction` | «каждый пятый», «2 из 5» — доля фигурками |
| `bars` | столбчатая диаграмма |
| `compare` | «было / стало», «мы / они» |
| `list` | пункты по очереди |
| `quote` | цитата с автором |
| `counter` | число, которое досчитывается под голос |
| `timeline` | шкала времени |
| `map` | схема квартала с точками и радиусами |
| `custom` | своя сцена: HTML/SVG/JS из файлов проекта |

Параметры и примеры — [docs/scenes.md](docs/scenes.md), список в терминале — `reels scenes`.

## Монтажи из одной озвучки

<p align="center"><img src="docs/media/edits.gif" alt="Первые секунды трёх монтажей: linear, from-end, suspense"></p>

Голос синтезируется один раз. Монтаж — это порядок блоков: `linear: "1234"`, `from-end: "4123"`,
`suspense: "2134"`. Куски мастер-файла раскладываются по таймлайну (`data-media-start` у `<audio>`), сцены,
субтитры и переходы пересчитываются сами. Три версии ролика для A/B-теста стоят один синтез.

## Автопроверка

<p align="center"><img src="docs/media/qa.png" width="720" alt="Настоящие дефекты вёрстки: обрезанный заголовок и текст поверх карты"></p>

Это настоящие кадры с дефектами из [local-model-test/](local-model-test/README.md): ни одна из проверенных
локальных моделей до 27B их не заметила. Конвейер ищет такие ошибки по DOM кадра — без «глаз» модели — и
останавливается на точке `frames`. Для человека он собирает контактный лист кадров, чтобы посмотреть ролик
до рендера:

<p align="center"><img src="docs/media/frames.png" alt="Контактный лист: 8 кадров ролика до рендера"></p>

## Пакетный режим

```bash
reels batch projects/*/ --yes      # все проекты подряд, общий кэш моделей
reels status projects/*/           # что готово, что ждёт подтверждения
```

Модели загружаются один раз на пакет. Без `--yes` каждый проект доходит до своей контрольной точки и ждёт —
можно подтвердить пачкой и запустить пакет ещё раз.

## Требования и установка

| | Минимум | Для озвучки и музыки |
|---|---|---|
| Python | 3.10+ | 3.10+ |
| Node.js | 22+ (HyperFrames через `npx`) | 22+ |
| ffmpeg | 6+ в PATH (или папка в `FFMPEG_DIR`) | 6+ |
| GPU | не нужен | NVIDIA с 8+ ГБ (проверено на RTX 3060 12 ГБ) |

```bash
pip install -e .          # конвейер, шаблоны, рендер, примеры с готовой озвучкой
pip install -e .[ml]      # + F5-TTS, RUAccent, faster-whisper, MusicGen (torch с CUDA ставьте заранее)
```

Модели скачиваются с Hugging Face при первом запуске; голоса Piper (`ru_RU-denis-medium`, `ru_RU-dmitri-medium`)
кладутся в `models/piper/`. HyperFrames 0.8.75 запускается через `npx` и при первом запуске скачивается сам,
телеметрия выключена.

Если HyperFrames не может скачать Chrome (ошибка 403 — так бывает в некоторых странах), укажите уже установленный
браузер на Chromium, например Edge на Windows:

```bash
export HYPERFRAMES_BROWSER_PATH="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
```

Пути для macOS и Linux — в [частых ошибках](docs/guide.md#частые-ошибки).

## Ручной режим

Конвейер вырос из набора скриптов, и они остаются в репозитории как пошаговый справочник:

- [`scripts/`](scripts) — `texts`, `accent`, `synth`, `words`, `captext`, `music_gen`, `sfx_lock`, `build`, `polish`,
  `hf.sh`, `finish.sh`: каждый этап отдельной командой;
- [docs/guide.md](docs/guide.md) — подробная инструкция: установка, свой голос, ударения, отбор дублей, границы
  блоков, музыка, монтаж, постер, проверка, частые ошибки; есть раздел для ИИ-агента;
- [docs/guide.pdf](docs/guide.pdf) — та же инструкция одним файлом с кодом всех скриптов (пересборка:
  `python tools/build_pdf.py`);
- [local-model-test/](local-model-test/README.md) — справятся ли небольшие локальные модели со сценами и проверкой
  кадров (коротко: шаблоны заполняют, рисовать и проверять вёрстку — нет).

```bash
mkdir my-reel && cp scripts/* my-reel/ && cp -r example/voice example/music my-reel/
cd my-reel && python build.py 1234 reel-linear && cd reel-linear
bash ../hf.sh render -o renders/reel-linear.mp4 --fps 30 --quality looks
```

## Лицензии

Код — Apache-2.0, см. [LICENSE](LICENSE). Модели в репозиторий не входят, у них свои лицензии: веса F5-TTS для
русского (`Misha24-10/F5-TTS_RUSSIAN`) и MusicGen-small — **CC-BY-NC 4.0, только некоммерческое использование**;
голоса Piper `denis` и `dmitri` — CC0. Озвучка и музыка в `example/` сделаны этими моделями, поэтому тоже только
для некоммерческих целей. Для коммерческой работы берите Qwen3-TTS (Apache-2.0) и стоковую или свою музыку.
Подробности — [NOTICE.md](NOTICE.md).

Чужой голос клонируйте только с согласия его владельца.

## Roadmap

- [ ] Qwen3-TTS как движок озвучки для коммерческих проектов
- [ ] Ещё темы и шаблоны: график-линия, карта мира, «до / после» с фото
- [ ] Английский и другие языки: выравнивание и субтитры без привязки к русскому
- [ ] Горизонтальный и квадратный формат из того же `reel.yaml`
- [ ] Веб-интерфейс для контрольных точек: слушать дубли и листать кадры в браузере
- [ ] Docker-образ с моделями и Chrome

## Contributing

Баг-репорты, новые шаблоны сцен и темы — очень кстати. Как запустить тесты, добавить шаблон и оформить PR —
в [CONTRIBUTING.md](CONTRIBUTING.md). История изменений — [CHANGELOG.md](CHANGELOG.md).
