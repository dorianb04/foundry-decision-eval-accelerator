import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from foundry_decision_eval.contracts import Rubric
from foundry_decision_eval.decision import DecisionClient, normalized_score
from foundry_decision_eval.main import create_app


@pytest.fixture
def rubric():
    return Rubric(name="grounded", question="Supported?", criteria=["No", "Partly", "Yes"])


def test_score_conversion_keeps_the_declared_scale(rubric):
    assert normalized_score({"type": "score", "score": 1.5}, rubric) == 0.75
    gate = Rubric(name="gate", question="Supported?", type="noul")
    assert normalized_score({"type": "noul", "noul": 0.8}, gate) == 0.8
    choice = Rubric(name="label", question="Supported?", type="choice", positive_label="yes")
    assert normalized_score({"type": "choice", "probabilities": {"yes": 0.2}}, choice) == 0.2


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 3])
def test_invalid_model_scores_are_not_passing_results(rubric, value):
    with pytest.raises(ValueError):
        normalized_score({"type": "score", "score": value}, rubric)


def test_decision_api_request_and_telemetry(rubric):
    class Credential:
        async def get_token(self, scope):
            assert scope == "https://cognitiveservices.azure.com/.default"
            return SimpleNamespace(token="synthetic-test-token")

    def handler(request):
        assert request.url.path == "/providers/microsoft/v1/systemone"
        body = json.loads(request.content)
        assert body["model"] == "Decision-1" and body["state"]["item"]["response"] == "answer"
        return httpx.Response(
            200,
            json={
                "model": "microsoft-decision-1",
                "answers": {"grounded": {"type": "score", "score": 1.5}},
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await DecisionClient(
                "https://example.test", "Decision-1", Credential(), http
            ).evaluate(rubric, {"query": "q", "response": "answer"})
        assert result["score"] == 75 and result["passed"] is True and result["reason"] is None
        assert result["properties"]["usage"]["input_tokens"] == 10
        assert result["properties"]["wall_time_ms"] >= 0

    asyncio.run(run())


class FakeDecision:
    def __init__(self, fail=False):
        self.fail = fail

    async def evaluate(self, rubric, item, sample):
        if self.fail:
            raise TimeoutError
        return {
            "schema_version": "0.0.1",
            "status": "completed",
            "score": 80,
            "reason": None,
            "passed": True,
            "properties": {"name": rubric.name},
        }


def body(name="grounded"):
    return {
        "schema_version": "0.0.1",
        "evaluator_name": name,
        "evaluator_version": "1",
        "evaluation_level": "turn",
        "data": {"item": {"query": "q", "response": "a"}},
    }


def test_endpoint_authentication_dispatch_and_contract(rubric):
    other = rubric.model_copy(update={"name": "other"})
    with TestClient(
        create_app(rubrics=[rubric, other], decision=FakeDecision(), api_key="fixture")
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/evaluate", json=body()).status_code == 401
        result = client.post("/evaluate", json=body("other"), headers={"api-key": "fixture"}).json()
        assert result["properties"]["name"] == "other" and result["reason"] is None
        unknown = client.post(
            "/evaluate", json=body("missing"), headers={"api-key": "fixture"}
        ).json()
        assert unknown["status"] == "error" and unknown["score"] is None


def test_model_timeout_returns_foundry_error_not_a_score(rubric):
    with TestClient(
        create_app(rubrics=[rubric], decision=FakeDecision(fail=True), api_key="fixture")
    ) as client:
        result = client.post("/evaluate", json=body(), headers={"api-key": "fixture"}).json()
        assert result["status"] == "error" and result["passed"] is None and result["score"] is None


def test_request_validation_does_not_echo_dataset_content(rubric):
    with TestClient(
        create_app(rubrics=[rubric], decision=FakeDecision(), api_key="fixture")
    ) as client:
        invalid = body()
        invalid["schema_version"] = "private-input"
        response = client.post("/evaluate", json=invalid, headers={"api-key": "fixture"})
        assert response.status_code == 422 and "private-input" not in response.text
