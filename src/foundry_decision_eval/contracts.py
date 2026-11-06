from typing import Any, Literal

from pydantic import BaseModel, Field


class Rubric(BaseModel):
    name: str
    version: str = "1"
    question: str
    type: Literal["score", "noul", "choice"] = "score"
    criteria: list[str] | dict[str, str] | None = None
    positive_label: str | None = None
    threshold: int = Field(default=70, ge=0, le=100, strict=True)
    required_fields: list[str] = Field(default_factory=lambda: ["query", "response"])


class EvaluationData(BaseModel):
    item: dict[str, Any]
    sample: dict[str, Any] | None = None


class EvaluationRequest(BaseModel):
    schema_version: Literal["0.0.1"]
    evaluator_name: str
    evaluator_version: str
    evaluation_level: Literal["turn", "conversation"]
    data: EvaluationData


def error_result(code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "0.0.1",
        "status": "error",
        "score": None,
        "reason": None,
        "passed": None,
        "error": {"code": code, "message": message},
    }
