import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from foundry_decision_eval.cli import execute, parser
from foundry_decision_eval.contracts import Rubric
from foundry_decision_eval.judge import judge


class Credential:
    async def get_token(self, _scope):
        return SimpleNamespace(token="synthetic")


@pytest.mark.parametrize(
    "content",
    [
        '{"score":80,"reason":"Supported by synthetic evidence"}',
        '{"score":999,"reason":"bad"}',
        "not-json",
    ],
)
def test_judge_validates_shape_and_uses_same_item(content):
    rubric = Rubric.model_validate_json(Path("examples/rubric.json").read_text())
    item = {"query": "q", "response": "r", "context": "c"}

    def handler(req):
        payload = json.loads(req.content)
        assert json.loads(payload["messages"][-1]["content"]) == item
        assert payload["model"] == "judge-deployment"
        assert req.url.path == "/openai/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await judge(
                item,
                rubric,
                endpoint="https://example.test",
                deployment="judge-deployment",
                credential=Credential(),
                client=client,
            )
        if content.startswith('{"score":80'):
            assert result.score == 80 and result.passed is True
        else:
            assert result.status == "error" and result.score is None

    asyncio.run(run())


def test_registration_is_dry_run_without_azure_auth(monkeypatch, capsys):
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    assert execute(parser().parse_args(["register", "--name", "e", "--connection", "c"])) == 0
    assert json.loads(capsys.readouterr().out)["definition"]["type"] == "endpoint"


def test_demo_is_explicitly_fixture_replay(capsys):
    assert execute(parser().parse_args(["demo"])) == 0
    assert "NOT inference" in capsys.readouterr().out
