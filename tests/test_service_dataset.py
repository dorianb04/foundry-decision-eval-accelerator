import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from foundry_decision_eval.contracts import EvaluationResult, failure
from foundry_decision_eval.dataset import field_value, save_json, select
from foundry_decision_eval.service import create_app


class Scorer:
    async def evaluate(self, request):
        if request.data.item.get("fail"):
            return failure("upstream_http_error", "Decision returned HTTP 429")
        return EvaluationResult(status="completed", score=80, threshold=70, passed=True)


def body():
    return {
        "schema_version": "0.0.1",
        "evaluator_name": "e",
        "evaluator_version": "1",
        "evaluation_level": "turn",
        "data": {"item": {"response": "synthetic"}},
    }


def test_requires_auth_for_external_service():
    with pytest.raises(ValueError):
        create_app(Scorer(), api_key=None)


def test_authentication_and_wire_contract():
    with TestClient(create_app(Scorer(), api_key="fixture-key")) as client:
        assert client.post("/evaluate", json=body()).status_code == 401
        result = client.post("/evaluate", json=body(), headers={"api-key": "fixture-key"})
        assert result.status_code == 200
        data = result.json()
        assert data["schema_version"] == "0.0.1"
        assert data["score"] == 80 and data["passed"] is True
        assert data["reason"] is None and "error" not in data


def test_upstream_error_contract():
    with TestClient(create_app(Scorer(), api_key=None, local_only=True)) as client:
        data = body()
        data["data"]["item"]["fail"] = True
        result = client.post("/evaluate", json=data).json()
        assert result["status"] == "error"
        assert result["score"] is None and result["passed"] is None
        assert result["error"]["code"] == "upstream_http_error"


def test_invalid_schema_returns_explicit_error_without_echoing_input():
    with TestClient(create_app(Scorer(), api_key=None, local_only=True)) as client:
        data = body()
        data["schema_version"] = "bad-private-value"
        response = client.post("/evaluate", json=data)
        assert response.status_code == 422
        assert response.json()["status"] == "error"
        assert "bad-private-value" not in response.text


def test_optional_sample_and_conversation_are_accepted():
    with TestClient(create_app(Scorer(), api_key=None, local_only=True)) as client:
        data = body()
        data["evaluation_level"] = "conversation"
        data["data"]["sample"] = {"response": "generated sample"}
        assert client.post("/evaluate", json=data).json()["status"] == "completed"


def test_selection_reproducibility_and_field_mapping(tmp_path):
    dataset = tmp_path / "synthetic.jsonl"
    dataset.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": n,
                    "nested": {"answer": f"a{n}"},
                    "unused": "not exported",
                }
            )
            for n in range(130)
        ),
        encoding="utf-8",
    )
    mapping = {"id": "id", "response": "nested.answer"}
    a = select(dataset, mapping, limit=5, seed=42)
    b = select(dataset, mapping, limit=5, seed=42)
    assert a == b and a.provenance["zero_based_indices"] == [6, 28, 57, 62, 70]
    assert all(set(item) == {"id", "response"} for item in a.items)
    assert a.provenance["selected_row_count"] == 5
    assert len(a.provenance["dataset_sha256"]) == 64


@pytest.mark.parametrize("limit", [0, -1, 11, 100])
def test_smoke_limit_is_enforced(limit):
    with pytest.raises(ValueError):
        select(Path("examples/synthetic.jsonl"), {"query": "query"}, limit=limit)


def test_missing_field_never_silently_becomes_empty():
    with pytest.raises(ValueError, match="missing mapped"):
        select(Path("examples/synthetic.jsonl"), {"response": "missing"})
    assert field_value({"sample.output_text": "literal"}, "sample.output_text") == "literal"


def test_save_refuses_overwrite(tmp_path):
    target = tmp_path / "output.json"
    save_json(target, {"one": 1})
    with pytest.raises(FileExistsError):
        save_json(target, {"two": 2})
