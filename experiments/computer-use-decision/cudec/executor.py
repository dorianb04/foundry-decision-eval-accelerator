"""Executor: resolves the chosen target from an observed node, re-checks freshness and occlusion.

Model output is only ever an operation name plus an element index chosen from the observed
table. It never becomes a selector, coordinate, shell command or JavaScript: node ids come
from our own snapshot, click coordinates are computed by code from the node's current geometry,
and option values come from the observed <option> list.
"""

from __future__ import annotations

from dataclasses import dataclass

from .observe import SNAPSHOT_JS, Action, Observation

RESOLVE_JS = """(id) => {
  const e = window.__cu && window.__cu.nodes.get(id);
  if (!e || !e.isConnected) return {ok: false, reason: 'missing'};
  const r = e.getBoundingClientRect();
  const x = r.x + r.width / 2, y = r.y + r.height / 2;
  if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return {ok: false, reason: 'offscreen'};
  const hit = document.elementFromPoint(x, y);
  const ok = !!hit && (hit === e || e.contains(hit) || hit.closest('label')?.control === e);
  return {ok, reason: ok ? '' : 'covered', x, y};
}"""

SCROLL_FRACTION = 0.7
MAX_TEXT_LEN = 200


@dataclass
class ExecResult:
    status: str  # ok | stale | covered | missing | offscreen | refused
    detail: str = ""
    fresh: Observation | None = None


async def observe(page) -> Observation:
    # A navigation triggered by the previous action can destroy the JS context mid-read: retry.
    for attempt in range(4):
        try:
            raw = await page.evaluate(SNAPSHOT_JS)
            break
        except Exception as exc:
            if attempt == 3 or "context was destroyed" not in str(exc):
                raise
            await settle(page)
    return Observation.from_raw(raw)


async def settle(page) -> None:
    await page.wait_for_timeout(60)  # let a click-triggered navigation start
    try:
        await page.wait_for_load_state("load", timeout=3000)
    except Exception:
        pass
    await page.wait_for_timeout(40)


def sanitize_text(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if not text or len(text) > MAX_TEXT_LEN:
        return None
    return text


async def execute(page, obs: Observation, action: Action, text: str | None = None) -> ExecResult:
    fresh = await observe(page)
    if fresh.fp != obs.fp:
        return ExecResult("stale", "page changed since the observation", fresh)
    if action.op in ("SCROLL_DOWN", "SCROLL_UP"):
        sign = 1 if action.op == "SCROLL_DOWN" else -1
        await page.evaluate(f"window.scrollBy(0, {sign} * Math.round(innerHeight * {SCROLL_FRACTION}))")
        await settle(page)
        return ExecResult("ok")
    if action.node is None:
        return ExecResult("refused", "no node")
    pos = await page.evaluate(RESOLVE_JS, action.node)
    if not pos["ok"]:
        return ExecResult(pos["reason"], "target not clickable")
    if action.op == "CLICK":
        await page.mouse.click(pos["x"], pos["y"])
    elif action.op == "TYPE_TEXT":
        clean = sanitize_text(text)
        if clean is None:
            return ExecResult("refused", "invalid text")
        await page.mouse.click(pos["x"], pos["y"])
        await page.keyboard.press("Control+A")
        await page.keyboard.type(clean)
    elif action.op == "SELECT":
        handle = await page.evaluate_handle("(id) => window.__cu.nodes.get(id)", action.node)
        el = handle.as_element()
        if el is None or action.option is None:
            return ExecResult("refused", "no select element")
        await el.select_option(value=action.option)
    else:
        return ExecResult("refused", f"unsupported op {action.op}")
    await settle(page)
    return ExecResult("ok")
