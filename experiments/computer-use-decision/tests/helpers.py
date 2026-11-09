"""Test helpers: scripted (oracle) policy and fake clients. No model calls anywhere."""

from __future__ import annotations

from dataclasses import dataclass

from cudec.policy import Decision


class ScriptedPolicy:
    """Follows a hand-written path: (op, label-substring[, scroll_if_missing]) then DONE."""

    name = "scripted"
    consequences = True

    def __init__(self, goal: str, script: list[tuple], done_at_end: bool = True):
        self.goal, self.script, self.done_at_end = goal, list(script), done_at_end
        self.i = 0

    async def decide(self, obs, space, history):
        if self.i >= len(self.script):
            return Decision("done" if self.done_at_end else "handoff", op="DONE", requests=0)
        op, needle, *rest = self.script[self.i]
        for a in space.get(op, []):
            if needle in a.label:
                self.i += 1
                return Decision("act", a, op=op)
        if rest and rest[0] and "SCROLL_DOWN" in space:
            return Decision("act", space["SCROLL_DOWN"][0], op="SCROLL_DOWN")
        return Decision("handoff", op="HANDOFF", reason=f"script cannot find {op} {needle!r}")


class FakeChat:
    """Stands in for ChatClient: returns canned field text by field label."""

    def __init__(self, texts: dict[str, str]):
        self.texts = texts
        self.calls = 0

    async def complete_json(self, deployment, system, user, schema, **kw):
        import json

        self.calls += 1
        label = json.loads(user)["field"]["label"]
        return {"json": {"text": self.texts[label]}, "latency_ms": 1.0, "input_tokens": 10,
                "output_tokens": 5, "model": "fake"}


@dataclass
class FakeDecisionClient:
    """Returns a canned `answers` dict; records the request it was given."""

    answers: dict
    last: tuple | None = None

    async def ask(self, state, questions, purpose="step"):
        self.last = (state, questions)
        return {"answers": self.answers, "latency_ms": 1.0, "input_tokens": 100, "output_tokens": 3}
