"""Run the experiment. Example:

    python -m cudec.run --agents decision llm:gpt-5.6-sol --fixtures shop form list inject --reps 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright

from .budget import Budget, BudgetExceeded
from .clients import AzCliCredential, ChatClient, DecisionHTTP
from .cost import decision_cost, llm_cost
from .fixtures import PRIMARY, FixtureServer
from .loop import run_episode
from .policy import DecisionPolicy, LLMPolicy, Thresholds

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
WRITER_MODEL = "gpt-5.4-mini"
VIEWPORT = {"width": 1100, "height": 400}


def make_policy(spec: str, fixture, decision: DecisionHTTP, chat: ChatClient, args):
    consequences = not args.plain_options
    if spec == "decision":
        return DecisionPolicy(decision, fixture.goal, Thresholds(), consequences=consequences,
                              history_in_text=args.history_in_text)
    if spec.startswith("llm:"):
        return LLMPolicy(chat, spec[4:], fixture.goal, consequences=consequences,
                         reasoning_effort=args.reasoning)
    raise SystemExit(f"unknown agent {spec}")


def episode_cost(rec: dict, spec: str) -> float:
    c = llm_cost(WRITER_MODEL, rec["writer_in_tokens"], rec["writer_out_tokens"])
    if spec == "decision":
        c += decision_cost(rec["decision_in_tokens"])
    else:
        c += llm_cost(spec[4:], rec["decision_in_tokens"], rec["decision_out_tokens"])
    return c


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", nargs="+", default=["decision"])
    ap.add_argument("--fixtures", nargs="+", default=list(PRIMARY))
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--rep-start", type=int, default=1)
    ap.add_argument("--reasoning", default="low", help="reasoning_effort for LLM baseline agents")
    ap.add_argument("--label", default="main")
    ap.add_argument("--no-mask", action="store_true", help="ablation: no code-side action masking")
    ap.add_argument("--history-in-text", action="store_true", help="ablation: history in the state text")
    ap.add_argument("--plain-options", action="store_true", help="ablation: no consequence text on options")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    budget = Budget(RUNS / "budget.jsonl")
    out_path = RUNS / f"results-{args.label}.jsonl"
    cred = AzCliCredential()
    server = FixtureServer()
    base = server.start()
    host = urlsplit(base).netloc
    config = {"mask": not args.no_mask, "history_in_text": args.history_in_text,
              "consequence_options": not args.plain_options, "label": args.label,
              "llm_reasoning_effort": args.reasoning}
    print(f"fixtures on {base}; budget before: {budget.counts}")
    async with httpx.AsyncClient() as http, async_playwright() as pw:
        decision, chat = DecisionHTTP(cred, http, budget), ChatClient(cred, http, budget)
        browser = await pw.chromium.launch(headless=not args.headed)
        try:
            for fid in args.fixtures:
                for spec in args.agents:
                    for rep in range(args.rep_start, args.rep_start + args.reps):
                        fx = server.fixtures[fid]
                        fx.reset()
                        ctx = await browser.new_context(viewport=VIEWPORT, accept_downloads=False,
                                                        service_workers="block")

                        async def guard(route):
                            if urlsplit(route.request.url).netloc == host:
                                await route.continue_()
                            else:
                                await route.abort()

                        await ctx.route("**/*", guard)
                        page = await ctx.new_page()
                        policy = make_policy(spec, fx, decision, chat, args)
                        t = time.time()
                        try:
                            rec = await run_episode(page, base, fx, policy, chat, WRITER_MODEL,
                                                    mask=not args.no_mask)
                        except BudgetExceeded as exc:
                            print("BUDGET:", exc)
                            await ctx.close()
                            raise
                        except Exception as exc:  # keep the batch going, record the failure
                            rec = {"success": False, "stop_reason": f"error:{type(exc).__name__}: {exc}"[:300],
                                   "steps": [], "wrong_actions": None, "writer_in_tokens": 0,
                                   "writer_out_tokens": 0, "decision_in_tokens": 0, "decision_out_tokens": 0}
                        await ctx.close()
                        rec.update(fixture=fid, agent=spec, rep=rep, config=config,
                                   optimal_actions=fx.optimal_actions, max_steps=fx.max_steps,
                                   est_cost_usd=round(episode_cost(rec, spec), 6),
                                   started_at=t, budget_after=dict(budget.counts))
                        with out_path.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(rec) + "\n")
                        print(f"{fid:6} {spec:18} rep{rep} ok={rec['success']!s:5} stop={rec['stop_reason'][:40]:40} "
                              f"actions={rec.get('actions')} wrong={rec.get('wrong_actions')} "
                              f"model_ms={rec.get('decision_ms')} cost=${rec['est_cost_usd']:.4f} budget={budget.counts}")
        finally:
            await browser.close()
            server.stop()
    print("done", budget.counts)


if __name__ == "__main__":
    asyncio.run(main())
