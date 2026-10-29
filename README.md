<h1 align="center">local-ai-reels</h1>

<p align="center"><b>Тема → готовый вертикальный ролик 1080×1920 с голосом, субтитрами, графикой и музыкой. Локально, без платных API.</b></p>

<p align="center">
  <a href="https://github.com/alteralto/local-ai-reels/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/alteralto/local-ai-reels/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/alteralto/local-ai-reels/actions/workflows/lint.yml"><img alt="lint" src="https://github.com/alteralto/local-ai-reels/actions/workflows/lint.yml/badge.svg"></a>
  <a href="https://github.com/alteralto/local-ai-reels/actions/workflows/rust.yml"><img alt="rust" src="https://github.com/alteralto/local-ai-reels/actions/workflows/rust.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg">
  <img alt="Node 22+" src="https://img.shields.io/badge/node-22%2B-339933.svg">
</p>

> **English:** a local conveyor for vertical AI reels / shorts. Type a topic in the desktop app — or write
> `reel.yaml` and run `python -m reels run` — and get finished MP4s: voice cloning (F5-TTS / Qwen3-TTS / Piper),
> Whisper-verified takes, word-synced subtitles, motion graphics in HTML/SVG + GSAP, ducked music, automated
> layout QA, covers and subtitles for publishing. Runs on GPU or CPU, no paid APIs.
> **[Read in English →](README.en.md)**

<p align="center"><img src="docs/media/hero.gif" width="320" alt="Пример ролика: сцены-схемы, субтитры с подсветкой текущего слова, переходы между блоками"></p>

## Для кого

<table>
<tr>
<th width="50%">Обычным людям</th>
<th width="50%">ML-инженерам</th>
</tr>
<tr>
<td valign="top">

- **Настольное приложение**: написали, о чём ролик, выбрали оформление, нажали «Сделать ролик» — получили
  готовое видео. Сценарий пишет локальная LLM, дальше всё идёт само.
- Установщики под Linux, macOS и Windows сами определяют видеокарту.
- Командная строка не нужна. Видеокарта желательна, но не обязательна.
- [Как пользоваться → app/desktop](app/desktop/README.md)

<img src="app/desktop/screenshot-simple-done.png" alt="Reels Desktop, режим «Просто»: готовый ролик, «Сохранить видео», «Открыть папку»">

Из терминала почти так же просто: свои 4 абзаца → проект со сценами-заголовками:
`python -m reels new my-reel --text text.txt --theme night`.

</td>
<td valign="top">

- CLI и Python-пакет: проект — один `reel.yaml`, этапы с кэшем и контрольными точками.
- Профили железа (GPU / Apple / CPU), `reels doctor`, ручной выбор устройства и потоков.
- Сменные модели: F5-TTS, Qwen3-TTS, Piper, своя запись; Whisper, MusicGen.
- Rust-конвейер `reels-app` для пропускной способности на пачке роликов, Docker-образ,
  фабрика роликов по списку тем.
- Машиночитаемый контракт: `status --json`, `doctor --json`, коды выхода `0/1/2/3`.

</td>
</tr>
</table>

## Что умеет

- **Одна озвучка — много монтажей.** Блоки переставляются на таймлайне (`1234`, `4123`, `1324`), голос не
  синтезируется заново.
- **Клон голоса с ударениями.** F5-TTS + RUAccent (знак `+`), Qwen3-TTS для коммерции, Piper для CPU.
- **Сверка Whisper.** Каждый дубль распознаётся и сравнивается со сценарием, в монтаж идёт дословный.
- **Шаблоны сцен с якорями на слова диктора**: `at: "каждый пятый"` — анимация стартует ровно на этих словах.
- **Темы**: `blueprint`, `night`, `paper`, `mono` или свой акцентный цвет.
- **Автопроверка вёрстки** по DOM каждого кадра до рендера; после рендера — длительность, громкость, сверка текста.
- **Музыка с приглушением**: тише под голосом, громче в паузах и на финальной карточке.
- **Всё для публикации**: субтитры SRT/VTT, обложки 9:16 и 4:5, текст поста с хэштегами, отчёт.
- **Кэш и контрольные точки**: этап не перезапускается без нужды; человек подтверждает сценарий, ударения,
  дубль и кадры — или включает автопилот `--yes`.
- **Фабрика**: файл с темами → черновики локальной LLM → ролики и сводка.
- **`reels doctor`**: что за железо, какой профиль выбран, чего не хватает и как это поставить.

## Быстрый старт

Пример работает **без видеокарты и без моделей**: озвучка и музыка уже лежат в репозитории.

**Linux / macOS**

```bash
git clone https://github.com/alteralto/local-ai-reels && cd local-ai-reels
bash install.sh                 # .venv, PyTorch под ваше железо, пакет, проверка; --help — флаги
source .venv/bin/activate
python -m reels run examples/povodok-templates --yes
```

**Windows** (PowerShell)

```powershell
git clone https://github.com/alteralto/local-ai-reels; cd local-ai-reels
powershell -ExecutionPolicy Bypass -File install.ps1
.\.venv\Scripts\Activate.ps1
python -m reels run examples/povodok-templates --yes
```

**Docker** (CPU, готовая озвучка):

```bash
docker build -t local-ai-reels . && docker run --rm -v "$PWD:/work" local-ai-reels run examples/povodok-templates --yes
```

Ролики появятся в `examples/povodok-templates/out/`, рядом `report.md`. Нужны ffmpeg и Node.js 22+ —
установщик подскажет команду, если их нет.

<p align="center"><img src="docs/media/terminal.gif" width="760" alt="Запуск python -m reels run на примере"></p>

| Пример | Что показывает |
|---|---|
| [`examples/povodok`](examples/povodok) | перенос исходного ролика со своими SVG-сценами (`type: custom`) |
| [`examples/povodok-templates`](examples/povodok-templates) | тот же текст только на шаблонах — ни строки HTML |

Ещё три ролика — в разделе [«Другие ролики»](#другие-ролики).

## Как это работает

<p align="center"><img src="docs/media/pipeline.svg" alt="Этапы: reel.yaml → check → accent → voice → align → music → build → snapshot → render → finish → out/*.mp4; контрольные точки script, accents, take, frames"></p>

| Этап | Что делает | Чем |
|---|---|---|
| `check` | лимит слов, блоки, параметры сцен, якоря | — |
| `accent` | ударения, ручные правки `voice.accents` | RUAccent |
| `voice` | эталон голоса, дубли, отбор по сверке | F5-TTS / Qwen3-TTS / Piper / файл + faster-whisper |
| `align` | таймкоды слов, границы блоков | faster-whisper |
| `music` | фон по описанию, −18 LUFS, приглушение | MusicGen / файл |
| `build` | проект HyperFrames на каждый монтаж | HTML/SVG + GSAP |
| `snapshot` | кадры до рендера, автопроверка, контактный лист | HyperFrames, Chrome |
| `render` | MP4 1080×1920 | HyperFrames |
| `finish` | постер, замеры, сверка звука, субтитры, обложки, пост, отчёт | ffmpeg, faster-whisper |

Контрольные точки: **script** → **accents** → **take** → **frames**. Подтверждение привязано к хэшу артефакта:
поменялся сценарий — подтвердить заново. Подробно — [docs/architecture.md](docs/architecture.md).

## Скорость и железо

Профиль ускорения выбирается сам; посмотреть выбор и чего не хватает — `python -m reels doctor`.

| Профиль | Когда | Что происходит |
|---|---|---|
| `gpu-max` | NVIDIA от 8 ГБ | всё на видеокарте, модели держатся в памяти, постер — NVENC |
| `gpu-lowvram` | NVIDIA меньше 8 ГБ | модели по очереди, Whisper в `int8_float16`, 1 вариант музыки |
| `apple` | Apple Silicon | F5 / MusicGen на MPS, Whisper на CPU, постер — VideoToolbox |
| `cpu-fast` | нет видеокарты | F5 / Qwen3 → Piper, Whisper `small` int8, MusicGen пропускается |
| `cpu-quality` | только вручную | всё как задумано, но на CPU — медленно |

Задать вручную: `--profile`, `--device cuda:1`, `--threads 8`, `--allow-slow` (или `hardware:` в `reel.yaml`,
или `REELS_PROFILE` / `REELS_DEVICE` / `REELS_THREADS`). Подробно — [docs/hardware.md](docs/hardware.md).

Что известно о скорости:

- монтаж на 24 с рендерится около 2 минут на 4 ядрах CPU; на компьютере с RTX 3060 — около минуты (замер автора);
- в CI пример целиком проходит на обычном раннере GitHub без видеокарты меньше чем за 3 минуты;
- на пачке роликов `reels-app run` ведёт их конвейером: пока ролик A рендерится на процессоре, ролик B уже
  озвучивается на видеокарте ([app/README.md](app/README.md)).

## Свой ролик

```bash
python -m reels new my-reel --from examples/povodok-templates   # заготовка
$EDITOR my-reel/reel.yaml                                       # сценарий, сцены, голос, монтажи
python -m reels check my-reel                                   # проверка до озвучки
python -m reels run my-reel                                     # остановится на точке script
python -m reels approve my-reel script                          # подтвердить и продолжить
python -m reels run my-reel                                     # … accents → take → frames → готово
```

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
  - text: У двоих из пяти есть болезни, при которых туалет нужен часто.
    scene: {type: stat, value: "*43* %", label: "людей с болезнями, требующими частого туалета",
            at: "двоих из пяти"}
  # …
edits: {linear: "1234", from-end: "4123", suspense: "1324"}
voice: {engine: f5, voices: [denis, dmitri]}      # f5 | qwen3 | piper | file
music: {engine: musicgen, duck: true}             # musicgen | file | none
```

Флаги `run`: `--yes` автопилот, `--edit linear` один монтаж, `--from voice` / `--only snapshot` /
`--until build` — этапы, `--force` без кэша, `--qa warn` не останавливаться на замечаниях вёрстки,
`--no-input` не спрашивать в терминале. Состояние — `python -m reels status my-reel` (или `--json`).

Что получится в `my-reel/out/` на каждый монтаж:

| Файл | Что |
|---|---|
| `<slug>-<монтаж>.mp4` | ролик 1080×1920 с постером в первом кадре |
| `<slug>-<монтаж>-frames.jpg` | контактный лист кадров |
| `<slug>-<монтаж>.srt`, `.vtt` | субтитры, фразы как на экране |
| `<slug>-<монтаж>-cover.jpg`, `-cover-4x5.jpg` | обложки 1080×1920 и 1080×1350 |
| `<slug>-<монтаж>.txt` | текст поста: заголовок, текст, источник, хэштеги |
| `report.md`, `report.json` | замеры, громкость, сверка текста, замечания |

## Сценарий от локальной LLM

```bash
python -m reels draft "почему в городах исчезают общественные туалеты" -o drafts/toilets --facts facts.md
```

Любой OpenAI-совместимый сервер (LM Studio, Ollama; `--url`, `--model` или `REELS_LLM_URL` / `REELS_LLM_MODEL`).
Получается `reel.yaml` из четырёх блоков «крючок → факт → поворот → вывод» на шаблонах библиотеки. Цифры
модель берёт только из `--facts`; черновик всё равно ждёт подтверждения на точке `script`. Почему модели
заполняют шаблоны, а не рисуют сцены сами, — [local-model-test/](local-model-test/README.md).

## Фабрика

```text
# topics.txt — тема на строку, факты после « | » или с отступом
Почему в городе нет туалетов | RSPH, 2019: каждый пятый выходит из дома реже, чем хочет
Как работает светофор
    Первый электрический светофор — Кливленд, 1914.
```

```bash
python -m reels factory topics.txt -o reels/ --yes
```

Для каждой темы — черновик и ролик; остановки и ошибки не роняют фабрику, повторный запуск продолжает с того же
места. Сводка — `reels/factory.md` и `factory.json`. Несколько готовых проектов подряд — `python -m reels batch a b c`.

## Другие ролики

<p align="center"><img src="docs/media/examples.png" alt="Кадры трёх роликов: «Венера» (тема night), «Опёнок» (paper), «Оксфорд» (mono)"></p>

| Пример | Тема | О чём |
|---|---|---|
| [`examples/venera`](examples/venera) | `night` | день на Венере длиннее её года (NASA) |
| [`examples/opyonok`](examples/opyonok) | `paper` | самое большое живое существо — опёнок в Орегоне (Ferguson et al., 2003) |
| [`examples/oxford`](examples/oxford) | `mono` | в Оксфорде учили раньше, чем появился Теночтитлан |

Только шаблоны библиотеки, озвучка Piper на CPU, таймкоды слов — из длительностей фонем Piper, без Whisper.
Озвучки в репозитории нет (лицензия голоса не указана, см. [NOTICE.md](NOTICE.md)) — сделайте её своим голосом
Piper, лучше CC0 `ru_RU-dmitri-medium` или `ru_RU-denis-medium`:

```bash
python -m reels.align examples/venera --model models/piper/ru_RU-dmitri-medium.onnx
python -m reels run examples/venera --yes
```

<details><summary>Три ролика в движении (GIF, без звука)</summary>

<p align="center"><img src="docs/media/examples.gif" alt="Три ролика рядом: Венера, Опёнок, Оксфорд"></p>

</details>

## Сцены и темы

![Все шаблоны сцен](docs/media/gallery/scenes.png)

`headline`, `stat`, `fraction`, `bars`, `compare`, `list`, `quote`, `counter`, `timeline`, `map` и `custom` — своя
сцена на HTML/SVG/JS. Параметры и примеры — [docs/scenes.md](docs/scenes.md), в терминале — `python -m reels scenes`.

![Темы оформления](docs/media/gallery/themes.png)

## Монтажи из одной озвучки

<p align="center"><img src="docs/media/edits.gif" alt="Первые секунды трёх монтажей: linear, from-end, suspense"></p>

Монтаж — порядок блоков: `linear: "1234"`, `from-end: "4123"`, `suspense: "1324"`. Куски мастер-файла
раскладываются по таймлайну, сцены, субтитры и переходы пересчитываются сами. Три версии для A/B-теста — один синтез.

## Автопроверка

<p align="center"><img src="docs/media/qa.png" width="720" alt="Настоящие дефекты вёрстки: обрезанный заголовок и текст поверх карты"></p>

Настоящие дефекты из [local-model-test/](local-model-test/README.md): локальные модели до 27B их не заметили.
Конвейер находит их по DOM кадра — выход за край, наложения, залезание в зону субтитров — и останавливается
на точке `frames`. Для человека — контактный лист:

<p align="center"><img src="docs/media/frames.png" alt="Контактный лист: 8 кадров ролика до рендера"></p>

## Приложения

| | Что | Подробно |
|---|---|---|
| **Reels Desktop** | окно на Tauri. «Просто»: тема → готовое видео. «Эксперт»: проекты, этапы, выбор дубля, кадры, журнал | [app/desktop/README.md](app/desktop/README.md) |
| **reels-app** | Rust-конвейер по многим проектам: `run` (слоты GPU и CPU), `status`, `watch` (папка-входящие и `topics.txt`), `tui` | [app/README.md](app/README.md) |

| «Просто» | «Эксперт» |
|---|---|
| <img src="app/desktop/screenshot-simple.png" alt="Reels Desktop, режим «Просто»: тема, оформление и шаги работы"> | <img src="app/desktop/screenshot.png" alt="Reels Desktop, режим «Эксперт»: проекты, этапы, выбор дубля на точке take"> |

Оба приложения не дублируют логику: они вызывают `python -m reels` и читают `status --json`.

## Голоса

| Движок | Когда | Видеокарта | Лицензия весов |
|---|---|---|---|
| `f5` | некоммерческие ролики, точные ударения | нужна | CC-BY-NC 4.0 |
| `qwen3` | коммерческие ролики, клон голоса | нужна | Apache-2.0 |
| `piper` | CPU, черновики, эталоны для клонирования | не нужна | `denis`, `dmitri` — CC0 |
| `file` | своя запись | не нужна | ваша |

Настройка и установка каждого — [docs/voices.md](docs/voices.md).

## Установка подробно

- Установщики: `bash install.sh [--cpu | --gpu | --full] [--models] [--no-venv]`,
  `install.ps1 [-Cpu | -Gpu | -Full] [-Models] [-NoVenv]`. Без установщика: `pip install -e .`
  (+ `.[ml]` для F5, RUAccent, Whisper, MusicGen; PyTorch ставится заранее под вашу CUDA).
- Модели одной командой: `python tools/fetch_models.py` (F5 + Piper; `--whisper --musicgen`, `--dry-run`).
  Hugging Face недоступен — зеркало: `HF_ENDPOINT=https://<зеркало> python tools/fetch_models.py`.
- HyperFrames не может скачать Chrome (ошибка 403) — укажите установленный браузер в `HYPERFRAMES_BROWSER_PATH`:
  - Windows: `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`
  - macOS: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
  - Linux: `/usr/bin/chromium` или `/usr/bin/google-chrome`
- Профили, драйверы, PyTorch под CUDA и что ставить на каждой ОС — [docs/hardware.md](docs/hardware.md).

## Ручной режим

Конвейер вырос из скриптов, и они остались как пошаговый справочник:

- [`scripts/`](scripts) — каждый этап отдельной командой;
- [docs/guide.md](docs/guide.md) — подробная инструкция (есть [PDF](docs/guide.pdf) с кодом скриптов);
- [local-model-test/](local-model-test/README.md) — справятся ли небольшие локальные модели со сценами и проверкой кадров.

## Лицензии

Код — Apache-2.0 ([LICENSE](LICENSE)). Модели в репозиторий не входят: веса F5-TTS для русского и MusicGen —
**CC-BY-NC 4.0, только некоммерческое использование**; Qwen3-TTS — Apache-2.0, его и берите для коммерции;
голоса Piper `denis` и `dmitri` — CC0. Озвучка и музыка в `example/` — только для некоммерческих целей.
Подробно — [NOTICE.md](NOTICE.md). Чужой голос клонируйте только с согласия владельца.

## Roadmap

- [ ] Английский и другие языки: ударения, выравнивание и субтитры без привязки к русскому
- [ ] Горизонтальный и квадратный формат из того же `reel.yaml`
- [ ] Новые шаблоны: график-линия, карта мира, «до / после» с фото
- [ ] Docker-образ для видеокарты с моделями
- [ ] Параллельная фабрика (`factory --jobs` пока поддерживает только 1)

## Contributing

Баг-репорты, шаблоны сцен, темы и правки документации — очень кстати: [CONTRIBUTING.md](CONTRIBUTING.md).
История изменений — [CHANGELOG.md](CHANGELOG.md).
