# Железо и ускорение

Конвейер сам смотрит, что есть на компьютере, и выбирает **профиль ускорения**: есть видеокарта — всё
тяжёлое идёт на неё; нет — модели на CPU подбираются так, чтобы ролик собирался за минуты, а не за часы.
Профиль можно задать вручную в `reel.yaml`, переменной окружения или флагом.

Что выбрано и почему, чего не хватает и сколько примерно займёт ролик на этом компьютере:

```bash
python -m reels doctor              # отчёт по-русски
python -m reels doctor my-reel      # с учётом движков и раздела hardware проекта
python -m reels doctor --json       # для приложений
```

## Профили

| Профиль | Когда выбирается сам | Озвучка (F5 / Qwen3) | Whisper | MusicGen | Видеопамять | Кодировщик постера |
|---|---|---|---|---|---|---|
| `gpu-max` | NVIDIA от 8 ГБ | на видеокарте | `asr.model` (large-v3-turbo), float16, CUDA | все промпты, на видеокарте | модели держатся | `h264_nvenc` |
| `gpu-lowvram` | NVIDIA меньше 8 ГБ | на видеокарте | тот же, `int8_float16` | musicgen-small, 1 вариант | модели по очереди, очистка между этапами | `h264_nvenc` |
| `apple` | Mac на Apple Silicon (torch видит MPS) | на MPS | на CPU, int8, потоки = физические ядра (у CTranslate2 нет MPS) | все промпты, на MPS | — | `h264_videotoolbox` |
| `cpu-fast` | видеокарты нет | **заменяется на Piper** (если не `allow_slow`) | `small`, int8, потоки = физические ядра | **пропуск** (с `allow_slow` — 1 вариант на CPU) | — | `libx264` |
| `cpu-quality` | только вручную | F5 / Qwen3 на CPU, без замен | `asr.model`, int8 на CPU | все промпты на CPU | — | `libx264` |

Во всех профилях:

* **потоки CPU** (`threads`, по умолчанию все физические ядра) → `torch.set_num_threads`, `OMP_NUM_THREADS`,
  `MKL_NUM_THREADS`, `cpu_threads` у Whisper. Уже заданные в окружении `OMP_NUM_THREADS`/`MKL_NUM_THREADS`
  не трогаются, если `threads` не задан явно;
* **рендер HyperFrames** получает `--workers N`: половина логических ядер, не больше 6 и примерно по 2 ГБ ОЗУ
  на воркер (каждый воркер — отдельный Chrome);
* **постер** перекодируется аппаратным кодировщиком, если он есть; не сработал — автоматический повтор на `libx264`.

Замены движков делаются **только когда этап действительно выполняется**. Озвучка, сделанная F5 на видеокарте,
не переделывается Piper'ом, если потом открыть проект на ноутбуке: готовые этапы берутся из кэша.
Если сработала замена, в `build/voice/choice.json` появятся `"fallback": "piper"` и `"requested": "f5"`;
если пропущена музыка — в `build/music/choice.json` будет `"skipped"` с причиной.

Почему без видеокарты именно так: F5-TTS делает 6 дублей (2 голоса × 3 seed), каждый на 4 ядрах CPU идёт
несколько минут, Qwen3-TTS ещё медленнее; MusicGen — 3–6 минут на вариант. Piper и Whisper `small` на тех же
4 ядрах укладываются в секунды. Минус `small`: он слышит русский хуже, поэтому сверка дублей и готового микса
(порог 98 %) может чаще показывать предупреждения. Нужна точность — `hardware.whisper_model: large-v3-turbo`
(медленнее раза в четыре).

## Как задать вручную

Приоритет: **флаг / переменная окружения → `reel.yaml` → автоопределение.**

```yaml
# reel.yaml — все поля необязательные, ниже значения по умолчанию
hardware:
  profile: auto            # auto | gpu-max | gpu-lowvram | cpu-fast | cpu-quality | apple
  device: auto             # auto | cuda | cuda:N | mps | cpu — устройство torch (F5, Qwen3, MusicGen)
  cuda_device: 0           # номер видеокарты, как в nvidia-smi
  threads: 0               # потоки CPU; 0 — по числу физических ядер
  allow_slow: false        # true — F5/Qwen3/MusicGen на CPU без замен (медленно, но как задумано)
  whisper_model: auto      # auto — asr.model (на cpu-fast — small); или small / medium / large-v3-turbo / папка
  whisper_compute: auto    # auto | float16 | int8_float16 | int8 | int8_float32 | float32
  tts_engine_fallback: piper   # piper | none — чем заменять F5/Qwen3 без видеокарты (none — не заменять)
  music_variants: auto     # auto | число вариантов MusicGen
  render_workers: auto     # auto | число воркеров HyperFrames
  encoder: auto            # auto | libx264 | h264_nvenc | h264_videotoolbox | h264_qsv | h264_amf
```

| Переменная | Флаг (`run`, `batch`, `factory`) | Что делает |
|---|---|---|
| `REELS_PROFILE` | `--profile` | профиль |
| `REELS_DEVICE` | `--device` | устройство torch: `cuda`, `cuda:1`, `mps`, `cpu` |
| `REELS_THREADS` | `--threads` | потоки CPU |
| `REELS_ALLOW_SLOW=1` | `--allow-slow` | не заменять F5/Qwen3/MusicGen на CPU |

Свой `asr.model` (например `tiny`) или `asr.device` в `reel.yaml` важнее профиля; `hardware.whisper_model` —
важнее всего.

## Примеры

### Ноутбук без видеокарты

Ничего настраивать не нужно: профиль `cpu-fast`. Озвучка — Piper (скачайте голоса в `models/piper`, см.
[voices.md](voices.md)), музыку лучше дать своим файлом:

```yaml
voice: {engine: piper, voices: [dmitri]}
music: {engine: file, file: assets/music.mp3}
```

Хочется именно F5 и не жалко времени (ночной прогон):

```bash
python -m reels run my-reel --allow-slow          # или --profile cpu-quality
```

### RTX 3060 12 ГБ

Профиль `gpu-max` выбирается сам. Если параллельно открыт браузер с тяжёлыми вкладками или игра и не хватает
видеопамяти — `--profile gpu-lowvram` (модели по очереди, Whisper в int8).

```bash
python -m reels doctor     # проверьте, что PyTorch «видит CUDA»
```

Если `doctor` пишет «PyTorch … без CUDA» — стоит версия для процессора (на Windows `pip install torch` ставит
именно её). Переустановите командой из отчёта, например:

```bash
pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

### RTX 4090

`gpu-max`, всё на видеокарте. Можно добавить вариантов музыки и дублей — на 24 ГБ это почти бесплатно:

```yaml
voice: {seeds: [7, 11, 31, 42, 77]}
music: {prompts: ["…", "…", "…", "…"]}
```

Для карт RTX 50xx нужен PyTorch c CUDA 12.8: `--index-url https://download.pytorch.org/whl/cu128`.

### MacBook на M-серии

Профиль `apple`: F5 и MusicGen на MPS, Whisper на CPU (int8, все производительные ядра), постер — VideoToolbox.
PyTorch ставится обычным `pip install torch torchaudio`. Если какая-то операция не поддерживается на MPS:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=1 python -m reels run my-reel
```

### Сервер с несколькими GPU

Выберите карту по номеру из `nvidia-smi`:

```yaml
hardware: {cuda_device: 1}
```

или на один запуск: `python -m reels run my-reel --device cuda:1`. Несколько проектов параллельно — по
отдельному процессу на карту:

```bash
python -m reels batch a b c --device cuda:0 &
python -m reels batch d e f --device cuda:1 &
```

Совсем не трогать видеокарты (заняты обучением): `--device cpu` или `REELS_PROFILE=cpu-fast`.

## Что ставить

`python -m reels doctor` печатает команды под вашу ОС. Коротко:

| Что | Windows | macOS | Linux |
|---|---|---|---|
| ffmpeg | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` / `sudo dnf install ffmpeg` |
| Node.js 22+ | `winget install OpenJS.NodeJS.LTS` | `brew install node` | NodeSource `setup_22.x` / `sudo dnf install nodejs22` |
| PyTorch с CUDA | `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126` | `pip install torch torchaudio` | как в Windows |
| PyTorch без видеокарты | `… --index-url https://download.pytorch.org/whl/cpu` | — | как в Windows |
| Whisper на видеокарте | `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` (или библиотеки Purfview из README faster-whisper) | — | `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` + `LD_LIBRARY_PATH` |

Индекс PyTorch по драйверу NVIDIA: 570+ или RTX 50xx — `cu128`, 560+ — `cu126`, 550+ — `cu124`, старше — `cu118`
(или обновите драйвер).

## Для разработчиков

```python
from reels import hardware
hw = hardware.detect(deep=True)          # Hardware: ОС, ядра, ОЗУ, GPU, torch/CTranslate2, ffmpeg, Node, браузер
acc = hardware.resolve(cfg_hw, hw)       # Accel: профиль, устройство, Whisper, музыка, воркеры, кодировщик
hardware.apply(project)                  # план конвейера: потоки, замены движков (вызывает pipeline.run)
hardware.current()                       # активный Accel (util.device(), asr.load(), render, finish берут его)
hardware.doctor_report(project) / hardware.doctor_json(project)
```

Определение железа кэшируется; `torch` импортируется только когда он нужен (`deep=True`: есть этапы F5/Qwen3/
MusicGen или `util.device()`). Тесты профилей — `tests/test_hardware.py`, видеокарта для них не нужна.
