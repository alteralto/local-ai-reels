"""Проект ролика: папка с reel.yaml. Загрузка, значения по умолчанию, проверка, пути к артефактам.

Раскладка папки проекта:
    reel.yaml                  сценарий, сцены, голос, музыка, монтажи
    assets/                    свои файлы: эталон голоса, музыка, сцены custom (необязательно)
    build/                     всё сгенерированное (можно удалить целиком)
        voice/                 ударения, дубли, сверка, master.wav, words.json
        music/                 варианты MusicGen, music.wav (−18 LUFS)
        edits/<монтаж>/        проект HyperFrames: index.html, звук, snapshots/, renders/
    out/                       готовые ролики <slug>-<монтаж>.mp4, report.json, report.md
    .reels-state.json          хэши этапов и подтверждения контрольных точек
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_NAME = "reel.yaml"

DEFAULTS: dict[str, Any] = {
    "title": "",
    "slug": None,                 # имя файлов в out/; по умолчанию — имя папки проекта
    "lang": "ru",
    "limit": 60,                  # лимит слов на весь сценарий
    "theme": "blueprint",         # имя темы или {base: blueprint, accent: "#..."}
    "blocks": [],                 # [{name, text, scene: {type, ...}}]
    "source": "",                 # подпись источника внизу кадра
    "endcard": "",                # текст финальной карточки; *слово* — акцентом
    "tail": 1.5,                  # длительность финальной карточки, с
    "gap": 0.12,                  # пауза между блоками на таймлайне, с
    "cut_bias": 0.12,             # граница блока = середина паузы минус cut_bias (Whisper ставит начало слова поздно)
    "edits": {"linear": "1234"},  # монтажи: имя → порядок блоков
    "voice": {
        "engine": "f5",           # f5 | qwen3 | piper | file
        "voices": ["denis", "dmitri"],
        "seeds": [7, 11, 31],
        "ref": "assets/ref-{voice}.wav",   # эталон голоса (для f5 и qwen3); нет файла — сгенерировать Piper
        "ref_text": ("Утром на площади пусто: только голуби, лавочки и старый фонтан у входа в парк. "
                     "К обеду здесь соберутся туристы и уличные музыканты."),
        "piper_models": "models/piper",    # папка с ru_RU-<голос>-medium.onnx
        "speed": 1.0,
        "nfe_step": 32,
        "accents": {},            # ручные правки ударений: слово → слово с «+»
        "file": None,             # engine=file: готовая озвучка
        "words": None,            # engine=file: готовые таймкоды words.json (иначе распознаёт Whisper)
        "min_ratio": 0.98,        # дубль годен, если сверка Whisper не ниже
        "qwen_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",  # engine=qwen3: id на Hugging Face или папка с весами
        "qwen_language": "Russian",  # engine=qwen3: язык синтеза (Russian, English, … или Auto)
        "qwen_keep_accents": False,  # engine=qwen3: оставить «+» RUAccent в тексте (по умолчанию убираются)
    },
    "asr": {"model": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "device": "auto"},
    "music": {
        "engine": "musicgen",     # musicgen | file | none
        "prompts": ["minimal thoughtful piano with soft synth pulse, calm tension, 80 bpm, no drums",
                    "ambient cinematic underscore, soft piano motif, warm pad, slow, reflective"],
        "file": None,
        "volume": 0.24,           # громкость под голосом, если duck: false
        "duck": True,             # музыка тише под голосом и громче в паузах и на финальной карточке
        "duck_db": -15.0,         # насколько тише под речью, дБ
        "duck_base_db": -3.0,     # уровень музыки вне речи, дБ относительно music.wav (−18 LUFS)
        "seed": 5,
    },
    "captions": {"max_chars": 26, "max_words": 5},
    "render": {"fps": 30, "quality": "looks"},
    "poster": "auto",             # auto | секунда | {edit: <монтаж>, at: <секунда>}
    "hardware": {                 # ускорение: docs/hardware.md; переменные REELS_PROFILE/DEVICE/THREADS важнее
        "profile": "auto",        # auto | gpu-max | gpu-lowvram | cpu-fast | cpu-quality | apple
        "device": "auto",         # auto | cuda | cuda:N | mps | cpu — устройство torch (F5, Qwen3, MusicGen)
        "cuda_device": 0,         # номер видеокарты (как в nvidia-smi)
        "threads": 0,             # потоки CPU; 0 — по числу физических ядер
        "allow_slow": False,      # true — F5/Qwen3/MusicGen на CPU без замен (медленно)
        "whisper_model": "auto",  # auto — asr.model (на cpu-fast — small); или имя/папка модели
        "whisper_compute": "auto",  # auto | float16 | int8_float16 | int8 …
        "tts_engine_fallback": "piper",  # piper | none — чем заменить F5/Qwen3 без видеокарты
        "music_variants": "auto",  # auto | число вариантов MusicGen
        "render_workers": "auto",  # auto | число воркеров HyperFrames
        "encoder": "auto",        # auto | libx264 | h264_nvenc | h264_videotoolbox | h264_qsv | h264_amf
    },
}

VOICE_ENGINES = {"f5", "qwen3", "piper", "file"}
MUSIC_ENGINES = {"musicgen", "file", "none"}
HW_PROFILES = ("auto", "gpu-max", "gpu-lowvram", "cpu-fast", "cpu-quality", "apple")


class ConfigError(ValueError):
    """Ошибка в reel.yaml. Сообщение — по-русски и с указанием поля."""


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("accents",):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def word_count(text: str) -> int:
    """Словом считается всё, где есть буква или цифра; тире и знаки препинания не считаются."""
    return len([x for x in text.split() if re.search(r"\w", x)])


@dataclass
class Block:
    index: int                    # номер блока, с 1
    name: str
    text: str
    scene: dict = field(default_factory=dict)

    @property
    def words(self) -> int:
        return word_count(self.text)


@dataclass
class Project:
    dir: Path
    cfg: dict
    blocks: list[Block]

    # --- пути -------------------------------------------------------------
    @property
    def slug(self) -> str:
        return self.cfg.get("slug") or self.dir.name

    @property
    def build(self) -> Path:
        return self.dir / "build"

    @property
    def voice_dir(self) -> Path:
        return self.build / "voice"

    @property
    def music_dir(self) -> Path:
        return self.build / "music"

    @property
    def edits_dir(self) -> Path:
        return self.build / "edits"

    @property
    def out(self) -> Path:
        return self.dir / "out"

    @property
    def master_wav(self) -> Path:
        """Выбранный дубль озвучки — мастер-файл, из которого режутся все монтажи."""
        return self.voice_dir / "master.wav"

    @property
    def words_json(self) -> Path:
        """Пословные таймкоды мастер-файла: [{"w", "s", "e"}] (слова — как распознал Whisper)."""
        return self.voice_dir / "words.json"

    @property
    def timing_json(self) -> Path:
        """Результат этапа align: {"cuts": [...], "words": [...слова сценария с таймкодами...]}."""
        return self.voice_dir / "timing.json"

    @property
    def music_wav(self) -> Path:
        return self.music_dir / "music.wav"

    def edit_dir(self, edit: str) -> Path:
        return self.edits_dir / edit

    def out_mp4(self, edit: str) -> Path:
        return self.out / f"{self.slug}-{edit}.mp4"

    def path(self, p: str | None) -> Path | None:
        """Путь из reel.yaml: относительный — от папки проекта."""
        if not p:
            return None
        q = Path(p).expanduser()
        return q if q.is_absolute() else (self.dir / q)

    # --- сценарий ---------------------------------------------------------
    @property
    def script(self) -> str:
        """Весь текст диктора: блоки в порядке 1…N через пробел (так он и озвучивается одним файлом)."""
        return " ".join(b.text.strip() for b in self.blocks)

    @property
    def edits(self) -> dict[str, list[int]]:
        return {name: [int(c) for c in str(order)] for name, order in self.cfg["edits"].items()}

    @property
    def total_words(self) -> int:
        return sum(b.words for b in self.blocks)


def load(path: str | Path) -> Project:
    """Загружает проект: папку с reel.yaml или сам файл. Бросает ConfigError с понятным сообщением."""
    p = Path(path)
    cfg_file = p / CONFIG_NAME if p.is_dir() else p
    if not cfg_file.exists():
        raise ConfigError(f"нет {cfg_file}")
    try:
        raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{cfg_file}: ошибка YAML: {e}") from e
    return from_dict(raw, cfg_file.parent.resolve())


def from_dict(raw: dict, directory: Path) -> Project:
    if not isinstance(raw, dict):
        raise ConfigError("reel.yaml: ожидался словарь верхнего уровня")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"reel.yaml: неизвестные поля: {', '.join(sorted(unknown))}")
    cfg = _merge(DEFAULTS, raw)
    blocks = []
    for i, b in enumerate(cfg["blocks"], 1):
        if isinstance(b, str):
            b = {"text": b}
        if not isinstance(b, dict) or not str(b.get("text", "")).strip():
            raise ConfigError(f"blocks[{i}]: нужен непустой text")
        scene = b.get("scene") or {"type": "headline"}
        if isinstance(scene, str):
            scene = {"type": scene}
        if "type" not in scene:
            raise ConfigError(f"blocks[{i}].scene: нужен type")
        blocks.append(Block(i, str(b.get("name") or f"Блок {i}"), " ".join(str(b["text"]).split()), scene))
    if not 1 <= len(blocks) <= 9:
        raise ConfigError(f"blocks: нужно от 1 до 9 блоков, сейчас {len(blocks)}")
    if not cfg["edits"]:
        cfg["edits"] = {"linear": "".join(str(b.index) for b in blocks)}
    for name, order in cfg["edits"].items():
        order = str(order)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(name)):
            raise ConfigError(f"edits.{name}: имя монтажа — латиница, цифры, - и _")
        if sorted(order) != sorted(str(b.index) for b in blocks):
            raise ConfigError(f"edits.{name}: порядок «{order}» должен содержать каждый блок 1…{len(blocks)} ровно раз")
    if cfg["voice"]["engine"] not in VOICE_ENGINES:
        raise ConfigError(f"voice.engine: одно из {sorted(VOICE_ENGINES)}")
    if cfg["voice"]["engine"] == "file" and not cfg["voice"]["file"]:
        raise ConfigError("voice.engine: file — нужен voice.file")
    if cfg["music"]["engine"] not in MUSIC_ENGINES:
        raise ConfigError(f"music.engine: одно из {sorted(MUSIC_ENGINES)}")
    if cfg["music"]["engine"] == "file" and not cfg["music"]["file"]:
        raise ConfigError("music.engine: file — нужен music.file")
    hw = cfg["hardware"]
    if not isinstance(hw, dict):
        raise ConfigError("hardware: ожидался словарь (см. docs/hardware.md)")
    unknown = set(hw) - set(DEFAULTS["hardware"])
    if unknown:
        raise ConfigError(f"hardware: неизвестные поля: {', '.join(sorted(unknown))}")
    if str(hw["profile"]).lower() not in HW_PROFILES:
        raise ConfigError(f"hardware.profile: одно из {', '.join(HW_PROFILES)}")
    if not re.fullmatch(r"auto|cpu|mps|cuda(:\d+)?", str(hw["device"]).lower()):
        raise ConfigError("hardware.device: auto, cuda, cuda:N, mps или cpu")
    return Project(directory, cfg, blocks)
