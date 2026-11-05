from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential, DefaultAzureCredential
from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from openai import APIError

from .contracts import EvaluationData, EvaluationRequest, Rubric
from .dataset import read_jsonl, save_json, select
from .decision import DecisionScorer, interpret
from .foundry import discover, download_dataset, endpoint_definition, register_endpoint, run_cloud
from .judge import judge
from .service import create_app
from .stored import export_run_selection


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set {name}; see .env.example (environment files are not auto-loaded)")
    return value


def async_credential():
    if os.environ.get("AZURE_CREDENTIAL_MODE", "cli") == "default":
        return AsyncDefaultAzureCredential(process_timeout=5)
    if os.environ.get("AZURE_CREDENTIAL_MODE", "cli") != "cli":
        raise ValueError("AZURE_CREDENTIAL_MODE must be cli or default")
    return AsyncAzureCliCredential(process_timeout=5)


@contextmanager
def project_client():
    mode = os.environ.get("AZURE_CREDENTIAL_MODE", "cli")
    if mode not in {"cli", "default"}:
        raise ValueError("AZURE_CREDENTIAL_MODE must be cli or default")
    credential = (
        AzureCliCredential(process_timeout=5)
        if mode == "cli"
        else DefaultAzureCredential(process_timeout=5)
    )
    with (
        credential,
        AIProjectClient(
            endpoint=required_env("AZURE_AI_PROJECT_ENDPOINT"), credential=credential
        ) as project,
    ):
        yield project


async def score_selection(args: argparse.Namespace) -> int:
    selection = load_json(args.selection)
    items = selection["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise ValueError("Selection must contain 1..10 items")
    rubric = Rubric.model_validate(load_json(args.rubric))
    endpoint = required_env("AZURE_AI_RESOURCE_ENDPOINT")
    deployment = os.environ.get("DECISION_DEPLOYMENT", "Decision-1")
    results = []
    async with async_credential() as credential, httpx.AsyncClient() as client:
        scorer = DecisionScorer(endpoint, deployment, rubric, credential, client)
        for item in items:
            request = EvaluationRequest(
                schema_version="0.0.1",
                evaluator_name=rubric.name,
                evaluator_version=rubric.version,
                evaluation_level="turn",
                data=EvaluationData(item=item),
            )
            decision_result = await scorer.evaluate(request)
            row = {"decision": decision_result.model_dump(exclude_none=True)}
            if args.judge_deployment:
                judge_result = await judge(
                    item,
                    rubric,
                    endpoint=endpoint,
                    deployment=args.judge_deployment,
                    credential=credential,
                    client=client,
                )
                row["judge"] = judge_result.model_dump(exclude_none=True)
            results.append(row)
    report: dict[str, Any] = {
        "provenance": selection["provenance"],
        "rubric": rubric.model_dump(),
        "decision_deployment": deployment,
        "judge_deployment": args.judge_deployment,
        "results": results,
    }
    pairs = [row for row in results if "judge" in row]
    comparable = [
        row for row in pairs if row["decision"]["status"] == row["judge"]["status"] == "completed"
    ]
    if args.judge_deployment:
        report["comparison"] = {
            "paired_row_count": len(pairs),
            "completed_pair_count": len(comparable),
            "mean_absolute_score_difference": (
                sum(abs(row["decision"]["score"] - row["judge"]["score"]) for row in comparable)
                / len(comparable)
                if comparable
                else None
            ),
            "note": "Descriptive agreement only; neither model is ground truth or calibrated",
        }
    save_json(args.out, report)
    errors = sum(result["status"] != "completed" for row in results for result in row.values())
    print(json.dumps({"selected_rows": len(items), "noncompleted_results": errors}))
    return 1 if errors else 0


def serve(args: argparse.Namespace) -> None:
    local_only = args.allow_anonymous_local and args.host in {"127.0.0.1", "::1", "localhost"}
    if args.allow_anonymous_local and not local_only:
        raise ValueError("Anonymous mode is permitted only on a loopback host")
    rubric = Rubric.model_validate(load_json(args.rubric))
    credential = async_credential()
    client = httpx.AsyncClient()
    scorer = DecisionScorer(
        required_env("AZURE_AI_RESOURCE_ENDPOINT"),
        os.environ.get("DECISION_DEPLOYMENT", "Decision-1"),
        rubric,
        credential,
        client,
    )
    app = create_app(
        scorer,
        api_key=os.environ.get("EVALUATOR_API_KEY"),
        local_only=local_only,
        api_key_header=os.environ.get("EVALUATOR_API_KEY_HEADER", "api-key"),
    )

    async def run() -> None:
        try:
            await uvicorn.Server(
                uvicorn.Config(app, host=args.host, port=args.port, access_log=False)
            ).serve()
        finally:
            await client.aclose()
            await credential.close()

    asyncio.run(run())


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Foundry Decision evaluation accelerator")
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser(
        "demo", help="Replay a synthetic response, with no network or scoring"
    )
    demo.add_argument("--rubric", type=Path, default=Path("examples/rubric.json"))
    demo.add_argument("--response", type=Path, default=Path("examples/decision-response.json"))
    select_cmd = commands.add_parser("select", help="Freeze a reproducible 1..10-row subset")
    select_cmd.add_argument("--dataset", type=Path, required=True)
    select_cmd.add_argument("--mapping", type=Path, required=True)
    select_cmd.add_argument("--out", type=Path, required=True)
    select_cmd.add_argument("--limit", type=int, default=5)
    select_cmd.add_argument("--seed", type=int, default=42)
    select_cmd.add_argument("--dataset-id", help="Optional registered dataset provenance reference")
    score = commands.add_parser("score", help="Score only stored outputs in a frozen selection")
    score.add_argument("--selection", type=Path, required=True)
    score.add_argument("--rubric", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True)
    score.add_argument("--judge-deployment", help="Optional same-row, same-rubric LLM judge")
    service = commands.add_parser("serve", help="Run the endpoint evaluator HTTP adapter")
    service.add_argument("--rubric", type=Path, required=True)
    service.add_argument("--host", default="127.0.0.1")
    service.add_argument("--port", type=int, default=8080)
    service.add_argument("--allow-anonymous-local", action="store_true")
    discovery = commands.add_parser(
        "discover", help="List bounded metadata without dataset content"
    )
    discovery.add_argument("--limit", type=int, default=10)
    fetch = commands.add_parser(
        "fetch-dataset", help="Download an existing JSONL dataset privately"
    )
    fetch.add_argument("--name", required=True)
    fetch.add_argument("--version", required=True)
    fetch.add_argument("--blob-name")
    fetch.add_argument("--out", type=Path, required=True)
    inspect = commands.add_parser(
        "inspect-dataset", help="Show row count, field names and types only"
    )
    inspect.add_argument("--dataset", type=Path, required=True)
    export = commands.add_parser(
        "export-evaluator", help="Read an existing evaluator into local cache"
    )
    export.add_argument("--name", required=True)
    export.add_argument("--version", required=True)
    export.add_argument("--out", type=Path, required=True)
    stored = commands.add_parser(
        "export-run", help="Reuse stored traces for a frozen dataset subset"
    )
    stored.add_argument("--selection", type=Path, required=True)
    stored.add_argument("--eval-id", required=True)
    stored.add_argument("--run-id", required=True)
    stored.add_argument("--out", type=Path, required=True)
    register = commands.add_parser(
        "register", help="Plan/create an evaluator on an EXISTING connection"
    )
    register.add_argument("--name", required=True)
    register.add_argument("--connection", required=True)
    register.add_argument("--execute", action="store_true")
    cloud = commands.add_parser(
        "cloud-run", help="Plan/run dataset-only evaluation, with no target"
    )
    cloud.add_argument("--selection", type=Path, required=True)
    cloud.add_argument("--evaluator", required=True)
    cloud.add_argument("--existing-eval-id")
    cloud.add_argument("--out", type=Path, required=True)
    cloud.add_argument("--wait-seconds", type=float, default=300)
    cloud.add_argument("--execute", action="store_true")
    return root


def execute(args: argparse.Namespace) -> int:
    if args.command == "demo":
        result = interpret(load_json(args.response), Rubric.model_validate(load_json(args.rubric)))
        print(
            json.dumps({"mode": "synthetic fixture replay; NOT inference", **result.model_dump()})
        )
    elif args.command == "select":
        selection = select(args.dataset, load_json(args.mapping), limit=args.limit, seed=args.seed)
        if args.dataset_id:
            selection.provenance["registered_dataset_id"] = args.dataset_id
        save_json(args.out, {"items": selection.items, "provenance": selection.provenance})
        print(json.dumps(selection.provenance))
    elif args.command == "inspect-dataset":
        rows = read_jsonl(args.dataset)
        print(
            json.dumps(
                {
                    "row_count": len(rows),
                    "fields": {
                        key: sorted({type(row[key]).__name__ for row in rows if key in row})
                        for key in sorted(set().union(*(row.keys() for row in rows)))
                    },
                }
            )
        )
    elif args.command == "score":
        if args.out.exists():
            raise FileExistsError("Refusing to overwrite an existing report")
        return asyncio.run(score_selection(args))
    elif args.command == "serve":
        serve(args)
    elif args.command == "register" and not args.execute:
        print(json.dumps(endpoint_definition(args.name, args.connection)))
    elif args.command == "cloud-run" and not args.execute:
        selection = load_json(args.selection)
        print(
            json.dumps(
                {
                    "action": "reuse eval definition"
                    if args.existing_eval_id
                    else "create dataset-only eval",
                    "evaluator": args.evaluator,
                    "row_count": len(selection["items"]),
                    "provenance": selection["provenance"],
                    "requires": (
                        "reachable authenticated endpoint, existing connection "
                        "and catalog evaluator"
                    ),
                }
            )
        )
    else:
        with project_client() as project:
            if args.command == "discover":
                print(json.dumps(discover(project, args.limit)))
            elif args.command == "fetch-dataset":
                print(
                    json.dumps(
                        download_dataset(
                            project, args.name, args.version, args.out, blob_name=args.blob_name
                        )
                    )
                )
            elif args.command == "export-evaluator":
                evaluator = project.evaluators.get_version(name=args.name, version=args.version)
                save_json(args.out, evaluator.as_dict())
                print(json.dumps({"name": evaluator.name, "version": evaluator.version}))
            elif args.command == "export-run":
                if args.out.exists():
                    raise FileExistsError("Refusing to overwrite an existing stored selection")
                with project.get_openai_client() as client:
                    result = export_run_selection(
                        client, load_json(args.selection), eval_id=args.eval_id, run_id=args.run_id
                    )
                save_json(args.out, result)
                print(json.dumps(result["provenance"]))
            elif args.command == "register":
                evaluator = register_endpoint(project, args.name, args.connection)
                print(json.dumps({"name": evaluator.name, "version": evaluator.version}))
            elif args.command == "cloud-run":
                if args.out.exists():
                    raise FileExistsError("Refusing to overwrite an existing cloud report")
                selection = load_json(args.selection)
                report = run_cloud(
                    project,
                    selection["items"],
                    args.evaluator,
                    existing_eval_id=args.existing_eval_id,
                    wait_seconds=args.wait_seconds,
                )
                report["provenance"] = selection["provenance"]
                save_json(args.out, report)
                print(
                    json.dumps(
                        {
                            key: value
                            for key, value in report.items()
                            if key not in {"output_items", "provenance"}
                        }
                    )
                )
                return 0 if report["status"] == "completed" else 1
    return 0


def main() -> None:
    try:
        code = execute(parser().parse_args())
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1
    except (AzureError, APIError) as exc:
        # SDK exception text may contain signed storage URLs or service payloads.
        status = getattr(exc, "status_code", None)
        print(f"Cloud request failed: {type(exc).__name__}; HTTP status={status}", file=sys.stderr)
        code = 1
    sys.exit(code)
