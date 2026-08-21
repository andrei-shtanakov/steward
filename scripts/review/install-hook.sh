#!/bin/sh
# Установка pre-push хука. Opt-in: хук не появляется сам при клонировании,
# потому что решение тратить время и токены на каждый пуш — за разработчиком.
set -eu

root=$(git rev-parse --show-toplevel)
src="$root/.github/hooks/pre-push"
# `--git-path hooks` уважает `core.hooksPath`, если он задан (глобально или
# в репо) — проверено прогоном на обеих формах и на worktree; ставит именно
# туда, откуда git реально читает хуки, а не всегда в `.git/hooks`.
hooks_dir="$(git rev-parse --git-path hooks)"
dst="$hooks_dir/pre-push"

[ -f "$src" ] || { echo "нет $src" >&2; exit 2; }

if [ -e "$dst" ]; then
    echo "уже есть: $dst" >&2
    echo "снести вручную и повторить, если хотите заменить" >&2
    exit 2
fi

# `.git/hooks` создаётся самим `git init`, но каталог из `core.hooksPath`
# — нет: без mkdir -p `cp` падала бы сырой системной ошибкой под set -eu,
# а не понятным отказом.
if ! mkdir -p "$hooks_dir" 2>/dev/null; then
    echo "не удалось создать каталог хуков: $hooks_dir" >&2
    exit 2
fi

cp "$src" "$dst"
chmod +x "$dst"
echo "установлено: $dst"
echo "обход: git push --no-verify"
