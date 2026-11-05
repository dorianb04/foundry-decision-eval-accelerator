"""Evaluate stored outputs on the same small subset with Decision and native LLM judges."""

import argparse
import json
import os
import random
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from foundry_decision_eval.evaluators import load_rubrics
from foundry_decision_eval.foundry import create_evaluation, create_run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data", default="examples/synthetic.jsonl")
parser.add_argument("--config", default="examples/rubrics.json")
parser.add_argument("--name", default="decision-judge-smoke")
parser.add_argument("--judge-deployment", required=True)
parser.add_argument("--limit", type=int, default=5)
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--execute", action="store_true")
args = parser.parse_args()
if not 1 <= args.limit <= 10:
    parser.error("Use 1..10 rows for this smoke example")
path = Path(args.data)
if path.suffix == ".jsonl":
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
else:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = value["items"] if isinstance(value, dict) else value
indices = sorted(random.Random(args.seed).sample(range(len(rows)), min(args.limit, len(rows))))
items = [rows[index] for index in indices]
print({"rows": len(items), "seed": args.seed, "source_indices": indices})
if args.execute:
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"], credential=credential
        ) as project,
    ):
        evaluation = create_evaluation(
            project, args.name, load_rubrics(args.config), llm_deployment=args.judge_deployment
        )
        run = create_run(project, evaluation.id, items, args.name)
        print({"eval_id": evaluation.id, "run_id": run.id, "status": run.status})
