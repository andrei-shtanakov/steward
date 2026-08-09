"""Tests for the profile loader and SpecGraph (WS-001, REQ-201)."""

from pathlib import Path

import pytest

from steward.graph import (
    ProfileError,
    SpecGraph,
    SpecNode,
    load_profile,
    load_profile_data,
)
from steward.roles import Role, RolesCatalog, load_roles_catalog

CATALOG = RolesCatalog(
    version=1,
    slug_pattern="^[a-z][a-z0-9-]{1,31}$",
    roles=(Role("product", "Product"), Role("qa", "QA"), Role("architects", "Architecture")),
)

PROFILES = Path(__file__).resolve().parents[1] / "profiles"


def _graph(data):
    return load_profile_data(data, roles_catalog=CATALOG)


def _lite_data() -> dict:
    return {
        "profile": "lite",
        "solo_auto_approve": True,
        "artifacts": [
            {"id": "requirements", "owner_role": "product", "upstream": []},
            {"id": "design", "owner_role": "product", "upstream": ["requirements"]},
            {
                "id": "tasks",
                "owner_role": "product",
                "upstream": ["design"],
                "delegate": "spec-runner",
            },
        ],
    }


def test_load_data_builds_graph_with_all_nodes() -> None:
    graph = _graph(_lite_data())
    assert isinstance(graph, SpecGraph)
    assert graph.profile == "lite"
    assert graph.solo_auto_approve is True
    assert set(graph.nodes) == {"requirements", "design", "tasks"}


def test_nodes_are_specnodes() -> None:
    graph = _graph(_lite_data())
    assert isinstance(graph.nodes["design"], SpecNode)


def test_upstream_edges_parsed() -> None:
    graph = _graph(_lite_data())
    assert graph.nodes["requirements"].upstream == ()
    assert graph.nodes["design"].upstream == ("requirements",)


def test_delegate_field_parsed() -> None:
    graph = _graph(_lite_data())
    assert graph.nodes["tasks"].delegate == "spec-runner"
    assert graph.nodes["design"].delegate is None


def test_node_required_defaults_true() -> None:
    graph = _graph(_lite_data())
    assert graph.nodes["design"].required is True


def test_node_can_be_optional() -> None:
    data = _lite_data()
    data["artifacts"][0]["required"] = False
    graph = _graph(data)
    assert graph.nodes["requirements"].required is False


def test_solo_auto_approve_defaults_false() -> None:
    data = _lite_data()
    del data["solo_auto_approve"]
    graph = _graph(data)
    assert graph.solo_auto_approve is False


def test_dangling_upstream_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][1]["upstream"] = ["nonexistent"]
    with pytest.raises(ProfileError, match="nonexistent"):
        _graph(data)


def test_cycle_raises_profile_error() -> None:
    data = {
        "profile": "broken",
        "artifacts": [
            {"id": "a", "owner_role": "product", "upstream": ["b"]},
            {"id": "b", "owner_role": "product", "upstream": ["a"]},
        ],
    }
    with pytest.raises(ProfileError, match="cycle"):
        _graph(data)


def test_duplicate_id_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"].append({"id": "design", "owner_role": "product", "upstream": []})
    with pytest.raises(ProfileError, match="duplicate"):
        _graph(data)


def test_missing_owner_role_raises_profile_error() -> None:
    data = _lite_data()
    del data["artifacts"][0]["owner_role"]
    with pytest.raises(ProfileError, match="owner_role"):
        _graph(data)


def test_empty_artifacts_raises_profile_error() -> None:
    with pytest.raises(ProfileError, match="artifacts"):
        _graph({"profile": "empty", "artifacts": []})


def test_non_mapping_raises_profile_error() -> None:
    with pytest.raises(ProfileError):
        _graph(["not", "a", "mapping"])


def test_non_bool_solo_auto_approve_raises_profile_error() -> None:
    data = _lite_data()
    data["solo_auto_approve"] = "false"  # quoted YAML → truthy string, not a bool
    with pytest.raises(ProfileError, match="solo_auto_approve"):
        _graph(data)


def test_non_bool_required_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][0]["required"] = "false"
    with pytest.raises(ProfileError, match="required"):
        _graph(data)


def test_empty_string_upstream_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][1]["upstream"] = ""
    with pytest.raises(ProfileError, match="upstream"):
        _graph(data)


def test_null_upstream_treated_as_empty() -> None:
    data = _lite_data()
    data["artifacts"][0]["upstream"] = None
    graph = _graph(data)
    assert graph.nodes["requirements"].upstream == ()


def test_duplicate_upstream_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][1]["upstream"] = ["requirements", "requirements"]
    with pytest.raises(ProfileError, match="upstream"):
        _graph(data)


def test_topo_order_upstream_before_downstream() -> None:
    order = _graph(_lite_data()).topo_order()
    assert order.index("requirements") < order.index("design")
    assert order.index("design") < order.index("tasks")


def test_topo_order_covers_all_nodes() -> None:
    graph = _graph(_lite_data())
    assert set(graph.topo_order()) == set(graph.nodes)


def test_load_profile_reads_file(tmp_path) -> None:
    # Exercises load_profile's file-reading path with an inline sample,
    # independent of shipped profile data — see test_shipped_lite_profile_
    # loads_canonical / test_shipped_team_profile_loads_canonical below for
    # the real files.
    profile_path = tmp_path / "sample.yaml"
    profile_path.write_text(
        "profile: sample\n"
        "solo_auto_approve: true\n"
        "artifacts:\n"
        "  - {id: a, owner_role: product, upstream: []}\n"
        "  - {id: b, owner_role: qa, upstream: [a]}\n",
        encoding="utf-8",
    )
    graph = load_profile(profile_path, roles_catalog=CATALOG)
    assert graph.profile == "sample"
    assert graph.solo_auto_approve is True
    assert set(graph.nodes) == {"a", "b"}
    assert graph.nodes["b"].upstream == ("a",)
    assert graph.nodes["b"].owner_role == "qa"


def test_canonical_owner_role_loads() -> None:
    g = _graph(
        {
            "profile": "p",
            "artifacts": [{"id": "a", "owner_role": "product", "upstream": []}],
        }
    )
    assert g.nodes["a"].owner_role == "product"
    assert g.nodes["a"].reviewer_roles == ()
    assert g.nodes["a"].allowed_approver_roles is None


@pytest.mark.parametrize("bad", ["@product", "product,qa", "@product,@qa", "", 7, None])
def test_legacy_or_malformed_owner_role_rejected(bad) -> None:
    with pytest.raises(ProfileError, match="owner_role"):
        _graph(
            {
                "profile": "p",
                "artifacts": [{"id": "a", "owner_role": bad, "upstream": []}],
            }
        )


def test_unresolvable_owner_role_rejected() -> None:
    with pytest.raises(ProfileError, match="ghost"):
        _graph(
            {
                "profile": "p",
                "artifacts": [{"id": "a", "owner_role": "ghost", "upstream": []}],
            }
        )


def test_reviewer_roles_parse_and_resolve() -> None:
    g = _graph(
        {
            "profile": "p",
            "artifacts": [
                {"id": "a", "owner_role": "product", "reviewer_roles": ["qa"], "upstream": []}
            ],
        }
    )
    assert g.nodes["a"].reviewer_roles == ("qa",)


@pytest.mark.parametrize("field", ["reviewer_roles", "allowed_approver_roles"])
@pytest.mark.parametrize("bad", [[], ["ghost"], ["qa", "qa"], ["@qa"], "qa", [7]])
def test_bad_role_arrays_rejected(field, bad) -> None:
    with pytest.raises(ProfileError, match=field):
        _graph(
            {
                "profile": "p",
                "artifacts": [{"id": "a", "owner_role": "product", field: bad, "upstream": []}],
            }
        )


@pytest.mark.parametrize("field", ["reviewer_roles", "allowed_approver_roles"])
def test_explicit_null_role_array_rejected(field) -> None:
    # Absent is the ONLY spelling of "use the default" — an explicit null
    # (reviewer_roles: null / bare `reviewer_roles:` in YAML) would be a second
    # representation of absence and must fail, not silently pass as absent.
    with pytest.raises(ProfileError, match="null"):
        _graph(
            {
                "profile": "p",
                "artifacts": [{"id": "a", "owner_role": "product", field: None, "upstream": []}],
            }
        )


def test_allowed_approver_roles_exact_allowlist_stored() -> None:
    # Owner's ruling: an explicit list REPLACES the {owner_role} default —
    # separation of duties must be expressible. The loader stores it verbatim;
    # it never unions in the owner.
    g = _graph(
        {
            "profile": "p",
            "artifacts": [
                {
                    "id": "a",
                    "owner_role": "product",
                    "allowed_approver_roles": ["qa"],
                    "upstream": [],
                }
            ],
        }
    )
    assert g.nodes["a"].allowed_approver_roles == ("qa",)
    assert "product" not in g.nodes["a"].allowed_approver_roles


def test_shipped_lite_profile_loads_canonical() -> None:
    catalog = load_roles_catalog(PROFILES / "roles.yaml")
    graph = load_profile(PROFILES / "lite.yaml", catalog)
    assert graph.profile == "lite"
    assert graph.solo_auto_approve is True
    for node_id in ("requirements", "design", "tasks"):
        assert graph.nodes[node_id].owner_role == "owner"
        assert graph.nodes[node_id].reviewer_roles == ()


def test_shipped_team_profile_loads_canonical() -> None:
    catalog = load_roles_catalog(PROFILES / "roles.yaml")
    graph = load_profile(PROFILES / "team.yaml", catalog)
    assert graph.profile == "team"
    requirements = graph.nodes["requirements"]
    assert requirements.owner_role == "product"
    assert requirements.reviewer_roles == ("architects",)
    assert graph.nodes["design"].owner_role == "architects"
    assert graph.nodes["acceptance"].owner_role == "qa"
    assert graph.nodes["decomposition"].owner_role == "tech-lead"
    assert graph.nodes["tasks"].owner_role == "stream-owner"
