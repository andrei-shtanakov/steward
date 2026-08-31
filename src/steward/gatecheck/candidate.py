"""Prospective gate-check over a **candidate revision** (steward#140).

A candidate revision is bundle content that is not yet a git ref: files edited
but not committed, or a directory assembled by a pipeline before it decides
whether to commit at all. ``gate-check`` otherwise reads git facts from a
checkout (:class:`~steward.gatecheck.git_facts.LiveGitFacts`) or from an
injected facts file (``--no-fs``); neither can describe content that no ref
points at.

Why this is a mode and not a facts adapter
------------------------------------------

The tempting shape — "an adapter that answers every ``GitFacts`` question for
uncommitted content" — cannot be written honestly, because the questions split
into two kinds:

*Content questions.* ``blob_hash`` asks for the git object id of a file's
bytes. That is a pure function of the content (``sha1("blob <len>\\0" + bytes)``,
:func:`blob_hash_of`) and needs no ref, no index and no ``git`` binary. It is
also the question the stale-cascade gates (``GC-STALE*``) are built on, so a
candidate revision's pin cascade is checkable **before** the commit exists —
and checkable *more* correctly than the live path, which reads ``HEAD:<path>``
and would compare pins against the last commit rather than against the content
in front of it.

*Ref questions.* ``on_default_branch``, ``is_ancestor``, ``changed_paths_since``
and ``merge_provenance`` ask where content sits in history. For a candidate the
answer is not "no" — it is "not askable yet". Returning ``False`` would fabricate
an answer, and fabricating "not on the default branch" would make
``check_status_git`` emit ``GC-GIT-BRANCH`` against every ``status: approved``
artifact in the bundle: a wall of findings about a question nobody asked. So
:class:`CandidateGitFacts` **raises** :class:`FactsError` on those, and the mode
does not run the checks that would reach them. The gates are then reported as
*not evaluated* (:data:`NOT_EVALUATED`) rather than passed — a mode that
silently drops ref-bound gates would be exactly the "unknown rendered green"
this repo's own review scale calls a ``major``.

``approvals`` is the one method that neither fabricates nor raises: ``None``
already means "no authoritative source" in the protocol, which is precisely
true here, and every caller already treats it as skip-not-violation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from steward.gatecheck.git_facts import Approval, FactsError, MergeProvenance

__all__ = [
    "NOT_EVALUATED",
    "CandidateGitFacts",
    "blob_hash_of",
]

#: Gate classes a prospective run structurally cannot evaluate, each with the
#: reason. Declared, not silently dropped: the CLI prints this list on every
#: candidate run so the output says what it did *not* check.
#:
#: Scope note: this describes the **mode**, not the run. A ref-bound run emits
#: no such list, and that absence does not claim every gate fired — a gate can
#: still be inapplicable to a given bundle (no arch manifest, authoring stage).
NOT_EVALUATED: tuple[tuple[str, str], ...] = (
    (
        "GC-GIT-BRANCH",
        "status↔git зеркало спрашивает, лежит ли артефакт на дефолтной ветке; "
        "у кандидатной ревизии ещё нет ref'а, на котором он мог бы лежать",
    ),
    (
        "GC-GIT-ROLE",
        "авторизация аппрувера читается из approval-фактов форджи, привязанных "
        "к PR/мержу; у кандидатной ревизии ни того, ни другого не существует",
    ),
    (
        "GC-ARCH-CONFORMANCE",
        "вердикт conformance-отчёта сверяется с self-freshness по истории "
        "(ancestors/changed_paths_since) — истории у кандидата нет; схема и "
        "наличие evidence при этом проверяются (GC-ARCH-SCHEMA/-EVIDENCE)",
    ),
    (
        "GC-APPROVAL-MISSING",
        "merge-evidence приезжает из approval-facts/v2 по merge SHA; кандидат "
        "не смержен и SHA не имеет (стадия release в этом режиме запрещена)",
    ),
)


def blob_hash_of(data: bytes) -> str:
    """Git blob object id of ``data`` — the same hash ``git hash-object`` prints.

    Computed in-process rather than by spawning ``git``: the value is defined
    by the object format, not by a repository, and a candidate revision may
    well sit outside any checkout. ``sha1`` here is a content address in git's
    own object format, not a security primitive.
    """
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()  # noqa: S324 — git object id, not a MAC


class CandidateGitFacts:
    """``GitFacts`` over on-disk candidate content: hashes yes, history no.

    Paths arrive bundle-relative (``Artifact.path``), matching the other
    adapters' contract, and resolve under ``spec_dir``.
    """

    def __init__(self, spec_dir: Path) -> None:
        self._spec_dir = spec_dir

    def blob_hash(self, path: str) -> str | None:
        """Blob id of the candidate's content, or ``None`` when the file is gone.

        ``None`` keeps the protocol's meaning — "the hash could not be
        resolved" — which the stale-cascade check already renders as a warning
        rather than a proven mismatch.
        """
        target = self._spec_dir / path
        try:
            return blob_hash_of(target.read_bytes())
        except OSError:
            return None

    def approvals(self, path: str) -> tuple[Approval, ...] | None:  # noqa: ARG002
        """``None`` — unavailable, never "authoritatively empty" (see module doc)."""
        return None

    def on_default_branch(self, path: str) -> bool:
        raise FactsError(self._no_ref("on_default_branch", path))

    def is_ancestor(self, commit: str) -> bool:
        raise FactsError(self._no_ref("is_ancestor", commit))

    def changed_paths_since(self, commit: str) -> list[str]:
        raise FactsError(self._no_ref("changed_paths_since", commit))

    def merge_provenance(self, path: str) -> MergeProvenance | None:
        raise FactsError(self._no_ref("merge_provenance", path))

    @staticmethod
    def _no_ref(method: str, subject: str) -> str:
        return (
            f"{method}({subject!r}): кандидатная ревизия не является git-ref'ом — "
            "вопрос об истории к ней не задаётся. Гейт, дошедший сюда, обязан быть "
            "объявлен в candidate.NOT_EVALUATED, а не получить выдуманный ответ"
        )
