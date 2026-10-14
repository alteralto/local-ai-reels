"""Расставляет ударения RUAccent (turbo3.1 + словарь) в репликах: {ключ: текст} → {ключ: текст с «+»}.

python accent.py <in.json> <out.json>
Аббревиатуры пишем словами до разметки («пи-ди-эф», «три-дэ»), иначе модель их не прочтёт.
"""
import json
import os
import sys

# папка моделей RUAccent (~690 МБ): первый запуск скачивает их туда, дальше берёт оттуда
WORKDIR = os.environ.get("RUACCENT_DIR", "models/ruaccent")
from ruaccent import RUAccent  # noqa: E402

source, target = sys.argv[1], sys.argv[2]
texts = json.load(open(source, encoding="utf-8"))
accent = RUAccent()
accent.load(omograph_model_size="turbo3.1", use_dictionary=True, workdir=WORKDIR)
result = {key: accent.process_all(text) for key, text in texts.items()}
json.dump(result, open(target, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for key, text in result.items():
    print(key, "|", text)
