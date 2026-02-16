from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

import yaml


@dataclass(frozen=True)
class FieldRule:
    name: str
    pattern: str
    required: bool = True
    group: int | str = 1
    default: str = ""


def load_field_rules(path: str | Path) -> list[FieldRule]:
    field_map_path = Path(path)
    if not field_map_path.exists():
        raise FileNotFoundError(f"Field map not found: {field_map_path}")

    with field_map_path.open("r", encoding="utf-8") as file:
        payload: dict[str, Any] = yaml.safe_load(file) or {}

    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("Field map must include a non-empty `fields` list.")

    rules: list[FieldRule] = []
    for idx, raw in enumerate(fields, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"`fields[{idx}]` must be an object.")

        name = str(raw.get("name", "")).strip()
        pattern = str(raw.get("pattern", "")).strip()

        if not name:
            raise ValueError(f"`fields[{idx}].name` is required.")
        if not pattern:
            raise ValueError(f"`fields[{idx}].pattern` is required.")

        try:
            re.compile(pattern, flags=re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise ValueError(
                f"Invalid regex for field `{name}`: {pattern} ({exc})"
            ) from exc

        rules.append(
            FieldRule(
                name=name,
                pattern=pattern,
                required=bool(raw.get("required", True)),
                group=raw.get("group", 1),
                default=str(raw.get("default", "")),
            )
        )

    return rules
