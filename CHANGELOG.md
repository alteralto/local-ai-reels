# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] — конвейер

### Добавлено

- Пакет `reels/` и команда `python -m reels` (`reels` после `pip install -e .`): `new`, `check`, `run`, `approve`,
  `status`, `batch`, `draft`, `scenes`, `clean`.
- Проект ролика описывается одним файлом `reel.yaml`: сценарий по блокам, сцены, тема, голос, музыка, монтажи.
- Этапы `check → accent → voice → align → music → build → snapshot → render → finish` с кэшем по хэшу входов
  (`.reels-state.json`) и перезапуском `--from` / `--only` / `--until` / `--force`.
- Контрольные точки `script`, `accents`, `take`, `frames`; подтверждение `reels approve` привязано к хэшу артефакта,
  автопилот — `--yes`.
- Библиотека шаблонов сцен: `headline`, `stat`, `fraction`, `bars`, `compare`, `list`, `quote`, `counter`,
  `timeline`, `map`, `custom`; якоря анимации на слова диктора (`at: "каждый пятый"`).
- Темы `blueprint`, `night`, `paper`, `mono` и свой акцентный цвет.
- Автопроверка вёрстки кадров по DOM до рендера и контактный лист кадров; после рендера — замеры, громкость
  и сверка текста Whisper; отчёт `out/report.md` и `report.json`.
- `reels draft` — черновик `reel.yaml` по теме через локальную LLM (LM Studio, Ollama, OpenAI-совместимый API).
- `reels batch` — пакетный запуск многих проектов со сводкой.
- Примеры `examples/povodok` (перенос исходного ролика со своими SVG-сценами) и `examples/povodok-templates`
  (тот же текст на шаблонах); оба работают без GPU на готовой озвучке.
- README на русском и английском, картинки и GIF в `docs/media/` и их генератор `tools/make_media.py`;
  CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, шаблоны issue и PR.

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
