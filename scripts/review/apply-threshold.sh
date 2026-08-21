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
        # Голый флаг без значения (нет $2) — ошибка конфигурации, а не молчаливый
        # сдвиг мимо края позиционных параметров: без сторожа `shift 2` на bash
        # молча съедает лишнее (exit 1), а на dash падает с сообщением самого
        # shell мимо usage() — платформозависимое поведение опаснее прямого exit 2.
        --verdict) [ $# -ge 2 ] || { usage; exit 2; }; verdict="$2"; shift 2 ;;
        --format)  [ $# -ge 2 ] || { usage; exit 2; }; format="$2"; shift 2 ;;
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
#
# Порог ниже читает `.severity` как allow-list блокирующих значений: всё, что
# модель выдаст вне enum'а схемы (другая капитализация, синоним, отсутствие
# поля целиком), должно быть отвергнуто ЗДЕСЬ, а не молча оценено как «не
# blocker/major» — иначе негодный вердикт красится зелёным, инвертируя
# инвариант кита. `type == "object" and (...)` использует короткое замыкание
# jq, чтобы на элементе-не-объекте (например строке) не индексировать
# `.severity` и не падать рантайм-ошибкой jq — сбой всё равно был бы поймён
# ниже через `||` и превращён в код 2, но чистое `false` предпочтительнее
# случайного кода выхода jq, просочившегося сквозь редирект.
#
# `file`/`summary`/`failure` обязаны быть строкой либо отсутствовать (null —
# `cell` ниже подставляет вместо него пустую строку): нестроковое значение
# (число, объект, массив) проходило бы этот guard и падало бы ПОЗЖЕ, внутри
# `gsub` в `cell`, уже после того как заголовок и `note` напечатаны — вызывающий
# получил бы частично заполненный `body.md` и код 5, вне объявленного §7
# набора. `severity` в этот список не входит: его форма уже держится
# отдельной строкой выше через `IN(...)`.
jq -e '(.findings | type == "array")
       and all(.findings[]; type == "object"
               and (.severity | IN("blocker", "major", "minor", "nit"))
               and ([.file, .summary, .failure] | all(. == null or type == "string")))' \
    "$verdict" >/dev/null 2>&1 \
    || { echo "вердикт нечитаем: находка без пригодного severity/file/summary/failure" >&2; exit 2; }

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
