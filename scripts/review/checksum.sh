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

# Обязательный ИНВЕНТАРЬ кита (§5 спеки) — ПОЛНЫЕ вендор-пути: PIN обязан
# покрыть каждый член — перечень существует, чтобы исключать известных
# не-китовых соседей (install-hook.sh, настроенный промпт), а не чтобы
# позволять пропуски: PIN без checksum.sh оставлял бы подменённый файл
# непроверенным при зелёном исходе (major гейта на #101, второй заход).
# Пути, не basename: обманки с правильными именами в чужих путях
# удовлетворяли бы инвентарь, пока настоящие вендор-пути дрейфуют (major
# третьего захода). Раскладка кита не свободна: local.sh вычисляет соседей
# от своего каталога, схему — от .github/codex; смена раскладки или состава
# — правка кита через ревью, синхронно со спекой.
required_kit="scripts/review/build-prompt.sh scripts/review/collect-context.sh scripts/review/apply-threshold.sh scripts/review/local.sh scripts/review/checksum.sh .github/codex/review-schema.json"

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
seen_paths=""
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
    # Запись вне канонического инвентаря — негодный PIN (код 2), не дрейф:
    # настроенная копия review-prompt.md — данные репо вне copy-integrity
    # (§5), и PIN с такой записью превращал бы легальную настройку в «дрейф
    # кита, ре-вендорьте» (minor четвёртого захода гейта на #101). PIN —
    # ровно инвентарь, ни больше ни меньше.
    case " $required_kit " in
        *" $path "*) ;;
        *)
            echo "PIN перечисляет файл вне состава кита (§5): $path —" \
                "copy-integrity его не покрывает, уберите строку." >&2
            exit 2 ;;
    esac
    checked=$((checked + 1))
    seen_paths="$seen_paths $path"
    # Симлинк в ЛЮБОМ компоненте пути — отказ ЦЕЛОСТНОСТИ, даже при
    # совпадающих байтах цели: `-f` разыменовывает, и структурно
    # подменённый кит проходил бы зелёным — как ссылкой на месте самого
    # файла (major четвёртого захода), так и заменой всего каталога
    # `scripts/review/` на ссылку (major пятого). Компоненты выше `$root`
    # не проверяются намеренно: `/tmp` на macOS — легальный симлинк, а
    # контракт раскладки начинается от корня репо. Тот же класс, что
    # «обычный файл, не симлинк» при извлечении механики из base в CI.
    symlinked=""
    prefix="$root"
    rest="$path"
    while [ -n "$rest" ]; do
        seg=${rest%%/*}
        if [ "$seg" = "$rest" ]; then rest=""; else rest=${rest#*/}; fi
        prefix="$prefix/$seg"
        if [ -L "$prefix" ]; then symlinked="$prefix"; break; fi
    done
    if [ -n "$symlinked" ]; then
        echo "СИМЛИНК в пути: $path (компонент $symlinked) — вендор-копия" \
            "обязана лежать обычными файлами в обычных каталогах." >&2
        failed=$((failed + 1))
        continue
    fi
    if [ ! -f "$root/$path" ]; then
        echo "ФАЙЛ ОТСУТСТВУЕТ: $path (есть в PIN, нет в копии)" >&2
        failed=$((failed + 1))
        continue
    fi
    # Нечитаемый файл — отказ ЧЕКЕРА (код 2), не дрейф: байты сверить
    # невозможно, и «РАСХОЖДЕНИЕ, ре-вендорьте» было бы ложным диагнозом —
    # сломанный пайплайн хеширования давал пустой hash_actual (minor гейта
    # на #101).
    if [ ! -r "$root/$path" ]; then
        echo "файл из PIN нечитаем — сверить нечем (это не дрейф):" \
            "$path" >&2
        exit 2
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

# Непокрытый член инвентаря — негодный PIN (код 2), не дрейф копии: файл
# может быть цел, но проверка о нём молчит.
# Пробельный кейс безопасен: канонические вендор-пути пробелов не содержат,
# а путь ИЗ PIN с пробелом просто не совпадёт с каноническим членом.
missing_kit=""
for member in $required_kit; do
    case " $seen_paths " in
        *" $member "*) ;;
        *) missing_kit="$missing_kit $member" ;;
    esac
done
if [ -n "$missing_kit" ]; then
    echo "PIN не покрывает состав кита (§5):$missing_kit — subset-PIN это" \
        "негодная конфигурация, не пройденная проверка." >&2
    exit 2
fi

if [ "$failed" -gt 0 ]; then
    echo "копия кита разошлась с PIN: $failed из $checked файла(ов)." \
        "Ре-вендор — см. runbook потребителя; это отказ целостности, не" \
        "дефект текущего PR." >&2
    exit 1
fi
echo "копия кита совпадает с PIN: $checked файла(ов)."
