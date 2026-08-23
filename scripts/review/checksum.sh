#!/bin/sh
# Переносимая сверка вендор-копии кита с PIN — copy-integrity потребителя,
# у которого нет своего механизма (Rust, Elixir; Python-репо предпочитают
# существующий contract-тест — §5 спеки). Только POSIX shell и стандартные
# утилиты, как весь кит.
#
# PIN — файл в формате sha256sum: `<64 hex>  <путь-от-корня>` по строке на
# файл; строки `#...` и пустые — комментарии (SOURCE-строка с SHA продюсера
# живёт там же). Проверяется ПЕРЕЧЕНЬ, не каталог: install-hook.sh и
# настроенная копия review-prompt.md — штатные соседи кита у потребителя, и
# «лишний файл» не должен спотыкать целостность (§5).
#
# Коды: 0 — копия совпадает; 1 — расхождение или отсутствие файла из
# перечня (все, не только первое: чинить по одному файлу за прогон — N
# прогонов вместо одного); 2 — ошибка конфигурации (нет PIN, битая строка,
# пустой перечень — нулевая проверка неотличима от пройденной и потому
# отказ, не успех).
#
# БУТСТРАП (@id:review-kit-checksum-bootstrap), сказано честно: сверка себя
# собой — не защита. PIN обязан перечислять и сам checksum.sh, но
# согласованная подмена «скрипт + его строка в PIN» этим не ловится; вторая
# гарантия — upstream-drift watch по расписанию у потребителя, сверяющий
# копию с деревом продюсера, и ревью PR, через которое едет любой ре-вендор.
set -eu

usage() {
    echo "usage: checksum.sh --pin <file> [--root <dir>]" >&2
}

hash_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        echo "нет ни sha256sum, ни shasum — сверить копию нечем." >&2
        exit 2
    fi
}

pin=""
root="."
while [ $# -gt 0 ]; do
    case "$1" in
        --pin)  [ $# -ge 2 ] || { usage; exit 2; }; pin="$2"; shift 2 ;;
        --root) [ $# -ge 2 ] || { usage; exit 2; }; root="$2"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

[ -n "$pin" ] || { usage; exit 2; }
[ -f "$pin" ] || { echo "нет файла PIN: $pin" >&2; exit 2; }
[ -r "$pin" ] || { echo "файл PIN нечитаем: $pin" >&2; exit 2; }
[ -d "$root" ] || { echo "нет каталога корня: $root" >&2; exit 2; }

checked=0
failed=0
# Построчный разбор без word-splitting пути: путь — всё после «хеш + два
# пробела», пробелы в нём легальны.
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        ''|'#'*) continue ;;
    esac
    hash_expected=${line%%  *}
    path=${line#*  }
    # Битая строка — отказ, не пропуск: пропущенная строка это непроверенный
    # файл, который выглядел бы проверенным.
    case "$hash_expected" in
        *[!0-9a-f]*|'')
            echo "битая строка PIN (ожидался '<sha256>  <путь>'): $line" >&2
            exit 2 ;;
    esac
    if [ ${#hash_expected} -ne 64 ] || [ "$path" = "$line" ] || [ -z "$path" ]; then
        echo "битая строка PIN (ожидался '<sha256>  <путь>'): $line" >&2
        exit 2
    fi
    checked=$((checked + 1))
    if [ ! -f "$root/$path" ]; then
        echo "ФАЙЛ ОТСУТСТВУЕТ: $path (есть в PIN, нет в копии)" >&2
        failed=$((failed + 1))
        continue
    fi
    hash_actual=$(hash_file "$root/$path")
    if [ "$hash_actual" != "$hash_expected" ]; then
        echo "РАСХОЖДЕНИЕ: $path (PIN $hash_expected, копия $hash_actual)" >&2
        failed=$((failed + 1))
    fi
done < "$pin"

if [ "$checked" -eq 0 ]; then
    echo "PIN не перечисляет ни одного файла — нулевая проверка это отказ," \
        "не успех: $pin" >&2
    exit 2
fi

if [ "$failed" -gt 0 ]; then
    echo "копия кита разошлась с PIN: $failed из $checked файла(ов)." \
        "Ре-вендор — см. runbook потребителя; это отказ целостности, не" \
        "дефект текущего PR." >&2
    exit 1
fi
echo "копия кита совпадает с PIN: $checked файла(ов)."
