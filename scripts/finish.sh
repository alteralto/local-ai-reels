#!/usr/bin/env bash
# Музыка под норму, постер в кадр 0 и проверка готового ролика.
#   bash finish.sh music music-1.wav                 → music.wav (−18 LUFS, 48 кГц, стерео)
#   bash finish.sh poster render.mp4 10.75 out.mp4   → кадр с 10,75 с вшит первым кадром
#   bash finish.sh check out.mp4                     → размер/fps/длительность и громкость
# ffmpeg и ffprobe берутся из PATH; другая папка: FFMPEG_DIR=<папка с ffmpeg>
FF="${FFMPEG_DIR:-}"; FF="${FF:+$FF/}"
set -e
case "$1" in
  music)
    "${FF}ffmpeg" -y -v error -i "$2" -af "loudnorm=I=-18:TP=-2:LRA=11" -ar 48000 -ac 2 music.wav
    "${FF}ffmpeg" -hide_banner -nostats -i music.wav -af ebur128 -f null - 2>&1 | grep -A3 Summary | grep "I:" ;;
  poster)
    "${FF}ffmpeg" -y -v error -ss "$3" -i "$2" -frames:v 1 _poster.png
    "${FF}ffmpeg" -y -v error -i "$2" -i _poster.png -filter_complex "[0:v][1:v]overlay=0:0:enable='eq(n,0)'[v]" \
      -map "[v]" -map 0:a -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p -c:a copy -movflags +faststart "$4"
    rm -f _poster.png ;;
  check)
    "${FF}ffprobe" -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate:format=duration -of default=nw=1 "$2"
    "${FF}ffmpeg" -hide_banner -nostats -i "$2" -af ebur128 -f null - 2>&1 | grep -A3 Summary | grep "I:" ;;
  *) echo "music | poster | check"; exit 1 ;;
esac
