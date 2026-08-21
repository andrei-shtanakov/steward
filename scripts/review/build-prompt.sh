#!/bin/sh
# Склейка инструкций ревьюера с дифом. Оба приходят ФАЙЛАМИ и склеиваются
# через `cat`: диф никогда не попадает ни в аргумент командной строки, ни в
# подстановку выражений — иначе его содержимое интерпретировалось бы как часть
# команды, а диф это недоверенный текст.
set -eu

usage() {
    echo "usage: build-prompt.sh --prompt <file> --diff <file>" >&2
}

prompt=""
diff=""

while [ $# -gt 0 ]; do
    case "$1" in
        --prompt) prompt="${2:-}"; shift 2 ;;
        --diff)   diff="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[ -n "$prompt" ] || { usage; exit 2; }
[ -n "$diff" ] || { usage; exit 2; }
[ -f "$prompt" ] || { echo "нет файла инструкций: $prompt" >&2; exit 2; }
[ -f "$diff" ] || { echo "нет файла дифа: $diff" >&2; exit 2; }

cat "$prompt"
printf '\n\n--- ДИФ НАЧАЛО ---\n'
cat "$diff"
printf '\n--- ДИФ КОНЕЦ ---\n'
