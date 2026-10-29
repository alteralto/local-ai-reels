# Архитектура конвейера

Пакет `reels/` — конвейер: `reel.yaml` → готовые `out/<slug>-<монтаж>.mp4` с отчётом. Запуск: `python -m reels`
(или команда `reels` после `pip install -e .`).

```
reel.yaml ─► check ─► accent ─► voice ─► align ─► music ─► build ─► snapshot ─► render ─► finish
             (gate)   (gate)    (gate)                              (gate)
```

Каждый этап — функция `stage(project, ctx)`; конвейер (`pipeline.py`) хранит хэш входов каждого этапа
в `.reels-state.json` и пропускает этап, если входы не менялись и выходы на месте. Контрольные точки (gate)
останавливают конвейер до подтверждения человеком: `reels approve <проект> <gate>` или `--yes` (автопилот).
Подтверждение привязано к хэшу артефакта: поменялся сценарий — нужно подтвердить заново.

## Модули и контракты

| Модуль | Что | Главные функции |
|---|---|---|
| `project.py` | загрузка и проверка `reel.yaml`, значения по умолчанию, пути | `load(path) -> Project`, `Project.script`, `.edits`, `.master_wav`, `.words_json`, `.timing_json`, `.music_wav`, `.edit_dir(e)`, `.out_mp4(e)`, `.path(p)` |
| `timing.py` | слова сценария ↔ Whisper, границы блоков, раскладка монтажа, якоря | `aligned`, `block_cuts`, `place`, `block_words`, `anchor` |
| `util.py` | вывод, запуск команд, ffmpeg, хэши, устройство | `say`, `run`, `ff`, `sha`, `device`, `read_json`, `write_json`, `wav_duration`, `ToolError` |
| `textcheck.py` | проверка сценария до озвучки | `check_script(project) -> list[Issue]` |
| `asr.py` | faster-whisper: распознавание, пословные таймкоды, сверка | `transcribe(path, cfg) -> str`, `words(path, cfg) -> list[dict]`, `ratio(script, heard) -> float` |
| `voice.py` | ударения (RUAccent), озвучка (F5-TTS / Piper / файл), отбор дубля | `accent(project) -> Path`, `synthesize(project) -> dict`, `timestamps(project) -> Path` |
| `music.py` | музыка (MusicGen / файл / без), громкость −18 LUFS, проверка провалов | `make_music(project, seconds) -> Path \| None`, `dips(path) -> list` |
| `sfx.py` | синтез звуков: вжух на склейке, щелчок, поп | `whoosh(path)`, `click(path)`, `pop(path)` |
| `scenes/` | библиотека шаблонов сцен (`@scene`), темы | `REGISTRY`, `render_scene(ctx, params)`, `Theme` |
| `themes.py` | темы оформления | `get_theme(spec) -> Theme`, `THEMES` |
| `compose.py` | сборка проекта HyperFrames для монтажа | `build_edit(project, edit, order, timing) -> Path`, `snapshot_times(project, order, timing) -> list[float]`, `poster_time(project, order, timing) -> float` |
| `render.py` | HyperFrames: lint, snapshot, render; поиск браузера | `lint(dir)`, `snapshot(dir, times) -> list[Path]`, `render(dir, out, fps, quality) -> Path` |
| `qa.py` | автопроверка вёрстки кадров по DOM и контактный лист | `layout_check(dir, times) -> list[dict]`, `contact_sheet(pngs, out) -> Path` |
| `finish.py` | постер в кадр 0, замеры, сверка готового звука, отчёт | `poster(src, at, dst)`, `probe(mp4) -> dict`, `loudness(path) -> float`, `verify(mp4, script, asr_cfg) -> dict` |
| `draft.py` | черновик `reel.yaml` по теме через локальную LLM (OpenAI-совместимый API) | `draft(topic, out_dir, ...) -> Path` |
| `pipeline.py` | этапы, кэш, контрольные точки, пакетный запуск | `run(project, ...)`, `STAGES` |
| `cli.py` | команды `new check run batch status approve draft scenes ...` | `main(argv)` |

### Таймкоды (`build/voice/timing.json`)

```json
{"cuts": [0.0, 4.2, 11.44, 17.6, 22.4],
 "words": [{"w": "У", "s": 0.0, "e": 0.52}, ...]}
```

`words` — слова **сценария** (пунктуация и регистр авторские) с таймкодами Whisper в мастер-файле,
`cuts` — границы блоков (N+1 чисел), считаются автоматически по числу слов в блоках (`timing.block_cuts`).

### Сцены

Шаблон — функция `(ctx: SceneCtx, params: dict) -> SceneOut(html, css, js)`, регистрируется `@scene(name, SceneDoc)`.
В `reel.yaml` у блока: `scene: {type: <имя>, ...параметры}`. Время анимации задаётся якорями на слова диктора:
`at: "каждый пятый"` — начало фразы, `"туалет@end"` — конец слова, `"40%"` — доля блока, `1.5` — секунды.
Подробно — `reels/scenes/base.py` и каталог `docs/scenes.md`.

Правила для шаблонов: CSS начинается с `ctx.sel`; JS пишет только в `tl` с позициями `t + <секунды>`;
параметры попадают в HTML только через `esc()`/`rich()`; ничего не ставить ниже 1450 px (там субтитры).
