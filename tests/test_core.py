"""Ядро: reel.yaml, таймкоды, якоря, проверка сценария."""
from pathlib import Path

import pytest

from conftest import BLOCKS
from reels import timing as T
from reels.project import ConfigError, from_dict, load, word_count
from reels.textcheck import _anchor_in_text, check_script


def project(**over):
    raw = {"blocks": [{"text": t, "scene": {"type": "headline", "lines": ["x"]}} for t in BLOCKS],
           "edits": {"linear": 1234, "from-end": 4123}, "source": "RSPH, 2019", **over}
    return from_dict(raw, Path("/tmp/p"))


def test_word_count_ignores_dashes():
    assert word_count("Генплан без туалетов — это карта") == 5
    assert project().total_words == 59


def test_defaults_and_paths():
    p = project()
    assert p.cfg["voice"]["engine"] == "f5" and p.cfg["music"]["volume"] == 0.24
    assert p.edits == {"linear": [1, 2, 3, 4], "from-end": [4, 1, 2, 3]}
    assert p.out_mp4("linear") == Path("/tmp/p/out/p-linear.mp4")
    assert p.script.startswith("У каждого") and p.script.endswith("ваш город.")


@pytest.mark.parametrize("over, msg", [
    ({"edits": {"bad": 1224}}, "ровно раз"),
    ({"edits": {"Плохо": 1234}}, "латиница"),
    ({"voice": {"engine": "x"}}, "voice.engine"),
    ({"voice": {"engine": "file"}}, "voice.file"),
    ({"music": {"engine": "file"}}, "music.file"),
    ({"colour": 1}, "неизвестные поля"),
])
def test_validation(over, msg):
    with pytest.raises(ConfigError, match=msg):
        project(**over)


def test_examples_load():
    root = Path(__file__).resolve().parent.parent
    for d in sorted((root / "examples").glob("*/")):
        p = load(d)
        assert p.total_words <= p.cfg["limit"]
        if d.name.startswith("povodok"):  # озвучка из example/ — текст должен совпадать дословно
            assert p.script == " ".join(" ".join(b.split()) for b in BLOCKS)


def test_cuts_match_hand_made(example_words):
    p = project()
    words = T.aligned(example_words, p.script)
    cuts = T.block_cuts(words, T.block_token_counts(p))
    # ручные границы оригинального build.py: [0.0, 4.2, 11.45, 17.6, 22.4]
    assert cuts == pytest.approx([0.0, 4.2, 11.45, 17.6, 22.4], abs=0.02)
    assert words[-1]["w"] == "город." and words[23]["w"] == "хочет, —"


def test_aligned_mismatch(example_words):
    with pytest.raises(ValueError, match="разошлась"):
        T.aligned(example_words[:-1], project().script)


def test_place_reorders_master():
    cuts = [0.0, 4.2, 11.45, 17.6, 22.4]
    layout, total = T.place([4, 1, 2, 3], cuts, gap=0.12, tail=1.5)
    assert [x.block for x in layout] == [4, 1, 2, 3]
    assert layout[0].media_start == 17.6 and layout[1].start == pytest.approx(4.92)
    assert total == pytest.approx(22.4 + 3 * 0.12 + 1.5, abs=0.01)


def test_anchor(example_words):
    p = project()
    words = T.aligned(example_words, p.script)
    cuts = T.block_cuts(words, T.block_token_counts(p))
    bw = T.block_words(words, cuts, 2)
    s = T.anchor(bw, "каждый пятый")
    assert s == pytest.approx(T.anchor(bw, "Каждый") , abs=1e-6) and s > 2
    assert T.anchor(bw, "туалет@end") == pytest.approx(bw[-1]["e"])
    assert T.anchor(bw, "хочет+0.25") == pytest.approx(T.anchor(bw, "хочет") + 0.25)
    assert T.anchor(bw, "50%") == pytest.approx(bw[-1]["e"] / 2, abs=1e-3)
    assert T.anchor(bw, None, 1.5) == 1.5 and T.anchor(bw, 2) == 2
    with pytest.raises(ValueError, match="не найден"):
        T.anchor(bw, "метро")


def test_textcheck_flags_problems():
    p = project(limit=40)
    p.blocks[1].text = "Это значит, что 20 % людей боятся PDF."
    p.blocks[2].scene = {"type": "nope"}
    p.blocks[3].scene = {"type": "headline", "at": "слово-которого-нет"}
    issues = check_script(p)
    text = "\n".join(map(str, issues))
    assert "лимите 40" in text
    assert "начинается с «Это»" in text
    assert "цифры" in text and "PDF" in text
    assert "неизвестный тип сцены «nope»" in text
    assert "якорь «слово-которого-нет»" in text


def test_anchor_in_text():
    assert _anchor_in_text("каждый пятый+0.2", "Каждый пятый выходит")
    assert _anchor_in_text("пятый@end", "каждый пятый.")
    assert _anchor_in_text("40%", "что угодно")
    assert not _anchor_in_text("шестой", "каждый пятый")
