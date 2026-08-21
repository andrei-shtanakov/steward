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

# Различаем ГДЕ задан core.hooksPath, а не КУДА он указывает: локальный
# (per-repo) core.hooksPath — осознанный выбор пользователя ИМЕННО для этого
# репозитория, и ставить туда можно, даже если каталог физически лежит вне
# `.git` (см. mkdir -p ниже — этот сценарий уже поддержан). ГЛОБАЛЬНЫЙ (или
# system) core.hooksPath — одно значение на ВСЕ репозитории пользователя;
# уважать его и поставить наш opt-in хук туда означало бы, что он молча
# станет глобальным — сработает на пуше в любой другой репозиторий, где
# REVIEW_KIT_DIR резолвится в чужой scripts/review, кита там нет, и пуш в
# несвязанный проект блокируется. `git config --local` смотрит только в
# `.git/config` этого репозитория — эффективное значение (`--git-path`
# выше) при отсутствующем локальном может прийти только из global/system.
if ! git config --local --get core.hooksPath >/dev/null 2>&1 \
    && git config --get core.hooksPath >/dev/null 2>&1; then
    echo "core.hooksPath задан не в этом репозитории (глобально или" \
        "системно), эффективное значение: $hooks_dir." >&2
    echo "Ставить сюда pre-push нельзя: opt-in для одного репозитория" \
        "молча стал бы глобальным для всех репозиториев, где действует" \
        "этот core.hooksPath." >&2
    echo "Настройте core.hooksPath локально для этого репозитория" \
        "(git config core.hooksPath <путь>, без --global), если хотите" \
        "использовать хук здесь." >&2
    exit 2
fi

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
