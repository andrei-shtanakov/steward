#!/bin/sh
# Порог серьёзности и рендер вердикта. Публикацией НЕ занимается: stdout уходит
# в `gh pr comment` из CI и в терминал из локального прогона. Разрез проходит
# ровно здесь, иначе в скрипт пришлось бы тащить `gh` и права на запись.
#
# `pipefail` не используется — его нет в POSIX sh.
set -eu

usage() {
    echo "usage: apply-threshold.sh --verdict <file> [--format markdown|text]" >&2
}

verdict=""
format="markdown"

while [ $# -gt 0 ]; do
    case "$1" in
        --verdict) verdict="${2:-}"; shift 2 ;;
        --format)  format="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[ -n "$verdict" ] || { usage; exit 2; }
[ -f "$verdict" ] || { echo "вердикт не найден: $verdict" >&2; exit 2; }

case "$format" in
    markdown|text) ;;
    *) echo "неизвестный --format: $format" >&2; exit 2 ;;
esac

# Невалидный вердикт — отказ, а не «замечаний нет». Отдельный код (2), чтобы
# вызывающий отличал негодный вердикт от находок выше порога.
jq -e '.findings | type == "array"' "$verdict" >/dev/null 2>&1 \
    || { echo "вердикт нечитаем: .findings не массив" >&2; exit 2; }

total=$(jq '.findings | length' "$verdict")
blocking=$(jq '[.findings[]
    | select(.severity == "blocker" or .severity == "major")] | length' "$verdict")

if [ "$format" = markdown ]; then
    echo "## Ревью Codex — независимый чек"
else
    echo "Ревью Codex — независимый чек"
fi
echo
jq -r '.note // ""' "$verdict"
echo

if [ "$total" = 0 ]; then
    echo "Находок нет."
elif [ "$format" = markdown ]; then
    echo "| уровень | файл | находка | сценарий отказа |"
    echo "|---|---|---|---|"
    # Текст вердикта пишет модель: перевод строки или `|` внутри поля разорвал
    # бы таблицу молча. Схлопываем и экранируем.
    jq -r '
        def cell: (. // "") | gsub("\\r?\\n"; " ") | gsub("\\|"; "\\|");
        .findings[]
        | "| \(.severity|cell) | `\(.file|cell)` | \(.summary|cell) | \(.failure|cell) |"
    ' "$verdict"
else
    jq -r '
        def cell: (. // "") | gsub("\\r?\\n"; " ");
        .findings[]
        | "[\(.severity|cell)] \(.file|cell)\n    \(.summary|cell)\n    сценарий: \(.failure|cell)\n"
    ' "$verdict"
fi

echo
echo "_Порог: красным делают \`blocker\` и \`major\`. Это чек, не аппрув —"
echo "и не замена ревью человека._"

[ "$blocking" -eq 0 ] || exit 1
