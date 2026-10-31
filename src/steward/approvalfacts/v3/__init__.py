"""`approval-facts/v3`: факты акта approve (ревью, отзывы, доставка), история политики.

Публичный API — `__all__` (спека steward §3.1, §3.2; решения M0-J, M0-K); инструкция
потребителя — `docs/approval-facts-v3.md`. Вспомогательные имена (`canon_ts`,
`validate_review_facts`, словари состояний) остаются в своих подмодулях. Импорт пакета
не делает ввода-вывода.
"""

from steward.approvalfacts.v3.act import read_act_facts
from steward.approvalfacts.v3.effective import effective_reviews
from steward.approvalfacts.v3.history import POLICY_FILES, read_activation, read_policy_history
from steward.approvalfacts.v3.model import (
    HISTORY_VIOLATION_CODES,
    UNAVAILABLE_CODES,
    Activation,
    Delivery,
    Dismissal,
    EventKey,
    HistoryViolation,
    PathBlob,
    Review,
    ReviewFacts,
    Unavailable,
)
from steward.approvalfacts.v3.passes import read_open_review
from steward.approvalfacts.v3.transport import (
    GhTransport,
    ReplayTransport,
    Request,
    Response,
    Transport,
    replay_transport,
)
from steward.approvalfacts.v3.vectors import VectorOutcome, run_vector

__all__ = [
    "HISTORY_VIOLATION_CODES",
    "POLICY_FILES",
    "UNAVAILABLE_CODES",
    "Activation",
    "Delivery",
    "Dismissal",
    "EventKey",
    "GhTransport",
    "HistoryViolation",
    "PathBlob",
    "ReplayTransport",
    "Request",
    "Response",
    "Review",
    "ReviewFacts",
    "Transport",
    "Unavailable",
    "VectorOutcome",
    "effective_reviews",
    "read_act_facts",
    "read_activation",
    "read_open_review",
    "read_policy_history",
    "replay_transport",
    "run_vector",
]
