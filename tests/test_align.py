"""reels.align: слова из длительностей фонем Piper (без модели и без звука)."""
from reels.align import words_from_phonemes

SR = 1000  # 1 отсчёт = 1 мс — удобно считать


def test_words_split_on_space_and_skip_punctuation():
    al = [("^", 100), ("d", 50), ("ˈ", 10), ("a", 90), (" ", 30), ("n", 40), ("ˈ", 10), ("e", 50), (";", 300),
          (" ", 20), ("t", 60), ("ˈ", 10), ("ɑ", 80), (".", 200), ("$", 50)]
    words = words_from_phonemes(al, SR)
    assert words == [{"s": 0.1, "e": 0.25}, {"s": 0.28, "e": 0.38}, {"s": 0.7, "e": 0.85}]


def test_offset_and_total_length():
    al = [("^", 10), ("a", 20), (" ", 5), ("b", 15), ("$", 10)]
    words = words_from_phonemes(al, SR, offset=2.0)
    assert words == [{"s": 2.01, "e": 2.03}, {"s": 2.035, "e": 2.05}]


def test_no_words_from_silence_only():
    assert words_from_phonemes([("^", 10), (".", 100), ("$", 10)], SR) == []
