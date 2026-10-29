"""Железо и ускорение: что есть на компьютере (detect), какие настройки взять (resolve), отчёт doctor.

Профили (раздел hardware в reel.yaml, переменные REELS_PROFILE / REELS_DEVICE / REELS_THREADS / REELS_ALLOW_SLOW,
флаги --profile / --device / --threads):

    gpu-max      NVIDIA от 8 ГБ: всё на видеокарте, Whisper large-v3-turbo float16, все варианты MusicGen
    gpu-lowvram  NVIDIA до 8 ГБ: Whisper int8_float16, модели по очереди с очисткой памяти, один вариант музыки
    apple        Mac на Apple Silicon: F5/MusicGen на MPS, Whisper на CPU int8 (у CTranslate2 нет MPS)
    cpu-fast     без видеокарты: Whisper small int8 на всех физических ядрах, F5/Qwen3 → Piper, MusicGen — пропуск
    cpu-quality  те же модели, что на видеокарте, но на CPU: медленно, зато без замен

Приоритет: переменные окружения (и флаги CLI, которые их ставят) > reel.yaml > автоопределение.
Определение железа дешёвое и кэшируется; torch импортируется, только если он нужен этапам (deep=True).
"""
from __future__ import annotations

import copy
import gc
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .project import DEFAULTS, HW_PROFILES, ConfigError

PROFILES = HW_PROFILES
ENCODERS = ("libx264", "h264_nvenc", "h264_videotoolbox", "h264_qsv", "h264_amf")
SLOW_VOICE = {"f5", "qwen3"}          # движки озвучки, которым нужна видеокарта
LOWVRAM_GB = 7.5                       # «8 ГБ» карты nvidia-smi показывает как 7.6–8.0 ГБ
CPU_WHISPER = "small"
MUSICGEN_SMALL = "facebook/musicgen-small"
TORCH_INDEX = "https://download.pytorch.org/whl/{tag}"


# --- детектор -----------------------------------------------------------------------------------------------------
@dataclass
class GPU:
    index: int
    name: str
    vram_gb: float
    driver: str | None = None


@dataclass
class Hardware:
    os: str = "linux"                  # linux | windows | darwin
    os_release: str = ""
    arch: str = "x86_64"               # x86_64 | arm64 | …
    python: str = ""
    cpu: str = ""
    cores_physical: int = 1
    cores_logical: int = 1
    ram_gb: float | None = None
    gpus: list[GPU] = field(default_factory=list)
    nvidia_smi: str | None = None      # путь к nvidia-smi, если есть
    torch: str | None = None           # версия torch; None — не установлен
    torch_cuda: bool | None = None     # None — не проверялось (deep=False) или torch нет
    torch_cuda_version: str | None = None
    torch_mps: bool | None = None
    ct2_cuda: int | None = None        # видеокарт для CTranslate2 (faster-whisper); None — не проверялось / нет
    ffmpeg: str | None = None
    ffmpeg_version: str | None = None
    hw_encoders: list[str] = field(default_factory=list)
    node: str | None = None            # «v22.11.0»
    npx: str | None = None
    browser: str | None = None
    packages: dict = field(default_factory=dict)
    deep: bool = False

    @property
    def apple_silicon(self) -> bool:
        return self.os == "darwin" and self.arch in ("arm64", "aarch64")

    def gpu(self, index: int = 0) -> GPU | None:
        return next((g for g in self.gpus if g.index == index), self.gpus[0] if self.gpus else None)

    @property
    def driver_major(self) -> int | None:
        d = next((g.driver for g in self.gpus if g.driver), None)
        m = re.match(r"(\d+)", d or "")
        return int(m.group(1)) if m else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["apple_silicon"] = self.apple_silicon
        return d


def _out(cmd: list, timeout: float = 6) -> str | None:
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return (p.stdout or "") + (p.stderr or "") if p.returncode == 0 else None


def _cpu_info(osname: str, arch: str) -> tuple[str, int, int, float | None]:
    """(название, физических ядер, логических доступных, ОЗУ в ГБ)."""
    logical = os.cpu_count() or 1
    try:
        logical = len(os.sched_getaffinity(0)) or logical  # контейнер / taskset
    except AttributeError:
        pass
    name, phys, ram = platform.processor() or "", None, None
    try:
        import psutil  # noqa: WPS433 — необязательный
        phys = psutil.cpu_count(logical=False)
        ram = psutil.virtual_memory().total / 2**30
    except Exception:  # noqa: BLE001
        pass
    if osname == "linux":
        try:
            txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^model name\s*:\s*(.+)$", txt, re.M)
            name = m.group(1).strip() if m else name
            pairs = set(re.findall(r"physical id\s*:\s*(\d+).*?core id\s*:\s*(\d+)", txt, re.S))
            phys = phys or (len(pairs) or None)
        except OSError:
            pass
        if ram is None:
            try:
                m = re.search(r"MemTotal:\s*(\d+)", Path("/proc/meminfo").read_text())
                ram = int(m.group(1)) / 2**20 if m else None
            except OSError:
                pass
    elif osname == "darwin":
        name = (_out(["sysctl", "-n", "machdep.cpu.brand_string"]) or name).strip()
        v = (_out(["sysctl", "-n", "hw.physicalcpu"]) or "").strip()
        phys = phys or (int(v) if v.isdigit() else None)
        v = (_out(["sysctl", "-n", "hw.memsize"]) or "").strip()
        ram = ram or (int(v) / 2**30 if v.isdigit() else None)
    elif osname == "windows" and ram is None:
        try:
            import ctypes  # noqa: WPS433

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MS()
            st.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
                ram = st.ullTotalPhys / 2**30
        except Exception:  # noqa: BLE001
            pass
    if not phys:  # x86 обычно с SMT (2 потока на ядро); ARM — без
        phys = logical // 2 if logical >= 4 and arch in ("x86_64", "amd64", "i686") else logical
    return name, max(1, min(phys, logical)), logical, round(ram, 1) if ram else None


def _nvidia() -> tuple[str | None, list[GPU]]:
    exe = shutil.which("nvidia-smi")
    if not exe and sys.platform == "win32":
        p = Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe")
        exe = str(p) if p.is_file() else None
    if not exe:
        return None, []
    out = _out([exe, "--query-gpu=index,name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    return exe, parse_nvidia_smi(out or "")


def parse_nvidia_smi(out: str) -> list[GPU]:
    """Строки «0, NVIDIA GeForce RTX 3060, 12288, 560.94» → [GPU]."""
    gpus = []
    for line in out.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        mem = re.match(r"[\d.]+", parts[2])
        gpus.append(GPU(int(parts[0]), parts[1], round(float(mem.group(0)) / 1024, 1) if mem else 0.0, parts[3]))
    return gpus


def parse_encoders(out: str) -> list[str]:
    """Аппаратные H.264-кодировщики из `ffmpeg -encoders`."""
    names = set(re.findall(r"^\s*V\S*\s+(\S+)", out or "", re.M))
    return [e for e in ENCODERS[1:] if e in names]


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _probe_torch(hw: Hardware) -> None:
    if not _has("torch"):
        return
    try:
        import torch  # noqa: WPS433
    except Exception:  # noqa: BLE001 — сломанная установка torch
        return
    hw.torch = str(getattr(torch, "__version__", "?"))
    hw.torch_cuda = bool(torch.cuda.is_available())
    hw.torch_cuda_version = getattr(torch.version, "cuda", None)
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    hw.torch_mps = bool(mps and mps.is_available())
    if hw.torch_cuda and not hw.gpus:  # нет nvidia-smi (WSL, нестандартный путь) — список из torch
        for i in range(torch.cuda.device_count()):
            pr = torch.cuda.get_device_properties(i)
            hw.gpus.append(GPU(i, pr.name, round(pr.total_memory / 2**30, 1)))


def _probe_ct2(hw: Hardware) -> None:
    if not _has("ctranslate2"):
        return
    try:
        import ctranslate2  # noqa: WPS433
        hw.ct2_cuda = int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 — нет cuBLAS/cuDNN и т. п.
        hw.ct2_cuda = 0


_CACHE: dict[bool, Hardware] = {}


def detect(deep: bool = False, refresh: bool = False) -> Hardware:
    """Что есть на компьютере. deep=True — ещё и импорт torch (CUDA/MPS глазами torch). Кэшируется."""
    if refresh:
        _CACHE.clear()
    if True in _CACHE:
        return _CACHE[True]
    if not deep and False in _CACHE:
        return _CACHE[False]
    base = _CACHE.setdefault(False, _CACHE.get(False) or _detect_shallow())
    if not deep:
        return base
    hw = copy.deepcopy(base)
    _probe_torch(hw)
    if hw.ct2_cuda is None and hw.gpus:
        _probe_ct2(hw)
    hw.deep = True
    _CACHE[True] = hw
    return hw


def _detect_shallow() -> Hardware:
    osname = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    arch = platform.machine().lower() or "x86_64"
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch)
    name, phys, logical, ram = _cpu_info(osname, arch)
    smi, gpus = _nvidia()
    hw = Hardware(os=osname, os_release=platform.release(), arch=arch, python=platform.python_version(), cpu=name,
                  cores_physical=phys, cores_logical=logical, ram_gb=ram, gpus=gpus, nvidia_smi=smi)
    hw.torch = "installed" if _has("torch") else None
    if gpus:
        _probe_ct2(hw)
    from . import util  # noqa: WPS433
    ffm = util.ff()
    if shutil.which(ffm) or Path(ffm).is_file():
        hw.ffmpeg = shutil.which(ffm) or ffm
        v = _out([ffm, "-version"]) or ""
        m = re.match(r"ffmpeg version (\S+)", v)
        hw.ffmpeg_version = m.group(1) if m else None
        hw.hw_encoders = parse_encoders(_out([ffm, "-hide_banner", "-encoders"]) or "")
    node = shutil.which("node")
    hw.node = (_out([node, "--version"]) or "").strip() or None if node else None
    hw.npx = shutil.which("npx.cmd" if osname == "windows" else "npx")
    try:
        from . import render  # noqa: WPS433
        hw.browser = render.browser() or render.find_browser()
    except Exception:  # noqa: BLE001
        hw.browser = None
    hw.packages = {k: _has(m) for k, m in (("faster-whisper", "faster_whisper"), ("f5-tts", "f5_tts"),
                                           ("qwen-tts", "qwen_tts"), ("ruaccent", "ruaccent"),
                                           ("piper-tts", "piper"), ("transformers", "transformers"),
                                           ("scipy", "scipy"), ("torch", "torch"))}
    if not hw.packages["piper-tts"] and shutil.which("piper"):
        hw.packages["piper-tts"] = True
    return hw


# --- профили → настройки --------------------------------------------------------------------------------------------
@dataclass
class Accel:
    profile: str
    reason: str
    device: str = "cpu"                # cuda | mps | cpu — для torch (F5, Qwen3, MusicGen)
    cuda_device: int = 0
    threads: int = 1                   # потоки CPU (torch, OMP/MKL, Whisper на CPU)
    allow_slow: bool = False           # тяжёлые модели на CPU без замен
    whisper_model: str | None = None   # None — asr.model из reel.yaml
    whisper_pinned: bool = False       # hardware.whisper_model задан явно — важнее asr.model
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    whisper_threads: int = 1
    whisper_workers: int = 1
    tts_fallback: str | None = "piper"
    nfe_step: int | None = None        # None — voice.nfe_step из reel.yaml
    music_device: str = "cpu"
    music_variants: int | None = None  # None — по варианту на каждый промпт
    music_model: str | None = None     # None — music.model из reel.yaml (или musicgen-small)
    sequential: bool = False           # выгружать модели и чистить память видеокарты между этапами
    render_workers: int = 1
    encoder: str = "libx264"
    sources: dict = field(default_factory=dict)   # откуда взято: {"profile": "env REELS_PROFILE", …}
    warnings: list[str] = field(default_factory=list)

    @property
    def torch_device(self) -> str:
        """Строка устройства для torch: cuda, cuda:1, mps или cpu."""
        if self.device == "cuda" and self.cuda_device:
            return f"cuda:{self.cuda_device}"
        return self.device

    @property
    def gpu(self) -> bool:
        return self.device in ("cuda", "mps")

    def voice_plan(self, engine: str) -> tuple[str, str | None]:
        """(движок, предупреждение): F5/Qwen3 без видеокарты → запасной движок, если медленный режим не разрешён."""
        if engine in SLOW_VOICE and not self.gpu and not self.allow_slow and self.tts_fallback:
            return self.tts_fallback, (
                f"ВНИМАНИЕ: озвучка {engine} без видеокарты займёт десятки минут — беру запасной движок "
                f"{self.tts_fallback} (голос будет проще, свой эталон голоса не используется). Оставить {engine}: "
                "hardware.allow_slow: true в reel.yaml, REELS_ALLOW_SLOW=1 или профиль cpu-quality.")
        if engine in SLOW_VOICE and not self.gpu:
            return engine, f"озвучка {engine} на CPU: медленно (см. python -m reels doctor)"
        return engine, None

    def music_plan(self, engine: str, n_prompts: int) -> tuple[str, int, str | None]:
        """(движок, сколько вариантов MusicGen, предупреждение)."""
        if engine != "musicgen":
            return engine, 0, None
        if not self.gpu and not self.allow_slow:
            return "none", 0, ("ВНИМАНИЕ: MusicGen без видеокарты — несколько минут на вариант; музыка пропущена. "
                               "Своя музыка: music.engine: file; генерировать всё равно: hardware.allow_slow: true "
                               "или REELS_ALLOW_SLOW=1.")
        n = n_prompts if self.music_variants is None else max(1, min(self.music_variants, n_prompts))
        note = None if n == n_prompts else f"MusicGen: {n} из {n_prompts} вариантов (профиль {self.profile})"
        return engine, n, note

    def to_dict(self) -> dict:
        d = asdict(self)
        d["torch_device"] = self.torch_device
        return d


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "да")
    return bool(v)


def _auto(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "auto"))


def render_workers(hw: Hardware) -> int:
    """Воркеры HyperFrames (по Chrome на каждый): половина логических ядер, не больше 6 и по ~2 ГБ ОЗУ на воркер."""
    w = max(1, min(hw.cores_logical // 2, 6))
    if hw.ram_gb:
        w = min(w, max(1, int((hw.ram_gb - 4) // 2)))
    return w


def resolve(cfg_hw: dict | None, hw: Hardware, env: dict | None = None) -> Accel:
    """Профиль и конкретные настройки по разделу hardware из reel.yaml, переменным окружения и железу."""
    cfg = {**DEFAULTS["hardware"], **(cfg_hw or {})}
    env = os.environ if env is None else env
    src = {}

    def pick(key: str, var: str):
        if env.get(var) not in (None, ""):
            src[key] = f"переменная {var}"
            return env[var]
        if not _auto(cfg.get(key)) and cfg.get(key) != DEFAULTS["hardware"].get(key):
            src[key] = f"hardware.{key} в reel.yaml"
        return cfg.get(key)

    profile = str(pick("profile", "REELS_PROFILE") or "auto").strip().lower()
    device = str(pick("device", "REELS_DEVICE") or "auto").strip().lower()
    threads_raw = pick("threads", "REELS_THREADS")
    allow_slow = _bool(pick("allow_slow", "REELS_ALLOW_SLOW"))
    if profile not in PROFILES:
        raise ConfigError(f"hardware.profile: «{profile}» — одно из {', '.join(PROFILES)}")
    m = re.fullmatch(r"(auto|cpu|mps|cuda)(?::(\d+))?", device)
    if not m or (m.group(2) and m.group(1) != "cuda"):
        raise ConfigError(f"hardware.device: «{device}» — auto, cuda, cuda:N, mps или cpu")
    device = m.group(1)
    try:
        cuda_device = int(m.group(2) if m.group(2) else cfg.get("cuda_device") or 0)
        threads = int(threads_raw or 0)
    except (TypeError, ValueError) as e:
        raise ConfigError("hardware.threads и hardware.cuda_device — целые числа") from e
    warnings: list[str] = []
    g = hw.gpu(cuda_device)
    if hw.gpus and cuda_device not in [x.index for x in hw.gpus]:
        warnings.append(f"hardware.cuda_device={cuda_device}: такой видеокарты нет, беру {g.index} ({g.name})")
        cuda_device = g.index
    torch_cuda = hw.torch_cuda if hw.torch_cuda is not None else bool(hw.gpus)
    ct2_cuda = (hw.ct2_cuda > 0) if hw.ct2_cuda is not None else bool(hw.gpus) or bool(hw.torch_cuda)
    cuda_any = bool(hw.gpus) and (torch_cuda or ct2_cuda)
    mps_any = hw.torch_mps if hw.torch_mps is not None else (hw.apple_silicon and bool(hw.torch))

    # 1. профиль
    if profile == "auto":
        if device == "cpu":
            profile, reason = "cpu-fast", "устройство cpu задано явно"
        elif device == "mps" or (device == "auto" and not cuda_any and mps_any):
            profile, reason = "apple", "Mac на Apple Silicon: torch на MPS, Whisper на CPU"
        elif device == "cuda" or cuda_any:
            if g and g.vram_gb and g.vram_gb < LOWVRAM_GB:
                profile, reason = "gpu-lowvram", f"{g.name}: {g.vram_gb:g} ГБ видеопамяти (< 8 ГБ)"
            else:
                profile = "gpu-max"
                reason = f"{g.name}, {g.vram_gb:g} ГБ видеопамяти" if g else "CUDA задана явно"
        else:
            profile = "cpu-fast"
            reason = "видеокарта не найдена" + (" (NVIDIA есть, но ни torch, ни faster-whisper её не видят)"
                                                if hw.gpus else "")
    else:
        reason = f"задан явно ({src.get('profile', 'hardware.profile')})"

    # 2. устройство torch
    if device != "auto":
        tdev = device
        if device == "cuda" and not torch_cuda:
            warnings.append("device=cuda задано явно, но torch не видит CUDA — загрузка моделей может упасть")
        if device == "mps" and not mps_any:
            warnings.append("device=mps задано явно, но torch не видит MPS")
    elif profile.startswith("gpu-"):
        tdev = "cuda" if torch_cuda else "cpu"
        if not torch_cuda:
            warnings.append(f"профиль {profile}, но torch не видит CUDA — F5/Qwen3/MusicGen пойдут на CPU. "
                            "Поставьте torch с CUDA (python -m reels doctor покажет команду)")
    elif profile == "apple":
        tdev = "mps" if mps_any else "cpu"
        if not mps_any:
            warnings.append("профиль apple, но torch не видит MPS — модели пойдут на CPU")
    else:
        tdev = "cpu"

    # 3. потоки
    if threads <= 0:
        threads = hw.cores_physical
    threads = max(1, threads)

    # 4. Whisper (CTranslate2: только CUDA или CPU)
    if device == "cpu" or profile in ("cpu-fast", "cpu-quality", "apple"):
        wdev = "cpu"
    else:
        wdev = "cuda" if ct2_cuda else "cpu"
    if profile.startswith("gpu-") and not ct2_cuda and hw.gpus:
        warnings.append("faster-whisper не видит CUDA (нет cuBLAS/cuDNN 9?) — Whisper пойдёт на CPU")
    if _auto(cfg.get("whisper_compute")):
        wcomp = ("int8_float16" if profile == "gpu-lowvram" else "float16") if wdev == "cuda" else "int8"
    else:
        wcomp = str(cfg["whisper_compute"])
        src["whisper_compute"] = "hardware.whisper_compute в reel.yaml"
    wpinned = not _auto(cfg.get("whisper_model"))
    wmodel = str(cfg["whisper_model"]) if wpinned else (CPU_WHISPER if profile == "cpu-fast" else None)

    # 5. музыка, озвучка, рендер
    mv = cfg.get("music_variants")
    if _auto(mv):
        mvar = 1 if profile in ("gpu-lowvram", "cpu-fast") else None
    else:
        mvar = max(1, int(mv))
    fb = cfg.get("tts_engine_fallback")
    fb = None if fb is None or str(fb).strip().lower() in ("", "none", "keep", "off", "false") else str(fb)
    if fb not in (None, "piper"):
        raise ConfigError(f"hardware.tts_engine_fallback: «{fb}» — piper или none")
    rw = cfg.get("render_workers")
    rworkers = render_workers(hw) if _auto(rw) else max(1, int(rw))
    enc = cfg.get("encoder")
    if _auto(enc):
        if profile.startswith("gpu-") and hw.gpus and "h264_nvenc" in hw.hw_encoders:
            enc = "h264_nvenc"
        elif profile == "apple" and "h264_videotoolbox" in hw.hw_encoders:
            enc = "h264_videotoolbox"
        else:
            enc = "libx264"
    elif enc not in ENCODERS:
        raise ConfigError(f"hardware.encoder: «{enc}» — auto или одно из {', '.join(ENCODERS)}")
    return Accel(profile=profile, reason=reason, device=tdev, cuda_device=cuda_device, threads=threads,
                 allow_slow=allow_slow or profile == "cpu-quality", whisper_model=wmodel, whisper_pinned=wpinned,
                 whisper_device=wdev, whisper_compute=wcomp, whisper_threads=threads, whisper_workers=1,
                 tts_fallback=fb, music_device=tdev, music_variants=mvar,
                 music_model=MUSICGEN_SMALL if profile == "gpu-lowvram" else None,
                 sequential=profile == "gpu-lowvram", render_workers=rworkers, encoder=str(enc), sources=src,
                 warnings=warnings)


# --- активный план (конвейер) -------------------------------------------------------------------------------------
_ACTIVE: dict = {}          # {"dir": Path проекта, "accel": Accel}
_DEFAULT: dict = {}         # кэш current() без проекта: ключ — переменные окружения
_TORCH_SET: dict = {}


def _env_key() -> tuple:
    return tuple(os.environ.get(k, "") for k in ("REELS_PROFILE", "REELS_DEVICE", "REELS_THREADS", "REELS_ALLOW_SLOW"))


def _needs_torch(cfg: dict) -> bool:
    return cfg["voice"]["engine"] in SLOW_VOICE or cfg["music"]["engine"] == "musicgen"


def apply(project, hw: Hardware | None = None, env: dict | None = None, quiet: bool = False) -> Accel:
    """План ускорения для проекта перед запуском конвейера: потоки CPU, запасные движки, сводка в вывод.

    Решения о замене движков (F5/Qwen3 → Piper, MusicGen → без музыки) применяются, только когда этап
    действительно выполняется (voice.synthesize, music.make_music) — готовые этапы из кэша не трогаются.
    """
    from . import util  # noqa: WPS433
    hw = hw or detect(deep=_needs_torch(project.cfg))
    acc = resolve(project.cfg.get("hardware"), hw, env)
    _ACTIVE.clear()
    _ACTIVE.update({"dir": Path(project.dir).resolve(), "accel": acc})
    e = os.environ if env is None else env
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        if "threads" in acc.sources or not e.get(var):
            e[var] = str(acc.threads)
    _TORCH_SET.clear()
    if not quiet:
        v, vnote = acc.voice_plan(project.cfg["voice"]["engine"])
        mus = project.cfg["music"]
        prompts = mus["prompts"] if isinstance(mus["prompts"], list) else [mus["prompts"]]
        m, n, mnote = acc.music_plan(mus["engine"], len(prompts))
        util.say(f"  железо: профиль {acc.profile} ({acc.reason}); torch {acc.torch_device}, Whisper "
                 f"{acc.whisper_device}/{acc.whisper_compute}, потоков {acc.threads}, рендер ×{acc.render_workers}, "
                 f"кодек {acc.encoder}")
        for w in acc.warnings + [x for x in (vnote, mnote) if x]:
            util.say(f"  ! {w}", err=True)
    return acc


def current(deep: bool = True) -> Accel:
    """Настройки для текущего запуска: план конвейера (apply) или автоопределение + переменные окружения."""
    if _ACTIVE:
        return _ACTIVE["accel"]
    key = (deep, _env_key())
    if key not in _DEFAULT:
        _DEFAULT[key] = resolve({}, detect(deep=deep))
    return _DEFAULT[key]


def active_for(project) -> Accel | None:
    """План, если конвейер запущен для этого проекта (apply); иначе None — движки как в reel.yaml."""
    if _ACTIVE and _ACTIVE["dir"] == Path(project.dir).resolve():
        return _ACTIVE["accel"]
    return None


def reset() -> None:
    """Сбросить план и кэши (тесты, смена проекта в одном процессе)."""
    _ACTIVE.clear()
    _DEFAULT.clear()
    _TORCH_SET.clear()


def configure_torch(acc: Accel | None = None) -> None:
    """torch.set_num_threads(потоки профиля) — один раз на план, только если torch уже нужен."""
    acc = acc or current()
    if _TORCH_SET.get("threads") == acc.threads or not _has("torch"):
        return
    try:
        import torch  # noqa: WPS433
        torch.set_num_threads(acc.threads)
        _TORCH_SET["threads"] = acc.threads
    except Exception:  # noqa: BLE001
        pass


def release(force: bool = False) -> None:
    """Выгрузить кэшированные модели (Whisper, MusicGen) и очистить память видеокарты — для gpu-lowvram."""
    if not force and not (_ACTIVE and _ACTIVE["accel"].sequential):
        return
    for mod, attr in (("reels.asr", "_MODELS"), ("reels.music", "_GEN")):
        cache = getattr(sys.modules.get(mod), attr, None)
        if isinstance(cache, dict):
            cache.clear()
    gc.collect()
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def after_stage(stage: str) -> None:
    """Вызывается конвейером после этапа: на gpu-lowvram освобождает видеопамять перед следующей моделью."""
    if stage in ("voice", "align", "music", "finish"):
        release()


ENCODER_ARGS = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                   "-pix_fmt", "yuv420p"],
    "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-q:v", "60", "-pix_fmt", "yuv420p"],
    "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", "20", "-preset", "medium", "-pix_fmt", "nv12"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20",
                 "-pix_fmt", "yuv420p"],
    "libx264": ["-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p"],
}


def video_codecs(acc: Accel | None = None) -> list[tuple[str, list[str]]]:
    """Кодировщики для перекодирования по порядку попыток: аппаратный (если выбран), затем libx264."""
    enc = (acc or current(deep=False)).encoder
    out = [(enc, ENCODER_ARGS[enc])]
    if enc != "libx264":
        out.append(("libx264", ENCODER_ARGS["libx264"]))
    return out


# --- doctor -------------------------------------------------------------------------------------------------------
def torch_index(hw: Hardware) -> str:
    """Тег колеса PyTorch: cu128 (RTX 50xx / драйвер 570+), cu126 (560+), cu124 (550+), cu118, cpu."""
    if not hw.gpus:
        return "cpu"
    d = hw.driver_major or 0
    if d >= 570 or any(re.search(r"RTX\s*50\d\d", x.name) for x in hw.gpus):
        return "cu128"
    if d >= 560:
        return "cu126"
    if d >= 550 or d == 0:
        return "cu124"
    return "cu118"


def torch_install(hw: Hardware) -> str:
    """Команда установки PyTorch под это железо (поверх torch без CUDA — с --force-reinstall)."""
    if hw.os == "darwin":
        return "pip install torch torchaudio   # на Mac MPS уже внутри"
    tag = torch_index(hw)
    force = "--force-reinstall " if hw.torch and hw.gpus and hw.torch_cuda is False else ""
    return f"pip install {force}torch torchaudio --index-url {TORCH_INDEX.format(tag=tag)}"


_HINTS = {
    "ffmpeg": {"windows": "winget install Gyan.FFmpeg   (или winget install ffmpeg), затем откройте терминал заново",
               "darwin": "brew install ffmpeg",
               "linux": "sudo apt install ffmpeg   |   sudo dnf install ffmpeg (RPM Fusion)"},
    "node": {"windows": "winget install OpenJS.NodeJS.LTS, затем откройте терминал заново",
             "darwin": "brew install node",
             "linux": "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs"
                      "   |   sudo dnf install nodejs22"},
}
_PIP = {"faster-whisper": "pip install faster-whisper", "f5-tts": "pip install f5-tts",
        "qwen-tts": "pip install -U qwen-tts", "ruaccent": "pip install ruaccent", "piper-tts": "pip install piper-tts",
        "transformers": "pip install transformers scipy", "scipy": "pip install scipy"}


def missing(hw: Hardware, voice_engine: str = "f5", music_engine: str = "musicgen") -> list[tuple[str, str]]:
    """[(что, как поставить)] для этого компьютера и движков проекта."""
    out = []
    if not hw.ffmpeg:
        out.append(("ffmpeg", _HINTS["ffmpeg"][hw.os]))
    m = re.match(r"v?(\d+)", hw.node or "")
    if not hw.node or not hw.npx:
        out.append(("Node.js 22+ (npx)", _HINTS["node"][hw.os]))
    elif m and int(m.group(1)) < 22:
        out.append((f"Node.js 22+ (сейчас {hw.node})", _HINTS["node"][hw.os]))
    need = ["faster-whisper"]
    need += {"f5": ["f5-tts", "ruaccent", "piper-tts"], "qwen3": ["qwen-tts", "piper-tts"],
             "piper": ["piper-tts"]}.get(voice_engine, [])
    if music_engine == "musicgen":
        need += ["transformers", "scipy"]
    for pkg in need:
        if hw.packages and not hw.packages.get(pkg, True):
            out.append((pkg, _PIP[pkg]))
    if (voice_engine in SLOW_VOICE or music_engine == "musicgen") and not hw.torch:
        out.append(("PyTorch", torch_install(hw)))
    elif hw.gpus and hw.torch and hw.torch_cuda is False:
        out.append((f"PyTorch с CUDA (сейчас {hw.torch} без CUDA)", torch_install(hw)))
    if hw.gpus and hw.ct2_cuda == 0:
        out.append(("CUDA для faster-whisper (cuBLAS + cuDNN 9)",
                    "pip install nvidia-cublas-cu12 \"nvidia-cudnn-cu12==9.*\"" +
                    ("   (Linux: добавьте их папки lib в LD_LIBRARY_PATH)" if hw.os == "linux" else "")))
    return out


def _fmt(sec: float) -> str:
    if sec <= 0:
        return "—"
    if sec < 90:
        return f"~{max(1, round(sec))} с"
    if sec < 5400:
        return f"~{round(sec / 60)} мин"
    return f"~{sec / 3600:.1f} ч"


def estimate(acc: Accel, hw: Hardware, voice_engine: str = "f5", music_engine: str = "musicgen", takes: int = 6,
             variants: int = 2, edits: int = 1) -> list[tuple[str, float, str]]:
    """Грубая оценка времени этапов для ролика 25 с: [(этап, секунды, пояснение)]."""
    cpu = min(2.0, max(0.3, 4 / max(1, acc.threads)))         # относительно 4 физических ядер
    g = hw.gpu(acc.cuda_device)
    tier = 1.0 if not g else (0.5 if g.vram_gb >= 16 else 1.0 if g.vram_gb >= LOWVRAM_GB else 1.4)
    dev = acc.device
    wmodel = (acc.whisper_model or "turbo").lower()
    if acc.whisper_device == "cuda":
        whisper = (2 if acc.whisper_compute == "float16" else 3) * tier
    elif any(x in wmodel for x in ("tiny", "base")):
        whisper = 3 * cpu
    elif "small" in wmodel:
        whisper = 8 * cpu
    elif "medium" in wmodel:
        whisper = 25 * cpu
    else:
        whisper = 35 * cpu
    v_engine, _ = acc.voice_plan(voice_engine)
    per_take = {"f5": {"cuda": 6 * tier, "mps": 25, "cpu": 240 * cpu},
                "qwen3": {"cuda": 20 * tier, "mps": 90, "cpu": 600 * cpu},
                "piper": {"cuda": 2 * cpu, "mps": 2 * cpu, "cpu": 2 * cpu}}.get(v_engine, {"cuda": 0, "mps": 0, "cpu": 0})
    n_takes = takes if v_engine in SLOW_VOICE else (2 if v_engine == "piper" else 1)
    m_engine, n_var, _ = acc.music_plan(music_engine, variants)
    per_var = {"cuda": 20 * tier, "mps": 60, "cpu": 300 * cpu}[dev if dev in ("cuda", "mps") else "cpu"]
    enc = {"h264_nvenc": 3, "h264_videotoolbox": 5, "h264_qsv": 5, "h264_amf": 5}.get(acc.encoder, 25 * cpu)
    render = max(30.0, 120 * (2 / max(1, acc.render_workers)) ** 0.6 * cpu ** 0.4)
    rows = [
        ("accent", 15 if voice_engine == "f5" else 1, "RUAccent" if voice_engine == "f5" else "без ударений"),
        ("voice", n_takes * (per_take[dev if dev in ("cuda", "mps") else "cpu"] + whisper) + 5,
         f"{v_engine}: дублей {n_takes} + сверка Whisper"),
        ("align", whisper + 5, f"Whisper {acc.whisper_model or 'из asr.model'} на {acc.whisper_device}"),
        ("music", n_var * per_var if m_engine == "musicgen" else 0,
         f"MusicGen: вариантов {n_var}" if m_engine == "musicgen" else ("пропуск" if music_engine == "musicgen"
                                                                           else music_engine)),
        ("build", 2 * edits, ""),
        ("snapshot", 20 * cpu ** 0.5 * edits, "кадры для проверки"),
        ("render", render * edits, f"HyperFrames, воркеров {acc.render_workers}"),
        ("finish", (enc + whisper) * edits, f"постер ({acc.encoder}) + сверка"),
    ]
    return [(s, float(t), note) for s, t, note in rows]


def _project(project):
    if project is None:
        return None
    if isinstance(project, (str, Path)):
        from .project import load  # noqa: WPS433
        return load(project)
    return project


def doctor_json(project=None, hw: Hardware | None = None, env: dict | None = None) -> dict:
    """То же, что doctor_report, для приложений: {"hardware", "accel", "plan", "missing", "estimate"}."""
    p = _project(project)
    hw = hw or detect(deep=True)
    cfg = p.cfg if p else DEFAULTS
    acc = resolve(cfg.get("hardware"), hw, env)
    ve, me = cfg["voice"]["engine"], cfg["music"]["engine"]
    prompts = cfg["music"]["prompts"] if isinstance(cfg["music"]["prompts"], list) else [cfg["music"]["prompts"]]
    v_eff, v_note = acc.voice_plan(ve)
    m_eff, n_var, m_note = acc.music_plan(me, len(prompts))
    takes = len(cfg["voice"]["voices"]) * len(cfg["voice"]["seeds"])
    est = estimate(acc, hw, ve, me, takes=takes, variants=len(prompts), edits=len(p.edits) if p else 1)
    return {"project": str(p.dir) if p else None, "hardware": hw.to_dict(), "accel": acc.to_dict(),
            "plan": {"voice": {"engine": ve, "effective": v_eff, "note": v_note},
                     "music": {"engine": me, "effective": m_eff, "variants": n_var, "note": m_note}},
            "missing": [{"what": w, "how": h} for w, h in missing(hw, ve, me)],
            "torch_install": torch_install(hw),
            "estimate": [{"stage": s, "seconds": round(t), "note": n} for s, t, n in est],
            "estimate_total": round(sum(t for _, t, _ in est))}


def doctor_report(project=None, hw: Hardware | None = None, env: dict | None = None) -> str:
    """Отчёт по-русски: железо, профиль и почему, настройки этапов, чего не хватает, оценка времени."""
    d = doctor_json(project, hw, env)
    h, a, plan = d["hardware"], d["accel"], d["plan"]
    osn = {"windows": "Windows", "darwin": "macOS", "linux": "Linux"}.get(h["os"], h["os"])
    L = ["Local AI Reels — проверка компьютера" + (f" для {d['project']}" if d["project"] else ""), "",
         "■ Компьютер",
         f"  ОС: {osn} {h['os_release']} ({h['arch']}), Python {h['python']}",
         f"  Процессор: {h['cpu'] or '—'}; ядер: {h['cores_physical']} физ. / {h['cores_logical']} лог.",
         f"  Память: {h['ram_gb']:g} ГБ" if h["ram_gb"] else "  Память: не определена"]
    if h["gpus"]:
        for g in h["gpus"]:
            L.append(f"  Видеокарта {g['index']}: {g['name']}, {g['vram_gb']:g} ГБ"
                     + (f", драйвер {g['driver']}" if g.get("driver") else ""))
    else:
        L.append("  Видеокарта NVIDIA: нет" + ("" if h["os"] != "darwin" else
                                               (" (Apple Silicon — ускорение через MPS)" if h["apple_silicon"] else "")))
    if h["torch"]:
        cuda = {True: f"видит CUDA {h['torch_cuda_version'] or ''}".strip(), False: "без CUDA",
                None: "CUDA не проверялась"}[h["torch_cuda"]]
        mps = ", MPS есть" if h["torch_mps"] else ""
        L.append(f"  PyTorch {h['torch']}: {cuda}{mps}")
    else:
        L.append("  PyTorch: не установлен")
    if h["ct2_cuda"] is not None:
        L.append(f"  faster-whisper (CTranslate2): видеокарт с CUDA — {h['ct2_cuda']}")
    L.append(f"  ffmpeg: {h['ffmpeg_version'] or 'есть'}" + (
        f"; аппаратные кодировщики: {', '.join(h['hw_encoders'])}" if h["hw_encoders"] else "; аппаратных кодировщиков нет")
        if h["ffmpeg"] else "  ffmpeg: НЕ НАЙДЕН")
    L.append(f"  Node.js: {h['node']}" if h["node"] else "  Node.js: НЕ НАЙДЕН")
    L.append(f"  Браузер для HyperFrames: {h['browser']}" if h["browser"]
             else "  Браузер для HyperFrames: не найден — HyperFrames скачает свой Chrome при первом рендере")
    L += ["", f"■ Профиль: {a['profile']} — {a['reason']}"]
    for k, s in a["sources"].items():
        L.append(f"  {k}: {s}")
    L.append("  сменить: hardware.profile в reel.yaml, REELS_PROFILE=… или флаг --profile")
    wm = a["whisper_model"] or "asr.model из reel.yaml"
    v = plan["voice"]
    m = plan["music"]
    L += ["", "■ Настройки по этапам",
          f"  потоки CPU: {a['threads']} (torch, OMP/MKL, Whisper на CPU)",
          f"  озвучка: {v['engine']}" + (f" → {v['effective']} (запасной)" if v["effective"] != v["engine"] else "")
          + f" на {a['torch_device'] if v['effective'] in SLOW_VOICE else 'CPU'}",
          f"  Whisper: {wm}, {a['whisper_device']}, {a['whisper_compute']}"
          + (f", потоков {a['whisper_threads']}" if a["whisper_device"] == "cpu" else f", карта {a['cuda_device']}"),
          f"  музыка: {m['engine']}" + (f" → {m['effective']}" if m["effective"] != m["engine"] else "")
          + (f", вариантов {m['variants']} на {a['music_device']}" if m["effective"] == "musicgen" else "")
          + (f", модель {a['music_model']}" if a["music_model"] and m["effective"] == "musicgen" else ""),
          f"  видеопамять: {'модели по очереди, очистка между этапами' if a['sequential'] else 'модели держатся в памяти'}",
          f"  рендер HyperFrames: воркеров {a['render_workers']}",
          f"  кодировщик постера: {a['encoder']}" + (" (при ошибке — повтор на libx264)" if a["encoder"] != "libx264"
                                                      else "")]
    notes = [x for x in (v["note"], m["note"]) if x] + a["warnings"]
    if notes:
        L += ["", "■ Внимание"] + [f"  ! {x}" for x in notes]
    L += ["", "■ Чего не хватает"]
    L += [f"  ✗ {x['what']}: {x['how']}" for x in d["missing"]] or ["  всё на месте"]
    if h["gpus"] or h["os"] == "darwin":
        L.append(f"  PyTorch под это железо: {d['torch_install']}")
    else:
        L.append(f"  PyTorch без видеокарты (легче): {d['torch_install']}")
    L += ["", "■ Оценка времени для ролика 25 с на этом компьютере (грубо)"]
    for e in d["estimate"]:
        L.append(f"  {e['stage']:<9} {_fmt(e['seconds']):>9}  {e['note']}")
    L.append(f"  {'итого':<9} {_fmt(d['estimate_total']):>9}  (повторный запуск берёт готовые этапы из кэша)")
    return "\n".join(L) + "\n"


__all__ = ["GPU", "Hardware", "Accel", "PROFILES", "ENCODER_ARGS", "detect", "resolve", "apply", "current",
           "active_for", "reset", "configure_torch", "release", "after_stage", "video_codecs", "render_workers",
           "doctor_report", "doctor_json", "missing", "estimate", "torch_install", "parse_nvidia_smi",
           "parse_encoders"]
