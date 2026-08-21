#!/bin/sh
# Установка pre-push хука. Opt-in: хук не появляется сам при клонировании,
# потому что решение тратить время и токены на каждый пуш — за разработчиком.
set -eu

root=$(git rev-parse --show-toplevel)
src="$root/.github/hooks/pre-push"
dst="$(git rev-parse --git-path hooks)/pre-push"

[ -f "$src" ] || { echo "нет $src" >&2; exit 2; }

if [ -e "$dst" ]; then
    echo "уже есть: $dst" >&2
    echo "снести вручную и повторить, если хотите заменить" >&2
    exit 2
fi

cp "$src" "$dst"
chmod +x "$dst"
echo "установлено: $dst"
echo "обход: git push --no-verify"
