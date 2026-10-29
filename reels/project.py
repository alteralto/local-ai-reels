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
    "endcard": "",                # текст финальной карточки (*слово* — акцентом) или сцена {type, ...} / {shots: [...]}
    "brand": {},                  # фирменная шапка: {logo, name, rule, header, partners} — см. BRAND_DEFAULTS
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
    "captions": {"style": "karaoke", "max_chars": 26, "max_words": 5},  # style: karaoke | none
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

BRAND_DEFAULTS: dict[str, Any] = {
    "logo": None,                 # assets/logo.svg|png — логотип 56 px слева вверху
    "name": "",                   # название организации, 2–3 строки (\n — перенос), капслоком
    "rule": True,                 # тонкая линия под шапкой (y≈150)
    "header": True,               # шапка на каждом блоке; у блока или кадра — brand: false
    "partners": [],               # ряд партнёров на финальной карточке: ["ИМЯ", {logo: путь}, {name: "ИМЯ"}]
}
CAPTION_STYLES = ("karaoke", "none")
TRANSITIONS = ("cut", "slam", "wipe", "fade")
SFX_SOUNDS = ("knock", "bang", "pop", "whoosh", "stamp", "click", "swoosh")   # reels.sfx.SOUNDS
ROLES = ("hook", "context", "value", "payoff", "cta", "alt-hook")
VOICE_ENGINES = {"f5", "qwen3", "piper", "file"}
MUSIC_ENGINES = {"musicgen", "file", "none"}
HW_PROFILES = ("auto", "gpu-max", "gpu-lowvram", "cpu-fast", "cpu-quality", "apple")


class ConfigError(ValueError):
    """Ошибка в reel.yaml. Сообщение — по-русски и с указанием поля."""


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        # accents и edits заменяются целиком: свои монтажи проекта не смешиваются с linear по умолчанию
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("accents", "edits"):
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
    scene: dict = field(default_factory=dict)   # {type, ...} или {type: <тип 1-го кадра>, shots: [...]}
    sfx: list = field(default_factory=list)     # звуковые акценты блока [{at, sound, volume}]
    role: str = ""                               # роль блока (hook, context, value, payoff, cta, alt-hook)
    brand: bool = True                           # показывать фирменную шапку на блоке

    @property
    def shots(self) -> list[dict] | None:
        """Кадры блока (несколько сцен подряд) или None, если сцена одна."""
        return self.scene.get("shots") if isinstance(self.scene.get("shots"), list) else None

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


def sfx_cues(raw, where: str) -> list[dict]:
    """Звуковые акценты: [{at, sound, volume}] → проверенный список (volume по умолчанию 0.6)."""
    if not raw:
        return []
    if isinstance(raw, (dict, str)):
        raw = [raw]
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: ожидался список [{{at, sound, volume}}]")
    out = []
    for k, c in enumerate(raw):
        if isinstance(c, str):
            c = {"sound": c}
        if not isinstance(c, dict) or str(c.get("sound")) not in SFX_SOUNDS:
            raise ConfigError(f"{where}[{k}]: sound — одно из {', '.join(SFX_SOUNDS)}")
        try:
            vol = float(c.get("volume", .6))
        except (TypeError, ValueError):
            raise ConfigError(f"{where}[{k}].volume: число 0…1") from None
        out.append({"at": c.get("at"), "sound": str(c["sound"]), "volume": min(max(vol, 0.0), 1.0)})
    return out


def scene_spec(scene, where: str) -> dict:
    """Сцена блока: {type, ...} или кадры {shots: [{type, at, transition, sfx, ...}, ...]}.

    У сцены из кадров type — тип первого кадра (для каталогов и проверок), сами кадры — в shots.
    """
    if isinstance(scene, str):
        scene = {"type": scene}
    if isinstance(scene, list):
        scene = {"shots": scene}
    if not isinstance(scene, dict):
        raise ConfigError(f"{where}: ожидался словарь {{type, ...}} или {{shots: [...]}}")
    if "shots" not in scene:
        if "type" not in scene:
            raise ConfigError(f"{where}: нужен type")
        return dict(scene)
    shots = scene["shots"]
    if not isinstance(shots, list) or not shots:
        raise ConfigError(f"{where}.shots: нужен непустой список кадров")
    out = []
    for k, sh in enumerate(shots, 1):
        if isinstance(sh, str):
            sh = {"type": sh}
        if not isinstance(sh, dict) or "type" not in sh:
            raise ConfigError(f"{where}.shots[{k}]: нужен type")
        tr = sh.get("transition", "cut")
        if tr not in TRANSITIONS:
            raise ConfigError(f"{where}.shots[{k}].transition: одно из {', '.join(TRANSITIONS)}")
        sh = dict(sh)
        if "sfx" in sh:
            sh["sfx"] = sfx_cues(sh["sfx"], f"{where}.shots[{k}].sfx")
        out.append(sh)
    return {**{k: v for k, v in scene.items() if k != "shots"}, "type": out[0]["type"], "shots": out}


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
        scene = scene_spec(b.get("scene") or ({"shots": b["shots"]} if b.get("shots") else {"type": "headline"}),
                           f"blocks[{i}].scene")
        role = str(b.get("role") or "")
        if role and role not in ROLES:
            raise ConfigError(f"blocks[{i}].role: одно из {', '.join(ROLES)}")
        blocks.append(Block(i, str(b.get("name") or f"Блок {i}"), " ".join(str(b["text"]).split()), scene,
                            sfx=sfx_cues(b.get("sfx"), f"blocks[{i}].sfx"), role=role,
                            brand=b.get("brand", True) is not False))
    if not 1 <= len(blocks) <= 9:
        raise ConfigError(f"blocks: нужно от 1 до 9 блоков, сейчас {len(blocks)}")
    if not raw.get("edits"):   # монтажей нет — один linear по всем блокам (сколько бы их ни было)
        cfg["edits"] = {"linear": "".join(str(b.index) for b in blocks)}
    # linear (если есть) — первым, как было до замены edits целиком; остальные — в порядке reel.yaml
    cfg["edits"] = dict(sorted(cfg["edits"].items(), key=lambda kv: kv[0] != "linear"))
    have = {str(b.index) for b in blocks}
    for name, order in cfg["edits"].items():
        order = str(order)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(name)):
            raise ConfigError(f"edits.{name}: имя монтажа — латиница, цифры, - и _")
        if not order:
            raise ConfigError(f"edits.{name}: нужен хотя бы один блок")
        bad = sorted(set(order) - have)
        if bad:
            raise ConfigError(f"edits.{name}: в порядке «{order}» нет таких блоков: {', '.join(bad)} "
                              f"(блоки 1…{len(blocks)})")
        twice = sorted({c for c in order if order.count(c) > 1})
        if twice:
            raise ConfigError(f"edits.{name}: в порядке «{order}» блок {', '.join(twice)} повторяется — "
                              f"каждый блок входит в монтаж ровно раз (или не входит)")
    cap = cfg["captions"]
    if not isinstance(cap, dict) or str(cap.get("style", "karaoke")) not in CAPTION_STYLES:
        raise ConfigError(f"captions.style: одно из {', '.join(CAPTION_STYLES)}")
    brand = cfg["brand"] or {}
    if not isinstance(brand, dict):
        raise ConfigError("brand: ожидался словарь {logo, name, rule, header, partners}")
    unknown = set(brand) - set(BRAND_DEFAULTS)
    if unknown:
        raise ConfigError(f"brand: неизвестные поля: {', '.join(sorted(unknown))}")
    cfg["brand"] = {**BRAND_DEFAULTS, **brand}
    partners = cfg["brand"]["partners"] or []
    if not isinstance(partners, list) or not all(isinstance(x, (str, dict)) for x in partners):
        raise ConfigError("brand.partners: список строк или {logo: путь, name: имя}")
    card = cfg["endcard"]
    if isinstance(card, dict):
        cfg["endcard"] = scene_spec(card, "endcard")
    elif card is not None and not isinstance(card, str):
        raise ConfigError("endcard: строка текста или сцена {type, ...} / {shots: [...]}")
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
