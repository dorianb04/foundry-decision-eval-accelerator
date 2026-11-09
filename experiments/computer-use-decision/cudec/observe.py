"""Observation adapter + action space. Pure code, no model calls (unit-testable offline)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SNAPSHOT_JS = (Path(__file__).with_name("snapshot.js")).read_text(encoding="utf-8")

MAX_ELEMENTS = 120
MAX_TEXT_CHARS = 2400
MAX_TARGETS = 255  # Decision-1 choice limit per question

CLICK_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem"}
TYPE_ROLES = {"textbox", "searchbox", "spinbutton"}


@dataclass
class Observation:
    url: str
    title: str
    text: list[str]
    elements: list[dict]
    scroll: dict
    fp: str
    omitted: int = 0

    @classmethod
    def from_raw(cls, raw: dict) -> Observation:
        elements = raw["elements"]
        omitted = max(0, len(elements) - MAX_ELEMENTS)
        elements = elements[:MAX_ELEMENTS]
        names = {e["name"] for e in elements}
        text, used = [], 0
        for line in raw["text"]:
            if line in names:  # already represented by an element
                continue
            if used + len(line) > MAX_TEXT_CHARS:
                break
            text.append(line)
            used += len(line)
        for i, el in enumerate(elements, 1):
            el["i"] = str(i)
        fp = hashlib.sha1(raw["fp"].encode()).hexdigest()[:12]
        return cls(raw["url"], raw["title"], text, elements, raw["scroll"], fp, omitted)

    def state(self, goal: str, history: list[str] | None = None) -> dict:
        """The JSON state sent to the model. History is only included for the ablation."""
        page = {"path": self.url, "title": self.title, "text": self.text}
        scroll = []
        if self.scroll.get("can_up"):
            scroll.append("can scroll up")
        if self.scroll.get("can_down"):
            scroll.append("more content below")
        if scroll:
            page["scroll"] = ", ".join(scroll)
        els = []
        for e in self.elements:
            item = {"i": e["i"], "role": e["role"], "name": e["name"]}
            if e.get("value") not in (None, ""):
                item["value"] = e["value"]
            elif e["role"] in TYPE_ROLES or e["role"] == "select":
                item["value"] = ""
            if "checked" in e:
                item["checked"] = e["checked"]
            if e.get("required"):
                item["required"] = True
            els.append(item)
        state = {"task": goal, "page": page, "elements": els}
        if history is not None:
            state["recent_actions"] = history[-6:]
        return state

    def tokens_estimate(self, goal: str) -> int:
        return len(json.dumps(self.state(goal))) // 4


@dataclass(frozen=True)
class Action:
    op: str
    target: str = ""  # id the model chooses among: element index, or "index:option"
    node: int | None = None
    option: str | None = None
    key: tuple = ()
    label: str = ""
    desc: str = ""

    def describe(self) -> str:
        return f"{self.op} {self.label}".strip()


def _consequence(el: dict) -> str:
    role = el["role"]
    if role == "link":
        return f"navigates to {el.get('href', 'another page')}"
    if role == "button":
        return "submits the enclosing form" if el.get("submits") else "activates this button"
    if role in ("checkbox", "radio", "switch"):
        return "sets it to unchecked" if el.get("checked") else "sets it to checked"
    return "activates it"


def build_action_space(obs: Observation, consequences: bool = True) -> dict[str, list[Action]]:
    """Candidate actions per operation, in DOM order. Operations with no candidates are absent."""
    space: dict[str, list[Action]] = {}
    for el in obs.elements:
        i, role, name = el["i"], el["role"], el["name"]
        if role in CLICK_ROLES:
            state = f" ({'checked' if el['checked'] else 'unchecked'})" if "checked" in el else ""
            label = f'[{i}] {role} "{name}"{state}'
            desc = label + (f" -> {_consequence(el)}" if consequences else "")
            space.setdefault("CLICK", []).append(
                Action("CLICK", i, el["node"], None, ("CLICK", role, name, el.get("href", "")), label, desc)
            )
        elif role in TYPE_ROLES:
            cur = f" (current value: '{el.get('value', '')}')" if el.get("value") else " (empty)"
            label = f'[{i}] {role} "{name}"{cur}'
            desc = label + (" -> replaces the field's text with new text" if consequences else "")
            space.setdefault("TYPE_TEXT", []).append(
                Action("TYPE_TEXT", i, el["node"], None, ("TYPE_TEXT", role, name), label, desc)
            )
        elif role == "select":
            for j, opt in enumerate(el.get("options", []), 1):
                if opt["selected"] or opt["value"] == "":
                    continue
                label = f'[{i}] select "{name}" = "{opt["label"]}"'
                desc = label + (f" -> sets the dropdown '{name}' to '{opt['label']}'" if consequences else "")
                space.setdefault("SELECT", []).append(
                    Action("SELECT", f"{i}:{j}", el["node"], opt["value"],
                           ("SELECT", name, opt["label"]), label, desc)
                )
    if obs.scroll.get("can_down"):
        space["SCROLL_DOWN"] = [Action("SCROLL_DOWN", key=("SCROLL_DOWN",), label="scroll down",
                                       desc="scroll down -> reveals more of the page")]
    if obs.scroll.get("can_up"):
        space["SCROLL_UP"] = [Action("SCROLL_UP", key=("SCROLL_UP",), label="scroll up",
                                     desc="scroll up -> reveals the previous part of the page")]
    for op in ("CLICK", "TYPE_TEXT", "SELECT"):
        if op in space and len(space[op]) > MAX_TARGETS:
            space[op] = space[op][:MAX_TARGETS]
    return space


@dataclass
class LoopGuard:
    """Code-side loop protection (the model is never asked to remember failures).

    * `tried[fp]` holds action keys already executed on a page state: they are masked out of the
      candidate list the next time the same state is observed.
    * Fields we already filled with the value we typed are masked from TYPE_TEXT.
    * Stop conditions: `max_no_effect` consecutive actions that changed nothing, or the same
      page state visited `max_visits` times.
    """

    max_no_effect: int = 3
    max_visits: int = 3
    tried: dict[str, set] = field(default_factory=dict)
    typed: dict[tuple, str] = field(default_factory=dict)
    visits: dict[str, int] = field(default_factory=dict)
    no_effect_streak: int = 0

    def visit(self, fp: str) -> int:
        self.visits[fp] = self.visits.get(fp, 0) + 1
        return self.visits[fp]

    def mask(self, obs: Observation, space: dict[str, list[Action]]) -> tuple[dict, int]:
        tried = self.tried.get(obs.fp, set())
        out, masked = {}, 0
        for op, actions in space.items():
            keep = []
            for a in actions:
                typed_here = op == "TYPE_TEXT" and self.typed.get((obs.url, a.key[2])) is not None
                filled = typed_here and _value_of(obs, a) == self.typed[(obs.url, a.key[2])]
                if a.key in tried or filled:
                    masked += 1
                else:
                    keep.append(a)
            if keep:
                out[op] = keep
        return out, masked

    def record(self, before_fp: str, action: Action, text: str | None, url: str, changed: bool) -> None:
        self.tried.setdefault(before_fp, set()).add(action.key)
        if action.op == "TYPE_TEXT" and text is not None:
            self.typed[(url, action.key[2])] = text
        self.no_effect_streak = 0 if changed else self.no_effect_streak + 1

    def stalled(self) -> str | None:
        if self.no_effect_streak >= self.max_no_effect:
            return f"stalled: {self.no_effect_streak} consecutive actions with no effect"
        if self.visits and max(self.visits.values()) >= self.max_visits:
            return f"stalled: same page state visited {self.max_visits} times"
        return None


def _value_of(obs: Observation, action: Action) -> str:
    for el in obs.elements:
        if el["node"] == action.node:
            return el.get("value", "")
    return ""
