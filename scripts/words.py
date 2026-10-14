"""Пословные таймкоды выбранного дубля → words.json (для границ блоков и субтитров).

python words.py voice.wav
"""
import json
import sys

from faster_whisper import WhisperModel

asr = WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cuda", compute_type="float16")
segs, _ = asr.transcribe(sys.argv[1], language="ru", word_timestamps=True)
ws = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)} for s in segs for w in s.words]
json.dump(ws, open("words.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
# кандидаты в CUTS для build.py: середины пауз после концов фраз. Whisper ставит начало слова чуть поздно —
# в v3 окончательные границы вышли на ~0,1 с раньше середины (4.2 / 11.45 / 17.6 против 4.32 / 11.56 / 17.72)
import re  # noqa: E402
for a, b in zip(ws, ws[1:]):
    if re.search(r"[.!?:]$", a["w"]):
        print(f'после «{a["w"]}»: пауза {b["s"] - a["e"]:.2f} с, середина {(a["e"] + b["s"]) / 2:.2f}')
