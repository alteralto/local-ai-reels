"""Пакет для публикации готового монтажа: субтитры SRT/VTT, обложки, текст поста.

Всё пишется в project.out рядом с <slug>-<монтаж>.mp4:
    <slug>-<монтаж>.srt / .vtt       субтитры на таймлайне монтажа, фразы как на экране (3–5 слов)
    <slug>-<монтаж>-cover.jpg        кадр-постер 1080×1920
    <slug>-<монтаж>-cover-4x5.jpg    кроп 1080×1350 для ленты
    <slug>-<монтаж>.txt              заголовок, текст диктора, источник, хэштеги, длительность
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import timing as tm
from .project import Project
from .util import ToolError, ff, run

try:  # фразы субтитров — та же логика, что и на экране
    from .compose import chunks
except Exception:  # noqa: BLE001 — compose недоступен: локальная копия
    _WEAK = {"в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "и", "а", "но", "до", "из", "за", "по", "от",
             "не", "без", "для", "что", "как", "их", "его", "её", "ее", "это", "the", "a"}

    def chunks(words: list[dict], max_chars: int = 26, max_words: int = 5) -> list[list[dict]]:
        """Режет слова на фразы: по пунктуации или длине; фраза не кончается предлогом или союзом."""
        out, cur = [], []
        for w in words:
            cur.append(w)
            text = " ".join(x["w"] for x in cur)
            punct = re.search(r"[.,:;!?—–]$", w["w"])
            if punct and len(text) >= 10 or len(text) >= max_chars or len(cur) >= max_words:
                tail = []
                while not punct and len(cur) > 1 and cur[-1]["w"].lower() in _WEAK:
                    tail.insert(0, cur.pop())
                out.append(cur)
                cur = tail
        if cur:
            out.append(cur)
        return out

COVER_W, COVER_H = 1080, 1920
FEED_H = 1350            # 4:5 для ленты
FEED_Y = 220             # кроп чуть выше центра: внизу кадра (от 1450 px) субтитры
MAX_TAGS = 5
MIN_TAG_LEN = 6
STEM = 5                 # формы слова склеиваются по первым буквам
STOP = {
    "который", "которая", "которое", "которые", "которого", "которой", "которым", "которых", "потому", "поэтому",
    "сейчас", "только", "больше", "меньше", "теперь", "всегда", "никогда", "почему", "сколько", "каждый", "каждая",
    "каждое", "каждые", "каждого", "каждому", "каждой", "кажется", "просто", "вообще", "именно", "ничего", "нибудь",
    "сегодня", "раньше", "многие", "многих", "другие", "другой", "других", "своего", "своими", "своей", "такого",
    "такими", "такие", "между", "против", "вместо", "однако", "хотя", "чтобы", "откуда", "сейчас", "здесь", "можно",
    "нельзя", "должен", "должны", "будет", "будут", "этого", "этому", "этими", "всего", "всему", "очень", "через",
}


# --- субтитры -----------------------------------------------------------------
def cues(project: Project, order: list[int], timing: dict) -> list[tuple[float, float, str]]:
    """Реплики (начало, конец, текст) на таймлайне монтажа.

    Блоки раскладываются как в compose (timing.place): время слова = начало блока в монтаже + время от начала
    блока в мастер-файле. Реплика длится до начала следующей фразы блока или до конца блока.
    """
    cap = project.cfg.get("captions") or {}
    max_chars, max_words = int(cap.get("max_chars", 26)), int(cap.get("max_words", 5))
    cuts, words = timing["cuts"], timing["words"]
    placements, _ = tm.place(list(order), cuts, float(project.cfg["gap"]), float(project.cfg["tail"]))
    out: list[tuple[float, float, str]] = []
    for p in placements:
        bw = tm.block_words(words, cuts, p.block)      # время от начала блока (= от media_start)
        end = round(p.start + p.length, 3)
        phrases = chunks(bw, max_chars, max_words)
        for i, ch in enumerate(phrases):
            a = round(p.start + max(ch[0]["s"], 0.0), 3)
            z = round(p.start + phrases[i + 1][0]["s"], 3) if i + 1 < len(phrases) else end
            out.append((a, max(z, a + 0.05), " ".join(w["w"] for w in ch)))
    return out


def _ts(t: float, sep: str) -> str:
    ms = max(int(round(t * 1000)), 0)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def srt(items: list[tuple[float, float, str]]) -> str:
    return "\n".join(f"{i}\n{_ts(a, ',')} --> {_ts(z, ',')}\n{t}\n" for i, (a, z, t) in enumerate(items, 1))


def vtt(items: list[tuple[float, float, str]]) -> str:
    return "WEBVTT\n\n" + "\n".join(f"{_ts(a, '.')} --> {_ts(z, '.')}\n{t}\n" for a, z, t in items)


# --- обложки ------------------------------------------------------------------
def covers(mp4: Path, at: float, dst: Path, dst_4x5: Path) -> tuple[Path, Path]:
    """Кадр с секунды at: обложка 1080×1920 и кроп 4:5 (1080×1350), JPEG q=2. Кадра нет — берётся первый."""
    vf = (f"[0:v]scale={COVER_W}:{COVER_H}:force_original_aspect_ratio=increase,crop={COVER_W}:{COVER_H},"
          f"setsar=1,split=2[a][b];[b]crop={COVER_W}:{FEED_H}:0:{FEED_Y}[c]")
    for t in (max(at, 0.0), 0.0):
        for f in (dst, dst_4x5):
            f.unlink(missing_ok=True)
        run([ff(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", mp4, "-filter_complex", vf,
             "-map", "[a]", "-frames:v", "1", "-q:v", "2", "-update", "1", dst,
             "-map", "[c]", "-frames:v", "1", "-q:v", "2", "-update", "1", dst_4x5])
        if dst.is_file() and dst.stat().st_size and dst_4x5.is_file() and dst_4x5.stat().st_size:
            return dst, dst_4x5
    raise ToolError(f"не удалось взять кадр {at} с из {mp4}: ролик пустой?")


# --- текст поста ----------------------------------------------------------------
def hashtags(text: str, n: int = MAX_TAGS) -> list[str]:
    """До n хэштегов: самые частые длинные (≥6 букв) слова без стоп-слов.

    Формы одного слова грубо склеиваются по первым STEM буквам («поводок»/«поводком»), тег — самая короткая форма.
    Равная частота — по первому появлению в тексте. Детерминированно.
    """
    groups: dict[str, list[str]] = {}
    for w in re.findall(r"[^\W\d_]+", text.lower().replace("ё", "е")):
        if len(w) >= MIN_TAG_LEN and w not in STOP:
            groups.setdefault(w[:STEM], []).append(w)
    keys = sorted(groups, key=lambda k: -len(groups[k]))       # sorted устойчив: порядок появления сохраняется
    return ["#" + min(groups[k], key=lambda w: (len(w), groups[k].index(w))) for k in keys[:n]]


def _duration(project: Project, order: list[int], timing: dict) -> float:
    try:  # длительность как у собранного монтажа (хвост зависит от финальной карточки)
        from .compose import _layout
        return float(_layout(project, order, timing)[1])
    except Exception:  # noqa: BLE001
        return tm.place(list(order), timing["cuts"], float(project.cfg["gap"]), float(project.cfg["tail"]))[1]


def post_text(project: Project, order: list[int], duration: float) -> str:
    title = str(project.cfg.get("title") or "").strip() or project.slug
    narration = " ".join(project.blocks[b - 1].text.strip() for b in order)
    src = str(project.cfg.get("source") or "").strip()
    if src:
        src = src[0].upper() + src[1:] if src.lower().startswith("источник") else f"Источник: {src}"
    tags = hashtags(project.script)
    m, s = divmod(int(round(duration)), 60)
    parts = [title, narration, src, " ".join(tags), f"Длительность: {m}:{s:02d}"]
    return "\n\n".join(p for p in parts if p) + "\n"


# --- главное ------------------------------------------------------------------
def export_edit(project: Project, edit: str, order: list[int], timing: dict, mp4: Path, poster_at: float) -> dict:
    """Пакет публикации монтажа в project.out. timing — {"cuts", "words"} этапа align.
    Возвращает пути: {"srt", "vtt", "cover", "cover_4x5", "text"}."""
    out = project.out
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"{project.slug}-{edit}"
    items = cues(project, order, timing)
    p_srt, p_vtt = base.with_name(base.name + ".srt"), base.with_name(base.name + ".vtt")
    p_srt.write_text(srt(items), encoding="utf-8")
    p_vtt.write_text(vtt(items), encoding="utf-8")
    cover, cover45 = covers(Path(mp4), float(poster_at), base.with_name(base.name + "-cover.jpg"),
                            base.with_name(base.name + "-cover-4x5.jpg"))
    p_txt = base.with_name(base.name + ".txt")
    p_txt.write_text(post_text(project, order, _duration(project, order, timing)), encoding="utf-8")
    return {"srt": str(p_srt), "vtt": str(p_vtt), "cover": str(cover), "cover_4x5": str(cover45), "text": str(p_txt)}


__all__ = ["export_edit", "cues", "srt", "vtt", "covers", "hashtags", "post_text"]
