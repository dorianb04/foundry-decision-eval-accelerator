import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError
from pydantic import ValidationError

from foundry_decision_eval.contracts import (
    EvaluationData,
    EvaluationRequest,
    EvaluationResult,
    Rubric,
)
from foundry_decision_eval.decision import (
    TOKEN_SCOPE,
    DecisionError,
    DecisionScorer,
    interpret,
    resource_endpoint,
)


@pytest.fixture
def rubric():
    return Rubric.model_validate_json(Path("examples/rubric.json").read_text())


@pytest.fixture
def payload():
    return json.loads(Path("examples/decision-response.json").read_text())


class Credential:
    async def get_token(self, scope):
        assert scope == TOKEN_SCOPE
        return SimpleNamespace(token="synthetic-test-token")


def request(rubric, item=None):
    return EvaluationRequest(
        schema_version="0.0.1",
        evaluator_name=rubric.name,
        evaluator_version=rubric.version,
        evaluation_level="turn",
        data=EvaluationData(item=item or {"query": "q", "response": "r", "context": "c"}),
    )


def test_score_normalization_and_raw_preservation(rubric, payload):
    result = interpret(payload, rubric)
    assert result.score == 90
    assert result.threshold == 70 and result.passed is True
    assert result.reason is None
    assert result.properties["raw_answer"] == payload["answers"][rubric.name]
    assert result.properties["normalized_score"] == 0.9


def test_noul():
    rubric = Rubric(name="supported", type="noul", question="Supported?", threshold=75)
    result = interpret({"answers": {"supported": {"type": "noul", "noul": 0.75}}}, rubric)
    assert result.score == 75 and result.passed is True


def test_choice_scores_positive_label_not_selected_confidence():
    rubric = Rubric(
        name="label",
        type="choice",
        question="Supported?",
        criteria={"yes": "Supported", "no": "Not supported"},
        positive_label="yes",
    )
    result = interpret(
        {
            "answers": {
                "label": {
                    "type": "choice",
                    "choice": "no",
                    "probabilities": {"yes": 0.2, "no": 0.8},
                }
            }
        },
        rubric,
    )
    assert result.score == 20 and result.passed is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 2, "0.5", None])
def test_invalid_probability_is_never_a_score(value):
    rubric = Rubric(name="gate", question="Valid?", type="noul")
    with pytest.raises(DecisionError):
        interpret({"answers": {"gate": {"type": "noul", "noul": value}}}, rubric)


@pytest.mark.parametrize("mutation", ["name", "type", "probabilities", "sum", "score", "missing"])
def test_malformed_model_responses(rubric, payload, mutation):
    answer = payload["answers"][rubric.name]
    if mutation == "name":
        payload["answers"] = {"other": answer}
    elif mutation == "type":
        answer["type"] = "choice"
    elif mutation == "probabilities":
        answer["probabilities"] = {"bad": 1}
    elif mutation == "sum":
        answer["probabilities"]["0"] = 0.5
    elif mutation == "score":
        answer["score"] = 0
    elif mutation == "missing":
        payload = {}
    with pytest.raises(DecisionError):
        interpret(payload, rubric)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test",
        "https://example.test/api/projects/p",
        "https://u:p@example.test",
        "https://example.test?sig=secret",
        "https://example.test#x",
    ],
)
def test_reject_project_path_and_unsafe_resource_endpoints(endpoint):
    with pytest.raises(ValueError):
        resource_endpoint(endpoint)


def test_integer_threshold_contract(rubric):
    with pytest.raises(ValidationError):
        Rubric(**{**rubric.model_dump(), "threshold": 0.7})


def test_error_cannot_pass():
    with pytest.raises(ValidationError):
        EvaluationResult(status="skipped", score=100, passed=True)


def test_model_call_contract(rubric, payload):
    async def run():
        def handler(req):
            assert str(req.url) == "https://example.test/providers/microsoft/v1/systemone"
            body = json.loads(req.content)
            assert body["model"] == "Decision-1"
            assert body["questions"][rubric.name]["type"] == "score"
            assert body["state"]["item"]["response"] == "r"
            assert req.headers["authorization"] == "Bearer synthetic-test-token"
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await DecisionScorer(
                "https://example.test", "Decision-1", rubric, Credential(), client
            ).evaluate(request(rubric))
        assert result.status == "completed"

    asyncio.run(run())


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_upstream_http_failure_is_explicit(rubric, status):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(status, text="private upstream data")
            )
        ) as client:
            result = await DecisionScorer(
                "https://example.test", "Decision-1", rubric, Credential(), client
            ).evaluate(request(rubric))
        assert result.status == "error" and result.score is None and result.passed is None
        assert str(status) in result.error.message
        assert "private" not in result.model_dump_json()

    asyncio.run(run())


def test_timeout_includes_token_acquisition(rubric):
    class SlowCredential:
        async def get_token(self, scope):
            await asyncio.sleep(0.1)
            return SimpleNamespace(token="t")

    async def run():
        async with httpx.AsyncClient() as client:
            scorer = DecisionScorer(
                "https://example.test", "Decision-1", rubric, SlowCredential(), client, 0.01
            )
            result = await scorer.evaluate(request(rubric))
        assert result.error.code == "upstream_timeout" and result.score is None

    asyncio.run(run())


def test_authentication_failure(rubric):
    class BadCredential:
        async def get_token(self, scope):
            raise ClientAuthenticationError("sensitive credential detail")

    async def run():
        async with httpx.AsyncClient() as client:
            result = await DecisionScorer(
                "https://example.test", "Decision-1", rubric, BadCredential(), client
            ).evaluate(request(rubric))
        assert result.error.code == "authentication_error"
        assert "sensitive" not in result.model_dump_json()

    asyncio.run(run())


def test_missing_field_or_wrong_evaluator_does_not_call_upstream(rubric):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: pytest.fail("No network call expected"))
        ) as client:
            scorer = DecisionScorer(
                "https://example.test", "Decision-1", rubric, Credential(), client
            )
            assert (
                await scorer.evaluate(request(rubric, {"query": "q"}))
            ).error.code == "missing_fields"
            wrong = request(rubric).model_copy(update={"evaluator_version": "other"})
            assert (await scorer.evaluate(wrong)).error.code == "unknown_evaluator"

    asyncio.run(run())
