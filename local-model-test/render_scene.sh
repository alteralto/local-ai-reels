#!/usr/bin/env bash
# Вставляет сцену модели (scene.txt из папки результатов) вместо блока 2 примера «Поводок»
# и снимает кадры на 5, 8 и 11 секунде. Звук и таймкоды берутся из example/.
#   bash render_scene.sh <папка-результатов>
# HyperFrames запускается через scripts/hf.sh; другой запускатель — переменная HF_SH.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
HF="${HF_SH:-$ROOT/scripts/hf.sh}"
R="$(cd "$1" && pwd)"; G="$R/gen"
rm -rf "$G"; mkdir -p "$G"
cp "$ROOT/scripts/build.py" "$ROOT/scripts/captext.py" "$ROOT/scripts/polish.py" "$G/"
cp -r "$ROOT/example/voice" "$ROOT/example/music" "$G/"
python - "$R" <<'EOF'
import re, sys
R = sys.argv[1]
q = open(f"{R}/scene.txt", encoding="utf-8").read()
q = re.sub(r"<think>.*?</think>", "", q, flags=re.S)
h = re.search(r"```html\s*\n(.*?)```", q, re.S).group(1)
j = re.search(r"```(?:js|javascript)\s*\n(.*?)```", q, re.S).group(1)
p = f"{R}/gen/build.py"; s = open(p, encoding="utf-8").read()
a = s.index('    2: f"""'); b = s.index('    3: f"""')
s = s[:a] + '    2: f"""\n' + h.replace("{", "{{").replace("}", "}}") + '""",\n' + s[b:]
a = s.index('    2: """'); b = s.index('    3: """')
s = s[:a] + '    2: """\n      ' + j.strip().replace('"""', "'''") + '""",\n' + s[b:]
open(p, "w", encoding="utf-8").write(s)
EOF
cd "$G" && PYTHONIOENCODING=utf-8 python build.py 1234 reel >/dev/null && cd reel
bash "$HF" lint 2>&1 | grep -E "error\(s\)" || true
for i in 1 2 3; do
  bash "$HF" snapshot --at 5.0,8.0,11.0 --describe false --no-end >/dev/null 2>&1 && break
done
ls snapshots
