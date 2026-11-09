"""Diagnostic probe (not part of the agent comparison): on the injection fixture's product page,
remove DONE from the offered operations and see what Decision-1 would do instead.

    python -m cudec.probe --n 5
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from playwright.async_api import async_playwright

from .budget import Budget
from .clients import AzCliCredential, DecisionHTTP
from .executor import observe
from .fixtures import FixtureServer
from .observe import build_action_space
from .policy import DecisionPolicy
from .run import RUNS, VIEWPORT


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    budget = Budget(RUNS / "budget.jsonl")
    server = FixtureServer()
    base = server.start()
    out = []
    async with httpx.AsyncClient() as http, async_playwright() as pw:
        client = DecisionHTTP(AzCliCredential(), http, budget)
        browser = await pw.chromium.launch()
        page = await (await browser.new_context(viewport=VIEWPORT)).new_page()
        fx = server.fixtures["inject"]
        await page.goto(base + fx.base + "/p/1")
        obs = await observe(page)
        space = build_action_space(obs)
        policy = DecisionPolicy(client, fx.goal)
        state, questions = policy.build_request(obs, space, [])
        del questions["operation"]["criteria"]["DONE"]
        for _ in range(args.n):
            r = await client.ask(state, questions, purpose="probe:inject-no-done")
            a = r["answers"]
            out.append({"op_probs": a["operation"]["probabilities"],
                        "click_probs": a["click_target"]["probabilities"],
                        "noul": a["goal_achieved"]["noul"], "latency_ms": round(r["latency_ms"]),
                        "input_tokens": r["input_tokens"]})
        out.append({"click_labels": {x.target: x.label for x in space["CLICK"]}})
        await browser.close()
    server.stop()
    (RUNS / "results-probe.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
