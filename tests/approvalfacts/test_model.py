from steward.approvalfacts.model import RequestId, Result, canonical_scope_bytes, scope_digest

SHA = "221457933968be9e95acd51d548e080f739c794c"


def test_canonical_bytes_are_order_independent() -> None:
    """Канонизация обязана давать одинаковые байты при любом порядке входа —
    иначе scope_sha256 ловил бы порядок, а не содержание."""
    a = [RequestId("pr", 42), RequestId("merge_sha", SHA)]
    b = [RequestId("merge_sha", SHA), RequestId("pr", 42)]
    assert canonical_scope_bytes(a) == canonical_scope_bytes(b)


def test_canonical_bytes_have_no_whitespace() -> None:
    assert b", " not in canonical_scope_bytes([RequestId("pr", 42)])
    assert b'": ' not in canonical_scope_bytes([RequestId("pr", 42)])


def test_scope_digest_is_prefixed_sha256() -> None:
    digest = scope_digest([RequestId("pr", 42)])
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_comparable_excludes_request() -> None:
    """Алиасы одного мержа различаются request по определению; сравнивать
    их можно только по проекции без него (§4.3 инвариант 9)."""
    by_pr = Result(RequestId("pr", 42), "merged", SHA, "github:x", "Bot", "agent")
    by_sha = Result(RequestId("merge_sha", SHA), "merged", SHA, "github:x", "Bot", "agent")
    assert by_pr.comparable == by_sha.comparable
