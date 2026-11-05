import asyncio
import math
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx
from azure.core.credentials_async import AsyncTokenCredential

from .contracts import Rubric

TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class DecisionClient:
    def __init__(
        self,
        endpoint: str,
        deployment: str,
        credential: AsyncTokenCredential,
        http: httpx.AsyncClient,
    ):
        origin = urlsplit(endpoint)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
        ):
            raise ValueError("Use the HTTPS resource origin, not /api/projects/...")
        self.url = endpoint.rstrip("/") + "/providers/microsoft/v1/systemone"
        self.deployment = deployment
        self.credential = credential
        self.http = http

    async def evaluate(
        self, rubric: Rubric, item: dict[str, Any], sample: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        missing = [field for field in rubric.required_fields if item.get(field) in (None, "")]
        if missing:
            raise ValueError("Missing mapped fields: " + ", ".join(missing))
        question: dict[str, Any] = {"type": rubric.type, "instructions": rubric.question}
        if rubric.criteria is not None:
            question["criteria"] = rubric.criteria
        state: dict[str, Any] = {"item": item}
        if sample is not None:
            state["sample"] = sample
        start = perf_counter()
        async with asyncio.timeout(25):
            token = await self.credential.get_token(TOKEN_SCOPE)
            response = await self.http.post(
                self.url,
                headers={"Authorization": "Bearer " + token.token},
                json={
                    "model": self.deployment,
                    "state": state,
                    "questions": {rubric.name: question},
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            answer = payload["answers"][rubric.name]
            score = normalized_score(answer, rubric) * 100
        return {
            "schema_version": "0.0.1",
            "status": "completed",
            "score": score,
            "reason": None,
            "threshold": rubric.threshold,
            "passed": score >= rubric.threshold,
            "properties": {
                "raw_answer": answer,
                "usage": payload.get("usage"),
                "model": payload.get("model"),
                "deployment": self.deployment,
                "wall_time_ms": (perf_counter() - start) * 1000,
            },
        }


def normalized_score(answer: dict[str, Any], rubric: Rubric) -> float:
    if answer["type"] != rubric.type:
        raise ValueError("Decision answer type does not match the rubric")
    if rubric.type == "score":
        if not isinstance(rubric.criteria, list) or len(rubric.criteria) < 2:
            raise ValueError("score requires at least two ordered levels")
        raw = answer["score"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("Decision returned a non-numeric score")
        value = raw / (len(rubric.criteria) - 1)
    elif rubric.type == "noul":
        value = answer["noul"]
    else:
        if rubric.positive_label is None:
            raise ValueError("choice requires a positive_label")
        value = answer["probabilities"][rubric.positive_label]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Decision returned a non-numeric score")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Decision returned an invalid score")
    return float(value)
