"""Тесты локальной модели через LM Studio: проверка вёрстки четырёх кадров (два промпта) и генерация сцены «1 из 5».

python run_tests.py <model-id> <папка-результатов> [--no-vision]

Модель нужно загрузить заранее (lms load …) ровно в одном экземпляре. Скрипт её не грузит: при обрыве связи
он только ждёт и повторяет запрос. Перезагрузка из скрипта во время обрыва LM Link давала вторую копию модели.
"""
import re
import sys
import time
import urllib.error
from pathlib import Path

import ask as A

HERE = Path(__file__).resolve().parent
A.MODEL = sys.argv[1]
OUT = Path(sys.argv[2])
OUT.mkdir(exist_ok=True)
log = open(OUT / "log.md", "w", encoding="utf-8")
_ask = A.ask


def ask_retry(*a, **k):
    for attempt in range(10):
        try:
            return _ask(*a, **k)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            body = e.read()[:200].decode("utf-8", "replace") if isinstance(e, urllib.error.HTTPError) else ""
            print(f"  ошибка ({e} {' '.join(body.split())}); жду 30 с, попытка {attempt + 1}", flush=True)
            time.sleep(30)
    raise SystemExit("сервер так и не ответил")


def say(s):
    print(s, flush=True)
    log.write(s + "\n")
    log.flush()


QA_JSON = ("Это кадр вертикального ролика 1080×1920 (синяя чертёжная графика, крупный заголовок, субтитры внизу). "
           "Проверь вёрстку как строгий редактор. Ищи: текст поверх графики или другого текста, текст, обрезанный "
           "краем кадра, налезающие друг на друга строки, нечитаемые надписи. Полупрозрачные слова в субтитрах — это "
           "норма (подсветка по мере речи). Ответь строго JSON: "
           '{"ok": true/false, "defects": ["что и где"]}')
QA_DESC = ("Посмотри на кадр. Сначала перечисли все надписи на кадре сверху вниз и для каждой скажи: видна ли она "
           "целиком, не перекрывает ли её что-то и не перекрывает ли она сама графику. Потом одной строкой итог: "
           "«ИТОГ: ошибок нет» или «ИТОГ: есть ошибки — …».")
TRUTH = {"A.png": "чистый", "B.png": "ДЕФЕКТ: заголовок обрезан справа и налезает на 2-ю строку",
         "C.png": "ДЕФЕКТ: «2 из 5» поверх карты", "D.png": "чистый"}

say(f"# {A.MODEL}\n\n## Тест 1. QA кадров\n")
for name, truth in ({} if "--no-vision" in sys.argv else TRUTH).items():
    frame = HERE / "frames" / name
    t1, s1, _ = ask_retry(QA_JSON, [frame], max_tokens=4000)
    t2, s2, _ = ask_retry(QA_DESC, [frame], max_tokens=5000)
    verdict = re.findall(r"ИТОГ:.*", t2)
    say(f"### {name} — правда: {truth}\n- JSON ({s1} с): {' '.join(t1.split())[:400]}\n"
        f"- описание ({s2} с), итог: {verdict[-1] if verdict else '(нет строки ИТОГ) ' + ' '.join(t2.split())[-300:]}\n")

src = (HERE.parent / "scripts" / "build.py").read_text(encoding="utf-8")
scene3 = src[src.index('    3: f"""'):src.index('    4: f"""')]
anim3 = src[src.index('    3: """'):src.index('    4: """')]
P = (HERE / "scene_prompt.txt").read_text(encoding="utf-8").replace("{scene3}", scene3).replace("{anim3}", anim3)
text, sec, usage = ask_retry(P, max_tokens=12000, temperature=0.4)
(OUT / "scene.txt").write_text(text, encoding="utf-8")
say(f"## Тест 2. Сцена\n{sec} с, {usage.get('completion_tokens')} токенов; ответ в scene.txt\n")
