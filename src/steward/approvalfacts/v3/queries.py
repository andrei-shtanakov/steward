"""Канонические тексты GraphQL `approval-facts/v3` — побайтово строки §3.8 спеки.

Текст запроса входит в ключ `ReplayTransport` (R-M0-7): фикстура не может вернуть поле,
которого запрос не просит. Правка любого текста требует нового живого снимка
(`scripts/capture_approval_facts_v3_live.py` импортирует тексты отсюда).
"""

Q_DELIVERY = (
    "query StewardV3Delivery($owner:String!,$name:String!,$number:Int!)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{number state merged mergedAt mergeCommit{oid} mergedBy{login __typename}"
    " headRefOid baseRefName}}}"
)
Q_REVIEWS = (
    "query StewardV3Reviews($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{state headRefOid baseRefName reviews(first:100,after:$after)"
    "{nodes{databaseId state submittedAt commit{oid} author{login __typename}}"
    " pageInfo{hasNextPage endCursor}}}}}"
)
Q_TIMELINE = (
    "query StewardV3Timeline($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{timelineItems(itemTypes:[REVIEW_DISMISSED_EVENT,MERGED_EVENT],first:100,after:$after)"
    "{nodes{__typename ... on ReviewDismissedEvent{createdAt actor{login __typename}"
    " previousReviewState review{databaseId}} ... on MergedEvent{createdAt commit{oid}}}"
    " pageInfo{hasNextPage endCursor}}}}}"
)
Q_COMMIT_PULLS = (
    "query StewardV3CommitPulls($owner:String!,$name:String!,$sha:GitObjectID!,$after:String)"
    "{repository(owner:$owner,name:$name){object(oid:$sha){... on Commit"
    "{associatedPullRequests(first:100,after:$after){nodes{number mergeCommit{oid}}"
    " pageInfo{hasNextPage endCursor}}}}}}"
)


def q_blobs(n: int) -> str:
    """`StewardV3Blobs` для `n` путей: алиасы `m0..m{n-1}`, `h0..h{n-1}` (спека §3.8)."""
    decl = "".join(f",$m{i}:String!,$h{i}:String!" for i in range(n))
    sel = "".join(
        f" m{i}:object(expression:$m{i}){{__typename ... on Blob{{oid}}}}"
        f" h{i}:object(expression:$h{i}){{__typename ... on Blob{{oid}}}}"
        for i in range(n)
    )
    return (
        "query StewardV3Blobs($owner:String!,$name:String!,$merge:GitObjectID!,"
        f"$head:GitObjectID!{decl}){{repository(owner:$owner,name:$name)"
        f"{{mc:object(oid:$merge){{oid}} hc:object(oid:$head){{oid}}{sel}}}}}"
    )
