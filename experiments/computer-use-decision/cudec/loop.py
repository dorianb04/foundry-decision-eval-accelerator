"""One episode: observe -> mask -> decide -> (write text) -> execute -> verify. Per-step logging."""

from __future__ import annotations

import time

from .budget import BudgetExceeded
from .executor import execute, observe, sanitize_text
from .observe import LoopGuard, build_action_space
from .policy import write_text


async def run_episode(page, base_url: str, fixture, policy, chat, writer_model: str, *,
                      mask: bool = True, max_steps: int | None = None) -> dict:
    max_steps = max_steps or fixture.max_steps
    await page.goto(base_url + fixture.start_url_path)
    obs = await observe(page)
    guard = LoopGuard()
    history: list[str] = []
    steps: list[dict] = []
    counters = {"actions": 0, "no_effect": 0, "repeat_actions": 0, "stale": 0, "failed_exec": 0}
    stop, said_done, writer_ms = "step_limit", False, 0.0
    started = time.perf_counter()
    fresh_obs = True
    try:
        for _cycle in range(max_steps):
            visits = guard.visit(obs.fp) if fresh_obs else guard.visits.get(obs.fp, 1)
            fresh_obs = False
            reason = guard.stalled()
            if reason:
                stop = reason
                break
            full = build_action_space(obs, policy.consequences)
            space, n_masked = guard.mask(obs, full) if mask else (full, 0)
            step: dict = {"i": len(steps) + 1, "url": obs.url, "fp": obs.fp, "visits": visits,
                          "state_tokens_est": obs.tokens_estimate(policy.goal),
                          "n_elements": len(obs.elements),
                          "n_candidates": sum(len(v) for v in space.values()), "n_masked": n_masked}
            steps.append(step)
            dec = await policy.decide(obs, space, history)
            step.update(kind=dec.kind, op=dec.op, reason=dec.reason, noul=dec.noul,
                        op_probs={k: round(v, 4) for k, v in dec.op_probs.items()},
                        target_probs={k: round(v, 4) for k, v in dec.target_probs.items()},
                        model_ms=round(dec.latency_ms), input_tokens=dec.input_tokens,
                        output_tokens=dec.output_tokens)
            if dec.kind == "done":
                said_done = True
                stop = "done"
                break
            if dec.kind != "act":
                stop = {"handoff": "handoff", "lowconf": "low_confidence"}.get(dec.kind, "invalid_output")
                step["stop_detail"] = dec.reason
                break
            action = dec.action
            step.update(target=action.target, label=action.label)
            if action.key in guard.tried.get(obs.fp, set()):
                counters["repeat_actions"] += 1
                step["repeat"] = True
            text = None
            if action.op == "TYPE_TEXT":
                w = await write_text(chat, writer_model, policy.goal, obs, action)
                text = sanitize_text(w["text"])
                writer_ms += w["latency_ms"]
                step["writer"] = {"text": text, "ms": round(w["latency_ms"]), "in": w["input_tokens"],
                                  "out": w["output_tokens"]}
            res = await execute(page, obs, action, text)
            step["exec"] = res.status
            if res.status == "stale":
                counters["stale"] += 1
                obs, fresh_obs = res.fresh, True
                step["discarded"] = True
                continue
            counters["actions"] += 1
            new_obs = await observe(page)
            changed = res.status == "ok" and new_obs.fp != obs.fp
            if res.status != "ok":
                counters["failed_exec"] += 1
            if not changed:
                counters["no_effect"] += 1
            step["effect"] = changed
            guard.record(obs.fp, action, text, obs.url, changed)
            history.append(f"{action.op} {action.label}" + (f" = {text!r}" if text else "")
                           + ("" if changed else " (no visible effect)"))
            obs, fresh_obs = new_obs, True
    except BudgetExceeded:
        raise
    except Exception as exc:  # keep the partial trace; the batch continues
        stop = f"error:{type(exc).__name__}: {exc}"[:300]
    verdict = fixture.verify()
    success = bool(said_done and verdict["success"])
    if said_done and not verdict["success"]:
        stop = "false_done"
    return {
        "success": success,
        "stop_reason": stop,
        "state_success": bool(verdict["success"]),
        "wrong_actions": verdict["wrong_actions"],
        "end_state": verdict["detail"],
        **counters,
        "decisions": sum(1 for s in steps if "kind" in s),
        "wall_ms": round((time.perf_counter() - started) * 1000),
        "decision_ms": round(sum(s.get("model_ms", 0) for s in steps)),
        "writer_ms": round(writer_ms),
        "decision_in_tokens": sum(s.get("input_tokens", 0) for s in steps),
        "decision_out_tokens": sum(s.get("output_tokens", 0) for s in steps),
        "writer_in_tokens": sum(s.get("writer", {}).get("in", 0) for s in steps),
        "writer_out_tokens": sum(s.get("writer", {}).get("out", 0) for s in steps),
        "writer_calls": sum(1 for s in steps if "writer" in s),
        "steps": steps,
    }
