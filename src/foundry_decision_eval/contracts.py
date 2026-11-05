from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = "1"
    question: str = Field(min_length=1)
    type: Literal["score", "noul", "choice"] = "score"
    criteria: list[str] | dict[str, str] | None = None
    positive_label: str | None = None
    threshold: Annotated[StrictInt, Field(ge=0, le=100)] | None = 70
    required_fields: list[str] = Field(default_factory=lambda: ["query", "response"])

    @model_validator(mode="after")
    def validate_criteria(self) -> Rubric:
        if self.type == "score" and (not isinstance(self.criteria, list) or len(self.criteria) < 2):
            raise ValueError("score requires at least two ordered criteria")
        if self.type == "choice" and (
            not isinstance(self.criteria, dict)
            or len(self.criteria) < 2
            or self.positive_label not in self.criteria
        ):
            raise ValueError("choice requires labels and a positive_label in criteria")
        if (
            self.type == "noul"
            and self.criteria is not None
            and (not isinstance(self.criteria, dict) or set(self.criteria) != {"true", "false"})
        ):
            raise ValueError("noul criteria must contain exactly true and false")
        if not self.required_fields or len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields must be nonempty and unique")
        return self

    def question_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type, "instructions": self.question}
        if self.criteria is not None:
            body["criteria"] = self.criteria
        return body


class EvaluationData(BaseModel):
    item: dict[str, Any]
    sample: dict[str, Any] | None = None


class EvaluationRequest(BaseModel):
    schema_version: Literal["0.0.1"]
    evaluator_name: str
    evaluator_version: str
    evaluation_level: Literal["turn", "conversation"]
    data: EvaluationData


class ErrorDetail(BaseModel):
    code: str
    message: str


class EvaluationResult(BaseModel):
    schema_version: Literal["0.0.1"] = "0.0.1"
    score: float | bool | None = None
    reason: str | None = None
    status: Literal["completed", "error", "skipped"]
    properties: dict[str, Any] | None = None
    threshold: StrictInt | None = None
    passed: bool | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def validate_status(self) -> EvaluationResult:
        if self.status == "completed":
            if self.score is None or not math.isfinite(self.score) or self.error is not None:
                raise ValueError("completed results require a finite score and no error")
        elif self.score is not None or self.passed is not None:
            raise ValueError("error/skipped results cannot have a score or pass flag")
        if self.status == "error" and self.error is None:
            raise ValueError("error results require details")
        return self

    def wire(self) -> dict[str, Any]:
        return self.model_dump(exclude={"error"} if self.error is None else set())


def failure(code: str, message: str) -> EvaluationResult:
    return EvaluationResult(status="error", error=ErrorDetail(code=code, message=message))
