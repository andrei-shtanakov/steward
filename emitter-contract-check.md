# Проверка контракта emitter: decomposition → Maestro `project.yaml`

> Дата: 2026-07-05 · Прогон: `maestro.config.load_orchestrator_config` + `preflight.validate_project(check_fs=False)`
> (Python 3.10 sandbox с шимами `datetime.UTC`/`enum.StrEnum`/`typing.Self`; deps доставлены pip.)

## Результат

| Проба | Ожидание | Факт |
|---|---|---|
| Загрузка `project.yaml` | 5 workstreams, deps распознаны | ✅ LOAD OK, все id + depends_on корректны |
| `validate --no-fs` (baseline) | чисто | ✅ `ok=True`, 0 issues |
| `validate --strict` (baseline) | чисто | ✅ `ok=True`, warnings=[] |
| NEG: цикл (gate-check ↔ compile-down) | ошибка | ✅ `dag-cycle` error |
| NEG: пересечение scope | предупреждение | ✅ `scope-overlap` warning |
| NEG: висячая `depends_on` (`does-not-exist`) | ошибка/ворнинг | ❌ **не поймано** — `ok=True`, 0 issues |

**Вывод:** формат `project.yaml`, который отдаёт compile-down steward, **валиден для Maestro** —
загрузчик и preflight принимают его без правок; детекторы цикла и overlap реально срабатывают
(зелёный результат не пустой).

## Находка: Maestro `validate --no-fs` не ловит висячую зависимость

`depends_on: [does-not-exist]` проходит статическую валидацию молча (`ok=True`). То есть если
emitter steward сгенерит битую ссылку между workstream'ами, `maestro validate` её не отсекёт.

**Следствие для steward:** целостность `depends_on` (все id существуют) должен проверять **сам
gate-check** на верхнем слое, до компиляции вниз — это ложится на `REQ-203 traceability`
(обобщить: не только `traces_to`, но и dep-ссылки между WS). Не полагаться на Maestro-preflight
как на сеть безопасности здесь.

**Следствие для Maestro (отдельно):** кандидат-баг — `preflight.validate_project` стоит
дополнить проверкой «depends_on ссылается на существующий workstream». Мелко, но убирает класс
тихих ошибок. Зафиксировать в бэклоге Maestro.
