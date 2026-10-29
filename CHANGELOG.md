# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] — конвейер

### Конвейер и CLI

- Пакет `reels/` и команда `python -m reels` (`reels` после установки): `new`, `check`, `run`, `batch`, `status`,
  `approve`, `clean`, `scenes`, `draft`, `factory`, `doctor`.
- Проект ролика — один файл `reel.yaml`: сценарий по блокам, сцены, тема, голос, музыка, монтажи, постер, железо.
- Этапы `check → accent → voice → align → music → build → snapshot → render → finish` с кэшем по хэшу входов
  (`.reels-state.json`); перезапуск `--from` / `--only` / `--until` / `--force`, один монтаж — `--edit`.
- Контрольные точки `script`, `accents`, `take`, `frames`; `reels approve` привязан к хэшу артефакта; автопилот
  `--yes`, режим без вопросов `--no-input`, коды выхода `0/1/2/3`.
- `reels new --text` — проект из своих четырёх абзацев со сценами-заголовками; `reels new --from` — из образца.
- `reels status --json` и `reels doctor --json` — машиночитаемый контракт для приложений.

### Голос, звук, музыка

- Движки озвучки `f5` (F5-TTS + RUAccent), `qwen3` (Qwen3-TTS, Apache-2.0 — для коммерческих роликов), `piper`,
  `file`; сверка каждого дубля Whisper и отбор дословного.
- `python -m reels.align` — озвучка Piper с пословными таймкодами из длительностей фонем, без Whisper.
- Музыка MusicGen / файл / без музыки; громкость −18 LUFS; приглушение под голосом (`music.duck`).
- Синтезированные звуки на склейках.

### Графика и проверка

- Библиотека шаблонов сцен: `headline`, `stat`, `fraction`, `bars`, `compare`, `list`, `quote`, `counter`,
  `timeline`, `map`, `custom`; якоря анимации на слова диктора (`at: "каждый пятый"`).
- Темы `blueprint`, `night`, `paper`, `mono` и свой акцентный цвет; каталог `docs/scenes.md` и галерея собираются
  из кода (`tools/make_scenes_doc.py`, `tools/make_gallery.py`).
- Рендер без CDN: локальная копия GSAP рядом с `index.html` (`REELS_GSAP` или кэш), CDN — только запасной путь.
- Автопроверка вёрстки кадров по DOM до рендера, контактный лист; после рендера — замеры, громкость и сверка
  текста; отчёт `out/report.md` и `report.json`; `--qa warn` — только предупреждать.

### Публикация

- Рядом с каждым mp4: субтитры `.srt` / `.vtt`, обложки `-cover.jpg` (9:16) и `-cover-4x5.jpg` (4:5), текст поста
  `.txt`, контактный лист `-frames.jpg`; постер в первом кадре (`poster`).

### Сценарии и пакетный режим

- `reels draft` — черновик `reel.yaml` по теме через локальную LLM (LM Studio, Ollama, OpenAI-совместимый API),
  цифры — только из `--facts`.
- `reels factory` — список тем → черновики → ролики, сводка `factory.md` / `factory.json`, продолжение с места
  остановки.
- `reels batch` — несколько проектов подряд со сводкой.

### Железо и установка

- Профили ускорения `gpu-max`, `gpu-lowvram`, `apple`, `cpu-fast`, `cpu-quality` с автоопределением; `--profile`,
  `--device`, `--threads`, `--allow-slow`, раздел `hardware` в `reel.yaml`; замена F5/Qwen3 на Piper и пропуск
  MusicGen без видеокарты; аппаратный кодировщик постера.
- `reels doctor` — отчёт о железе, выбранном профиле и недостающих зависимостях с командами под ОС.
- Установщики `install.sh` (Linux, macOS) и `install.ps1` (Windows) с определением видеокарты.
- `Dockerfile` для CPU; `tools/fetch_models.py` — модели одной командой, зеркало через `HF_ENDPOINT`.

### Приложения

- `app/reels-app` (Rust): конвейер по многим проектам со слотами GPU и CPU (озвучка следующего ролика идёт
  параллельно с рендером предыдущего), `status`, `watch` (папка-входящие и `topics.txt`), `tui`.
- `app/desktop` (Tauri): настольное приложение с режимами «Просто» (тема → готовое видео) и «Эксперт» (этапы,
  контрольные точки, выбор дубля, кадры, журнал); установщики собирает CI.

### Примеры, документация, CI

- Примеры `examples/povodok` (свои SVG-сцены) и `examples/povodok-templates` (только шаблоны) — работают без GPU
  на готовой озвучке; `examples/venera`, `examples/opyonok`, `examples/oxford` — темы `night`, `paper`, `mono`
  (озвучка не входит в репозиторий, делается своим голосом Piper).
- README на русском и английском, `docs/architecture.md`, `docs/scenes.md`, `docs/voices.md`, `docs/hardware.md`,
  картинки и GIF в `docs/media/` (`tools/make_media.py`); CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, шаблоны issue и PR.
- CI: тесты на Linux, Windows и macOS, полный прогон примера, ruff, Rust (fmt, clippy, тесты), сборка
  настольного приложения; релиз в PyPI по тегу (`tools/bump_version.py`).

### Без изменений

- Скрипты `scripts/` и инструкция `docs/guide.md` остаются как ручной режим и справочник.

## [0.1.0] — скрипты

### Добавлено

- Скрипты пошаговой сборки ролика: `texts`, `accent` (RUAccent), `synth` (F5-TTS / Piper), `words`
  (faster-whisper), `captext`, `music_gen` (MusicGen), `sfx_lock`, `build` + `polish` (HyperFrames + GSAP),
  `hf.sh`, `finish.sh`.
- Пример «Поводок»: озвучка, пословные таймкоды и музыка в `example/`; три монтажа из одной озвучки.
- Инструкция `docs/guide.md` и `docs/guide.pdf` (сборка `tools/build_pdf.py`).
- `local-model-test/` — проверка небольших локальных моделей на рисовании сцен и поиске дефектов вёрстки.

[Unreleased]: https://github.com/alteralto/local-ai-reels/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/alteralto/local-ai-reels/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/alteralto/local-ai-reels/releases/tag/v0.1.0
