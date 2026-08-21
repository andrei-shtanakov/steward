#!/bin/sh
# Локальный прогон ревью. НЕ производит governance evidence и ничего не
# публикует: его назначение — быстрый фильтр перед пушем, чтобы не жечь раунды
# CI. Авторитет остаётся у чека в CI.
#
# Здесь неизбежно используются промпт, схема и скрипты АВТОРА: локально base и
# head — одно рабочее дерево. В CI они читаются из base, чтобы автор патча не
# переписал инструкции своему ревьюеру; локально такого разделения нет и быть
# не может. Это цена, а не дефект — см. спеку §4.1.
set -eu

kit_dir="${REVIEW_KIT_DIR:-$(dirname "$0")}"
schema="${REVIEW_SCHEMA:-.github/codex/review-schema.json}"
prompt="${REVIEW_PROMPT:-.github/codex/review-prompt.md}"
review_cmd="${REVIEW_CMD:-codex}"

base=""
head_ref="HEAD"
do_fetch=0
format="text"

usage() {
    echo "usage: local.sh [--base <ref>] [--head <ref>] [--fetch]" \
        "[--format markdown|text]" >&2
}

while [ $# -gt 0 ]; do
    case "$1" in
        # Голый флаг без значения (нет $2) — ошибка конфигурации, а не молчаливый
        # сдвиг мимо края позиционных параметров: без сторожа `shift 2` на bash
        # молча съедает лишнее (exit 1), а на dash падает с сообщением самого
        # shell мимо usage() — платформозависимое поведение опаснее прямого exit 2.
        --base)   [ $# -ge 2 ] || { usage; exit 2; }; base="$2"; shift 2 ;;
        --head)   [ $# -ge 2 ] || { usage; exit 2; }; head_ref="$2"; shift 2 ;;
        --format) [ $# -ge 2 ] || { usage; exit 2; }; format="$2"; shift 2 ;;
        --fetch)  do_fetch=1; shift ;;
        *) usage; exit 2 ;;
    esac
done

# --- ветка по умолчанию: отказ, а не догадка -------------------------------
# Угадывание по списку завело бы машинно-зависимое поведение. Флот за такое уже
# платил: список зеркал Robin держал устаревшие имена репо и молча был слеп к
# двум активным.
default_branch=""
if [ -z "$base" ]; then
    if ! ref=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null); then
        echo "не задан refs/remotes/origin/HEAD — ветку по умолчанию не угадываем." >&2
        echo "выполните: git remote set-head origin -a   (или укажите --base)" >&2
        exit 2
    fi
    default_branch="${ref#refs/remotes/origin/}"
    base="refs/remotes/origin/$default_branch"
fi

if [ "$do_fetch" -eq 1 ] && [ -n "$default_branch" ]; then
    git fetch -q origin "$default_branch"
fi

# --- свежесть базы ---------------------------------------------------------
# Устаревшая база молча сдвигает диапазон: локальный ревьюер смотрит на диф,
# которого в PR не будет. Показать обязаны; отказывать — нет, это инструмент
# скорости.
if [ -n "$default_branch" ]; then
    if remote_line=$(git ls-remote --heads origin "$default_branch" 2>/dev/null) \
        && [ -n "$remote_line" ]; then
        remote_sha=$(printf '%s' "$remote_line" | cut -f1)
        local_sha=$(git rev-parse "$base")
        if [ "$remote_sha" != "$local_sha" ]; then
            behind=$(git rev-list --count "$base..$remote_sha" 2>/dev/null || echo "?")
            echo "ВНИМАНИЕ: локальная база устарела." >&2
            echo "  локально:  $local_sha" >&2
            echo "  на origin: $remote_sha  (отстаём на $behind коммит(ов))" >&2
            echo "  диапазон посчитан по устаревшей базе; --fetch обновит." >&2
        fi
    else
        echo "ВНИМАНИЕ: свежесть базы не проверена (нет связи с origin)." >&2
    fi
fi

# --- диапазон --------------------------------------------------------------
head_sha=$(git rev-parse "$head_ref")
mb=$(git merge-base "$base" "$head_sha")
echo "база:     $(git rev-parse --short "$base")"
echo "голова:   $(git rev-parse --short "$head_sha")"
echo "диапазон: ${mb}..${head_sha}"

work=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$work'" EXIT

git diff "$mb..$head_sha" > "$work/diff.patch"
if [ ! -s "$work/diff.patch" ]; then
    echo "ревьюировать нечего: диф пуст"
    exit 0
fi

sh "$kit_dir/build-prompt.sh" --prompt "$prompt" --diff "$work/diff.patch" \
    > "$work/prompt.txt"

# Промпт идёт на stdin, а не аргументом: диф — недоверенный текст, и в argv он
# не попадает ни здесь, ни в CI. Ревьюер в песочнице read-only: он читает, а не
# правит рабочее дерево.
if ! "$review_cmd" exec --sandbox read-only \
        --output-schema "$schema" \
        --output-last-message "$work/verdict.json" \
        - < "$work/prompt.txt" >/dev/null 2>"$work/reviewer.err"; then
    echo "ревьюер не отработал:" >&2
    cat "$work/reviewer.err" >&2
    exit 3
fi

if [ ! -s "$work/verdict.json" ]; then
    echo "ревьюер завершился успешно, но вердикта не оставил" >&2
    exit 3
fi

sh "$kit_dir/apply-threshold.sh" --verdict "$work/verdict.json" --format "$format"
