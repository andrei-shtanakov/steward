"""Review-eval harness: измеримый eval review-kit (P2, review-kit-eval-harness).

Реэкспортируются два предиката порога — ими пользуются и метрики, и внешний
разбор вердикта, и оба отвечают на вопросы, которые нельзя путать:
`is_schema_valid_finding` — годна ли находка по схеме вердикта (негодную кит
отвергает кодом 2, не решая ничего про блокировку), `is_blocking` — красит ли
годная находка мерж.
"""

from steward.review_eval.threshold import is_blocking, is_schema_valid_finding

__all__ = ["is_blocking", "is_schema_valid_finding"]
