# local-ai-reels

**Vertical AI reels / shorts (1080×1920) built entirely on local models.** Voice cloning with F5-TTS and Russian
stress marks (RUAccent), word-level timings from faster-whisper, motion graphics written as HTML/SVG and rendered
to MP4 with HyperFrames + GSAP, background music from MusicGen, mixing with ffmpeg. No paid APIs; a 25-second
reel renders in about a minute on an RTX 3060. One voice-over → several edits: blocks are reordered on the
timeline, not re-synthesized. Docs are in Russian.

---

Вертикальные ролики 1080×1920 с озвучкой, субтитрами, графикой и музыкой — на локальных моделях, без платных
сервисов. Ролик на 25 секунд рендерится примерно за минуту на видеокарте уровня RTX 3060.

## Стек

| Этап | Технология | Что делает |
|---|---|---|
| Сценарий | сильная LLM (проверено на Claude Opus 5.5) + `texts.py` | 4 самостоятельных блока, не больше 60 слов; скрипт следит за лимитом |
| Эталон голоса | Piper (`ru_RU-denis`, `ru_RU-dmitri`, CC0) или своя запись | образец речи на 8–10 с |
| Ударения | RUAccent `turbo3.1` | размечает ударения в сценарии, чтобы синтез не ошибался |
| Озвучка | F5-TTS 1.1 + русская модель `Misha24-10/F5-TTS_RUSSIAN` | клонирует голос по образцу, 6 дублей на выбор |
| Сверка и таймкоды | faster-whisper `large-v3-turbo` | отбраковывает дубли с ошибками, даёт время каждого слова |
| Монтаж | HyperFrames 0.8.75 (HTML → MP4, headless Chrome) + GSAP 3.14 | сцены, субтитры и звук на одном таймлайне |
| Графика | SVG-код | планы, схемы, диаграммы — точнее, чем у генераторов картинок |
| Музыка и эффекты | MusicGen-small, numpy/scipy | фон по текстовому описанию, синтезированные звуки |
| Сведение, постер, проверка | ffmpeg | −18 LUFS, постер в первом кадре, замеры |

## Как это устроено

**Один мастер-файл озвучки — много монтажей.** Сценарий делится на четыре блока: крючок → факт → поворот →
панчлайн. Голос синтезируется один раз, а монтажи получаются перестановкой кусков этого файла на таймлайне
(`data-media-start` у `<audio>`):

```bash
python build.py 1234 reel-linear      # 1 → 2 → 3 → 4
python build.py 4123 reel-from-end    # 4 → 1 → 2 → 3
python build.py 1324 reel-suspense    # 1 → 3 → 2 → 4
```

**Клон голоса с ударениями.** F5-TTS повторяет тембр и темп образца, RUAccent заранее ставит ударения, Whisper
проверяет каждый дубль: в монтаж идёт только тот, что распознаётся дословно.

**Графика кодом.** Каждая сцена — HTML и SVG, анимация — один GSAP-таймлайн на весь ролик, привязанный
к пословным таймкодам. Субтитры (`polish.py`) идут фразами по 3–5 слов с подсветкой текущего слова и не рвутся
на предлогах; между блоками — переходы со звуком, сверху — полоска прогресса.

**Проверка до и после рендера.** Кадры `snapshot` смотрятся до рендера, а готовый файл проверяется на размер,
громкость и дословное распознавание текста поверх музыки.

## Быстрый старт на примере

Нужны Python 3.10+ (`numpy`, `scipy`), Node.js 22+ и ffmpeg в PATH. Модели для этого не нужны: озвучка
и таймкоды уже лежат в `example/`.

```bash
mkdir my-reel && cp scripts/* my-reel/ && cp -r example/voice example/music my-reel/
cd my-reel
python build.py 1234 reel-linear
cd reel-linear
bash ../hf.sh snapshot --at 2,8,14,20 --describe false --no-end   # кадры для проверки до рендера
bash ../hf.sh render -o renders/reel-linear.mp4 --fps 30 --quality looks
```

`hf.sh` запускает HyperFrames 0.8.75 через `npx` (при первом запуске он скачается) с выключенной телеметрией.

Если HyperFrames не может скачать Chrome (ошибка 403 — так бывает в некоторых странах), укажите уже установленный
браузер на Chromium, например Edge на Windows:

```bash
export HYPERFRAMES_BROWSER_PATH="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
```

Пути для macOS и Linux — в [частых ошибках](docs/guide.md#частые-ошибки).

## Полный цикл

Пошаговая инструкция — [docs/guide.md](docs/guide.md): установка, свой голос, ударения, отбор дублей, границы
блоков, музыка, монтаж, постер и проверка, частые ошибки. В ней есть раздел для ИИ-агента, который будет собирать
ролик по инструкции.

[docs/guide.pdf](docs/guide.pdf) — та же инструкция одним файлом, с кодом всех скриптов в приложении и во
вложениях PDF. Её можно отдать человеку или агенту без репозитория. Пересборка: `python tools/build_pdf.py`.

## Структура

```
scripts/            texts, accent, synth, words, captext, music_gen, sfx_lock, build, polish, hf.sh, finish.sh
example/            озвучка, пословные таймкоды и музыка примера
docs/               инструкция (md и pdf)
tools/build_pdf.py  сборка PDF из инструкции и скриптов
local-model-test/   справятся ли небольшие локальные модели со сценами и проверкой кадров
```

## Локальные LLM

Механику (запуск скриптов, подстановку текста, границы блоков) можно отдать небольшой локальной модели.
С графикой хуже: ни одна из проверенных моделей до 27B не нашла дефекты вёрстки на кадрах, а сцены, которые они
написали сами, ломали анимацию всего ролика или выходили пустыми. Подробности, кадры и ролики —
[local-model-test/](local-model-test/README.md).

## Лицензии

Код — Apache-2.0, см. [LICENSE](LICENSE). У части моделей и у файлов из `example/` лицензии некоммерческие —
см. [NOTICE.md](NOTICE.md).
