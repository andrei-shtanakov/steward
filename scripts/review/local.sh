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
# Умолчания относительны корню репо, а не cwd: README велит запускать
# `sh scripts/review/local.sh`, и запускать будут откуда попало — из
# подкаталога relative-путь до промпта/схемы молча не резолвится. Вне
# git-репозитория `git rev-parse` сам падает кодом 128 — под `set -e` это
# утекло бы наружу как есть, вне объявленного §7 набора 0/1/2/3; страж
# переводит это в код кита с понятным сообщением.
if ! repo_root=$(git rev-parse --show-toplevel 2>/dev/null); then
    echo "прогон вне git-репозитория: не удалось определить корень" \
        "(git rev-parse --show-toplevel)." >&2
    exit 2
fi
schema="${REVIEW_SCHEMA:-$repo_root/.github/codex/review-schema.json}"
prompt="${REVIEW_PROMPT:-$repo_root/.github/codex/review-prompt.md}"
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

if [ "$do_fetch" -eq 1 ] && [ -z "$default_branch" ]; then
    # Явный --base — не наша ветка по умолчанию; молча не сработавший
    # флаг был бы той же несопоставимостью диапазонов, ради которой
    # написан §6.1, только с другой стороны.
    echo "--fetch игнорируется: база задана явно через --base." >&2
fi

# --- свежесть базы -----------------------------------------------------
# Устаревшая база молча сдвигает диапазон: локальный ревьюер смотрит на
# диф, которого в PR не будет. Показать обязаны; отказывать — нет, это
# инструмент скорости.
#
# Три разных исхода `ls-remote`, и они не взаимозаменимы: "не смогли
# проверить" (сети нет, `ls-remote` сам упал ненулевым кодом) и
# "определённо нет" (`ls-remote` отработал успешно кодом 0, но вернул
# пусто — ветки с этим именем на origin больше нет, например
# `refs/remotes/origin/HEAD` не обновился после переименования ветки по
# умолчанию: сам он локальная ссылка и не обновляется собой) — РАЗНЫЕ
# состояния с разной причиной, и сваливать их в одно сообщение нельзя:
# второе — сигнал сильнее первого.
#
# ls-remote проверяется ЗДЕСЬ, ДО попытки --fetch, а не наоборот: иначе
# `git fetch` ветки, которой на origin уже нет, сам падает сырым кодом
# git, guard превращает это в "не удалось обновить" — и до диагностики,
# которая назвала бы настоящую причину (ветки нет, `set-head`), управление
# не доходит. Порядок — часть контракта: при переименованной ветке причина
# обязана быть настоящей что с `--fetch`, что без него.
if [ -n "$default_branch" ]; then
    ls_remote_ok=1
    if ! remote_line=$(git ls-remote --heads origin "$default_branch" 2>/dev/null); then
        ls_remote_ok=0
    fi

    if [ "$ls_remote_ok" -eq 1 ] && [ -z "$remote_line" ]; then
        # Ветки на origin больше нет — тянуть нечего, и молчать об
        # истинной причине нельзя. --fetch здесь не помог бы, поэтому
        # попытка фетча ниже намеренно пропущена.
        echo "ВНИМАНИЕ: ветки '$default_branch' нет на origin —" \
            "refs/remotes/origin/HEAD устарел (например, после" \
            "переименования ветки по умолчанию)." >&2
        echo "  диапазон посчитан против несуществующей на origin ветки." >&2
        echo "  выполните: git remote set-head origin -a" >&2
    elif [ "$do_fetch" -eq 1 ]; then
        # Отсутствие сети, протухшие credential'ы — `git fetch` сам падает
        # сырым кодом git (обычно 128) на команде, которую README же и
        # рекомендует. Под `set -e` это утекло бы наружу как есть, вне
        # объявленного §7 набора 0/1/2/3 — тот же класс, что уже правили
        # (repo-root, построение диапазона, схема/промпт). Сбой fetch —
        # ошибка конфигурации/окружения, код 2.
        if ! fetch_err=$(git fetch -q origin "$default_branch" 2>&1); then
            echo "не удалось обновить $default_branch с origin:" >&2
            echo "$fetch_err" >&2
            exit 2
        fi
    fi

    if [ "$ls_remote_ok" -eq 1 ] && [ -n "$remote_line" ]; then
        remote_sha=$(printf '%s' "$remote_line" | cut -f1)
        # Пересчитано ПОСЛЕ возможного --fetch выше: если он обновил
        # локальную ссылку, она уже совпадёт с remote_sha, и предупреждение
        # об устаревшей базе законно не напечатается.
        local_sha=$(git rev-parse "$base")
        if [ "$remote_sha" != "$local_sha" ]; then
            behind=$(git rev-list --count "$base..$remote_sha" 2>/dev/null || echo "?")
            echo "ВНИМАНИЕ: локальная база устарела." >&2
            echo "  локально:  $local_sha" >&2
            echo "  на origin: $remote_sha  (отстаём на $behind коммит(ов))" >&2
            echo "  диапазон посчитан по устаревшей базе; --fetch обновит." >&2
        fi
    elif [ "$ls_remote_ok" -eq 0 ]; then
        echo "ВНИМАНИЕ: свежесть базы не проверена (нет связи с origin)." >&2
    fi
fi

# --- диапазон --------------------------------------------------------------
# Обе команды могут упасть сырым кодом git (128 у rev-parse на неизвестной
# ссылке, 1 у merge-base без общего предка) — под `set -e` это утекло бы
# наружу как есть, а 1 в нашем контракте означает "есть находки уровня
# blocker/major": инвертированный сигнал, диапазон не построился, а хук
# скажет человеку, что ревью нашло проблемы. Оба вызова обёрнуты кодом 2.
if ! head_sha=$(git rev-parse "$head_ref" 2>/dev/null); then
    echo "не удалось разрешить --head $head_ref: неизвестная ссылка." >&2
    exit 2
fi
if ! mb=$(git merge-base "$base" "$head_sha" 2>/dev/null); then
    base_hint=$(git rev-parse --short "$base" 2>/dev/null || echo "$base")
    echo "не удалось построить диапазон между $base_hint и $head_sha:" \
        "нет общего предка либо база не разрешается." >&2
    exit 2
fi
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

# Схема и промпт — вход ревьюера. Отсутствие любого из них — конфигурационный
# отказ (код 2), не механический сбой самого ревьюера (код 3): без этого
# preflight'а отсутствующая схема доходила бы до `codex exec --output-schema
# <нет-файла>`, падала бы уже там, и ветка "ревьюер не отработал" маскировала
# бы сломанную локальную конфигурацию под сбой инструмента — тот же класс, что
# правили уже дважды (repo-root, построение диапазона). Промпт проверен здесь
# же ради симметрии сообщения — `build-prompt.sh` ниже и сам его проверяет, но
# заставлять вызывающего искать причину в чужом скрипте, когда отказ
# произошёл здесь, не лучше.
[ -f "$schema" ] || { echo "нет файла схемы: $schema" >&2; exit 2; }
[ -f "$prompt" ] || { echo "нет файла инструкций: $prompt" >&2; exit 2; }
# `-f` проверяет существование, не читаемость — тот же класс, что уже
# закрыт в build-prompt.sh для промпта и дифа (находка 21), но остался
# незакрытым здесь для схемы: нечитаемая (`chmod 000`) схема раньше
# доходила до `codex exec`, тот отказывал, и "ревьюер не отработал" (код
# 3) выдавался вместо честного конфигурационного отказа (код 2). Промпт
# симметрично проверен и здесь, хотя `build-prompt.sh` его уже проверяет —
# по той же причине, что и `-f` выше.
[ -r "$schema" ] || { echo "файл схемы нечитаем: $schema" >&2; exit 2; }
[ -r "$prompt" ] || { echo "файл инструкций нечитаем: $prompt" >&2; exit 2; }

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
