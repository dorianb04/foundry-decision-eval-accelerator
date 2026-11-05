import json
from pathlib import Path

from .contracts import Rubric


def load_rubrics(path: str | Path) -> list[Rubric]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rubrics = [Rubric.model_validate(item) for item in value]
    names = [(rubric.name, rubric.version) for rubric in rubrics]
    if not rubrics or len(set(names)) != len(names):
        raise ValueError("Configure unique evaluator name/version pairs")
    return rubrics
