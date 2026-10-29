# Безопасность

## Поддерживаемые версии

Исправления выходят для последней версии из ветки `main` (сейчас 0.2.x).

## Как сообщить об уязвимости

Не публикуйте детали в открытом issue. Воспользуйтесь
[приватным сообщением об уязвимости](https://github.com/alteralto/local-ai-reels/security/advisories/new) на GitHub
(вкладка Security → Report a vulnerability). Если это недоступно — откройте issue без подробностей и попросите
связаться с вами. Ответим в течение недели.

## Что важно знать

- `reels` запускает внешние программы: ffmpeg, Node.js/`npx` (HyperFrames) и headless Chrome. Сцены — это HTML и JS,
  которые выполняются в браузере при рендере. **Не запускайте чужие проекты (`reel.yaml`, сцены `type: custom`)
  без просмотра**, как не запускали бы чужой скрипт.
- Параметры шаблонов из `reel.yaml` экранируются (`esc()` / `rich()`), но сцены `custom` вставляются как есть.
- `reels draft` отправляет тему и файл фактов на указанный OpenAI-совместимый сервер (по умолчанию локальный
  `http://localhost:1234/v1`). Ответ модели — данные: он проверяется на `check` и ждёт подтверждения на точке `script`.
- Модели скачиваются с Hugging Face; проверяйте, откуда берёте веса.

---

*English:* report vulnerabilities privately via
[GitHub security advisories](https://github.com/alteralto/local-ai-reels/security/advisories/new), not in public
issues. Do not run untrusted projects: custom scenes are HTML/JS executed in headless Chrome during rendering.
