# Лицензии моделей и файлов-примеров

Лицензия Apache-2.0 этого репозитория относится только к коду. Модели в репозиторий не входят, у них свои лицензии:

| Модель | Лицензия | Что это значит |
|---|---|---|
| `Misha24-10/F5-TTS_RUSSIAN` (веса F5-TTS для русского) | CC-BY-NC 4.0 | только некоммерческое использование |
| `Qwen/Qwen3-TTS-12Hz-1.7B-Base`, `Qwen/Qwen3-TTS-12Hz-0.6B-Base` (движок `qwen3`) | Apache-2.0 | можно в коммерческих проектах |
| `facebook/musicgen-small` | CC-BY-NC 4.0 (веса) | только некоммерческое использование |
| Голоса Piper `ru_RU-denis-medium`, `ru_RU-dmitri-medium` | CC0 | без ограничений |
| Голос Piper `irina` (озвучка `examples/venera`, `opyonok`, `oxford` у автора) | не указана в `MODEL_CARD`; датасет RHVoice — GPLv2 | только для демонстрации; озвучка этим голосом в репозиторий не входит |
| Другие голоса Piper | разные | смотреть `MODEL_CARD` голоса: например, у `ruslan` — CC-BY-NC-SA |
| RUAccent, faster-whisper, HyperFrames, GSAP | см. их репозитории | — |

Для коммерческой работы озвучку стоит делать на Qwen3-TTS (Apache-2.0), а музыку брать стоковую или свою.

## Примеры `examples/venera`, `examples/opyonok`, `examples/oxford`

Эти примеры лежат в репозитории **без озвучки**: у автора они озвучены голосом Piper `irina`, лицензия которого
не указана, поэтому звук не распространяется. Озвучьте их сами голосом с понятной лицензией, например CC0
`ru_RU-dmitri-medium` или `ru_RU-denis-medium` (команда — в начале `reel.yaml` каждого примера):
`python -m reels.align examples/<пример> --model <голос Piper .onnx>`. Музыка в них — `example/music/music.wav`
(MusicGen, только некоммерческое использование, см. ниже).

## Файлы в `example/` и `local-model-test/results/`

`example/voice/voice.wav` синтезирован F5-TTS (русская модель под CC-BY-NC) по эталону голоса Piper `dmitri`.
`example/music/music.wav` сгенерирован MusicGen-small. Ролики в `local-model-test/results/` содержат эту же
озвучку и музыку. Используйте эти файлы только в некоммерческих целях.

Чужой голос клонировать можно только с согласия его владельца.
