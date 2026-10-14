#!/usr/bin/env bash
# HyperFrames 0.8.75 с выключенной телеметрией. Нужны Node.js 22+ и ffmpeg в PATH.
#   bash hf.sh lint | snapshot … | render …
# Необязательно: HF_CHROME=<путь к chrome-headless-shell>, если HyperFrames не находит браузер сам.
# Chrome не скачивается (403): HYPERFRAMES_BROWSER_PATH=<путь к Chrome или Edge> — подойдёт обычный браузер.
export HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1
[ -n "$HF_CHROME" ] && export PRODUCER_HEADLESS_SHELL_PATH="$HF_CHROME"
exec npx --yes hyperframes@0.8.75 "$@"
