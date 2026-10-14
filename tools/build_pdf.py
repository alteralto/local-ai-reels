"""Собирает docs/guide.pdf из docs/guide.md и скриптов: инструкция + альбомное приложение с кодом + вложения PDF.

python tools/build_pdf.py
Нужны: markdown, pymupdf и Chrome/Chromium (путь — переменная CHROME, по умолчанию chrome в PATH).
Проверяет, что каждая строка кода извлекается из PDF целиком, без переносов.
"""
import html
import os
import pathlib
import subprocess
import tempfile

import fitz
import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FILES = ["texts.py", "accent.py", "synth.py", "words.py", "captext.py", "music_gen.py",
         "sfx_lock.py", "build.py", "polish.py", "hf.sh", "finish.sh"]
TITLE = "local-ai-reels: стек и инструкция"

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
@page code { size: A4 landscape; margin: 10mm 10mm 10mm 10mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1b1f24; }
h1 { font-size: 20pt; color: #123a7a; border-bottom: 3px solid #ffd23f; padding-bottom: 4px; margin: 0 0 10px; }
h2 { font-size: 14pt; color: #123a7a; margin: 18px 0 6px; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12px 0 4px; break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9.2pt; }
th, td { border: 1px solid #c9d3e3; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #e8f0ff; } tr { break-inside: avoid; }
code { font-family: Consolas, monospace; font-size: 9pt; background: #f1f4f9; padding: 0 3px; border-radius: 3px; }
pre { background: #f1f4f9; border-left: 3px solid #123a7a; padding: 7px 9px; white-space: pre-wrap; word-break: break-all;
      break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.6pt; }
blockquote { margin: 6px 0; padding: 4px 10px; border-left: 3px solid #ffd23f; background: #fffbea; }
a { color: #123a7a; } ul { margin: 4px 0 8px; padding-left: 20px; } li { margin: 2px 0; }
.appendix { page: code; } .listing { break-before: page; }
.listing h3 { font-family: Consolas, monospace; font-size: 11pt; color: #123a7a; margin: 0 0 4px; }
pre.code { font-family: Consolas, monospace; font-size: 7.3pt; line-height: 1.32; white-space: pre; word-break: normal;
           overflow: visible; break-inside: auto; padding: 5px 7px; }
"""


def main():
    body = markdown.markdown((ROOT / "docs/guide.md").read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code", "sane_lists"])
    listings = "".join(f'<section class="listing"><h3>{n}</h3><pre class="code">'
                       f'{html.escape((SCRIPTS / n).read_text(encoding="utf-8"))}</pre></section>' for n in FILES)
    page = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{TITLE}</title><style>{CSS}</style>'
            f'</head><body>{body}<div class="appendix">{listings}</div></body></html>')
    with tempfile.TemporaryDirectory() as tmp:
        src, raw = pathlib.Path(tmp) / "guide.html", pathlib.Path(tmp) / "raw.pdf"
        src.write_text(page, encoding="utf-8")
        subprocess.run([os.environ.get("CHROME", "chrome"), "--headless", "--no-pdf-header-footer",
                        f"--print-to-pdf={raw}", src.as_uri()], check=True, capture_output=True)
        doc = fitz.open(raw)
        for n in FILES:
            doc.embfile_add(n, (SCRIPTS / n).read_bytes(), filename=n)
        doc.set_metadata({"title": TITLE, "author": "", "creator": "", "producer": ""})
        out = ROOT / "docs/guide.pdf"
        doc.save(out, garbage=3, deflate=True)
        doc.close()  # иначе на Windows временную папку не удалить
    with fitz.open(out) as saved:
        lines = {l.strip() for p in saved for l in p.get_text().splitlines()}
    for n in FILES:
        miss = [l.strip() for l in (SCRIPTS / n).read_text(encoding="utf-8").splitlines()
                if l.strip() and l.strip() not in lines]
        print(f"{n:<13} строк не извлекается целиком: {len(miss)}")
    print(out)


if __name__ == "__main__":
    main()
