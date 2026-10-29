#!/bin/sh
# Поддельный «python -m reels» для тестов моста: печатает прогресс как конвейер и выходит с нужным кодом.
# Поведение по имени папки проекта: *gate* → код 3, *fail* → код 1, *slow* → долгий этап, иначе 0.
[ "$1" = "-m" ] && [ "$2" = "reels" ] || { echo "bad args: $*" >&2; exit 9; }
shift 2
cmd=$1; shift
case "$cmd" in
  status)
    shift # --json
    printf '['
    sep=''
    for d in "$@"; do
      printf '%s{"dir":"%s","slug":"%s","title":"Т","edits":{"linear":"1234"},' "$sep" "$d" "$(basename "$d")"
      printf '"stages":[{"key":"check","stage":"check","edit":null,"status":"done","at":"2026-10-29 12:00"}],'
      printf '"gates":[{"gate":"script","stage":"check","status":"approved"},{"gate":"take","stage":"voice","status":"waiting"}],'
      printf '"outputs":{"linear":{"mp4":null,"frames":null}},"report":null,"error":null}'
      sep=','
    done
    printf ']\n'
    ;;
  run)
    d=$1
    echo "■ $(basename "$d")  ($d)"
    echo "  · check     без изменений"
    echo "  ▸ voice"
    echo "загрузка модели" >&2
    case "$d" in
      *slow*) sleep 30 ;;
      *gate*) echo "  ✓ voice     1.0 с"; echo "── ждёт подтверждения «take» ──"; exit 3 ;;
      *fail*) echo "✗ этап voice: CUDA not available" >&2; exit 1 ;;
    esac
    echo "  ✓ voice     1.0 с"
    echo "  ✓ build    [linear]  0.5 с"
    ;;
  approve) echo "«$2» подтверждено" ;;
  draft) mkdir -p "$3" && echo "title: Черновик" > "$3/reel.yaml" && echo "черновик: $3/reel.yaml" ;;
  *) echo "✗ нет команды $cmd" >&2; exit 2 ;;
esac
