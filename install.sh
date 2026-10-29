#!/usr/bin/env bash
# Установка local-ai-reels на Linux и macOS: виртуальное окружение, PyTorch под ваше железо, пакет, проверка.
#   bash install.sh            — сам определит видеокарту (NVIDIA CUDA / Apple Silicon / только CPU)
#   bash install.sh --cpu      — без видеокарты: готовая озвучка, Piper, Whisper int8 на процессоре
#   bash install.sh --gpu      — с видеокартой NVIDIA: F5-TTS, MusicGen, Whisper на GPU
#   bash install.sh --full     — все ML-зависимости даже без GPU (медленно, но всё работает на CPU)
#   bash install.sh --models   — после установки скачать модели (tools/fetch_models.py)
#   bash install.sh --no-venv  — ставить в текущий Python, без .venv
# Windows: install.ps1. Повторный запуск безопасен.
set -euo pipefail
cd "$(dirname "$0")"

MODE=auto FULL=0 MODELS=0 VENV=1
for a in "$@"; do
  case "$a" in
    --cpu) MODE=cpu ;; --gpu) MODE=cuda ;; --full) FULL=1 ;; --models) MODELS=1 ;; --no-venv) VENV=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "неизвестный флаг: $a (см. --help)"; exit 2 ;;
  esac
done

say() { printf '\033[1;33m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!\033[0m %s\n' "$*"; }

PY=$(command -v python3 || command -v python || true)
[ -n "$PY" ] || { warn "нет Python 3.10+: установите его (https://www.python.org/downloads/)"; exit 1; }
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { warn "нужен Python 3.10+, найден $("$PY" -V)"; exit 1; }

# --- железо -------------------------------------------------------------------------------------------------
OS=$(uname -s) ARCH=$(uname -m) CUDA=""
if [ "$MODE" = auto ]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    MODE=cuda
  elif [ "$OS" = Darwin ] && [ "$ARCH" = arm64 ]; then
    MODE=mps
  else
    MODE=cpu
  fi
fi
if [ "$MODE" = cuda ]; then
  CUDA=$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9]*\)\.\([0-9]*\).*/\1\2/p' | head -1)
  # колёса PyTorch: cu126 для драйверов с CUDA 12.6+, иначе cu124
  if [ -n "$CUDA" ] && [ "$CUDA" -ge 126 ]; then TORCH_INDEX=https://download.pytorch.org/whl/cu126
  else TORCH_INDEX=https://download.pytorch.org/whl/cu124; fi
  say "видеокарта NVIDIA (CUDA ${CUDA:-?}) → PyTorch из $TORCH_INDEX"
elif [ "$MODE" = mps ]; then
  TORCH_INDEX="" ; say "Apple Silicon → PyTorch с MPS"
else
  TORCH_INDEX=https://download.pytorch.org/whl/cpu ; say "без видеокарты → CPU-сборки, лёгкие модели"
fi

# --- окружение ----------------------------------------------------------------------------------------------
if [ "$VENV" = 1 ]; then
  [ -d .venv ] || { say "создаю .venv"; "$PY" -m venv .venv; }
  # shellcheck disable=SC1091
  . .venv/bin/activate
  PY=python
fi
"$PY" -m pip install -q --upgrade pip

EXTRAS="asr"
if [ "$MODE" != cpu ] || [ "$FULL" = 1 ]; then
  say "PyTorch"
  if [ -n "$TORCH_INDEX" ]; then "$PY" -m pip install -q torch torchaudio --index-url "$TORCH_INDEX"
  else "$PY" -m pip install -q torch torchaudio; fi
  EXTRAS="ml"
fi
[ "$MODE" = cpu ] && EXTRAS="$EXTRAS,piper"
say "local-ai-reels [$EXTRAS]"
"$PY" -m pip install -q -e ".[$EXTRAS]"

# --- внешние программы --------------------------------------------------------------------------------------
hint() {
  if [ "$OS" = Darwin ]; then echo "brew install $1"
  elif command -v apt-get >/dev/null; then echo "sudo apt-get install -y $2"
  elif command -v dnf >/dev/null; then echo "sudo dnf install -y $3"
  else echo "установите $1 через пакетный менеджер"; fi
}
command -v ffmpeg >/dev/null || warn "нет ffmpeg: $(hint ffmpeg ffmpeg ffmpeg)"
if command -v node >/dev/null; then
  NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
  [ "$NODE_MAJOR" -ge 22 ] || warn "Node.js $NODE_MAJOR, нужен 22+: https://nodejs.org или $(hint node nodejs nodejs)"
else
  warn "нет Node.js 22+ (нужен для рендера HyperFrames): https://nodejs.org или $(hint node nodejs nodejs)"
fi

[ "$MODELS" = 1 ] && { say "модели"; "$PY" tools/fetch_models.py; }

say "проверка"
"$PY" -m reels doctor 2>/dev/null || "$PY" -m reels scenes
cat <<EOF

Готово. Дальше:
  $( [ "$VENV" = 1 ] && echo "source .venv/bin/activate" )
  python -m reels run examples/povodok-templates --yes     # пример без GPU и без моделей
  python -m reels new my-reel && python -m reels run my-reel
EOF
