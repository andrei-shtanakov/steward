# `steward charter-check` — charter схемы 2 и реестр кодов воркстримов

Пункт 3 заявки steward#190. Контракт тот же, что у devtools
`governance/charter_guard.py` (спека bundle-criteria oracle rev 10, §1.1–1.2), чтобы
два парсера бандла не расходились.

```sh
uv run steward charter-check                      # рабочее дерево
uv run steward charter-check --base origin/master # + надгробие и неизменяемость кода
```

Проверяются `workstreams/*/spec/00-charter.md` репо.

| Код | Когда |
|---|---|
| `CHARTER-MALFORMED` | frontmatter есть, но не разбирается (не «схема 1»: опечатка не выключает оракул) |
| `CHARTER-SCHEMA` | `schema` вне словаря `1\|2` |
| `CHARTER-CODE` | схема 2 без `code` или `code` не `^[A-Z]{2,6}$` |
| `CHARTER-PLAN-ITEM` | нет `plan_item`, не `todo://<repo>/<id>`, чужой репо, или `@id:` нет в `TODO.md` |
| `CHARTER-COLLISION` | один код у двух charter'ов; нарушитель — позже влитый по first-parent (невлитый — всегда он) |
| `CHARTER-TOMBSTONE` | против `--base`: charter схемы 2 удалён, перенесён или понижен до схемы 1 |
| `CHARTER-CODE-CHANGE` | против `--base`: код сменился не у нарушителя коллизии |
| `CHARTER-BASE` | `--base` не резолвится в коммит (fail-closed, а не «в базе пусто») |

Порядок влития для `CHARTER-COLLISION` берётся из first-parent истории `--base`, а
без него — из HEAD. На ветке, в которую влит master, HEAD перечисляет charter'ы
master на мерж-коммите, то есть позже собственных: решение о коллизии принимайте
прогоном с `--base` (так делает CI). У devtools `charter_guard` то же свойство.

Без frontmatter — схема 1, не проверяется. Имя репо для `plan_item` — имя каталога
(канон: имя после `git clone`), переопределяется `--repo-name`. Коды выхода как у
gate-check: 0 — чисто, 1 — находки, 2 — не git-репо. Коды `CHARTER-*`, а не `GC-*`:
это проверка репо, а не гейт бандла, и закрытое пространство `GC-*` минтит только
каталог гейтов.
