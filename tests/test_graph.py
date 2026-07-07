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

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def _lite_data() -> dict:
    return {
        "profile": "lite",
        "solo_auto_approve": True,
        "artifacts": [
            {"id": "requirements", "owner_role": "@owner", "upstream": []},
            {"id": "design", "owner_role": "@owner", "upstream": ["requirements"]},
            {
                "id": "task",
                "owner_role": "@owner",
                "upstream": ["design"],
                "delegate": "spec-runner",
            },
        ],
    }


def test_load_data_builds_graph_with_all_nodes() -> None:
    graph = load_profile_data(_lite_data())
    assert isinstance(graph, SpecGraph)
    assert graph.profile == "lite"
    assert graph.solo_auto_approve is True
    assert set(graph.nodes) == {"requirements", "design", "task"}


def test_nodes_are_specnodes() -> None:
    graph = load_profile_data(_lite_data())
    assert isinstance(graph.nodes["design"], SpecNode)


def test_upstream_edges_parsed() -> None:
    graph = load_profile_data(_lite_data())
    assert graph.nodes["requirements"].upstream == ()
    assert graph.nodes["design"].upstream == ("requirements",)


def test_delegate_field_parsed() -> None:
    graph = load_profile_data(_lite_data())
    assert graph.nodes["task"].delegate == "spec-runner"
    assert graph.nodes["design"].delegate is None


def test_node_required_defaults_true() -> None:
    graph = load_profile_data(_lite_data())
    assert graph.nodes["design"].required is True


def test_node_can_be_optional() -> None:
    data = _lite_data()
    data["artifacts"][0]["required"] = False
    graph = load_profile_data(data)
    assert graph.nodes["requirements"].required is False


def test_solo_auto_approve_defaults_false() -> None:
    data = _lite_data()
    del data["solo_auto_approve"]
    graph = load_profile_data(data)
    assert graph.solo_auto_approve is False


def test_dangling_upstream_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][1]["upstream"] = ["nonexistent"]
    with pytest.raises(ProfileError, match="nonexistent"):
        load_profile_data(data)


def test_cycle_raises_profile_error() -> None:
    data = {
        "profile": "broken",
        "artifacts": [
            {"id": "a", "owner_role": "@o", "upstream": ["b"]},
            {"id": "b", "owner_role": "@o", "upstream": ["a"]},
        ],
    }
    with pytest.raises(ProfileError, match="cycle"):
        load_profile_data(data)


def test_duplicate_id_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"].append({"id": "design", "owner_role": "@owner", "upstream": []})
    with pytest.raises(ProfileError, match="duplicate"):
        load_profile_data(data)


def test_missing_owner_role_raises_profile_error() -> None:
    data = _lite_data()
    del data["artifacts"][0]["owner_role"]
    with pytest.raises(ProfileError, match="owner_role"):
        load_profile_data(data)


def test_empty_artifacts_raises_profile_error() -> None:
    with pytest.raises(ProfileError, match="artifacts"):
        load_profile_data({"profile": "empty", "artifacts": []})


def test_non_mapping_raises_profile_error() -> None:
    with pytest.raises(ProfileError):
        load_profile_data(["not", "a", "mapping"])


def test_non_bool_solo_auto_approve_raises_profile_error() -> None:
    data = _lite_data()
    data["solo_auto_approve"] = "false"  # quoted YAML → truthy string, not a bool
    with pytest.raises(ProfileError, match="solo_auto_approve"):
        load_profile_data(data)


def test_non_bool_required_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][0]["required"] = "false"
    with pytest.raises(ProfileError, match="required"):
        load_profile_data(data)


def test_empty_string_upstream_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][1]["upstream"] = ""
    with pytest.raises(ProfileError, match="upstream"):
        load_profile_data(data)


def test_null_upstream_treated_as_empty() -> None:
    data = _lite_data()
    data["artifacts"][0]["upstream"] = None
    graph = load_profile_data(data)
    assert graph.nodes["requirements"].upstream == ()


def test_duplicate_upstream_raises_profile_error() -> None:
    data = _lite_data()
    data["artifacts"][1]["upstream"] = ["requirements", "requirements"]
    with pytest.raises(ProfileError, match="upstream"):
        load_profile_data(data)


def test_topo_order_upstream_before_downstream() -> None:
    order = load_profile_data(_lite_data()).topo_order()
    assert order.index("requirements") < order.index("design")
    assert order.index("design") < order.index("task")


def test_topo_order_covers_all_nodes() -> None:
    graph = load_profile_data(_lite_data())
    assert set(graph.topo_order()) == set(graph.nodes)


def test_load_shipped_lite_profile() -> None:
    graph = load_profile(PROFILES_DIR / "lite.yaml")
    assert graph.profile == "lite"
    assert graph.solo_auto_approve is True
    assert set(graph.nodes) == {"requirements", "design", "task"}
    assert graph.nodes["task"].delegate == "spec-runner"


def test_load_shipped_team_profile() -> None:
    graph = load_profile(PROFILES_DIR / "team.yaml")
    assert graph.profile == "team"
    assert graph.solo_auto_approve is False
    assert set(graph.nodes) >= {
        "charter",
        "requirements",
        "design",
        "acceptance",
        "decomposition",
        "task",
    }
    assert graph.nodes["decomposition"].upstream == ("design", "acceptance")
    assert graph.nodes["task"].per == "workstream"
