"""Deterministic YAML rendering shared by the compile-down emitters."""

from __future__ import annotations

import yaml

__all__ = ["render_yaml"]


class _Dumper(yaml.SafeDumper):
    """SafeDumper that keeps multi-line strings as literal blocks (``|``)."""


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_representer)


def render_yaml(payload: dict) -> str:
    """Render a payload to YAML: insertion order kept, unicode intact."""
    return yaml.dump(
        payload,
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
