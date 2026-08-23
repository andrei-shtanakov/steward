"""Манифест контекста ЭТОГО репозитория собирается — проверяется в своём же PR.

Зачем отдельно от `test_collect_context.py`. Тот доказывает поведение сборщика
на синтетических репозиториях; здесь проверяется конкретный файл
`.github/codex/review-context.txt` в этом репо.

Разница принципиальная и найдена ревьюером на #97. Курируемый контекст читается
из base, и это значит, что PR, ВНОСЯЩИЙ правку в манифест, использует ещё старую
его версию: опечатка вроде `src/prodcer.py` или ссылка на переименованный файл
пройдёт зелёной, а покраснеет — каждый следующий чужой PR, когда битый манифест
уже станет base. Чинить это в самом codex-review нечем: он по построению не
смотрит в head. Зато `ci.yml` чекаутит именно head, и здесь проверка оказывается
ровно там, где нужно.

Проверка зовёт НАСТОЯЩИЙ `collect-context.sh`, а не повторяет его правила: вторая
реализация тех же правил завела бы пару, которая однажды разойдётся, и тогда
зелёный тест перестал бы что-либо значить о живом прогоне.

Оговорка: сверяется HEAD, то есть закоммиченное состояние. Локально правку
манифеста надо сначала закоммитить, иначе тест проверит предыдущую версию.
В CI head-коммит PR — это ровно то, что нужно.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "review" / "collect-context.sh"
MANIFEST = ".github/codex/review-context.txt"


def test_repo_manifest_collects_from_head() -> None:
    """Манифест этого репо в HEAD даёт годный пакет контекста."""
    present = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"HEAD:{MANIFEST}"],
        capture_output=True,
    )
    assert present.returncode == 0, (
        f"{MANIFEST} нет в HEAD. Если манифест удалён намеренно, этот тест надо "
        "снять тем же PR — молча пройти он не должен."
    )

    result = subprocess.run(
        ["sh", str(SCRIPT), "--base", "HEAD", "--manifest", MANIFEST],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0, (
        "манифест контекста не собирается из HEAD — после мержа он покрасил бы "
        f"каждый следующий PR:\n{result.stderr}"
    )
    assert result.stdout.startswith("--- ФАЙЛ ")
