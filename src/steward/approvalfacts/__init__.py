"""`approval-facts/v2`: контракт merge-акторов, продюсер, читатель, публикация.

v2 заморожен; актуальный — `steward.approvalfacts.v3` (его release-продюсер и читатель
переиспользуют отсюда `model`, `publish` и общие проверки `reader`; `reader` ещё
распознаёт v2-файл как `legacy_v2`). Удаление v2-продюсера — пункт TODO
`approval-facts-v2-producer-removal`.
"""

from steward.approvalfacts.model import (
    MAX_CLOCK_SKEW_SECONDS,
    MAX_LEASE_SECONDS,
    ApprovalFactsV2,
    Header,
    ObservationState,
    RequestId,
    RequestKind,
    Result,
    canonical_scope_bytes,
    scope_digest,
)
from steward.approvalfacts.publish import (
    FACTS_RELPATH,
    ConfigError,
    NotAGitRepository,
    build_header,
    parse_origin,
    publish,
    remove_previous,
    resolve_bundle_target,
    resolve_repo_root,
)
from steward.approvalfacts.reader import UnreadableFacts, detect_legacy_v1, load_facts

__all__ = [
    "FACTS_RELPATH",
    "MAX_CLOCK_SKEW_SECONDS",
    "MAX_LEASE_SECONDS",
    "ApprovalFactsV2",
    "ConfigError",
    "Header",
    "NotAGitRepository",
    "ObservationState",
    "RequestId",
    "RequestKind",
    "Result",
    "UnreadableFacts",
    "build_header",
    "canonical_scope_bytes",
    "detect_legacy_v1",
    "load_facts",
    "parse_origin",
    "publish",
    "remove_previous",
    "resolve_bundle_target",
    "resolve_repo_root",
    "scope_digest",
]
