from __future__ import annotations

import asyncio
import math
from typing import Any
from urllib.parse import urlsplit

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import ClientAuthenticationError

from .contracts import EvaluationRequest, EvaluationResult, Rubric, failure

TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class DecisionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def resource_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTPS resource origin, not the /api/projects/... endpoint")
    return value.rstrip("/")


def number(value: Any, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise DecisionError("invalid_model_response", "Decision returned an invalid numeric value")
    return float(value)


def interpret(payload: Any, rubric: Rubric) -> EvaluationResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise DecisionError("invalid_model_response", "Decision returned no answers object")
    answer = payload["answers"].get(rubric.name)
    if not isinstance(answer, dict) or answer.get("type") != rubric.type:
        raise DecisionError(
            "invalid_model_response", "Decision answer type/name did not match rubric"
        )
    if rubric.type == "noul":
        normalized = number(answer.get("noul"), 0, 1)
    else:
        probabilities = answer.get("probabilities")
        assert rubric.criteria is not None
        labels = (
            {str(i) for i in range(len(rubric.criteria))}
            if rubric.type == "score"
            else set(rubric.criteria)
        )
        if not isinstance(probabilities, dict) or set(probabilities) != labels:
            raise DecisionError(
                "invalid_model_response", "Decision probabilities do not match levels"
            )
        values = {label: number(value, 0, 1) for label, value in probabilities.items()}
        if not math.isclose(sum(values.values()), 1, abs_tol=0.005):
            raise DecisionError(
                "invalid_model_response", "Decision probabilities do not sum to one"
            )
        if rubric.type == "score":
            raw_score = number(answer.get("score"), 0, len(rubric.criteria) - 1)
            expected = sum(int(label) * value for label, value in values.items())
            if not math.isclose(raw_score, expected, abs_tol=0.01):
                raise DecisionError(
                    "invalid_model_response", "Decision score disagrees with levels"
                )
            normalized = raw_score / (len(rubric.criteria) - 1)
        else:
            if answer.get("choice") not in labels:
                raise DecisionError("invalid_model_response", "Decision selected an unknown label")
            assert rubric.positive_label is not None
            normalized = values[rubric.positive_label]
    score = 100 * normalized
    return EvaluationResult(
        status="completed",
        score=score,
        reason=None,
        threshold=rubric.threshold,
        passed=score >= rubric.threshold if rubric.threshold is not None else None,
        properties={
            "normalized_score": normalized,
            "score_scale": "100 * normalized_score; higher is better",
            "raw_answer": answer,
            "model": payload.get("model"),
            "usage": payload.get("usage"),
            "rubric": {"name": rubric.name, "version": rubric.version},
        },
    )


class DecisionScorer:
    def __init__(
        self,
        endpoint: str,
        deployment: str,
        rubric: Rubric,
        credential: AsyncTokenCredential,
        client: httpx.AsyncClient,
        budget_seconds: float = 25,
    ):
        self.endpoint = resource_endpoint(endpoint)
        self.deployment = deployment
        self.rubric = rubric
        self.credential = credential
        self.client = client
        if not 0 < budget_seconds <= 25:
            raise ValueError("budget_seconds must be in (0, 25] for Foundry's 30-second limit")
        self.budget_seconds = budget_seconds

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        if (
            request.evaluator_name != self.rubric.name
            or request.evaluator_version != self.rubric.version
        ):
            return failure("unknown_evaluator", "Evaluator name/version is not configured")
        item = request.data.item
        missing = [
            field
            for field in self.rubric.required_fields
            if field not in item or item[field] is None or item[field] == ""
        ]
        if missing:
            return failure("missing_fields", "Missing mapped fields: " + ", ".join(missing))
        state: dict[str, Any] = {"item": item}
        if request.data.sample is not None:
            state["sample"] = request.data.sample
        try:
            async with asyncio.timeout(self.budget_seconds):
                token = await self.credential.get_token(TOKEN_SCOPE)
                response = await self.client.post(
                    self.endpoint + "/providers/microsoft/v1/systemone",
                    headers={"Authorization": "Bearer " + token.token},
                    json={
                        "model": self.deployment,
                        "state": state,
                        "questions": {self.rubric.name: self.rubric.question_body()},
                    },
                    timeout=min(20, self.budget_seconds),
                )
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise DecisionError(
                        "invalid_model_response", "Decision returned invalid JSON"
                    ) from exc
                return interpret(payload, self.rubric)
        except (TimeoutError, httpx.TimeoutException):
            return failure("upstream_timeout", "Decision exceeded the bounded inference budget")
        except httpx.HTTPStatusError as exc:
            return failure(
                "upstream_http_error", f"Decision returned HTTP {exc.response.status_code}"
            )
        except httpx.RequestError:
            return failure("upstream_connection_error", "Could not connect to Decision endpoint")
        except ClientAuthenticationError:
            return failure("authentication_error", "Azure credential could not acquire a token")
        except DecisionError as exc:
            return failure(exc.code, str(exc))
