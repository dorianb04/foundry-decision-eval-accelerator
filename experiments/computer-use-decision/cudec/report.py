"""Aggregate runs/results-*.jsonl into results.json and results.md (tables + hand-written analysis.md)."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .cost import DECISION_IN_PER_M, LLM_RATES, decision_cost, llm_cost
from .policy import Thresholds

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def load(label: str) -> list[dict]:
    path = RUNS / f"results-{label}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def med(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def fmt(v, nd=0):
    return "-" if v is None else (f"{v:.{nd}f}" if nd else f"{round(v)}")


def main_runs() -> tuple[list[dict], list[dict]]:
    """Main table runs. A run that died on a harness error (not a model error) is replaced by its rerun."""
    runs = load("main")
    reruns = load("rerun")
    kept, excluded = [], []
    for r in runs:
        if r["stop_reason"].startswith("error:"):
            rr = next((x for x in reruns if (x["fixture"], x["agent"], x["rep"]) == (r["fixture"], r["agent"], r["rep"])), None)
            r["excluded"] = "harness error (execution context destroyed during navigation); rerun after fix"
            excluded.append(r)
            if rr:
                kept.append(rr)
                continue
        kept.append(r)
    return kept, excluded


def selector_cost(r: dict) -> float:
    if r["agent"] == "decision":
        return decision_cost(r.get("decision_in_tokens", 0))
    return llm_cost(r["agent"][4:], r.get("decision_in_tokens", 0), r.get("decision_out_tokens", 0))


def table(runs: list[dict], fixtures, agents) -> str:
    rows = ["| fixture | agent | success | actions med (optimal) | wrong med | no-effect med | model time med (s) | "
            "writer time med (s) | decisions med | selector cost med ($) | total est. cost med ($) | wall med (s) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for fid in fixtures:
        for agent in agents:
            rs = [r for r in runs if r["fixture"] == fid and r["agent"] == agent]
            if not rs:
                continue
            ok = sum(r["success"] for r in rs)
            rows.append(
                f"| {fid} | {agent} | {ok}/{len(rs)} | {fmt(med([r.get('actions') for r in rs]))} ({rs[0]['optimal_actions']}) "
                f"| {fmt(med([r.get('wrong_actions') for r in rs]))} | {fmt(med([r.get('no_effect') for r in rs]))} "
                f"| {fmt((med([r.get('decision_ms') for r in rs]) or 0) / 1000, 1)} "
                f"| {fmt((med([r.get('writer_ms') for r in rs]) or 0) / 1000, 1)} "
                f"| {fmt(med([r.get('decisions') for r in rs]))} "
                f"| {'%.6f' % med([selector_cost(r) for r in rs])} "
                f"| {'%.5f' % med([r['est_cost_usd'] for r in rs])} "
                f"| {fmt((med([r.get('wall_ms') for r in rs]) or 0) / 1000, 1)} |"
            )
    return "\n".join(rows)


def per_run(runs: list[dict]) -> str:
    rows = ["| fixture | agent | rep | success | stop | actions | wrong | no-effect | decisions | model s | writer s | cost $ |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        rows.append(
            f"| {r['fixture']} | {r['agent']} | {r['rep']} | {r['success']} | {r['stop_reason'][:44]} | {r.get('actions')} "
            f"| {r.get('wrong_actions')} | {r.get('no_effect')} | {r.get('decisions')} "
            f"| {(r.get('decision_ms') or 0) / 1000:.1f} | {(r.get('writer_ms') or 0) / 1000:.1f} | {r['est_cost_usd']:.5f} |"
        )
    return "\n".join(rows)


def confidence_summary(runs: list[dict]) -> dict:
    op_p, tgt_p, masked = [], [], 0
    for r in runs:
        for s in r["steps"]:
            if s.get("kind") == "act":
                op_p.append(s["op_probs"][s["op"]])
                if s.get("target_probs"):
                    tgt_p.append(max(s["target_probs"].values()))
            masked += 1 if s.get("n_masked") else 0
    return {"acting_steps": len(op_p), "min_op_prob": min(op_p), "min_target_prob": min(tgt_p),
            "steps_with_masked_candidates": masked}


def main() -> None:
    main_kept, excluded = main_runs()
    ablations = {lab: load(lab) for lab in ("abl-plain", "abl-history", "abl-nomask", "abl-sol-medium",
                                              "control-inject2")}
    probe = json.loads((RUNS / "results-probe.json").read_text()) if (RUNS / "results-probe.json").exists() else None
    budget_lines = [json.loads(x) for x in (RUNS / "budget.jsonl").read_text().splitlines()]
    counts = {k: sum(1 for b in budget_lines if b["kind"] == k) for k in ("decision", "llm")}
    all_runs = main_kept + excluded + [r for rs in ablations.values() for r in rs]
    meta = {
        "request_counts": counts, "caps": {"decision": 400, "llm": 200},
        "rates_usd_per_million": {"decision_input": DECISION_IN_PER_M, "decision_output": 0.0,
                                  **{k: {"input": v[0], "output": v[1]} for k, v in LLM_RATES.items()}},
        "thresholds": Thresholds().__dict__, "writer_model": "gpt-5.4-mini", "viewport": [1100, 400],
        "excluded_runs": [{"fixture": r["fixture"], "agent": r["agent"], "rep": r["rep"], "reason": r["excluded"]}
                          for r in excluded],
    }
    (ROOT / "results.json").write_text(json.dumps({"meta": meta, "runs": all_runs, "probe": probe}, indent=1),
                                       encoding="utf-8")

    fx = ["shop", "form", "list", "inject"]
    agents = ["decision", "llm:gpt-5.6-sol"]
    dec_runs = [r for r in main_kept if r["agent"] == "decision"]
    md = ["# Results: Decision-1 as action selector vs LLM-only (gpt-5.6-sol)", "",
          f"Request counts used (whole experiment, incl. pilots and probes): **{counts['decision']} Decision-1 / "
          f"{counts['llm']} LLM** (caps 400 / 200). Cost = estimates from token counts (rates below), not billed data. "
          "N=5 per cell, so medians and counts are descriptive only; no statistical-ranking claims.", "",
          "## Main table (N=5 per fixture and agent; medians)", "", table(main_kept, fx, agents), "",
          "model time = sum of per-step decision-request latencies; writer time = gpt-5.4-mini calls for TYPE_TEXT "
          "(used by both agents); decisions = model requests for action selection; cost includes the writer.", "",
          "## Control and ablations (small N, Decision agent unless stated)", ""]
    for lab, rs in ablations.items():
        if rs:
            md += [f"### {lab}", "", table(rs, sorted({r['fixture'] for r in rs}), sorted({r['agent'] for r in rs})), ""]
    md += ["## Decision-1 confidence on acting steps (main runs)", "", "```", json.dumps(confidence_summary(dec_runs)), "```", ""]
    if probe:
        md += ["## Injection probe (DONE removed, product page, 5 requests)", "", "```", json.dumps(probe[0]), "```", ""]
    md += ["## Per-run table (main runs)", "", per_run(main_kept), ""]
    if excluded:
        md += ["Excluded from the main table (harness error, replaced by rerun): "
               + ", ".join(f"{r['fixture']}/{r['agent']}/rep{r['rep']}" for r in excluded), ""]
    analysis = ROOT / "analysis.md"
    if analysis.exists():
        md += [analysis.read_text(encoding="utf-8")]
    (ROOT / "results.md").write_text("\n".join(md), encoding="utf-8")
    (ROOT / "budget-log.jsonl").write_text("\n".join(json.dumps(b) for b in budget_lines) + "\n", encoding="utf-8")
    print("wrote results.json/results.md", counts)


if __name__ == "__main__":
    main()
