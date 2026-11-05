from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Dataset line {line_number} must be a JSON object")
            rows.append(value)
    if not rows:
        raise ValueError("Dataset is empty")
    return rows


def field_value(row: dict[str, Any], path: str) -> Any:
    if path in row:
        return row[path]
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Dataset row is missing mapped path: {path}")
        value = value[part]
    return value


@dataclass(frozen=True)
class Selection:
    items: list[dict[str, Any]]
    provenance: dict[str, Any]


def select(path: Path, mapping: dict[str, str], *, limit: int = 5, seed: int = 42) -> Selection:
    if not 1 <= limit <= 10:
        raise ValueError("Smoke subsets must contain between 1 and 10 rows")
    if not mapping or any(not key or not value for key, value in mapping.items()):
        raise ValueError("Supply an explicit nonempty field mapping")
    rows = read_jsonl(path)
    indices = sorted(random.Random(seed).sample(range(len(rows)), min(limit, len(rows))))
    items = [
        {target: field_value(rows[index], source) for target, source in mapping.items()}
        for index in indices
    ]
    return Selection(
        items=items,
        provenance={
            "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_row_count": len(rows),
            "selected_row_count": len(items),
            "zero_based_indices": indices,
            "seed": seed,
            "algorithm": "python.random.Random.sample; sorted source order",
            "field_mapping": mapping,
        },
    )


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
