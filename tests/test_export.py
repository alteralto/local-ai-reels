"""Пакет публикации: SRT/VTT на таймлайне монтажа, обложки, текст поста."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import BLOCKS
from reels import timing as tm
from reels.export import cues, export_edit, hashtags
from reels.project import from_dict

ORDER = [4, 1, 2, 3]
SRT_TS = r"(\d\d):(\d\d):(\d\d),(\d\d\d)"


def _sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


@pytest.fixture
def proj(tmp_path, example_words):
    p = from_dict({"title": "Невидимый поводок", "slug": "povodok", "source": "источник: RSPH, 2019",
                   "blocks": BLOCKS, "edits": {"hook": "4123"}}, tmp_path)
    words = tm.aligned(example_words, p.script)
    cuts = tm.block_cuts(words, tm.block_token_counts(p), p.cfg["cut_bias"])
    return p, {"cuts": cuts, "words": words}


@pytest.fixture
def mp4(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    out = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=3",
                    "-f", "lavfi", "-i", "anullsrc", "-shortest", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def _size(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    s = json.loads(p.stdout)["streams"][0]
    return s["width"], s["height"]


def test_cues_follow_edit_timeline(proj):
    p, timing = proj
    items = cues(p, ORDER, timing)
    placements, _ = tm.place(ORDER, timing["cuts"], p.cfg["gap"], p.cfg["tail"])
    assert 0 <= items[0][0] < 1.0                                 # монтаж начинается с блока 4
    assert items[0][2].startswith("Генплан")
    for a, z, t in items:
        assert z > a and 1 <= len(t.split()) <= 6
        assert any(pl.start - 1e-3 <= a and z <= pl.start + pl.length + 1e-3 for pl in placements)
    assert " ".join(t for *_, t in items) == " ".join(BLOCKS[b - 1] for b in ORDER)


def test_export_package(proj, mp4):
    p, timing = proj
    res = export_edit(p, "hook", ORDER, timing, mp4, 1.0)
    assert set(res) == {"srt", "vtt", "cover", "cover_4x5", "text"}
    for v in res.values():
        assert Path(v).is_file() and Path(v).parent == p.out
    assert Path(res["srt"]).name == "povodok-hook.srt"

    # SRT: номера подряд, время по возрастанию, текст = сценарий в порядке монтажа
    srt = Path(res["srt"]).read_text(encoding="utf-8").strip().split("\n\n")
    prev_end, texts = 0.0, []
    for i, cue in enumerate(srt, 1):
        idx, ts, *text = cue.split("\n")
        assert idx == str(i)
        m = re.fullmatch(SRT_TS + " --> " + SRT_TS, ts)
        assert m, ts
        a, z = _sec(*m.groups()[:4]), _sec(*m.groups()[4:])
        assert prev_end - 1e-6 <= a < z
        prev_end = z
        texts.append(" ".join(text))
    assert " ".join(texts) == " ".join(BLOCKS[b - 1] for b in ORDER)

    vtt = Path(res["vtt"]).read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT\n\n")
    assert re.search(r"\d\d:\d\d:\d\d\.\d{3} --> \d\d:\d\d:\d\d\.\d{3}", vtt)
    assert vtt.count(" --> ") == len(srt)

    assert _size(res["cover"]) == (1080, 1920)
    assert _size(res["cover_4x5"]) == (1080, 1350)

    txt = Path(res["text"]).read_text(encoding="utf-8")
    assert txt.startswith("Невидимый поводок\n")
    assert "Генплан без туалетов" in txt and "Источник: RSPH, 2019" in txt and "Длительность:" in txt
    tags = re.findall(r"#\w+", txt)
    assert 1 <= len(tags) <= 5 and "#поводок" in tags and "#туалет" in tags


def test_poster_beyond_end_falls_back(proj, mp4):
    p, timing = proj
    res = export_edit(p, "hook", ORDER, timing, mp4, 99.0)
    assert _size(res["cover"]) == (1080, 1920)


def test_hashtags_deterministic():
    text = "Город город городской. Поводок, поводком, поводка! Каждого горожанина. Туалет туалетов."
    assert hashtags(text) == hashtags(text)
    tags = hashtags(text)
    assert tags[0] == "#поводок" and "#туалет" in tags and "#каждого" not in tags
    assert all(len(t) - 1 >= 6 and t == t.lower() for t in tags)
